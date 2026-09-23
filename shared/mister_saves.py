"""MiSTer save identity and on-disk format rules.

MiSTer cores write save files that do not always match what every other client
keeps for the same game, and the file name is not always the identity either.
This module is the single implementation of those rules, shared by the desktop
client (which reaches a MiSTer over SFTP) and the on-device client (which reads
the same files locally).

Keeping one copy matters: ``mister/sync_saves.sh`` re-implemented just the slug
normaliser by hand and quietly dropped region tags, so it has been writing
``GBA_zelda`` where every other client writes ``GBA_zelda_usa``. One duplicated
function, one silent wrong-key bug. Everything here is deliberately pure - no
filesystem, no network - so both callers can share it and the tests can cover
it without a device.

The rules, per system:

``PS1``
    ``.sav`` is a raw 128 KB memory card. Identity is the product code written
    *inside* the card, which beats a disc-serial file name, because variant
    discs boot under one serial and save under another. A card the core created
    but no game has written to is "blank": it stays visible so a server save can
    be downloaded into it, but must never upload over a real save.

``SAT``
    ``.sav`` is 64 KB byte-expanded internal backup RAM. The canonical form
    every other client and the server keeps is the 32 KB collapsed image, so
    that is what gets hashed and uploaded. Saturn backup RAM carries no disc id,
    so identity has to come from a ROM catalogue lookup on the game name.

``SEGACD``
    ``.sav`` is raw 8 KB internal BRAM - byte-for-byte what Genesis Plus GX
    keeps in a ``.brm``, so no conversion. Only the formatted-but-empty case
    needs detecting.

``MD``
    Mega Drive cartridge SRAM sits on the odd byte of the 68000's 16-bit bus.
    Emulators and this server store it expanded (save byte at every odd offset);
    the MiSTer core stores the bytes packed and pads to a fixed 64 KB. Handing a
    core a 16 KB emulator save leaves the game with nothing.

Everything else is raw SRAM and needs no conversion.
"""

from __future__ import annotations

import re
from typing import Callable, Optional


__all__ = [
    "MISTER_MD_SAVE_SIZE",
    "PS1_CARD_SIZE",
    "SaveIdentity",
    "HOUSEKEEPING_SYSTEMS",
    "content_key",
    "describe_difference",
    "same_content",
    "is_ps1_card_blank",
    "is_segacd_bram_blank",
    "md_from_mister",
    "md_to_mister",
    "ps1_card_save_names",
    "ps1_card_serials",
    "ps1_download_risk",
    "ps1_save_serial",
    "ps1_card_serial",
    "ps1_serial_from_filename",
    "needs_payload_read",
    "resolve_save_identity",
    "resolve_title_id",
]


# ── PS1 memory cards ────────────────────────────────────────────────────────
#
# A card is 128 KB: block 0 holds 16 directory frames of 128 bytes. Frame 0 is
# the header ("MC"), frames 1-15 describe the save blocks. A frame whose first
# byte is 0x51 is the first link of an in-use save, and carries the in-card
# file name at +0x0A, e.g. "BASLUS-01324SAVEGAME".

PS1_CARD_SIZE = 128 * 1024
_PS1_DIR_FRAMES = range(1, 16)
_PS1_FRAME_SIZE = 128
_PS1_FRAME_IN_USE = 0x51
_PS1_NAME_OFFSET = 0x0A
_PS1_NAME_LENGTH = 20

_PS1_INCARD_SERIAL_RE = re.compile(r"^B[A-Z]([A-Z]{4})[-_]?(\d{5})")

#: Serial-named cards the core writes when booting a real CD, in every written
#: form: ``SLPM-86219``, ``SLPM_86219``, ``SLPM86219``, ``SLUS_012.34``.
_PS1_FILENAME_SERIAL_RE = re.compile(
    r"^([A-Z]{4})[-_ ]?(\d{3})[-_. ]?(\d{2})$", re.IGNORECASE
)

#: Only recognised retail prefixes may be read as a serial, so an ordinary game
#: name can never be mistaken for one. This is the desktop client's list
#: verbatim - widening it would make ordinary names parse as serials and change
#: which server slot a save lands in.
PSX_RETAIL_PREFIXES = frozenset(
    {
        # North America
        "SLUS",
        "SCUS",
        "PAPX",
        # Europe
        "SLES",
        "SCES",
        "SCED",
        # Japan
        "SLPS",
        "SLPM",
        "SCPS",
        "SCPM",
        # Other
        "SLAJ",
        "SLEJ",
        "SCAJ",
    }
)


def ps1_serial_from_filename(stem: str) -> Optional[str]:
    """Compact disc serial from a serial-named save file stem, else None."""
    match = _PS1_FILENAME_SERIAL_RE.match(str(stem or "").strip())
    if not match:
        return None
    prefix = match.group(1).upper()
    if prefix not in PSX_RETAIL_PREFIXES:
        return None
    return "%s%s%s" % (prefix, match.group(2), match.group(3))


def ps1_card_serials(card: bytes) -> list:
    """Every distinct product code on a raw PS1 card, in directory order.

    A card is shared between games, so one card can carry saves for several
    serials; the first is what ``ps1_card_serial`` returns, and the full list
    is what lets ``resolve_title_id`` prefer the game the card is named after.
    """
    if len(card) < 2048 or card[:2] != b"MC":
        return []
    serials = []
    for frame in _PS1_DIR_FRAMES:
        offset = frame * _PS1_FRAME_SIZE
        if card[offset] != _PS1_FRAME_IN_USE:
            continue
        raw_name = card[offset + _PS1_NAME_OFFSET:
                        offset + _PS1_NAME_OFFSET + _PS1_NAME_LENGTH]
        raw_name = raw_name.split(b"\x00")[0]
        match = _PS1_INCARD_SERIAL_RE.match(
            raw_name.decode("ascii", errors="ignore"))
        if match:
            serial = "%s%s" % (match.group(1), match.group(2))
            if serial not in serials:
                serials.append(serial)
    return serials


def ps1_card_serial(card: bytes) -> Optional[str]:
    """Compact product code (``SLUS01324``) from a raw 128 KB PS1 card.

    The first save's, as the card's directory orders them. Returns None for
    an empty or unformatted card.
    """
    serials = ps1_card_serials(card)
    return serials[0] if serials else None


def ps1_card_save_names(card: bytes) -> list:
    """The in-card name of every save on a PlayStation memory card.

    A card is shared: it holds up to 15 saves belonging to *different games*.
    One seen on a real MiSTer carried nine saves across eight games, while the
    server's copy of the same card held one. Overwriting the card with the
    server's copy would have deleted the other eight games' progress, so the
    two sides have to be compared save by save, not just as whole cards.
    """
    if len(card) < 2048 or card[:2] != b"MC":
        return []
    names = []
    for frame in _PS1_DIR_FRAMES:
        offset = frame * _PS1_FRAME_SIZE
        if card[offset] != _PS1_FRAME_IN_USE:
            continue
        raw_name = card[offset + _PS1_NAME_OFFSET:
                        offset + _PS1_NAME_OFFSET + _PS1_NAME_LENGTH]
        name = raw_name.split(b"\x00")[0].decode("ascii", errors="replace")
        if name:
            names.append(name)
    return names


def ps1_save_serial(in_card_name: str):
    """The product code inside a card save's name, e.g. ``SLUS01324``."""
    match = _PS1_INCARD_SERIAL_RE.match(str(in_card_name or ""))
    if not match:
        return None
    return "%s%s" % (match.group(1), match.group(2))


def ps1_download_risk(local_card: bytes, remote_card: bytes, title_id: str):
    """What writing *remote_card* over *local_card* would cost.

    Returns ``(own_save_lost, other_saves_lost)``. A card is shared between
    games, so a download can drop saves that have nothing to do with the game
    being synced. Losing *this* game's save is the serious case - it is the
    save the user is actually syncing - and is what callers must refuse to do
    silently. Other games' saves are still reported, so the choice is informed.
    """
    theirs = set(ps1_card_save_names(remote_card))
    wanted = (title_id or "").upper()

    own_lost = False
    others = []
    for name in ps1_card_save_names(local_card):
        if name in theirs:
            continue
        if wanted and ps1_save_serial(name) == wanted:
            own_lost = True
        else:
            others.append(name)
    return own_lost, others


def is_ps1_card_blank(card: bytes) -> bool:
    """True for a formatted PS1 card holding no save blocks at all.

    The PSX core creates one the first time a game runs, so these are common
    and carry nothing worth syncing.
    """
    if len(card) < 2048 or card[:2] != b"MC":
        return False
    return all(card[frame * _PS1_FRAME_SIZE] != _PS1_FRAME_IN_USE
               for frame in _PS1_DIR_FRAMES)


# ── Sega CD backup RAM ──────────────────────────────────────────────────────
#
# A Sega CD backup RAM ends with a 0x40-byte format footer; directory entries
# are 0x20 bytes each and grow backwards from it, an unused slot being all
# zero. Same layout for the internal 8 KB BRAM and for RAM carts.

_SEGACD_FOOTER_SIZE = 0x40
_SEGACD_DIR_ENTRY_SIZE = 0x20
_SEGACD_FOOTER_MAGIC = b"SEGA_CD_ROM"


def is_segacd_bram_blank(data: bytes) -> bool:
    """True for a formatted Sega CD backup RAM holding no save files."""
    if len(data) < _SEGACD_FOOTER_SIZE + _SEGACD_DIR_ENTRY_SIZE:
        return False
    if _SEGACD_FOOTER_MAGIC not in data[-_SEGACD_FOOTER_SIZE:]:
        return False
    first_entry = data[-_SEGACD_FOOTER_SIZE - _SEGACD_DIR_ENTRY_SIZE:
                       -_SEGACD_FOOTER_SIZE]
    return not any(first_entry)


# ── Mega Drive SRAM ─────────────────────────────────────────────────────────

MISTER_MD_SAVE_SIZE = 65536
_MD_MIN_SRAM = 0x1000  # 4 KB - smallest size seen in the wild


def _md_expanded_to_packed(data: bytes) -> bytes:
    """Emulator/server layout -> the packed SRAM bytes the core stores."""
    return bytes(data[1::2])


def _md_packed_to_expanded(packed: bytes, filler: int = 0x00) -> bytes:
    """Packed SRAM bytes -> emulator/server layout (byte at each odd offset)."""
    out = bytearray(len(packed) * 2)
    out[0::2] = bytes([filler]) * len(packed)
    out[1::2] = packed
    return bytes(out)


def _md_sram_size(packed: bytes) -> int:
    """Guess a game's SRAM size from the used portion of a core image.

    The core pads to 64 KB with 0xFF, so the tail says nothing; round the used
    length up to the next power of two, which is how cartridges are sized.
    """
    used = len(packed.rstrip(b"\xff"))
    size = _MD_MIN_SRAM
    while size < used and size < MISTER_MD_SAVE_SIZE:
        size *= 2
    return size


def md_to_mister(data: bytes) -> bytes:
    """Server payload -> a 64 KB image the MegaDrive core will load."""
    if len(data) == MISTER_MD_SAVE_SIZE:
        return data  # already core-shaped
    packed = _md_expanded_to_packed(data)
    if len(packed) >= MISTER_MD_SAVE_SIZE:
        return packed[:MISTER_MD_SAVE_SIZE]
    return packed + b"\xff" * (MISTER_MD_SAVE_SIZE - len(packed))


def md_from_mister(data: bytes, target_size: int = 0) -> bytes:
    """64 KB core image -> the expanded layout every other client reads.

    ``target_size`` (the size already on the server) wins when known, so a save
    keeps the exact shape its counterpart clients expect.
    """
    if len(data) != MISTER_MD_SAVE_SIZE:
        return data  # not a core image - pass through untouched
    sram = (target_size // 2) if target_size else _md_sram_size(data)
    sram = max(sram, _MD_MIN_SRAM)
    return _md_packed_to_expanded(data[:sram])


# ── Saturn ──────────────────────────────────────────────────────────────────

def _saturn_helpers():
    """Imported lazily so this module stays importable without the Saturn code."""
    from shared.saturn_format import (
        list_saturn_archive_names,
        normalize_saturn_save,
    )

    return normalize_saturn_save, list_saturn_archive_names


# ── Per-system identity resolution ──────────────────────────────────────────

class SaveIdentity:
    """What one MiSTer save file turns out to be.

    ``hash_payload``
        The bytes whose SHA-256 is the save hash. Not always the file's own
        bytes: a Saturn save hashes its canonical 32 KB form and a Mega Drive
        core image hashes its expanded form, so that a save uploaded from a
        MiSTer matches the same save seen from any other client.
    ``serial``
        A product code recovered from inside the payload, when the system has
        one. Overrides the name-derived title id.
    ``serials``
        Every product code found, in the payload's own order, ``serial`` being
        the first. A PS1 card is shared between games, so there can be several.
    ``is_blank``
        The core created the file but no game has written a save into it.
    """

    __slots__ = ("hash_payload", "serial", "serials", "is_blank")

    def __init__(self, hash_payload: bytes, serial: Optional[str] = None,
                 is_blank: bool = False, serials=None):
        self.hash_payload = hash_payload
        if serials is None:
            serials = (serial,) if serial else ()
        self.serials = tuple(serials)
        self.serial = serial or (self.serials[0] if self.serials else None)
        self.is_blank = is_blank

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "SaveIdentity(serial=%r, is_blank=%r, payload=%d bytes)" % (
            self.serial, self.is_blank, len(self.hash_payload))


# ── Housekeeping bytes that are not save data ───────────────────────────────
#
# Two devices can hold the same save and still disagree byte-for-byte, because
# each writes its own bookkeeping into regions the game never reads. Comparing
# raw hashes then reports a conflict that no amount of syncing can settle: the
# core rewrites its bookkeeping on the next boot and the two sides differ again.
#
# Observed on real hardware:
#
#   PS1  three bytes, all in block 0 frame 63 - the card's *write-test* frame.
#        The MiSTer core writes "MC" plus a checksum there; a card that came
#        from a PSP had it zeroed. Directory and every save block identical.
#   SAT  six bytes in an archive's 10-byte comment field: the MiSTer core had
#        "??????" where the server's copy held half-width katakana. Same
#        archive, same save data.
#   MD   the *filler* at every even offset. Mega Drive SRAM lives on the odd
#        byte of the 68000's 16-bit bus, so the even bytes are whatever the
#        writer happened to leave there - this project writes 0x00, other
#        clients write something else. Every odd byte matched exactly.
#
# So equivalence is decided on the parts that carry meaning. This never changes
# what is stored or uploaded - it only stops a cosmetic difference being
# reported as a conflict.

#: PS1 block 0 is 64 frames of 128 bytes: frame 0 header, frames 1-15 the
#: directory, and frames 16-63 the broken-sector list, its replacement area and
#: the write-test frame. Save data lives in blocks 1-15, from offset 8192.
_PS1_BLOCK_SIZE = 8192
_PS1_DIRECTORY_END = 2048


def content_key(system: str, payload: bytes) -> bytes:
    """The part of a save that actually carries meaning.

    Two saves with the same content key hold the same progress, even when their
    bytes differ. Falls back to the payload itself for systems with no known
    housekeeping regions.
    """
    system = (system or "").upper()

    if system == "PS1":
        if len(payload) < _PS1_BLOCK_SIZE or payload[:2] != b"MC":
            return payload
        return (payload[:_PS1_DIRECTORY_END]
                + b"\x00" * (_PS1_BLOCK_SIZE - _PS1_DIRECTORY_END)
                + payload[_PS1_BLOCK_SIZE:])

    if system == "MD":
        # Only the odd bytes are the save; the even ones are bus filler.
        if len(payload) >= 2 and len(payload) % 2 == 0:
            return bytes(payload[1::2])
        return payload

    if system == "SAT":
        try:
            from shared.saturn_format import _parse_native_saturn, normalize_saturn_save

            saves = _parse_native_saturn(normalize_saturn_save(payload))
            if saves is None:
                return payload
            parts = sorted((save.name.encode("ascii", "replace"),
                            save.raw_data) for save in saves)
            return b"\x00".join(name + b"\x00" + data for name, data in parts)
        except Exception:
            return payload

    return payload


def same_content(system: str, left: bytes, right: bytes) -> bool:
    """True when two saves differ only in housekeeping bytes."""
    if left == right:
        return True
    return content_key(system, left) == content_key(system, right)


def _ps1_frame_region(frame: int) -> str:
    """Name a block 0 frame the way the card layout documents it."""
    if frame == 0:
        return "header"
    if frame < 16:
        return "directory"
    if frame < 36:
        return "broken-sector list"
    if frame < 56:
        return "broken-sector data"
    if frame == 63:
        return "write-test frame"
    return "unused"


def describe_difference(system: str, local: bytes, remote: bytes,
                        limit: int = 4) -> str:
    """Where two copies of a save disagree, in the save's own terms.

    A hash says only that two cards differ; this says *where*, which is what
    decides whether a difference is a save or a core rewriting its bookkeeping.
    On a PlayStation card each save block is named after the save it holds, so
    "block 3 (BASLUS-01251FF7-S01)" means that game's progress changed and
    "block 0 write-test frame" means nothing did. The output is short enough
    for a row detail; ``limit`` caps the regions named.
    """
    system = (system or "").upper()
    if local == remote:
        return ""
    if len(local) != len(remote):
        return "size %d vs %d bytes" % (len(local), len(remote))

    if system == "PS1" and len(local) >= _PS1_BLOCK_SIZE \
            and local[:2] == b"MC":
        regions = []
        seen = set()
        for offset in range(0, len(local), _PS1_FRAME_SIZE):
            if local[offset:offset + _PS1_FRAME_SIZE] == \
                    remote[offset:offset + _PS1_FRAME_SIZE]:
                continue
            block, frame = divmod(offset, _PS1_BLOCK_SIZE)
            frame //= _PS1_FRAME_SIZE
            if block == 0:
                key = ("b0", _ps1_frame_region(frame))
                text = "block 0 %s" % key[1]
            else:
                key = ("block", block)
                # The directory entry for block N is frame N of block 0; name
                # the save from whichever side has one so a deleted save is
                # still identifiable.
                name = ""
                for card in (local, remote):
                    entry = block * _PS1_FRAME_SIZE
                    raw = card[entry + _PS1_NAME_OFFSET:
                               entry + _PS1_NAME_OFFSET + _PS1_NAME_LENGTH]
                    name = raw.split(b"\x00")[0].decode("ascii", "replace")
                    if name:
                        break
                text = "block %d%s" % (block, " (%s)" % name if name else "")
            if key in seen:
                continue
            seen.add(key)
            regions.append(text)
        return _join_regions(regions, limit)

    # Generic: name the differing 1 KB ranges.
    chunk = 1024
    regions = []
    for offset in range(0, len(local), chunk):
        if local[offset:offset + chunk] != remote[offset:offset + chunk]:
            regions.append("0x%X" % offset)
    return _join_regions(regions, limit, unit="offset")


def _join_regions(regions: list, limit: int, unit: str = "") -> str:
    if not regions:
        return ""
    shown = regions[:limit]
    text = ", ".join(shown)
    if unit and shown:
        text = "%s %s" % (unit, text)
    if len(regions) > limit:
        text += " +%d more" % (len(regions) - limit)
    return text


#: Systems where a cosmetic byte difference has actually been observed, so the
#: extra round trip to compare content is worth it.
HOUSEKEEPING_SYSTEMS = frozenset({"PS1", "SAT", "MD"})


def needs_payload_read(system: str, size: int = 0) -> bool:
    """True when the save's bytes must be read to identify or hash it.

    Everything else can be hashed remotely (or skipped when a cached hash is
    still valid), which matters over SFTP where a read is expensive.
    """
    system = (system or "").upper()
    if system in ("PS1", "SAT", "SEGACD"):
        return True
    return system == "MD" and size == MISTER_MD_SAVE_SIZE


def resolve_save_identity(system: str, data: bytes) -> SaveIdentity:
    """Apply the per-system rules to one save file's bytes.

    Pure: give it the same bytes and it gives the same answer, on a desktop
    over SFTP or on the MiSTer itself.
    """
    system = (system or "").upper()

    if system == "PS1":
        serials = ps1_card_serials(data)
        return SaveIdentity(
            hash_payload=data,
            serials=serials,
            is_blank=not serials and is_ps1_card_blank(data),
        )

    if system == "SAT":
        normalize_saturn_save, list_saturn_archive_names = _saturn_helpers()
        return SaveIdentity(
            hash_payload=normalize_saturn_save(data),
            is_blank=not list_saturn_archive_names(data),
        )

    if system == "SEGACD":
        return SaveIdentity(hash_payload=data,
                            is_blank=is_segacd_bram_blank(data))

    if system == "MD" and len(data) == MISTER_MD_SAVE_SIZE:
        return SaveIdentity(
            hash_payload=md_from_mister(data),
            is_blank=not data.rstrip(b"\xff"),
        )

    return SaveIdentity(hash_payload=data)


def resolve_title_id(
    system: str,
    stem: str,
    identity: SaveIdentity,
    base_title_id: str,
    catalog_lookup: Optional[Callable[[str, str], Optional[str]]] = None,
) -> str:
    """Pick the server key for one MiSTer save.

    Precedence, highest first:

    1. A product code read from inside the payload (PS1). Variant discs boot
       under one serial and save under another, so the card wins over the name.
       A card is shared between games, though, and a copied or downloaded card
       can open with some *other* game's save: three MiSTer cards named for
       three games all keyed themselves by the Parasite Eve II save that
       happened to sit first. So when the card holds a save for the game the
       file is named after - by disc-serial name or catalogue hit - that save's
       serial wins over the first one. A card with no such save keeps the
       first-save rule, which is the variant-disc case.
    2. A disc-serial file name, which identifies a CD card the core created but
       no game has written to yet.
    3. A catalogue hit on the game name. Saturn backup RAM carries no disc id
       at all, and a translation patch renames the file beyond recognition
       (``Castlevania - Symphony of the Night`` for ``Akumajou Dracula X``),
       so the catalogue is the only bridge to the server's key. How loosely
       the lookup matches is the callback's business: serial-keyed systems
       tolerate regional spelling differences, while for a slug-keyed system
       the name *is* the identity and only an exact file-name hit may be
       honoured - the server can still key that exact file under a title id
       that is not its slug (``Famicom Detective Club … [T-En]`` filed under
       ``SNES_famicom_tantei_club_…``), and a save named after the file
       belongs in that slot.
    4. The slug derived from the file name.
    """
    filename_serial = ps1_serial_from_filename(stem) if system == "PS1" else None

    if identity.serial:
        serials = identity.serials or (identity.serial,)
        if len(serials) > 1:
            # Only worth resolving the name when the card is actually shared;
            # a single-game card cannot be keyed by the wrong game.
            named = filename_serial
            if not named and catalog_lookup is not None:
                named = catalog_lookup(system, stem)
            if named and named.upper() in serials:
                return named.upper()
        return identity.serial

    if filename_serial:
        return filename_serial

    if catalog_lookup is not None:
        catalog_id = catalog_lookup(system, stem)
        if catalog_id:
            return catalog_id

    return base_title_id
