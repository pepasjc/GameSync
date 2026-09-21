"""Pick one MSU pack per game from several source sets, checked against the
baseline ROMs already on the server.

Runs the server's own identity code (``rom_scanner._scan_msu_packs`` + the DAT
normalizer) over each set, so a pack's ``title_id`` here is exactly what the
catalog will assign — which is what has to equal the plain ROM's ``title_id``
for the two to share a save slot.

    cd server && uv run python ../tools/msu_pick.py \
        --set "F:/Isos/Namco2x6/MSU" \
        --set "C:/Users/pepas/Downloads/MSUROMS/Nintendo - Super Famicom - MSU1" \
        --baseline /tmp/snes_server_list.txt --system SNES \
        --report msu_report.md --plan msu_plan.txt

Later sets win ties (list the curated set last).  Within one set, several
variants of the same game are ranked: anything marked BETA / Demo loses, then
the name with the fewest bracket tags wins.  Every decision is in the report;
the plan is one ``<source path>\t<destination name>`` line per chosen pack
and is what ``msu_copy.sh`` / rsync consumes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT, ROOT / "server"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.services import dat_normalizer, rom_scanner  # noqa: E402
from shared import msu  # noqa: E402
from app.services.rom_scanner import (  # noqa: E402
    _identify_rom_slug,
    _resolve_msu_identities,
)

_LOSER_RE = re.compile(r"\bBETA\b|\(Demo\)|\bWIP\b", re.IGNORECASE)
_PAREN_RE = re.compile(r"\s*\([^)]*\)")
_BRACKET_RE = re.compile(r"\s*\[[^\]]*\]")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

#: Parenthesised notes a messy set hangs on a name that No-Intro would not.
_NOTE_PAREN_RE = re.compile(
    r"\s*\((?:BR|trilhas[^)]*|funciona[^)]*|com dublagem[^)]*|guitar|the retromancer)\)",
    re.IGNORECASE,
)
_REGION_FIXES = {"(US)": "(USA)", "(JP)": "(Japan)", "(EU)": "(Europe)", "(MSU-1)": "(MSU1)"}
_MESSY_RE = re.compile(r"\((?:US|JP|EU|BR|MSU-1)\)")
_REGIONS = {
    "usa", "japan", "europe", "world", "france", "germany", "spain", "italy",
    "australia", "korea", "china", "brazil", "canada", "sweden", "netherlands",
    "unl", "arcade",
}
_TRANSLATION_RE = re.compile(r"\[T-[A-Za-z]{2}\b|\(Traducido|\(En\)|\(Fr\)|\(De\)|\(Es\)")

#: Messy-set spellings of a title, folded onto the No-Intro title's key so a
#: proper copy of the game shadows them.
_TITLE_ALIASES = {
    "streetfighter2turbo": "streetfighteriiturbo",
    "rtype3": "rtypeiii",
    "yoshiisland": "supermarioworld2yoshisisland",
    "teenagemutantninjaturtles4": "teenagemutantninjaturtlesivturtlesintime",
    "superstarwarsanewhope": "superstarwars",
    "tetrisanddrmario": "tetrisdrmario",
    "rockmanandforte": "rockmanforte",
    "ys5kefinthelostcityofsand": "ysvkefinlostkingdomofsand",
    "megamanbass": "rockmanforte",
    "supermariorpg": "supermariorpglegendofthesevenstars",
}

#: Messy names whose No-Intro form the tidy rules can't derive.
_EXPLICIT_RENAMES = {
    "Tetris and Dr. Mario (US) (MSU-1).zip": "Tetris & Dr. Mario (USA) (MSU1).zip",
    "Ys 5 - Kefin The Lost City of Sand (US) (MSU-1).zip":
        "Ys V - Kefin, Lost Kingdom of Sand (Japan) (MSU1) [T-En by Aeon Genesis v1.00] [n].zip",
    "Super Mario RPG (USA) (MSU1).zip":
        "Super Mario RPG - Legend of the Seven Stars (USA) (MSU1).zip",
    "Wolfenstein 3-D (USA) (MSU1).zip": "Wolfenstein 3D (USA) (MSU1).zip",
    "Flashback (USA) (MSU1) [Hack by LuigiBlood v1.0].zip":
        "Flashback - The Quest for Identity (USA) (MSU1) [Hack by LuigiBlood v1.0].zip",
}


def is_messy(name: str) -> bool:
    """Named by hand rather than by No-Intro: ``(US)``, ``(BR)``, ``(MSU-1)``,
    or no region at all."""
    if _MESSY_RE.search(name):
        return True
    parens = {r.strip().lower() for paren in re.findall(r"\(([^)]*)\)", name)
              for r in paren.split(",")}
    return not (parens & _REGIONS)


def title_key(name: str) -> str:
    """The bare title, so two spellings of one game collide.

    ``Mega Man X (US) (BR) (MSU-1) (guitar)`` and ``Mega Man X (USA) (Rev 1)
    (MSU1) [Hack by Conn v16]`` are the same game to a person choosing one
    copy.
    """
    stem = tidy_name(name).rsplit(".zip", 1)[0]
    title = _BRACKET_RE.sub("", _PAREN_RE.sub("", stem)).strip()
    lowered = title.lower()
    if lowered.startswith("the "):
        lowered = lowered[4:]
    lowered = lowered.replace(", the", "")
    key = _NON_ALNUM_RE.sub("", lowered)
    return _TITLE_ALIASES.get(key, key)


def game_key(name: str) -> str:
    """Title plus region plus whether it is a translation.

    Proper copies dedupe on this, so ``Terranigma (France)`` survives beside
    ``Terranigma (Europe)`` and a ``(Traducido Es)`` Chrono Trigger beside
    the English one.
    """
    stem = tidy_name(name).rsplit(".zip", 1)[0]
    regions = sorted(
        r.strip().lower()
        for paren in re.findall(r"\(([^)]*)\)", stem)
        for r in paren.split(",")
        if r.strip().lower() in _REGIONS
    )
    translated = "t" if _TRANSLATION_RE.search(stem) else ""
    return f"{title_key(name)}|{','.join(regions)}|{translated}"


def tidy_name(name: str) -> str:
    """No-Intro-ish spelling for a messy pack name: ``(US) (MSU-1) (trilhas
    …)`` becomes ``(USA) (MSU1)``.  What the copy is filed under when the
    messy set is the only one that has the game."""
    stem, ext = (name.rsplit(".", 1) + [""])[:2]
    stem = _NOTE_PAREN_RE.sub("", stem)
    for old, new in _REGION_FIXES.items():
        stem = stem.replace(old, new)
    stem = re.sub(r"\(MSU1(?!\))", "(MSU1)", stem)  # "(MSU1.zip" typo
    stem = re.sub(r"\s{2,}", " ", stem).strip()
    out = f"{stem}.{ext}" if ext else stem
    return _EXPLICIT_RENAMES.get(name, out)


def _rank(name: str) -> tuple[int, int, int]:
    return (
        1 if _LOSER_RE.search(name) else 0,
        name.count("["),
        len(name),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="append", required=True, dest="sets")
    ap.add_argument("--baseline", required=True, help="file: one baseline ROM filename per line")
    ap.add_argument("--system", default="SNES")
    ap.add_argument("--report", default="msu_report.md")
    ap.add_argument("--plan", default="msu_plan.txt")
    args = ap.parse_args()

    dat_normalizer.init(ROOT / "server" / "data" / "dats")
    norm = dat_normalizer.get()
    system = args.system.upper()

    # Baseline: title_id -> filenames the server already keys under it.
    baseline: dict[str, list[str]] = defaultdict(list)
    for line in Path(args.baseline).read_text(encoding="utf-8", errors="replace").splitlines():
        name = line.strip()
        if not name:
            continue
        tid, _canon, _src = _identify_rom_slug(system, Path(name), norm)
        baseline[tid].append(name)

    # Each set through the scanner.  rom_dir is the set's parent so the
    # relative path keeps the set folder name.
    packs: dict[str, list[dict]] = defaultdict(list)
    per_set_counts: list[tuple[str, int]] = []
    cat = rom_scanner.RomCatalog()
    for index, folder in enumerate(args.sets):
        folder_path = Path(folder)
        scanned: list[dict] = []
        cat._scan_msu_packs(folder_path, system, norm, folder_path.parent, scanned)
        per_set_counts.append((folder, len(scanned)))
        # Settle identities exactly as the server will, with the baseline
        # standing in for the plain ROMs of the catalog.
        _resolve_msu_identities(
            scanned + [{"system": system, "title_id": tid} for tid in baseline]
        )
        for row in scanned:
            row["set_index"] = index
            row["set"] = folder
            row["abs_path"] = str(folder_path.parent / row["path"])
            packs[row["title_id"]].append(row)

    chosen: list[dict] = []
    lines: list[str] = ["# MSU pack pick", ""]
    for folder, count in per_set_counts:
        lines.append(f"- set {folder}: {count} packs")
    lines += ["", f"Baseline: {sum(len(v) for v in baseline.values())} ROMs, "
              f"{len(baseline)} title ids", ""]

    unmatched: list[dict] = []
    dat_miss: list[dict] = []
    ambiguous: list[tuple[dict, list[dict]]] = []

    # One copy per game, across sets and across spellings.  Proper copies
    # dedupe on title+region+translation, later set wins; a messy (hand
    # named) copy only survives when *no* proper copy of the title exists.
    all_rows = [r for rows in packs.values() for r in rows]
    proper_titles = {title_key(r["filename"]) for r in all_rows
                     if not is_messy(r["filename"])}
    best_by_key: dict[str, dict] = {}
    shadowed: list[dict] = []
    for r in sorted(all_rows, key=lambda r: (-r["set_index"], _rank(r["filename"]))):
        name = r["filename"]
        if is_messy(name) and title_key(name) in proper_titles:
            shadowed.append(r)
            continue
        key = game_key(name)
        if key in best_by_key:
            shadowed.append(r)
            continue
        best_by_key[key] = r
    packs.clear()
    for r in best_by_key.values():
        packs[r["title_id"]].append(r)

    renamed: list[tuple[str, str]] = []
    for tid in sorted(packs):
        rows = packs[tid]
        # Later sets win; within a set, rank the name.
        rows.sort(key=lambda r: (-r["set_index"], _rank(r["filename"])))
        pick = rows[0]
        pick["dest"] = tidy_name(pick["filename"])
        if pick["dest"] != pick["filename"]:
            renamed.append((pick["filename"], pick["dest"]))
            # The server will key the copy by its new name.
            renamed_row = dict(pick, filename=pick["dest"], name=pick["dest"][:-4])
            cands = []
            for cand in msu.identity_candidates(renamed_row["name"], msu.rom_member(
                    "msu1", [m["name"] for m in json.loads(pick["bundle_files"])])):
                tid_c, _c, src_c = _identify_rom_slug(system, Path(f"{cand}.zip"), norm)
                if tid_c not in {c[0] for c in cands}:
                    cands.append((tid_c, src_c))
            renamed_row["_msu_candidates"] = cands
            fb, _c, fb_src = _identify_rom_slug(
                system, Path(f"{msu.strip_hack_tags(renamed_row['name'])}.zip"), norm)
            renamed_row["_msu_fallback"] = (fb, fb_src)
            _resolve_msu_identities(
                [renamed_row] + [{"system": system, "title_id": t} for t in baseline])
            pick["title_id"], pick["source"] = renamed_row["title_id"], renamed_row["source"]
        chosen.append(pick)
        same_set = [r for r in rows if r["set_index"] == pick["set_index"]]
        if len(same_set) > 1:
            ambiguous.append((pick, same_set))
        if tid not in baseline:
            unmatched.append(pick)
        if pick["source"] == "filename":
            dat_miss.append(pick)

    lines += [f"## Chosen: {len(chosen)}", ""]
    lines.append("| title_id | pick | set | baseline | src |")
    lines.append("|---|---|---|---|---|")
    for pick in chosen:
        base = "; ".join(baseline.get(pick["title_id"], [])) or "**none**"
        lines.append(
            f"| `{pick['title_id']}` | {pick['filename']} | {pick['set_index']} "
            f"| {base} | {pick['source']} |"
        )

    lines += ["", f"## No baseline ROM on server: {len(unmatched)}", ""]
    lines += [f"- `{p['title_id']}` — {p['filename']}" for p in unmatched]

    lines += ["", f"## Identity not from DAT (check these): {len(dat_miss)}", ""]
    lines += [f"- `{p['title_id']}` — {p['filename']}" for p in dat_miss]

    lines += ["", f"## Several variants in the winning set: {len(ambiguous)}", ""]
    for pick, rows in ambiguous:
        lines.append(f"- `{pick['title_id']}` → **{pick['filename']}**")
        for r in rows[1:]:
            lines.append(f"    - dropped: {r['filename']}")

    lines += ["", f"## Renamed on copy (messy set spelling): {len(renamed)}", ""]
    lines += [f"- {old}  →  **{new}**" for old, new in renamed]

    lines += ["", f"## Dropped: a later set has the game under another spelling: {len(shadowed)}", ""]
    lines += [f"- {r['filename']} (set {r['set_index']})"
              for r in sorted(shadowed, key=lambda r: r["filename"])]

    lines += ["", "## Dropped duplicates from other sets", ""]
    for tid in sorted(packs):
        rows = packs[tid]
        for r in rows[1:]:
            if r["set_index"] != rows[0]["set_index"]:
                lines.append(f"- `{tid}`: {r['filename']} (set {r['set_index']})")

    Path(args.report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    Path(args.plan).write_text(
        "".join(f"{p['abs_path']}\t{p['dest']}\n" for p in chosen), encoding="utf-8"
    )
    total = sum(p["size"] for p in chosen)
    print(f"{len(chosen)} chosen ({total / 2**30:.1f} GB), {len(unmatched)} without "
          f"baseline, {len(dat_miss)} DAT misses, {len(ambiguous)} ambiguous")
    print(f"report: {args.report}\nplan:   {args.plan}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
