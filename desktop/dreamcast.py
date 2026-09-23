"""Dreamcast identity helpers — disc serial ⇄ ``DC_<serial>`` title id.

Dreamcast keys saves by the disc's serial (the ``IP.BIN`` product number), the
same way PS1/PS2/Saturn do — see ``shared/rom_id/dreamcast.py`` for the
canonical form and why Sega's ``MK`` prefix is folded away.  Every Dreamcast
save device already files by that serial:

* MemCard PRO DC — ``<root>/Dreamcast/<SERIAL>/<SERIAL>-1.vmu``
* openMenu Serial VMU — ``<serial SD>/OPENMENU/SAVES/<SERIAL>/SLOT1.VMU``

so a card save and a Flycast save land in one server slot, and a mis-named ROM
file can't split a game across two.  An emulator profile only knows the ROM's
filename, so this module also maps a name to its serial through the Dreamcast
DAT — the same index the server uses, read locally so the desktop client
doesn't need the server to key a save.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import systems as _systems  # noqa: F401 — puts the repo root on sys.path
from shared.rom_id import (
    canonical_dc_serial,
    dc_device_folder_ids,
    make_dc_title_id,
    make_title_id,
    normalize_rom_name,
    parse_dc_title_id,
)

# A MemCard PRO / openMenu virtual VMU is a bare 128 KB VMU image, the same
# payload Flycast writes — no header, no footer.
VMU_SIZE = 131072

# Folders on a MemCard PRO DC card that are not a game: the shared card every
# device creates for un-identified discs, and the menu's own VMU.
NON_GAME_CARD_FOLDERS = frozenset({"memorycard1", "memorycard2", "openmenu", "gdmenu"})


def normalize_game_id(value: str) -> str:
    """Device folder name / DAT serial → canonical serial (``MK-51000`` → ``51000``)."""
    return canonical_dc_serial(value)


def is_game_folder(name: str) -> bool:
    """False for a card folder that holds a shared or menu VMU, not a game."""
    return bool(normalize_game_id(name)) and name.strip().lower() not in (
        NON_GAME_CARD_FOLDERS
    )


@lru_cache(maxsize=1)
def serial_index() -> dict[str, str]:
    """``{canonical serial: game name}`` from the Dreamcast DAT.

    Empty when the DAT isn't present — a save still syncs under its serial,
    just without a resolved name.
    """
    try:
        import rom_normalizer as rn

        dat_path = rn.find_libretro_dat_for_system("DC")
        if dat_path is None or not Path(dat_path).is_file():
            return {}
        raw = rn.load_libretro_dat(Path(dat_path))
    except Exception:
        return {}

    index: dict[str, str] = {}
    for serial, name in raw.items():
        canonical = normalize_game_id(serial)
        if canonical and canonical not in index:
            index[canonical] = name
    return index


@lru_cache(maxsize=1)
def _serial_by_slug() -> dict[str, str]:
    """``{name slug: serial}`` — for keying a save an emulator only names."""
    by_slug: dict[str, str] = {}
    for serial, name in serial_index().items():
        slug = normalize_rom_name(name)
        if slug and slug != "unknown":
            by_slug.setdefault(slug, serial)
    return by_slug


def game_name_for_serial(serial: str) -> str:
    """Canonical game name for a serial, or ``""`` when the DAT has no match."""
    return serial_index().get(normalize_game_id(serial), "")


def serial_for_name(name: str) -> str:
    """Serial for a ROM/game name via the DAT, or ``""``.

    Region tags are part of the slug, so ``Sonic Adventure (USA)`` and
    ``Sonic Adventure (Europe)`` resolve to their own serials rather than
    collapsing onto one.
    """
    slug = normalize_rom_name(str(name or ""))
    if not slug or slug == "unknown":
        return ""
    return _serial_by_slug().get(slug, "")


def title_id_for_game_id(game_id: str) -> str:
    """``DC_<serial>`` for a device folder name or a DAT serial."""
    return make_dc_title_id(game_id)


def title_id_for_name(name: str) -> str:
    """``DC_<serial>`` for a game name, falling back to the name slug.

    The slug fallback keeps a disc the DAT doesn't know (homebrew, a fresh
    translation) addressable; it just won't share a slot with a card save,
    because the card files that disc under a serial we can't derive from a
    name.
    """
    serial = serial_for_name(name)
    if serial:
        return make_dc_title_id(serial)
    return make_title_id("DC", name)


def game_ids_for_title_id(title_id: str) -> list[str]:
    """Device folder names that a ``DC_…`` title id could be filed under.

    A serial id yields the disc's own spelling first (``MK51000`` before
    ``51000``, since that is the folder the console creates).  A legacy name-slug
    id is resolved through the DAT so a save stored under the old scheme can
    still be written to a card.
    """
    title_id = (title_id or "").strip()
    if not title_id:
        return []

    serial = parse_dc_title_id(title_id)
    if serial:
        return dc_device_folder_ids(serial)

    if title_id.upper().startswith("DC_"):
        # Legacy slug form, e.g. DC_sonic_adventure_usa.
        slug = title_id[3:].lower()
        found = _serial_by_slug().get(slug, "")
        if found:
            return dc_device_folder_ids(found)
    return []


def resolve_save_identity(game_id: str, title_txt_name: str = "") -> tuple[str, str]:
    """``(title_id, game_name)`` for a virtual VMU folder named after a serial.

    ``title_txt_name`` is the device's own label (openMenu's ``TITLE.TXT``); it
    is only used for display when the DAT has no entry, never for the title id
    — the id must come from the serial so every client agrees.
    """
    title_id = title_id_for_game_id(game_id)
    name = game_name_for_serial(game_id)
    if not name:
        name = (title_txt_name or "").strip() or normalize_game_id(game_id)
    return title_id, name
