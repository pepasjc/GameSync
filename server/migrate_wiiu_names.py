"""Give stored Wii U saves a real game name (and the right platform).

Background
----------
A Wii U save is keyed by its 16-hex title id (``0005000010143500``).  Unlike a
GameCube or Wii id, the low word is **not** the ASCII product code, so nothing
the server ships can name it: the Wii U DAT is keyed by 4-char product codes
and has no title-id column.  Saves uploaded by the Wii U homebrew client
before it started sending a name hint are therefore stored with ``name`` equal
to their own title id, and every client — the desktop app above all, which has
no local scan of its own — lists them as raw hex.

The mapping only exists in the title's ``meta.xml``:

    <title_id>0005000010143500</title_id>
    <product_code>WUP-P-ARDE</product_code>
    <longname_en>Super Mario 3D World</longname_en>

so this migration reads meta.xml from wherever the titles actually live —
a Cemu install (``mlc01/usr/title`` for installed games, loose ``<Game>/meta``
folders for dumps) or a Wii U NAND dump — and writes the names it finds into
the save metadata.  A ``--map`` file covers anything that is on no disk here.

Where a title's product code is known, the server's own Wii U DAT name wins
over the console's ``longname``, so a save named by this script matches one
named by an upload hint.

Also repairs ``platform``/``system`` on 00050 titles: saves uploaded before
the server learned to detect Wii U ids were filed as "3DS", which puts them in
the wrong bucket for every client's console-type filter.  That fix needs no
name source and always runs.

Usage
-----
    # See what would change (default: dry run)
    uv run python migrate_wiiu_names.py --meta-dir /mnt/games/Cemu

    # Apply
    uv run python migrate_wiiu_names.py --meta-dir /mnt/games/Cemu --apply

    # Several sources, plus a hand-written map for the stragglers
    uv run python migrate_wiiu_names.py \
        --meta-dir ~/Cemu --meta-dir /mnt/nand-dump \
        --map wiiu_names.json --apply

``--map`` takes JSON (``{"0005000010143500": "Super Mario 3D World"}``) or CSV
(``0005000010143500,Super Mario 3D World`` per line).

Idempotent, and never overwrites a name that already resolved — pass
``--force`` to re-derive names for titles that already have one.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.config import settings  # noqa: E402
from app.services import db, game_names  # noqa: E402
from shared.wiiu_meta import (  # noqa: E402
    build_meta_index,
    game_paths_from_settings,
    is_wiiu_title_id,
    mlc_path_from_settings,
)

WIIU = "WIIU"

# The DAT index lives in module state that only the running app populates, so
# a standalone run has to load it itself or every lookup silently misses.
_WIIU_DAT = Path(__file__).resolve().parent / "data" / "dats" / "Nintendo - Wii U.dat"


def load_dat(path: Path = _WIIU_DAT) -> int:
    if not path.is_file():
        return 0
    return game_names.load_libretro_dat_to_dicts(path)


# ──────────────────────────────────────────────────────────────────────────────
# Name sources
# ──────────────────────────────────────────────────────────────────────────────


def default_meta_dirs() -> list[Path]:
    """Cemu / NAND locations to try when the caller names none."""
    home = Path.home()
    candidates = [
        home / "Cemu",
        home / "Documents" / "Cemu",
        home / "AppData" / "Roaming" / "Cemu",
        home / ".local" / "share" / "Cemu",
        home / ".config" / "Cemu",
        home / ".var" / "app" / "info.cemu.Cemu" / "data" / "Cemu",
        home / "Emulation" / "storage" / "cemu",
        Path("/mnt/mlc01"),
    ]
    return [c for c in candidates if c.is_dir()]


def index_from_dir(root: Path) -> dict[str, tuple[str | None, str | None]]:
    """Build a title_id -> (name, game_code) index for one directory.

    ``root`` may be a Cemu install, an ``mlc01`` itself, a NAND dump, or a
    plain folder of game dumps — all four are checked rather than asking the
    user which one they handed us.
    """
    mlc: Path | None = None
    if (root / "usr" / "title").is_dir() or (root / "sys" / "title").is_dir():
        mlc = root
    elif (root / "mlc01" / "usr").is_dir():
        mlc = root / "mlc01"

    settings_xml = root / "settings.xml"
    game_roots = game_paths_from_settings(settings_xml)
    game_roots.extend([root, root / "games", root / "roms" / "wiiu"])

    configured_mlc = mlc_path_from_settings(settings_xml)
    if mlc is None and configured_mlc is not None:
        mlc = configured_mlc

    return build_meta_index(mlc, game_roots)


def load_map_file(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Read a map file into ``(names, codes)``, both keyed by title id.

    Accepts CSV ``title_id,name[,game_code]`` or JSON — either
    ``{"<tid>": "Name"}`` or ``{"<tid>": {"name": ..., "code": ...}}``.  The
    optional product code lets the server's own DAT name win, so a title named
    from a map matches one named by an upload hint.
    """
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return {}, {}

    names: dict[str, str] = {}
    codes: dict[str, str] = {}

    def add(title_id: str, name: str, code: str = "") -> None:
        key = title_id.strip().upper()
        if not key or key == "TITLE_ID":
            return
        if name.strip():
            names[key] = name.strip()
        code = code.strip().upper()
        if code:
            codes[key] = code if code.startswith("WIIU_") else f"WIIU_{code[-4:]}"

    if text[0] in "{[":
        data = json.loads(text)
        if isinstance(data, list):
            data = {row["title_id"]: row for row in data}
        for key, value in data.items():
            if isinstance(value, dict):
                add(str(key), str(value.get("name", "")), str(value.get("code", "")))
            else:
                add(str(key), str(value))
        return names, codes

    for row in csv.reader(text.splitlines()):
        if len(row) < 2:
            continue
        add(row[0], row[1], row[2] if len(row) > 2 else "")
    return names, codes


def build_name_source(
    meta_dirs: list[Path], map_files: list[Path]
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Collect ``(meta_names, map_names, codes)`` keyed by uppercase title id.

    Map names are kept apart from scanned ones because they outrank everything
    — a hand-written map exists to correct whatever the automatic sources got
    wrong.  Among scanned sources, first hit wins.
    """
    meta_names: dict[str, str] = {}
    map_names: dict[str, str] = {}
    codes: dict[str, str] = {}

    for root in meta_dirs:
        for title_id, (name, code) in index_from_dir(root).items():
            key = title_id.upper()
            if name and key not in meta_names:
                meta_names[key] = name
            if code and key not in codes:
                codes[key] = code

    for path in map_files:
        file_names, file_codes = load_map_file(path)
        map_names.update(file_names)
        codes.update(file_codes)

    return meta_names, map_names, codes


def resolve_name(
    title_id: str,
    meta_names: dict[str, str],
    map_names: dict[str, str],
    codes: dict[str, str],
) -> tuple[str | None, str]:
    """Best name for a title plus its source ("map" / "dat" / "meta" / "").

    Precedence: an explicit map, then the DAT (by title id, then by product
    code), then a console/Cemu meta.xml longname.  The DAT outranks meta.xml
    so a migrated name matches the catalogue casing every other system uses.
    """
    name = map_names.get(title_id)
    if name:
        return name, "map"

    # The Wii U DAT carries title_id lines (tools/enrich_wiiu_dat_titleids.py),
    # so most titles resolve with no --meta-dir and no --map at all.
    typed = game_names.lookup_names_typed([title_id])
    if title_id in typed:
        return typed[title_id][0], "dat"

    code = codes.get(title_id)
    if code:
        typed = game_names.lookup_names_typed([code])
        if code in typed:
            return typed[code][0], "dat"

    name = meta_names.get(title_id)
    if name:
        return name, "meta"
    return None, ""


# ──────────────────────────────────────────────────────────────────────────────
# Metadata writes
# ──────────────────────────────────────────────────────────────────────────────


def _write_row(row: dict, name: str, apply: bool) -> None:
    """Persist name + Wii U platform/system to the DB and any legacy JSON."""
    if not apply:
        return

    updated = dict(row)
    updated["name"] = name
    updated["platform"] = WIIU
    updated["system"] = WIIU
    db.upsert(updated)

    meta_path = settings.save_dir / row["title_id"] / "metadata.json"
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return
        data["name"] = name
        data["platform"] = WIIU
        data["system"] = WIIU
        meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def dump_unnamed(path: Path) -> int:
    """Write a CSV of the Wii U saves that still have no name.

    The server usually runs on a headless box with no Cemu install and no NAND
    dump, so there is nothing local to scan.  This produces the skeleton of a
    ``--map`` file: fill in the names (Cemu shows them in its game list) and
    feed it back with ``--map``.
    """
    rows = [
        r
        for r in db.list_all()
        if is_wiiu_title_id(str(r.get("title_id", "")))
        and (not r.get("name") or r["name"] == r["title_id"])
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["title_id", "name"])
        for row in sorted(rows, key=lambda r: r["title_id"]):
            writer.writerow([row["title_id"], ""])
    print(f"Wrote {len(rows)} unnamed Wii U title(s) to {path}")
    return len(rows)


def migrate(
    meta_dirs: list[Path] | None = None,
    map_files: list[Path] | None = None,
    apply: bool = False,
    force: bool = False,
    verbose: bool = True,
) -> tuple[int, int, int]:
    """Rename Wii U saves stored under their own title id.

    Returns ``(examined, renamed, platform_fixed)``.  ``examined`` counts Wii U
    saves considered, ``renamed`` those that got (or would get) a name, and
    ``platform_fixed`` those whose platform/system was wrong — a Wii U save
    filed as 3DS by an older server.
    """
    load_dat()

    meta_names, map_names, codes = build_name_source(meta_dirs or [], map_files or [])

    if verbose and map_files and not map_names:
        print(
            "Note: the map file(s) contain no filled-in names — the second\n"
            "column is still empty, so there is nothing to apply.\n"
        )

    examined = renamed = platform_fixed = 0

    for row in db.list_all():
        title_id = str(row.get("title_id", ""))
        if not is_wiiu_title_id(title_id):
            continue
        key = title_id.upper()
        examined += 1

        current_name = str(row.get("name") or "")
        needs_name = force or not current_name or current_name == title_id
        wrong_platform = (
            str(row.get("platform") or "") != WIIU or str(row.get("system") or "") != WIIU
        )

        new_name = current_name
        source = ""
        if needs_name:
            resolved, source = resolve_name(key, meta_names, map_names, codes)
            if resolved and resolved != current_name:
                new_name = resolved
                renamed += 1
            elif needs_name and not resolved and verbose:
                print(f"  {title_id}  no name in any source — still unnamed")

        if wrong_platform:
            platform_fixed += 1

        if new_name == current_name and not wrong_platform:
            continue

        if verbose:
            bits = []
            if new_name != current_name:
                bits.append(f'name "{current_name}" -> "{new_name}" ({source})')
            if wrong_platform:
                bits.append(
                    f"platform {row.get('platform') or '?'}/"
                    f"{row.get('system') or '?'} -> WIIU/WIIU"
                )
            print(f"  {title_id}  " + "; ".join(bits))

        _write_row(row, new_name or title_id, apply)

    if verbose:
        mode = "applied" if apply else "dry run"
        print(
            f"\n{examined} Wii U save(s) examined, {renamed} renamed, "
            f"{platform_fixed} platform fix(es) [{mode}]"
        )
        if not apply and (renamed or platform_fixed):
            print("Re-run with --apply to write the changes.")

    return examined, renamed, platform_fixed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--meta-dir",
        action="append",
        default=[],
        metavar="DIR",
        help="Cemu install, mlc01, NAND dump or folder of game dumps to read "
        "meta.xml from (repeatable). Auto-detects common Cemu locations "
        "when omitted.",
    )
    parser.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="FILE",
        help="JSON or CSV of title_id -> name, applied over scanned results "
        "(repeatable).",
    )
    parser.add_argument(
        "--dump-unnamed",
        metavar="FILE",
        help="Write a CSV skeleton of every still-unnamed Wii U title and "
        "exit. Fill in the names, then re-run with --map FILE --apply.",
    )
    parser.add_argument("--apply", action="store_true", help="Write the changes.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-derive names for titles that already have one.",
    )
    args = parser.parse_args()

    print(f"Save dir: {settings.save_dir}")

    if args.dump_unnamed:
        dump_unnamed(Path(args.dump_unnamed).expanduser())
        return 0

    meta_dirs = [Path(d).expanduser() for d in args.meta_dir]
    missing = [d for d in meta_dirs if not d.is_dir()]
    for d in missing:
        print(f"warning: --meta-dir {d} is not a directory, skipping")
    meta_dirs = [d for d in meta_dirs if d.is_dir()]

    if not meta_dirs:
        meta_dirs = default_meta_dirs()
        if meta_dirs:
            print("Scanning auto-detected Cemu locations:")
            for d in meta_dirs:
                print(f"  {d}")

    map_files = [Path(f).expanduser() for f in args.map]
    for f in map_files:
        if not f.is_file():
            print(f"error: --map {f} not found")
            return 2

    if not meta_dirs and not map_files:
        print(
            "No name source. Pass --meta-dir pointing at a Cemu install / NAND\n"
            "dump, or --map with a title_id,name file.  Platform repairs still\n"
            "run, but nothing can be renamed."
        )

    migrate(meta_dirs=meta_dirs, map_files=map_files, apply=args.apply, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
