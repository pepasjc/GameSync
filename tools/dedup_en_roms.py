#!/usr/bin/env python3
"""Collapse the Retroplay En-ROMs download list to one file per game.

The En-ROMs set ships every permutation of a translation as its own file, so a
single game can appear 28 times (translator x localization style x nameset x
optional speed hack).  This picks one winner per game using a deterministic
score, and routes the genuinely subjective cases -- where the candidates come
from *different translators* -- to a manual review file instead of guessing.

Inputs   : en-roms2025-missing.txt  (one archive.org URL per line)
Outputs  : en-roms2025-download.txt  final list to fetch
           en-roms2025-review.md     clusters needing a human pick
           en-roms2025-review.csv    same, machine-readable
           en-roms2025-dropped.csv   every dropped URL + why + what beat it

Usage:
    python tools/dedup_en_roms.py
    python tools/dedup_en_roms.py --loc Retranslated --keep-gameplay-mods
"""
from __future__ import annotations

import argparse
import csv
import re
import urllib.parse
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --- tag classification -----------------------------------------------------
# Markers of AI/machine translation.  Built from the tags actually present in
# the 2025 set rather than guessed; a human-translated sibling always wins.
AI_RE = re.compile(
    r"\b(AI|A\.I\.|LLM|GPT|ChatGPT|DeepSeek|DeepL|Gemini|Claude|Opus|Fable|"
    r"Google Translate|machine translat\w*|MTL|slop|automated translat\w*)\b",
    re.I,
)

# Improvements over the base translation: technical fixes plus the addendum
# patches ([Add by ...]) that layer extra content on top of a translation.
# Taking these is treated as a win.
IMPROVE_RE = re.compile(
    r"(FastROM hack|Region lock removed|Splash screen removed|Undub|En dub|"
    r"Bug ?[Ff]ix|corrected|Parallax Patch|Music Persistance|"
    r"Preserve Resizing Of Glyphs|no SRAM|Font mod|Add by|Extended by)",
    re.I,
)

# Balance, difficulty, naming and cosmetic-style choices.  Pure taste with no
# better side, so prefer the variant carrying *fewer* of them -- i.e. the
# plainest build -- unless --keep-gameplay-mods is passed.
GAMEPLAY_RE = re.compile(
    r"(Esper Balance|monster stats|nameset|spell names|HardType|Easy Mode|"
    r"Trainer|Strange Dance|buttons|button colors|Battle Galuf|"
    r"Captured Monster Name|Script Port|Legend of the Crystals|Char\b|Anime|"
    r"Portraits|Style\b|Title Screen|logo|title\b|SlowROM hack|palette|"
    r"Edition\b|Mode \d)",
    re.I,
)


def classify(tag: str) -> int:
    """+1 improvement, -1 taste/balance mod, 0 neutral.  First match wins so a
    tag can never score in both directions."""
    if tag.startswith("T-En"):
        return 0
    if IMPROVE_RE.search(tag):
        return 1
    if GAMEPLAY_RE.search(tag):
        return -1
    return 0

LOC_TAGS = ("Relocalized", "Retranslated", "Delocalized")
# Extra content rather than an alternate build -- kept alongside the base pick.
DLC_RE = re.compile(r"^DLC\b", re.I)
# Optical/media dump of the same release; collapse, preferring the first.
MEDIA_ORDER = ("Cartridge", "Floppy", "Disk", "Cassette", "Tape")
# Not a ROM -- audio-analysis image that rides along in the set.
JUNK_RE = re.compile(r"_spectrogram$", re.I)

EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,4}$")
VER_RE = re.compile(r"\sv(\d[\w.\-]*)(.*)$")


def natural_version(v: str) -> tuple:
    """Sort key for mixed version strings: 1.16, 0.9b3, 20241021, 3.06a."""
    parts = re.findall(r"\d+|[A-Za-z]+", v)
    return tuple((0, int(p)) if p.isdigit() else (1, p.lower()) for p in parts)


class Entry:
    def __init__(self, url: str):
        self.url = url
        path = urllib.parse.unquote(url).split("/")
        self.system = path[5]
        self.filename = path[-1]
        self.stem = EXT_RE.sub("", self.filename)
        self.tags = re.findall(r"\[([^\]]*)\]", self.stem)

        # Base name = everything before the first tag.  Entries that *start*
        # with a tag (e.g. "[BIOS] Super CD-ROM System (Japan)") have no
        # prefix, so fall back to the whole stem to avoid grouping them all
        # together under an empty key.
        head = self.stem.split("[")[0].strip()
        self.base = head if head else self.stem

        # Media suffix trails the tags: "Aleste (Japan) [T-En by ...] (Floppy)"
        m = re.search(r"\(([^)]+)\)\s*$", self.stem)
        self.media = m.group(1) if m and m.group(1) in MEDIA_ORDER else ""

        # Identity = who made this build.  Prefer the T-En tag; fall back to
        # any "... by ..." tag so competing editions that carry no T-En tag
        # ("Woolsey Uncensored Edition by Rodimus Primal") still separate.
        ident = next((t for t in self.tags if t.startswith("T-En")), "")
        if not ident:
            ident = next((t for t in self.tags if " by " in t), "")
        # Versions are not always last: "v1.0 git d4bc3be", "v1.00 VWF",
        # "v1.0 RC1", "v1.0.0 AS".  Anchoring on the trailing token would make
        # each build look like a different translator and force it to manual
        # review, so split at the version and keep the remainder as a suffix.
        vm = VER_RE.search(ident)
        if vm:
            self.version = vm.group(1)
            self.version_suffix = vm.group(2).strip()
            self.translator = ident[: vm.start()].strip()
        else:
            self.version = ""
            self.version_suffix = ""
            self.translator = ident
        # Some builds are stamped with a bare git hash instead of a version
        # ("[T-En by alarixnia 181c6c3]").  Left in place each hash reads as a
        # separate translator, fragmenting one project into a dozen clusters.
        hm = re.search(r"\s+(?=[0-9a-f]{7,8}\b)([0-9a-f]*\d[0-9a-f]*)$", self.translator)
        if hm and self.translator[: hm.start()].strip():
            self.version_suffix = (self.version_suffix + " " + hm.group(1)).strip()
            self.translator = self.translator[: hm.start()].strip()
        self.translator = re.sub(r"^T-En(-US)?", "", self.translator)
        self.translator = re.sub(r"^\s*&\s*", "", self.translator)
        self.translator = re.sub(r"^\s*by\s+", "", self.translator).strip()

        self.is_dlc = any(DLC_RE.match(t) for t in self.tags)
        self.incomplete = "i" in self.tags or "b" in self.tags
        self.ai = bool(AI_RE.search(self.stem))
        self.loc = next((t for t in self.tags if t in LOC_TAGS), "")
        kinds = [classify(t) for t in self.tags]
        self.tech = sum(1 for k in kinds if k > 0)
        self.gameplay = sum(1 for k in kinds if k < 0)

    @property
    def group(self) -> tuple:
        # DLC entries form their own group so they survive alongside the base.
        return (self.system, self.base, self.is_dlc)

    def score(self, loc_order: list[str], keep_mods: bool) -> tuple:
        loc_rank = len(loc_order) - loc_order.index(self.loc) if self.loc in loc_order else 0
        media_rank = (
            len(MEDIA_ORDER) - MEDIA_ORDER.index(self.media)
            if self.media in MEDIA_ORDER
            else 0
        )
        return (
            0 if self.incomplete else 1,      # complete beats incomplete/bad dump
            0 if self.ai else 1,              # human beats machine translation
            natural_version(self.version),    # newest build of this translation
            self.version_suffix,              # stable across git-hash builds
            loc_rank,                         # localization-style preference
            self.tech,                        # more technical improvements
            self.gameplay if keep_mods else -self.gameplay,
            media_rank,
            -len(self.stem),                  # stable: prefer the plainer name
            self.stem,
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=ROOT / "en-roms2025-missing.txt")
    ap.add_argument("--loc", default="Relocalized", choices=LOC_TAGS)
    ap.add_argument(
        "--keep-gameplay-mods",
        action="store_true",
        help="prefer variants that carry [Add by ...] and balance mods",
    )
    ap.add_argument(
        "--auto-translator",
        action="store_true",
        help="auto-pick across different translators instead of flagging them",
    )
    args = ap.parse_args()

    loc_order = [args.loc] + [t for t in LOC_TAGS if t != args.loc]

    entries, dats, junk = [], [], []
    for line in args.input.read_text(encoding="utf-8").splitlines():
        url = line.strip()
        if not url:
            continue
        e = Entry(url)
        if e.system == "DATs":
            dats.append(e)
        elif JUNK_RE.search(e.stem):
            junk.append(e)
        else:
            entries.append(e)

    groups: dict[tuple, list[Entry]] = defaultdict(list)
    for e in entries:
        groups[e.group].append(e)

    keep: list[Entry] = []
    review: list[tuple[tuple, list[Entry]]] = []
    dropped: list[tuple[Entry, str, str]] = []
    loc_overridden: list[tuple[Entry, Entry]] = []

    for key, members in sorted(groups.items()):
        if len(members) == 1:
            keep.append(members[0])
            continue

        ranked = sorted(members, key=lambda e: e.score(loc_order, args.keep_gameplay_mods),
                        reverse=True)
        translators = {e.translator for e in members}

        if len(translators) > 1 and not args.auto_translator:
            # One finalist per translator, deduped internally; a human picks
            # between the finalists.
            finalists = []
            for t in sorted(translators):
                same = [e for e in members if e.translator == t]
                best = max(same, key=lambda e: e.score(loc_order, args.keep_gameplay_mods))
                finalists.append(best)
                for e in same:
                    if e is not best:
                        dropped.append((e, "worse variant of same translator", best.stem))
            review.append((key, finalists))
            continue

        winner = ranked[0]
        keep.append(winner)
        for e in ranked[1:]:
            if e.incomplete and not winner.incomplete:
                why = "incomplete/bad dump"
            elif e.ai and not winner.ai:
                why = "AI/machine translation"
            elif natural_version(e.version) < natural_version(winner.version):
                why = "older translation version"
            elif e.loc != winner.loc:
                why = f"localization style ({e.loc or 'none'} < {winner.loc or 'none'})"
            elif e.tech < winner.tech:
                why = "fewer technical improvements"
            elif e.gameplay != winner.gameplay:
                why = "gameplay/naming mod variant"
            elif e.media != winner.media:
                why = f"media variant ({e.media or 'n/a'})"
            else:
                why = "duplicate variant"
            dropped.append((e, why, winner.stem))
        # Surface where a newer build cost us the preferred localization style.
        if winner.loc and winner.loc != args.loc:
            alt = next((e for e in ranked[1:] if e.loc == args.loc), None)
            if alt:
                loc_overridden.append((winner, alt))

    keep.sort(key=lambda e: (e.system, e.stem))
    out = ROOT / "en-roms2025-download.txt"
    out.write_text(
        "\n".join([e.url for e in keep] + [e.url for e in dats]) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with open(ROOT / "en-roms2025-dropped.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["system", "filename", "reason", "beaten_by", "url"])
        for e, why, winner in sorted(dropped, key=lambda r: (r[0].system, r[0].stem)):
            w.writerow([e.system, e.filename, why, winner, e.url])

    lines = [
        "# En-ROMs 2025 — clusters needing a manual pick",
        "",
        f"{len(review)} games have variants from **different translators or competing "
        "editions**, which is not a mechanical choice. Each cluster below is already "
        "deduped *within* each translator, so you only pick between the finalists.",
        "",
        "Mark your choice, then add its URL to `en-roms2025-download.txt`.",
        "",
    ]
    with open(ROOT / "en-roms2025-review.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["system", "game", "translator", "version", "flags", "filename", "url"])
        cur = None
        for (system, base, is_dlc), finalists in sorted(
            review, key=lambda r: (r[0][0], r[0][1])
        ):
            if system != cur:
                cur = system
                lines += [f"## {system}", ""]
            lines.append(f"### {base}{' [DLC]' if is_dlc else ''}  ({len(finalists)} options)")
            for e in sorted(finalists, key=lambda x: x.translator):
                flags = []
                if e.incomplete:
                    flags.append("INCOMPLETE")
                if e.ai:
                    flags.append("AI-TRANSLATED")
                if e.loc:
                    flags.append(e.loc)
                if e.tech:
                    flags.append(f"+{e.tech} tech")
                if e.gameplay:
                    flags.append(f"+{e.gameplay} mods")
                flag = f"  _{', '.join(flags)}_" if flags else ""
                lines.append(f"- `{e.translator or '(unknown)'}` v{e.version or '?'} — "
                             f"{e.stem[len(base):].strip() or '(plain)'}{flag}")
                w.writerow([system, base, e.translator, e.version, "|".join(flags),
                            e.filename, e.url])
            lines.append("")
    (ROOT / "en-roms2025-review.md").write_text("\n".join(lines), encoding="utf-8",
                                                newline="\n")

    total_in = len(entries)
    print(f"input game URLs      : {total_in}")
    print(f"auto-resolved keeps  : {len(keep)}")
    print(f"needs manual review  : {len(review)} clusters, "
          f"{sum(len(f) for _, f in review)} finalists")
    print(f"dropped              : {len(dropped)}")
    print(f"non-ROM junk skipped : {len(junk)}")
    print(f"DATs passed through  : {len(dats)}")
    if loc_overridden:
        print(f"\n{len(loc_overridden)} picks lost '{args.loc}' to a newer version:")
        for winner, alt in loc_overridden[:10]:
            print(f"  kept  {winner.stem}")
            print(f"  over  {alt.stem}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
