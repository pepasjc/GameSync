"""Wii U ``meta.xml`` reading, shared by every Python client.

A Wii U save is keyed by its 16-hex title id (``0005000010143500``), and that
id says nothing about the game: unlike a GameCube or Wii id, its low word is
not the ASCII product code, so no DAT can name it.  The only mapping that
exists is inside the title's own ``meta.xml``, which carries all three of the
things a client needs:

    <title_id>0005000010143500</title_id>
    <product_code>WUP-P-ARDE</product_code>     -> "WIIU_ARDE", the DAT key
    <longname_en>Super Mario 3D World</longname_en>

So whichever machine has the game — a console, a Cemu install, an unpacked
dump — is the one that can name the save, for itself and (by pushing the
result to the server) for every other client.

Cemu keeps installed titles under ``mlc01/usr/title``, but most libraries are
*loose* folders (``<Game>/code``, ``content``, ``meta``) that were never
installed, so both locations have to be searched.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Iterable, Optional

__all__ = [
    "TITLE_HIGH",
    "META_HIGH_IDS",
    "CONTENT_TYPE_BY_HIGH",
    "CONTENT_TYPE_SUFFIX",
    "base_title_id",
    "build_meta_index",
    "content_type",
    "decorate_name",
    "game_paths_from_settings",
    "host_dir",
    "is_wiiu_title_id",
    "iter_meta_files",
    "mlc_path_from_settings",
    "parse_meta_xml",
    "read_meta_file",
]

# Wii U application titles are 00050000xxxxxxxx.  An update (0005000E) or DLC
# (0005000C) shares the low word and holds no save data of its own, but its
# meta.xml names the base game — which is the only naming source a patched
# disc game has, since a disc title installs no 00050000 content.
TITLE_HIGH = "00050000"
META_HIGH_IDS = ("00050000", "0005000E", "0005000C")

# Title-id high word -> what kind of content it is.  The low word is shared
# across all of a game's pieces, which is what makes grouping possible without
# any external database: 0005000E10145C00 is the update for 0005000010145C00.
CONTENT_TYPE_BY_HIGH: dict[str, str] = {
    "00050000": "game",
    "0005000E": "update",
    "0005000C": "dlc",
    "00050002": "demo",
}

# Display suffix appended to the base game's name.  "game" gets none — a base
# title is just the game.
CONTENT_TYPE_SUFFIX: dict[str, str] = {
    "update": "(Update)",
    "dlc": "(DLC)",
    "demo": "(Demo)",
}

_TITLE_ID_RE = re.compile(r"^0005[0-9A-Fa-f]{12}$")
_HEX8_RE = re.compile(r"^[0-9A-Fa-f]{8}$")

_LONGNAME_EN_RE = re.compile(
    r"<longname_en[^>]*>(.*?)</longname_en>", re.IGNORECASE | re.DOTALL
)
_LONGNAME_ANY_RE = re.compile(
    r"<longname_[a-z]+[^>]*>(.*?)</longname_", re.IGNORECASE | re.DOTALL
)
_PRODUCT_CODE_RE = re.compile(
    r"<product_code[^>]*>(.*?)</product_code>", re.IGNORECASE | re.DOTALL
)
_META_TITLE_ID_RE = re.compile(
    r"<title_id[^>]*>(.*?)</title_id>", re.IGNORECASE | re.DOTALL
)
_MLC_PATH_RE = re.compile(r"<mlc_path[^>]*>(.*?)</mlc_path>", re.IGNORECASE | re.DOTALL)
_GAME_PATHS_RE = re.compile(
    r"<GamePaths>(.*?)</GamePaths>", re.IGNORECASE | re.DOTALL
)
_ENTRY_RE = re.compile(r"<Entry[^>]*>(.*?)</Entry>", re.IGNORECASE | re.DOTALL)

# meta.xml runs well past 8 KB — <longname_en> sits after the header fields,
# so a short read silently loses the name for some titles.
_META_XML_MAX = 128 * 1024


def is_wiiu_title_id(title_id: str) -> bool:
    return bool(title_id) and bool(_TITLE_ID_RE.match(title_id.strip()))


def content_type(title_id: str) -> str:
    """Classify a Wii U title id: ``game``/``update``/``dlc``/``demo``.

    Returns ``""`` for anything that isn't a recognised Wii U title id, so
    callers can use a falsy result as "not Wii U content".
    """
    if not is_wiiu_title_id(title_id):
        return ""
    return CONTENT_TYPE_BY_HIGH.get(title_id.strip().upper()[:8], "")


def base_title_id(title_id: str) -> str:
    """The base-game id an update/DLC/demo belongs to.

    Swaps the high word for ``00050000`` and keeps the shared low word, so
    ``0005000E10145C00`` -> ``0005000010145C00``.  A base id maps to itself.
    Returns ``""`` when the input isn't a Wii U title id.
    """
    if not is_wiiu_title_id(title_id):
        return ""
    return TITLE_HIGH + title_id.strip().upper()[8:]


def decorate_name(base_name: str, title_id: str) -> str:
    """Label a base-game name for the piece ``title_id`` actually refers to.

    ``("Super Mario 3D World (USA)", "0005000E…")`` ->
    ``"Super Mario 3D World (USA) (Update)"``.  A base title is returned
    unchanged.
    """
    suffix = CONTENT_TYPE_SUFFIX.get(content_type(title_id), "")
    if not suffix or not base_name:
        return base_name
    return f"{base_name} {suffix}"


def _clean(raw: str) -> str:
    """Unescape and collapse a meta.xml text node to one display line."""
    return " ".join(html.unescape(raw).split())


def host_dir(raw: str) -> Optional[Path]:
    """Resolve a path out of a Cemu settings.xml to a real directory.

    Cemu installed under Proton/Wine stores Windows-style paths; ``Z:`` is
    Wine's mapping of ``/``, so dropping that drive letter and flipping the
    separators recovers the host path.  Any other drive letter belongs to a
    prefix we cannot resolve, so it is left alone rather than guessed at.
    """
    value = (raw or "").strip()
    if not value:
        return None

    direct = Path(value)
    if direct.is_dir():
        return direct

    if len(value) >= 3 and value[1] == ":" and value[0].upper() == "Z":
        translated = Path(value[2:].replace("\\", "/"))
        if translated.is_dir():
            return translated

    return None


def _settings_text(settings_xml: Path) -> Optional[str]:
    try:
        if not settings_xml.is_file():
            return None
        return settings_xml.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def mlc_path_from_settings(settings_xml: Path) -> Optional[Path]:
    """``<mlc_path>`` from a Cemu settings.xml, when it points somewhere real.

    An empty element means "next to the executable", which callers already
    cover with their own candidate list, so it reads as absent here.
    """
    text = _settings_text(settings_xml)
    if text is None:
        return None
    m = _MLC_PATH_RE.search(text)
    if not m:
        return None
    return host_dir(html.unescape(m.group(1)))


def game_paths_from_settings(settings_xml: Path) -> list[Path]:
    """``<GamePaths>`` entries from a Cemu settings.xml that exist on disk."""
    text = _settings_text(settings_xml)
    if text is None:
        return []
    block = _GAME_PATHS_RE.search(text)
    if not block:
        return []
    paths: list[Path] = []
    for entry in _ENTRY_RE.findall(block.group(1)):
        path = host_dir(html.unescape(entry))
        if path is not None and path not in paths:
            paths.append(path)
    return paths


def read_meta_file(path: Path) -> Optional[str]:
    """Read a meta.xml, or None when it is missing / not actually one."""
    try:
        if not path.is_file():
            return None
        with open(path, "rb") as f:
            raw = f.read(_META_XML_MAX)
    except Exception:
        return None
    text = raw.decode("utf-8", errors="replace")
    if "<longname" not in text and "<product_code" not in text:
        return None
    return text


def parse_meta_xml(text: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return ``(title_id, display_name, game_code)`` from a meta.xml body.

    ``title_id`` is folded onto the application id, so an update's or DLC's
    meta.xml names the base game whose save actually exists.
    ``game_code`` is the server's Wii U DAT key form (``WIIU_ARDE``).
    """
    title_id: Optional[str] = None
    m = _META_TITLE_ID_RE.search(text)
    if m:
        raw = _clean(m.group(1)).replace("-", "").upper()
        if len(raw) == 16 and all(c in "0123456789ABCDEF" for c in raw):
            title_id = TITLE_HIGH + raw[8:]

    name: Optional[str] = None
    m = _LONGNAME_EN_RE.search(text) or _LONGNAME_ANY_RE.search(text)
    if m:
        name = _clean(m.group(1)) or None

    game_code: Optional[str] = None
    m = _PRODUCT_CODE_RE.search(text)
    if m:
        # WUP-P-ARDE -> WUPPARDE; the DAT is keyed by the trailing 4 chars.
        code = _clean(m.group(1)).upper().replace("-", "")
        if len(code) >= 4 and code[-4:].isalnum():
            game_code = f"WIIU_{code[-4:]}"

    return title_id, name, game_code


def iter_meta_files(
    mlc_root: Optional[Path] = None,
    game_roots: Iterable[Path] = (),
) -> Iterable[tuple[Path, Optional[str]]]:
    """Yield ``(meta_xml_path, fallback_title_id)`` for every candidate found.

    The fallback is only set for MLC content, where the directory name *is*
    the title id — a loose dump's folder name tells us nothing, so those rely
    on the ``<title_id>`` element.
    """
    if mlc_root is not None:
        for base in ("usr", "sys"):
            for high in META_HIGH_IDS:
                high_dir = mlc_root / base / "title" / high
                if not high_dir.is_dir():
                    continue
                try:
                    children = sorted(high_dir.iterdir())
                except Exception:
                    continue
                for title_dir in children:
                    if not title_dir.is_dir() or not _HEX8_RE.match(title_dir.name):
                        continue
                    yield (
                        title_dir / "meta" / "meta.xml",
                        (TITLE_HIGH + title_dir.name).upper(),
                    )

    # <root>/<Game>/meta/meta.xml, plus one level deeper for the
    # "<root>/<Game>/<Game>/meta" nesting some dump tools produce.
    for root in game_roots:
        if not root.is_dir():
            continue
        yield (root / "meta" / "meta.xml", None)
        for pattern in ("*/meta/meta.xml", "*/*/meta/meta.xml"):
            try:
                found = sorted(root.glob(pattern))
            except Exception:
                continue
            for meta in found:
                yield (meta, None)


def build_meta_index(
    mlc_root: Optional[Path] = None,
    game_roots: Iterable[Path] = (),
) -> dict[str, tuple[Optional[str], Optional[str]]]:
    """Map ``title_id -> (display_name, game_code)`` from every meta.xml found.

    Later sources only fill gaps, never overwrite a value already resolved.
    """
    index: dict[str, tuple[Optional[str], Optional[str]]] = {}

    for meta_path, fallback_title_id in iter_meta_files(mlc_root, game_roots):
        text = read_meta_file(meta_path)
        if text is None:
            continue
        title_id, name, code = parse_meta_xml(text)
        title_id = title_id or fallback_title_id
        if not title_id:
            continue
        prev_name, prev_code = index.get(title_id, (None, None))
        index[title_id] = (name or prev_name, code or prev_code)

    return index
