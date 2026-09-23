"""Dreamcast disc serials → the canonical ``DC_<serial>`` sync id.

Dreamcast joins the disc-serial systems (PS1/PS2/PSP/Vita/Saturn): a save is
keyed by the product number stamped in the disc's ``IP.BIN`` header, not by a
name slug.  That is what every Dreamcast device already uses to file saves —
MemCard PRO DC creates ``Dreamcast/T1249M/``, openMenu's Serial VMU creates
``OPENMENU/SAVES/T1249M/`` — so keying by serial means a save lands in one slot
no matter which device or emulator wrote it, and a mis-named ROM file can't
split a game across two slots.

Two spellings have to fold into one
-----------------------------------
Sega's own releases are inconsistent between the disc and the Redump DAT::

    Sonic Adventure (USA)   IP.BIN "MK-51000"      DAT serial "51000"
    18 Wheeler (Europe)     IP.BIN "MK-51064-50"   DAT serial "MK-51064-50"

Since either source may be the one we have, the canonical form drops a leading
``MK`` (Sega's publisher prefix) along with punctuation.  Both spellings then
land on ``51000`` / ``5106450``, and no third-party code is affected — those
start with ``T`` (``T-1249M``) or ``HDR`` for Sega Japan.

The region suffix is deliberately *kept*: ``MK-51064-50`` is the PAL disc and
``MK-51064`` the NTSC one, and they stay separate slots, exactly as the old
name-slug ids kept ``_europe`` and ``_usa`` apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DC_TITLE_ID_PREFIX = "DC_"

_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]")
# Sega's publisher prefix, only ever followed by the numeric product code.
_SEGA_PREFIX_RE = re.compile(r"^MK(?=\d)")


def canonical_dc_serial(serial: str) -> str:
    """``MK-51000`` / ``51000`` / ``t-1249m`` → ``51000`` / ``51000`` / ``T1249M``."""
    compact = _NON_ALNUM_RE.sub("", str(serial or "")).upper()
    return _SEGA_PREFIX_RE.sub("", compact)


def make_dc_title_id(serial: str) -> str:
    """Canonical sync id for a Dreamcast disc serial (``""`` if unusable)."""
    canonical = canonical_dc_serial(serial)
    return f"{DC_TITLE_ID_PREFIX}{canonical}" if canonical else ""


def parse_dc_title_id(title_id: str) -> str | None:
    """Serial inside a ``DC_<serial>`` id, or ``None`` for any other form.

    A name-slug id (``DC_sonic_adventure_usa``, what this project used before
    Dreamcast moved to serials) is *not* a serial id and returns ``None``.
    """
    text = str(title_id or "").strip()
    if not text.upper().startswith(DC_TITLE_ID_PREFIX):
        return None
    body = text[len(DC_TITLE_ID_PREFIX) :]
    if not body or "_" in body or not body.isalnum():
        return None
    return body.upper()


def dc_device_folder_ids(serial: str) -> list[str]:
    """Folder names a Dreamcast device may use for a serial, best first.

    Devices name their folders after ``IP.BIN``, which for Sega's numeric codes
    includes the ``MK`` the canonical form dropped — so a purely numeric serial
    is offered both ways, with the disc's own spelling first (that is the folder
    the console creates).
    """
    canonical = canonical_dc_serial(serial)
    if not canonical:
        return []
    if canonical.isdigit():
        return [f"MK{canonical}", canonical]
    return [canonical]


# ---------------------------------------------------------------------------
# IP.BIN disc header
#
# Every Dreamcast disc starts its data track with a 256-byte header carrying
# the fields a menu or a sync client needs.  Offsets follow the layout decoded
# by Aaru's ``Aaru.Decoders.Sega.Dreamcast.IPBin`` (MIT) — read for the offsets
# only, no code copied:
#
#     0x00  16  hardware id, always "SEGA SEGAKATANA "
#     0x20   4  CRC of product number + version
#     0x2B   1  disc number    0x2C '/'    0x2D total discs
#     0x30   8  region codes ("JUE", space filled)
#     0x38   7  peripherals (hex string; byte 5 == '1' means VGA support)
#     0x40  10  product number ("MK-51035")
#     0x4A   6  product version ("V1.005")
#     0x50   8  release date ("19991223")
#     0x80 128  product name
# ---------------------------------------------------------------------------

IP_BIN_MAGIC = b"SEGA SEGAKATANA "
IP_BIN_SIZE = 256

IMAGE_EXTENSIONS = (".gdi", ".cdi", ".iso", ".bin", ".img", ".mdf", ".ccd")

# A GDI track holds its header in the first sector, so only the first sectors
# need scanning (2352-byte raw sectors put it at offset 16, 2048-byte ones at 0).
_TRACK_SCAN_LIMIT = 64 * 1024
# A single-file image (CDI, ISO, BIN) can carry audio tracks ahead of the data
# track, so the header sits further in.  Capped: a full scan of a 1 GB image on
# an SD card costs more than the metadata is worth, and callers have the DAT.
_IMAGE_SCAN_LIMIT = 128 * 1024 * 1024
_CHUNK = 1024 * 1024

_GDI_LINE_RE = re.compile(
    r'^\s*(?P<num>\d+)\s+(?P<lba>\d+)\s+(?P<type>\d+)\s+(?P<sector>\d+)\s+'
    r'(?P<name>"[^"]+"|\S+)\s+(?P<offset>\d+)\s*$'
)


@dataclass(frozen=True)
class IpBin:
    """The menu/sync-relevant subset of a Dreamcast disc header."""

    name: str
    disc: str
    vga: bool
    region: str
    version: str
    date: str
    product: str
    crc: str

    @property
    def serial(self) -> str:
        """Canonical serial for this disc — the key its saves live under."""
        return canonical_dc_serial(self.product)

    @property
    def game_id(self) -> str:
        """Product number as a device spells its folder: ``MK51035``."""
        return _NON_ALNUM_RE.sub("", self.product).upper()

    @property
    def title_id(self) -> str:
        """``DC_<serial>`` for this disc."""
        return make_dc_title_id(self.product)


def _field(data: bytes, offset: int, length: int) -> str:
    text = data[offset : offset + length].decode("ascii", errors="ignore")
    nul = text.find("\0")
    if nul > -1:
        text = text[:nul]
    return text.strip()


def parse_ip_bin(data: bytes) -> Optional[IpBin]:
    """Decode a 256-byte IP.BIN header, or ``None`` if it isn't one."""
    if len(data) < IP_BIN_SIZE or not data.startswith(IP_BIN_MAGIC):
        return None

    disc_no = data[0x2B]
    disc_total = data[0x2D]
    if disc_no == 0x20 or disc_total == 0x20:
        # Space-filled: a single-disc release that left the field blank.
        disc = "1/1"
    else:
        disc = f"{chr(disc_no)}/{chr(disc_total)}"

    return IpBin(
        name=_field(data, 0x80, 128),
        disc=disc,
        vga=data[0x38 + 5] == ord("1"),
        region=_field(data, 0x30, 8),
        version=_field(data, 0x4A, 6),
        date=_field(data, 0x50, 8),
        product=_field(data, 0x40, 10),
        crc=_field(data, 0x20, 4),
    )


def _scan_for_header(path: Path, limit: int) -> Optional[IpBin]:
    """Find the first IP.BIN header in ``path`` within ``limit`` bytes.

    The 256-byte header always fits inside one sector's payload, so it is
    contiguous whether the image stores 2048- or 2352-byte sectors.
    """
    keep = len(IP_BIN_MAGIC) + IP_BIN_SIZE
    try:
        with open(path, "rb") as fh:
            carry = b""
            carry_offset = 0  # absolute offset of carry[0]
            scanned = 0
            while scanned < limit:
                chunk = fh.read(min(_CHUNK, limit - scanned))
                if not chunk:
                    break
                scanned += len(chunk)
                window = carry + chunk
                index = window.find(IP_BIN_MAGIC)
                if index > -1:
                    # Seek rather than slice: the header can straddle the
                    # window's tail.
                    fh.seek(carry_offset + index)
                    return parse_ip_bin(fh.read(IP_BIN_SIZE))
                carry = window[-keep:]
                carry_offset += len(window) - len(carry)
    except OSError:
        return None
    return None


def parse_gdi_track_files(gdi_path: Path) -> list[Path]:
    """Track files referenced by a ``.gdi`` sheet, in sheet order."""
    try:
        lines = gdi_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    tracks: list[Path] = []
    for line in lines[1:]:
        match = _GDI_LINE_RE.match(line)
        if match:
            tracks.append(gdi_path.parent / match.group("name").strip('"'))
    return tracks


def read_ip_bin(path: Path) -> Optional[IpBin]:
    """Read the disc header from an image, following a ``.gdi`` to its tracks.

    ``.chd`` is compressed and cannot be read inline — callers fall back to the
    DAT for those.
    """
    path = Path(path)
    if path.suffix.lower() == ".gdi":
        for track in parse_gdi_track_files(path):
            header = _scan_for_header(track, _TRACK_SCAN_LIMIT)
            if header is not None:
                return header
        return None
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        return None
    return _scan_for_header(path, _IMAGE_SCAN_LIMIT)


def find_disc_image(folder: Path) -> Optional[Path]:
    """The disc image inside a game folder, ``.gdi`` first."""
    try:
        files = [f for f in sorted(Path(folder).iterdir()) if f.is_file()]
    except OSError:
        return None
    for ext in IMAGE_EXTENSIONS:
        for candidate in files:
            if candidate.suffix.lower() == ext:
                return candidate
    return None


def read_folder_ip_bin(folder: Path) -> Optional[IpBin]:
    """Disc header for a folder holding one game, or ``None`` if unreadable."""
    image = find_disc_image(Path(folder))
    return read_ip_bin(image) if image is not None else None


# ---------------------------------------------------------------------------
# DAT parser + name-based lookup  (the CHD fallback)
# ---------------------------------------------------------------------------

_GAME_OPEN = "game ("
_GAME_CLOSE = ")"
_NAME_RE = re.compile(r'^\s*name\s+"(.+)"\s*$')
_SERIAL_RE = re.compile(r'^\s*serial\s+"(.+)"\s*$')
_BRACKET_TAG_RE = re.compile(r"\s*\[[^\]]*\]")
_TRAILING_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")


def parse_dreamcast_dat(text: str) -> dict[str, str]:
    """Parse a clrmamepro Dreamcast DAT into ``{lower game name: serial}``.

    The game-level ``serial`` field (at one indent) is authoritative; per-rom
    serials inside ``rom ( ... )`` are ignored.
    """
    result: dict[str, str] = {}
    in_game = False
    cur_name = ""
    cur_serial = ""

    for line in text.splitlines():
        stripped = line.strip()
        if stripped == _GAME_OPEN:
            in_game = True
            cur_name = ""
            cur_serial = ""
            continue
        if stripped == _GAME_CLOSE and in_game:
            if cur_name and cur_serial:
                # First occurrence wins, matching the Saturn parser.
                result.setdefault(cur_name.lower(), cur_serial)
            in_game = False
            continue
        if not in_game:
            continue
        if not cur_name:
            match = _NAME_RE.match(line)
            if match:
                cur_name = match.group(1)
                continue
        if not cur_serial:
            match = _SERIAL_RE.match(line)
            if match:
                cur_serial = match.group(1)

    return result


def lookup_dreamcast_serial_in_dat(
    rom_name: str, name_to_serial: dict[str, str]
) -> Optional[str]:
    """Find a serial for ``rom_name`` in a pre-parsed DAT map.

    Exact (case-insensitive) match first, then progressively strip trailing
    ``(...)`` groups so ``"Shenmue (USA) (Disc 1) [T-En]"`` still reaches
    ``"Shenmue (USA)"``.
    """
    if not name_to_serial:
        return None

    name = _BRACKET_TAG_RE.sub("", rom_name).strip()
    hit = name_to_serial.get(name.lower())
    if hit:
        return hit

    while True:
        stripped = _TRAILING_PAREN_RE.sub("", name).strip()
        if stripped == name or not stripped:
            return None
        name = stripped
        hit = name_to_serial.get(name.lower())
        if hit:
            return hit


# ---------------------------------------------------------------------------
# DAT discovery + caching
# ---------------------------------------------------------------------------

_DAT_CANDIDATE_NAMES = (
    "Sega - Dreamcast.dat",
    "Sega - Dreamcast (libretro).dat",
)


def _default_dat_candidates() -> list[Path]:
    """Walk up from this file looking for the Dreamcast DAT.

    Covers the server layout (``server/data/dats/``), a future
    ``shared/data/dats/``, and the Android asset folder when the repo is
    checked out directly.
    """
    start = Path(__file__).resolve().parent
    roots = [start] + list(start.parents)
    subdirs = (
        Path("data") / "dats",
        Path("server") / "data" / "dats",
        Path("shared") / "data" / "dats",
        Path("android") / "app" / "src" / "main" / "assets",
    )
    return [
        root / sub / name
        for root in roots
        for sub in subdirs
        for name in _DAT_CANDIDATE_NAMES
    ]


_dat_cache: dict[str, dict[str, str]] = {}


def load_dreamcast_dat(dat_path: Optional[Path] = None) -> dict[str, str]:
    """Load + parse a Dreamcast DAT, cached by absolute path."""
    if dat_path is None:
        for candidate in _default_dat_candidates():
            if candidate.is_file():
                dat_path = candidate
                break
    if dat_path is None or not Path(dat_path).is_file():
        return {}

    key = str(Path(dat_path).resolve())
    cached = _dat_cache.get(key)
    if cached is not None:
        return cached

    try:
        text = Path(dat_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    parsed = parse_dreamcast_dat(text)
    _dat_cache[key] = parsed
    return parsed


# ---------------------------------------------------------------------------
# Public resolver — the entry point every client uses
# ---------------------------------------------------------------------------


def resolve_dreamcast_title_id(
    rom_path: Optional[Path] = None,
    rom_name: Optional[str] = None,
    dat_path: Optional[Path] = None,
) -> Optional[str]:
    """Canonical ``DC_<serial>`` title id for a game, or ``None``.

    Order of attempts, mirroring ``shared.rom_id.saturn.resolve_saturn_title_id``:

      1. read ``IP.BIN`` straight out of ``rom_path`` (GDI/CDI/ISO/BIN);
      2. otherwise, or on failure, look ``rom_name`` up in the Dreamcast DAT.

    Callers fall back to their own filename-slug logic on ``None``, so a disc
    nobody can identify still gets a stable key.
    """
    if rom_path is not None:
        header = read_ip_bin(Path(rom_path))
        if header is not None and header.title_id:
            return header.title_id

    name = rom_name
    if name is None and rom_path is not None:
        name = Path(rom_path).stem
    if not name:
        return None

    serial = lookup_dreamcast_serial_in_dat(name, load_dreamcast_dat(dat_path))
    return make_dc_title_id(serial) if serial else None


__all__ = [
    "DC_TITLE_ID_PREFIX",
    "IMAGE_EXTENSIONS",
    "IP_BIN_MAGIC",
    "IP_BIN_SIZE",
    "IpBin",
    "canonical_dc_serial",
    "dc_device_folder_ids",
    "find_disc_image",
    "load_dreamcast_dat",
    "lookup_dreamcast_serial_in_dat",
    "make_dc_title_id",
    "parse_dreamcast_dat",
    "parse_dc_title_id",
    "parse_gdi_track_files",
    "parse_ip_bin",
    "read_folder_ip_bin",
    "read_ip_bin",
    "resolve_dreamcast_title_id",
]
