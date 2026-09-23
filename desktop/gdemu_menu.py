"""GDMENU / openMenu game-list generation for a GDEMU SD card.

The menu's game list is an ini file — ``LIST.INI`` for GDMENU, ``OPENMENU.INI``
for openMenu — with one block per numbered folder::

    [OPENMENU]
    num_items=3

    [ITEMS]
    01.name=openMenu
    01.disc=1/1
    01.vga=1
    01.region=JUE
    01.version=V0.1.0
    01.date=20220101
    01.product=T0000

    02.name=Crazy Taxi (USA)
    ...

**That file lives inside the menu disc image in folder 01**, not on the card:
openMenu opens it off its own disc (``gd_list.c``: ``PATH_PREFIX
"OPENMENU.INI"`` where the prefix is the GD-ROM).  Nothing on the card is read
at boot, so a game installed without updating that file is bootable but
invisible in the menu.  ``update_card_menu`` therefore writes the regenerated
list twice: staged at the SD card root (a plain record, and what GD MENU Card
Manager would consume), and patched into the menu image itself by
``openmenu_image``, which is the copy the console actually reads.  When an image
can't be patched safely the staged file is still written and the caller reports
that the menu needs a rebuild.

Format mirrors GD MENU Card Manager's ``Manager.FillListText``
(https://github.com/sonik-br/GDMENUCardManager), plus the ``.folder`` /
``.type`` keys openMenu's virtual-folder build adds, so the result is a drop-in
for the file that project generates.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

from dreamcast_ipbin import IpBin, read_folder_ip_bin

GDMENU_LIST_FILE = "LIST.INI"
OPENMENU_LIST_FILE = "OPENMENU.INI"
MENU_KIND_GDMENU = "gdmenu"
MENU_KIND_OPENMENU = "openmenu"

# Menu discs identify themselves in IP.BIN by name.
_MENU_NAMES = {"GDMENU": MENU_KIND_GDMENU, "OPENMENU": MENU_KIND_OPENMENU}

# Fallbacks for a folder whose disc header can't be read (an unsupported image
# layout, or a CDI whose data track sits past the scan limit).  Region JUE and
# VGA on match what every menu manager defaults to for an unknown disc.
_FALLBACK_REGION = "JUE"
_FALLBACK_DISC = "1/1"

_ENTRY_RE = re.compile(
    r"^(?P<number>\d{2,4})\."
    r"(?P<key>name|disc|vga|region|version|date|product|folder|type)=(?P<value>.*)$"
)


@dataclass
class MenuEntry:
    """One numbered folder as the menu list describes it."""

    number: str
    name: str
    disc: str = _FALLBACK_DISC
    vga: bool = True
    region: str = _FALLBACK_REGION
    version: str = ""
    date: str = ""
    product: str = ""
    # openMenu's virtual-folder build carries two more keys per item: the
    # folder path the game is filed under (empty = the root list) and the item
    # type.  GD MENU Card Manager caches both as folder.txt / type.txt.
    folder: str = ""
    type: str = "game"


def numbered_folders(root: Path) -> list[tuple[str, Path]]:
    """``[(folder name, path)]`` for every numeric folder, in menu order."""
    found: list[tuple[int, str, Path]] = []
    try:
        entries = list(os.scandir(root))
    except OSError:
        return []
    for entry in entries:
        if entry.is_dir() and entry.name.isdigit():
            found.append((int(entry.name), entry.name, Path(entry.path)))
    return [(name, path) for _n, name, path in sorted(found)]


def folder_text_file(folder: Path, filename: str) -> str:
    """Contents of one of the card's little metadata caches, or ``""``.

    GD MENU Card Manager writes ``name.txt``, ``serial.txt``, ``disc.txt``,
    ``region.txt``, ``version.txt``, ``date.txt``, ``vga.txt``, ``type.txt`` and
    ``folder.txt`` beside each game so it needn't re-read the disc header; we
    write and read the same files.
    """
    wanted = filename.lower()
    try:
        for entry in os.scandir(folder):
            if entry.is_file() and entry.name.lower() == wanted:
                return (
                    Path(entry.path)
                    .read_text(encoding="utf-8", errors="ignore")
                    .strip()
                )
    except OSError:
        pass
    return ""


def folder_name_txt(folder: Path) -> str:
    """The folder's ``name.txt`` label (``""`` when absent)."""
    return folder_text_file(folder, "name.txt")


def parse_list_ini(path: Path) -> dict[str, MenuEntry]:
    """Read a previously generated list ini into ``{folder number: entry}``.

    Used as a cache: re-reading a disc header for every folder on every install
    would mean re-scanning the whole card, so entries whose ``name.txt`` still
    matches are carried over untouched.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}

    entries: dict[str, MenuEntry] = {}
    for line in text.splitlines():
        match = _ENTRY_RE.match(line.strip())
        if not match:
            continue
        number = match.group("number")
        key = match.group("key")
        value = match.group("value").strip()
        entry = entries.setdefault(number, MenuEntry(number=number, name=""))
        if key == "vga":
            entry.vga = value == "1"
        elif key == "name":
            entry.name = value
        else:
            setattr(entry, key, value)
    return entries


def _entry_from_ip_bin(
    number: str, name: str, ip: IpBin, folder: Path | None = None
) -> MenuEntry:
    entry = MenuEntry(
        number=number,
        name=name or ip.name,
        disc=ip.disc or _FALLBACK_DISC,
        vga=ip.vga,
        region=ip.region or _FALLBACK_REGION,
        version=ip.version,
        date=ip.date,
        product=ip.game_id,
    )
    if folder is not None:
        entry.folder = folder_text_file(folder, "folder.txt")
        entry.type = folder_text_file(folder, "type.txt") or "game"
    return entry


def _fallback_entry(number: str, name: str) -> MenuEntry:
    """Best guess for a folder whose disc header couldn't be read.

    The product number is the one field a menu really needs beyond the name
    (openMenu matches box art on it), so it is recovered from the Dreamcast DAT
    via the game's name when possible.
    """
    product = ""
    if name:
        try:
            from dreamcast import serial_for_name

            product = serial_for_name(name)
        except Exception:
            product = ""
    return MenuEntry(number=number, name=name or number, product=product)


def build_entries(root: Path, cached: dict[str, MenuEntry] | None = None) -> list[MenuEntry]:
    """Describe every numbered folder on the card, reusing ``cached`` where valid.

    A cached entry is only reused when the folder's ``name.txt`` still says the
    same thing, so a folder replaced outside GameSync is re-read.  The name is
    also matched across folder numbers: an alphabetical renumber moves nearly
    every game, and re-reading a disc header per folder would mean re-scanning
    the whole card for a change that altered no game's metadata.
    """
    cached = cached or {}
    by_name = {
        entry.name: entry for entry in cached.values() if entry.name
    }
    entries: list[MenuEntry] = []
    for number, folder in numbered_folders(root):
        name = folder_name_txt(folder)
        previous = cached.get(number)
        if previous is None and name:
            moved = by_name.get(name)
            if moved is not None:
                previous = replace(moved, number=number)
        if previous is not None and name and previous.name == name:
            entries.append(replace(previous, number=number))
            continue
        ip = read_folder_ip_bin(folder)
        if ip is not None:
            entries.append(_entry_from_ip_bin(number, name, ip, folder))
        else:
            fallback = _fallback_entry(number, name)
            fallback.folder = folder_text_file(folder, "folder.txt")
            fallback.type = folder_text_file(folder, "type.txt") or "game"
            entries.append(fallback)
    return entries


def detect_menu_kind(root: Path, default: str = MENU_KIND_OPENMENU) -> str:
    """Which menu occupies folder ``01`` — read from its disc header, then from
    whichever list ini the card already carries."""
    menu_folder = Path(root) / "01"
    if menu_folder.is_dir():
        ip = read_folder_ip_bin(menu_folder)
        if ip is not None:
            kind = _MENU_NAMES.get(ip.name.strip().upper())
            if kind:
                return kind
        label = folder_name_txt(menu_folder).strip().upper()
        if label in _MENU_NAMES:
            return _MENU_NAMES[label]
    if (Path(root) / OPENMENU_LIST_FILE).is_file():
        return MENU_KIND_OPENMENU
    if (Path(root) / GDMENU_LIST_FILE).is_file():
        return MENU_KIND_GDMENU
    return default


def render_list_ini(entries: list[MenuEntry], menu_kind: str) -> str:
    """Render the list ini text for ``menu_kind``.

    GDMENU takes a bare ``[GDMENU]`` section; openMenu wants an item count and
    an ``[ITEMS]`` section, and reads a ``.product`` key GDMENU has no use for.
    """
    lines: list[str] = []
    if menu_kind == MENU_KIND_OPENMENU:
        lines.append("[OPENMENU]")
        lines.append(f"num_items={len(entries)}")
        lines.append("")
        lines.append("[ITEMS]")
    else:
        lines.append("[GDMENU]")

    for entry in entries:
        number = entry.number
        lines.append(f"{number}.name={entry.name}")
        lines.append(f"{number}.disc={entry.disc}")
        lines.append(f"{number}.vga={'1' if entry.vga else '0'}")
        lines.append(f"{number}.region={entry.region}")
        lines.append(f"{number}.version={entry.version}")
        lines.append(f"{number}.date={entry.date}")
        if menu_kind == MENU_KIND_OPENMENU:
            lines.append(f"{number}.product={entry.product}")
            lines.append(f"{number}.folder={entry.folder}")
            lines.append(f"{number}.type={entry.type}")
        lines.append("")
    return "\n".join(lines)


def list_file_for(menu_kind: str) -> str:
    return (
        OPENMENU_LIST_FILE if menu_kind == MENU_KIND_OPENMENU else GDMENU_LIST_FILE
    )


def refresh_menu_list(root: Path, menu_kind: str = "") -> Path | None:
    """Regenerate the staged game list at the SD card root.

    Returns the file written, or ``None`` when the card holds no numbered
    folders or the write failed (a read-only card must never fail an install).
    """
    return (update_card_menu(root, menu_kind) or {}).get("staged")


def update_card_menu(root: Path, menu_kind: str = "") -> dict | None:
    """Regenerate the game list, stage it at the card root, patch the menu image.

    The staged copy at the root is the record of what the list should be; the
    patched copy inside folder 01's disc image is what the console actually
    reads (see ``openmenu_image``).  Patching is best effort — an image this
    can't rewrite safely leaves the staged file as the only output, and the
    caller reports that the menu needs a rebuild.

    Returns ``{"staged", "patched", "error", "entries"}`` or ``None`` when the
    card holds no numbered folders.
    """
    root = Path(root)
    kind = menu_kind or detect_menu_kind(root)
    target = root / list_file_for(kind)
    entries = build_entries(root, parse_list_ini(target))
    if not entries:
        return None

    text = render_list_ini(entries, kind)
    staged: Path | None = None
    try:
        target.write_text(text, encoding="utf-8")
        staged = target
    except OSError:
        staged = None

    patched = None
    error = ""
    menu_folder = root / "01"
    if menu_folder.is_dir():
        try:
            from openmenu_image import MenuImageError, patch_menu_list

            try:
                patched = patch_menu_list(menu_folder, text, list_file_for(kind))
            except MenuImageError as exc:
                error = str(exc)
        except Exception as exc:  # unreadable image, permissions, ...
            error = str(exc)
    else:
        error = "No menu disc in folder 01."

    return {"staged": staged, "patched": patched, "error": error, "entries": entries}
