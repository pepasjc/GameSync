"""Cemu (Wii U) save scanner for EmuDeck on Steam Deck.

Cemu mirrors the console's own layout, so a Wii U save lives at::

    <mlc01>/usr/save/00050000/<tidlo>/user/<persistentId>/...

which is exactly what the Wii U homebrew client (``wiiu/source/natives.c``)
walks on real hardware.  Keeping the same shape means both sides produce the
same bundle:

  * ``title_id`` = the native 16-hex id, uppercase (``00050000`` + ``tidlo``)
  * bundle members are paths relative to the ``user/`` directory
  * the save hash is the concatenation of file contents sorted by relative
    path — ``sha256_dir_tree_files``, matching the server's bundle hash

Names come from the title's own ``meta.xml`` (Cemu keeps installed content
under the same ``mlc01`` tree the console uses).  The server cannot name a
Wii U save from its title id — the low word is not the product code — but it
*can* resolve the 4-char product code from the Wii U DAT, so the scanner also
extracts ``<product_code>`` and passes it up as ``WIIU_<code>`` for the
upload's ``game_code`` parameter.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Generator, Optional

_REPO_ROOT = str(Path(__file__).parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared.systems import normalize_system_code  # noqa: E402

# meta.xml handling is shared with the desktop client — both need to turn a
# 16-hex Wii U title id into a name, and neither can do it from the id alone.
from shared.wiiu_meta import (  # noqa: E402
    TITLE_HIGH as _TITLE_HIGH,
    build_meta_index as _build_meta_index,
    game_paths_from_settings,
    host_dir as _host_dir,
    mlc_path_from_settings as _mlc_path_from_settings,
    parse_meta_xml,
)

from .base import find_paths, sha256_dir_tree_files
from .models import GameEntry, SyncStatus

_HEX8_RE = re.compile(r"^[0-9A-Fa-f]{8}$")
_TITLE_ID_RE = re.compile(r"^0005[0-9A-Fa-f]{12}$")


# ──────────────────────────────────────────────────────────────────────────────
# mlc01 discovery
# ──────────────────────────────────────────────────────────────────────────────

# Resolved per call rather than at import: the Steam Deck client is long-lived
# and tests relocate HOME, so a module-level snapshot would go stale.
_FLATPAK_CEMU_DATA_REL = ".var/app/info.cemu.Cemu/data/Cemu"
_FLATPAK_CEMU_CONFIG_REL = ".var/app/info.cemu.Cemu/config/Cemu"
_LOCAL_CEMU_DATA_REL = ".local/share/Cemu"
_CONFIG_CEMU_REL = ".config/Cemu"


def _settings_candidates(emulation_path: Path) -> list[Path]:
    home = Path.home()
    return [
        home / _CONFIG_CEMU_REL / "settings.xml",
        home / _FLATPAK_CEMU_CONFIG_REL / "settings.xml",
        home / _FLATPAK_CEMU_DATA_REL / "settings.xml",
        home / _LOCAL_CEMU_DATA_REL / "settings.xml",
        emulation_path / "storage" / "cemu" / "settings.xml",
        emulation_path / "storage" / "Cemu" / "settings.xml",
        emulation_path / "roms" / "wiiu" / "settings.xml",
        home / "Applications" / "Cemu" / "settings.xml",
    ]


def _mlc_candidates(emulation_path: Path) -> list[Path]:
    home = Path.home()
    return [
        emulation_path / "storage" / "cemu" / "mlc01",
        emulation_path / "storage" / "Cemu" / "mlc01",
        emulation_path / "saves" / "cemu" / "mlc01",
        emulation_path / "saves" / "Cemu" / "mlc01",
        emulation_path / "roms" / "wiiu" / "mlc01",
        emulation_path / "cemu" / "mlc01",
        home / _FLATPAK_CEMU_DATA_REL / "mlc01",
        home / _LOCAL_CEMU_DATA_REL / "mlc01",
        home / _CONFIG_CEMU_REL / "mlc01",
        home / "Applications" / "Cemu" / "mlc01",
        home / "Emulation" / "storage" / "cemu" / "mlc01",
    ]


def _normalize_mlc_candidate(path: Path) -> Path | None:
    """Accept [path] as an mlc01 root, or its ``mlc01`` child, else None.

    Users reasonably point at either the ``mlc01`` folder or the Cemu install
    that contains it, so both are taken.  A folder Cemu has created but never
    written a save to has ``usr/title`` and no ``usr/save`` yet — refusing it
    would send a freshly-downloaded save to the guessed location instead.
    """
    if not path.is_dir():
        return None
    for candidate in (path, path / "mlc01"):
        if not candidate.is_dir():
            continue
        usr = candidate / "usr"
        if (usr / "save").is_dir() or (usr / "title").is_dir():
            return candidate
    return None


def _override_mlc_root(save_dir_override: str) -> Path | None:
    """Resolve the user's explicit Cemu folder to an ``mlc01``.

    The override is a deliberate statement about where Cemu lives, so it also
    honours a ``settings.xml`` sitting in that folder — pointing at an install
    whose MLC was relocated elsewhere is exactly the case the candidate search
    cannot cover.  An unrecognisable path falls through to auto-detection
    rather than silently scanning nothing.
    """
    raw = (save_dir_override or "").strip()
    if not raw:
        return None

    root = Path(raw).expanduser()
    for settings_xml in (root / "settings.xml", root.parent / "settings.xml"):
        configured = _mlc_path_from_settings(settings_xml)
        resolved = _normalize_mlc_candidate(configured) if configured else None
        if resolved is not None:
            return resolved

    resolved = _normalize_mlc_candidate(root)
    if resolved is not None:
        return resolved

    # Named mlc01 and real, just empty — Cemu will populate it, and the user
    # said this is the place.
    if root.is_dir() and root.name.lower() == "mlc01":
        return root
    return None


def resolve_mlc_root(
    emulation_path: Path, save_dir_override: str = ""
) -> Path | None:
    """Locate Cemu's ``mlc01`` directory.

    An explicit override wins over everything — EmuDeck rarely installs Cemu
    under the Emulation folder, so the guesses below can all miss.  After that
    a configured ``<mlc_path>`` wins over the directory guesses: users who
    moved the MLC to an SD card have a settings.xml that says so, and the
    default location is then an empty leftover.
    """
    override = _override_mlc_root(save_dir_override)
    if override is not None:
        return override

    for settings_xml in _settings_candidates(emulation_path):
        if not settings_xml.is_file():
            continue
        configured = _mlc_path_from_settings(settings_xml)
        if configured is not None and (configured / "usr" / "save").is_dir():
            return configured

    return find_paths(*_mlc_candidates(emulation_path))


def resolve_save_root(
    emulation_path: Path, save_dir_override: str = ""
) -> Path | None:
    mlc = resolve_mlc_root(emulation_path, save_dir_override)
    if mlc is None:
        return None
    save_root = mlc / "usr" / "save" / _TITLE_HIGH
    return save_root if save_root.is_dir() else None


# ──────────────────────────────────────────────────────────────────────────────
# meta.xml
# ──────────────────────────────────────────────────────────────────────────────


def _game_path_candidates(emulation_path: Path, mlc_root: Optional[Path]) -> list[Path]:
    """Directories that may hold *loose* Wii U game folders.

    Most Cemu users never install a game to the MLC — they keep unpacked dumps
    (``<Game>/code``, ``content``, ``meta``) in a games folder and launch the
    .rpx directly.  Those titles have no ``mlc01/usr/title`` entry at all, so
    the MLC-only lookup left every one of them showing a raw title id.
    """
    home = Path.home()
    roots = [
        emulation_path / "roms" / "wiiu",
        emulation_path / "roms" / "wiiu" / "roms",
        emulation_path / "storage" / "cemu" / "games",
        home / "Emulation" / "roms" / "wiiu",
    ]
    if mlc_root is not None:
        roots.append(mlc_root.parent / "games")

    # Cemu's own configured game folders win — they are where the user
    # actually keeps their dumps.
    for settings_xml in _settings_candidates(emulation_path):
        for path in game_paths_from_settings(settings_xml):
            roots.insert(0, path)

    seen: set[Path] = set()
    unique: list[Path] = []
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        if root.is_dir():
            unique.append(root)
    return unique


def build_meta_index(
    emulation_path: Path, mlc_root: Optional[Path]
) -> dict[str, tuple[Optional[str], Optional[str]]]:
    """Map ``title_id -> (display_name, game_code)`` for this machine.

    Covers installed MLC content *and* loose game folders — most Cemu
    libraries are unpacked dumps that never touch the MLC, so an MLC-only
    lookup left every one of those saves showing a raw title id.
    """
    return _build_meta_index(
        mlc_root=mlc_root,
        game_roots=_game_path_candidates(emulation_path, mlc_root),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Scan
# ──────────────────────────────────────────────────────────────────────────────


def _dir_mtime(save_dir: Path) -> float:
    try:
        return max(
            (fp.stat().st_mtime for fp in save_dir.rglob("*") if fp.is_file()),
            default=0.0,
        )
    except Exception:
        return 0.0


def _dir_size(save_dir: Path) -> int:
    total = 0
    for fp in save_dir.rglob("*"):
        if fp.is_file():
            total += fp.stat().st_size
    return total


def default_save_path(
    emulation_path: Path, title_id: str, save_dir_override: str = ""
) -> Path:
    """Where a Wii U save for [title_id] belongs under Cemu's mlc01."""
    upper = title_id.upper()
    if not _TITLE_ID_RE.match(upper):
        raise ValueError(f"Invalid Wii U title_id: {title_id}")

    mlc = resolve_mlc_root(emulation_path, save_dir_override)
    if mlc is None:
        mlc = emulation_path / "storage" / "cemu" / "mlc01"
    return mlc / "usr" / "save" / upper[:8] / upper[8:].lower() / "user"


def scan(
    emulation_path: Path, save_dir_override: str = ""
) -> Generator[GameEntry, None, None]:
    """Scan Cemu's mlc01 for Wii U saves."""
    mlc = resolve_mlc_root(emulation_path, save_dir_override)
    if mlc is None:
        return
    save_root = mlc / "usr" / "save" / _TITLE_HIGH
    if not save_root.is_dir():
        return

    meta_index = build_meta_index(emulation_path, mlc)

    for title_dir in sorted(save_root.iterdir()):
        if not title_dir.is_dir() or not _HEX8_RE.match(title_dir.name):
            continue

        save_dir = title_dir / "user"
        if not save_dir.is_dir():
            continue
        if not any(fp.is_file() for fp in save_dir.rglob("*")):
            continue

        title_id = (_TITLE_HIGH + title_dir.name).upper()
        name, game_code = meta_index.get(title_id, (None, None))

        entry = GameEntry(
            title_id=title_id,
            display_name=name or title_id,
            system="WIIU",
            emulator="Cemu",
            save_path=save_dir,
            is_multi_file=True,
            save_mtime=_dir_mtime(save_dir),
            game_code=game_code,
        )
        try:
            entry.save_hash = sha256_dir_tree_files(save_dir)
            entry.save_size = _dir_size(save_dir)
        except Exception:
            pass
        yield entry


def build_server_only_entries(
    server_saves: dict[str, dict],
    seen_ids: set[str],
    emulation_path: Path,
    save_dir_override: str = "",
) -> list[GameEntry]:
    """Placeholders for Wii U saves that exist only on the server.

    The save path is predictable from the title id alone, so these rows are
    directly downloadable — no ROM needed, same as the 3DS/PS3 builders.

    A save uploaded by the Wii U console carries no name the server can
    resolve (its title id's low word is not the product code), so the local
    meta.xml index names these rows whenever the game is on this machine.
    """
    results: list[GameEntry] = []
    meta_index: dict[str, tuple[Optional[str], Optional[str]]] | None = None

    for title_id, info in server_saves.items():
        if title_id in seen_ids:
            continue
        if not _TITLE_ID_RE.match(title_id):
            continue

        system = normalize_system_code(
            str(
                info.get("system")
                or info.get("console_type")
                or info.get("platform")
                or ""
            )
        )
        if system != "WIIU":
            continue

        try:
            save_path = default_save_path(
                emulation_path, title_id, save_dir_override
            )
        except ValueError:
            continue

        if meta_index is None:
            meta_index = build_meta_index(
                emulation_path, resolve_mlc_root(emulation_path, save_dir_override)
            )
        local_name, game_code = meta_index.get(title_id.upper(), (None, None))

        server_name = info.get("name") or info.get("game_name") or ""
        display_name = (
            server_name if server_name and server_name != title_id else None
        ) or local_name or title_id

        results.append(
            GameEntry(
                title_id=title_id,
                display_name=display_name,
                game_code=game_code,
                system="WIIU",
                emulator="Cemu",
                save_path=save_path,
                is_multi_file=True,
                status=SyncStatus.SERVER_ONLY,
                server_hash=info.get("save_hash"),
                server_title_id=info.get("title_id") or title_id,
                server_timestamp=info.get("client_timestamp"),
                server_size=info.get("save_size"),
            )
        )

    return results
