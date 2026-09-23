"""Patch the game list inside a GDEMU menu disc image.

openMenu and GDMENU read their game list off the menu disc itself — openMenu
opens ``OPENMENU.INI`` with a ``/cd/`` prefix (``backend/gd_list.c``) — so a
game installed onto the card stays invisible until that file changes.  Nothing
else on the card is consulted.  GD MENU Card Manager solves this by rebuilding
the whole menu image; this module instead rewrites the one file in place, which
needs no menu payload and no ISO builder.

Why it is safe to do in place:

* The menu's data tracks are plain ISO9660 with 2048-byte user sectors (the
  ``.iso`` tracks in its ``disc.gdi``) — no raw sector headers, no EDC/ECC to
  recompute, so a byte written is a byte the console reads.
* The list is one small file that gets a whole sector to itself.  A rewrite is
  refused unless the new text fits the sectors already allocated to it *and*
  the slack past the old end is still zero-filled, which proves no other file
  was packed into the same sector.
* Only two things change: the file's bytes, and the 4+4-byte size fields of the
  directory records that point at it.

Every touched region is backed up first (see ``BACKUP_DIR``) so a bad patch can
be undone byte for byte.

ISO9660 directory record layout used here (ECMA-119 §9.1)::

    0   1   record length
    2   8   extent LBA, both-endian (LE then BE)
    10  8   data length, both-endian
    25  1   file flags
    32  1   file identifier length
    33  n   file identifier ("OPENMENU.INI;1")
"""

from __future__ import annotations

import json
import re
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

SECTOR_SIZE = 2048
BACKUP_DIR = Path(__file__).parent / ".menu_image_backups"

# ISO9660 stores "NAME.EXT;1"; the version suffix is part of the identifier.
_INI_IDENTIFIERS = {
    "OPENMENU.INI": b"OPENMENU.INI;1",
    "LIST.INI": b"LIST.INI;1",
}
GDI_LINE_RE = re.compile(
    r'^\s*(?P<num>\d+)\s+(?P<lba>\d+)\s+(?P<type>\d+)\s+(?P<sector>\d+)\s+'
    r'(?P<name>"[^"]+"|\S+)\s+(?P<offset>\d+)\s*$'
)


class MenuImageError(RuntimeError):
    """The menu image can't be patched safely — caller should stage instead."""


@dataclass(frozen=True)
class GdiTrack:
    number: int
    lba: int
    kind: int  # 4 = data, 0 = audio
    sector_size: int
    path: Path

    @property
    def sectors(self) -> int:
        try:
            return self.path.stat().st_size // self.sector_size
        except OSError:
            return 0

    def contains(self, lba: int) -> bool:
        return self.kind == 4 and self.lba <= lba < self.lba + self.sectors

    def offset_of(self, lba: int) -> int:
        return (lba - self.lba) * self.sector_size


@dataclass
class _DirRecord:
    track: GdiTrack
    offset: int  # byte offset of the record inside its track file
    extent: int
    size: int


@dataclass
class MenuList:
    """Where a menu image keeps its game list, and how much room it has."""

    name: str
    content_track: GdiTrack
    content_offset: int
    size: int
    capacity: int
    records: list[_DirRecord] = field(default_factory=list)


def parse_gdi(gdi_path: Path) -> list[GdiTrack]:
    """Tracks declared by a ``.gdi`` sheet, quoted filenames included."""
    tracks: list[GdiTrack] = []
    try:
        lines = gdi_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        raise MenuImageError(f"Cannot read {gdi_path}: {exc}") from exc
    for line in lines[1:]:
        match = GDI_LINE_RE.match(line)
        if not match:
            continue
        name = match.group("name").strip('"')
        tracks.append(
            GdiTrack(
                number=int(match.group("num")),
                lba=int(match.group("lba")),
                kind=int(match.group("type")),
                sector_size=int(match.group("sector")),
                path=gdi_path.parent / name,
            )
        )
    return tracks


def _iter_identifier_offsets(path: Path, needle: bytes) -> list[int]:
    """Byte offsets of every ``needle`` occurrence in ``path``."""
    offsets: list[int] = []
    chunk_size = 4 * 1024 * 1024
    overlap = len(needle) + 64
    try:
        with open(path, "rb") as fh:
            base = 0
            carry = b""
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                window = carry + chunk
                start = 0
                while True:
                    found = window.find(needle, start)
                    if found < 0:
                        break
                    offsets.append(base - len(carry) + found)
                    start = found + 1
                base += len(chunk)
                carry = window[-overlap:]
    except OSError as exc:
        raise MenuImageError(f"Cannot read {path}: {exc}") from exc
    return offsets


def _read_record(track: GdiTrack, name_offset: int, identifier: bytes) -> _DirRecord | None:
    record_offset = name_offset - 33
    if record_offset < 0:
        return None
    try:
        with open(track.path, "rb") as fh:
            fh.seek(record_offset)
            record = fh.read(33)
    except OSError:
        return None
    if len(record) < 33:
        return None
    length = record[0]
    name_len = record[32]
    if name_len != len(identifier) or length < 33 + name_len:
        return None
    if record[25] & 0x02:  # directory flag — not our file
        return None
    extent = struct.unpack_from("<I", record, 2)[0]
    size = struct.unpack_from("<I", record, 10)[0]
    if struct.unpack_from(">I", record, 6)[0] != extent:
        return None  # both-endian fields disagree: not a directory record
    return _DirRecord(track=track, offset=record_offset, extent=extent, size=size)


def find_menu_lists(menu_folder: Path, name: str = "OPENMENU.INI") -> list[MenuList]:
    """Every copy of the game list inside a menu folder's disc image.

    A GD-ROM image carries two independent ISO9660 filesystems — one in the
    low-density area, one in the high density area — and a menu build puts the
    list in both, so there are normally two copies to keep in step.  Within one
    filesystem the directory records and the file's data can also live in
    *different* tracks: openMenu's build indexes from the high-density track
    (``track03``) while the bytes sit in the last data track (``track05``).
    Records are therefore grouped by extent, and each group's content is
    resolved separately.
    """
    identifier = _INI_IDENTIFIERS.get(name.upper())
    if identifier is None:
        raise MenuImageError(f"Unsupported menu list file: {name}")

    gdi_files = sorted(menu_folder.glob("*.gdi"))
    if not gdi_files:
        raise MenuImageError(f"No .gdi sheet in {menu_folder} — cannot patch the menu.")
    tracks = [t for t in parse_gdi(gdi_files[0]) if t.kind == 4]
    if not tracks:
        raise MenuImageError(f"{gdi_files[0].name} declares no data tracks.")
    unsupported = [t for t in tracks if t.sector_size != SECTOR_SIZE]

    records: list[_DirRecord] = []
    for track in tracks:
        if track.sector_size != SECTOR_SIZE:
            continue
        for offset in _iter_identifier_offsets(track.path, identifier):
            record = _read_record(track, offset, identifier)
            if record is not None:
                records.append(record)
    if not records:
        detail = (
            " (its data tracks use raw 2352-byte sectors, which this patcher "
            "does not rewrite)"
            if unsupported
            else ""
        )
        raise MenuImageError(f"No {name} found in {menu_folder}{detail}.")

    by_extent: dict[int, list[_DirRecord]] = {}
    for record in records:
        by_extent.setdefault(record.extent, []).append(record)

    located: list[MenuList] = []
    for extent, group in sorted(by_extent.items()):
        sizes = {r.size for r in group}
        if len(sizes) != 1:
            raise MenuImageError(
                f"{name} directory records at LBA {extent} disagree on size "
                f"{sorted(sizes)} — refusing to patch."
            )
        size = sizes.pop()
        content_track = next((t for t in tracks if t.contains(extent)), None)
        if content_track is None:
            raise MenuImageError(
                f"{name} points at LBA {extent}, which is outside every data track."
            )
        located.append(
            MenuList(
                name=name,
                content_track=content_track,
                content_offset=content_track.offset_of(extent),
                size=size,
                capacity=max(SECTOR_SIZE, -(-size // SECTOR_SIZE) * SECTOR_SIZE),
                records=group,
            )
        )
    return located


def read_menu_lists(menu_folder: Path, name: str = "OPENMENU.INI") -> list[str]:
    """Each copy of the game-list text as the console would read it."""
    texts: list[str] = []
    for located in find_menu_lists(menu_folder, name):
        with open(located.content_track.path, "rb") as fh:
            fh.seek(located.content_offset)
            texts.append(fh.read(located.size).decode("utf-8", errors="replace"))
    return texts


def _slack_is_free(located: MenuList) -> bool:
    """True when everything between the file's end and its last sector is zero.

    A non-zero tail means the image packed another file into the same sector,
    so growing this one would corrupt it.
    """
    slack = located.capacity - located.size
    if slack <= 0:
        return True
    with open(located.content_track.path, "rb") as fh:
        fh.seek(located.content_offset + located.size)
        return not fh.read(slack).strip(b"\x00")


def _backup(located: MenuList, label: str) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / f"{stamp}-{label}.json"
    with open(located.content_track.path, "rb") as fh:
        fh.seek(located.content_offset)
        original = fh.read(located.capacity)
    payload = {
        "name": located.name,
        "content_file": str(located.content_track.path),
        "content_offset": located.content_offset,
        "size": located.size,
        "content_hex": original.hex(),
        "records": [
            {"file": str(r.track.path), "offset": r.offset, "size": r.size}
            for r in located.records
        ],
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def patch_menu_list(
    menu_folder: Path,
    text: str,
    name: str = "OPENMENU.INI",
) -> dict:
    """Rewrite every copy of the game list inside the menu image.

    ``text`` is written with CRLF line endings, matching what the menu's own
    builder produces.  Every copy is checked for room *before* the first byte is
    written, so a list that fits one filesystem but not the other leaves the
    image exactly as it was rather than half-updated.  Raises ``MenuImageError``
    in that case.  Returns a summary dict (bytes written, copies, backups).
    """
    copies = find_menu_lists(menu_folder, name)
    payload = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")

    for located in copies:
        if len(payload) > located.capacity:
            raise MenuImageError(
                f"New {name} is {len(payload)} bytes but only {located.capacity} are "
                f"allocated in {located.content_track.path.name} — rebuild the menu "
                "with GD MENU Card Manager."
            )
        if not _slack_is_free(located):
            raise MenuImageError(
                f"The sector holding {name} in {located.content_track.path.name} is "
                "shared with other data — refusing to patch."
            )

    backups = [_backup(located, menu_folder.name or "menu") for located in copies]

    for located in copies:
        # Content first: a half-finished patch that still advertises the old
        # size reads as the old list, not as garbage.
        with open(located.content_track.path, "r+b") as fh:
            fh.seek(located.content_offset)
            fh.write(payload + b"\x00" * (located.capacity - len(payload)))
            fh.flush()

        for record in located.records:
            with open(record.track.path, "r+b") as fh:
                fh.seek(record.offset + 10)
                fh.write(
                    struct.pack("<I", len(payload)) + struct.pack(">I", len(payload))
                )
                fh.flush()

    return {
        "written": len(payload),
        "copies": len(copies),
        "previous_sizes": [located.size for located in copies],
        "content_files": [located.content_track.path for located in copies],
        "records": sum(len(located.records) for located in copies),
        "backups": backups,
    }


def restore_backup(backup_path: Path) -> None:
    """Undo a patch from its backup file."""
    payload = json.loads(Path(backup_path).read_text(encoding="utf-8"))
    original = bytes.fromhex(payload["content_hex"])
    with open(payload["content_file"], "r+b") as fh:
        fh.seek(int(payload["content_offset"]))
        fh.write(original)
    size = int(payload["size"])
    for record in payload["records"]:
        with open(record["file"], "r+b") as fh:
            fh.seek(int(record["offset"]) + 10)
            fh.write(struct.pack("<I", size) + struct.pack(">I", size))
