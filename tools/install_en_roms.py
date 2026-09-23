#!/usr/bin/env python3
"""Install the deduped En-ROMs picks into the NAS ROM tree.

Reads en-roms2025-download.txt, maps each archive.org system folder to the
matching Z:\\roms folder, and lands the file in whatever format that folder
already uses -- the collection stores carts bare (.nes/.sfc/.gba) but keeps
handhelds and computers zipped (.zip), so some downloads are extracted and
others are copied verbatim.

Files superseding an existing translation are not overwritten in place: the
old file is moved to Z:\\roms\\_superseded\\ first, so nothing is destroyed
until you have confirmed the replacements boot.

Dry run by default.  Pass --apply to actually touch the disk.

Usage:
    python tools/install_en_roms.py                # preview
    python tools/install_en_roms.py --apply
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
import zlib
import urllib.parse
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dedup_en_roms as dedup  # noqa: E402  reuse the variant-scoring rules

LOC_ORDER = ["Relocalized", "Retranslated", "Delocalized"]
SRC = Path("F:/Isos/downloads/EN-ROMS")
DST = Path("Z:/roms")
QUARANTINE = DST / "_superseded"

# archive.org system folder -> existing Z:\roms folder.
# Systems with no folder are skipped by design (see --report-skipped output).
FOLDER_MAP = {
    "Bandai - WonderSwan": "wonderswan",
    "Bandai - WonderSwan Color": "wonderswancolor",
    "Microsoft - XBOX": "xbox",
    "NEC - PC Engine": "pcengine",
    "NEC - PC Engine CD": "pcenginecd",
    "Nintendo - Famicom": "nes",
    "Nintendo - Family Computer Disk System": "fds",
    "Nintendo - Game Boy": "gb",
    "Nintendo - Game Boy Advance": "gba",
    "Nintendo - Game Boy Color": "gbc",
    "Nintendo - GameCube": "gc",
    "Nintendo - Nintendo 3DS": "3ds",
    "Nintendo - Nintendo 64": "n64",
    "Nintendo - Nintendo 64DD": "n64dd",
    "Nintendo - Nintendo DS": "nds",
    "Nintendo - Nintendo DSi": "nds",
    "Nintendo - Super Famicom": "snes",
    "Nintendo - Virtual Boy": "virtualboy",
    "Nintendo - Wii": "wii",
    "Panasonic - 3DO Interactive Multiplayer": "3do",
    "SNK - Neo Geo CD": "neogeocd",
    "SNK - Neo Geo Pocket Color": "ngpc",
    "Sega - Dreamcast": "dreamcast",
    "Sega - Game Gear": "gamegear",
    "Sega - Master System": "mastersystem",
    "Sega - Mega CD": "segacd",
    "Sega - Mega Drive": "megadrive",
    "Sega - SG-1000": "sg-1000",
    "Sega - SG-1000 - SC-3000": "sg-1000",
    "Sega - Saturn": "saturn",
    "Sharp - X68000": "x68000",
    "Sony - PlayStation": "ps1",
    "Sony - PlayStation 2": "ps2",
    "Sony - PlayStation 3": "ps3",
    "Sony - PlayStation Portable": "psp",
}

# Target folders that store bare ROMs -- a downloaded .zip must be extracted.
# Every other folder already stores .zip or .chd, so the download is copied
# as-is.  Derived from the extension mix the catalog reports per folder.
UNZIP_TARGETS = {
    "nes", "snes", "gb", "gba", "gbc", "fds", "gc", "n64", "n64dd",
    "megadrive", "mastersystem", "gamegear", "virtualboy", "ngpc",
}

# Extensions each unzip target actually stores, used to drop sidecar files.
FOLDER_EXT = {
    "nes": {".nes"}, "snes": {".sfc", ".smc"}, "gb": {".gb"}, "gba": {".gba"},
    "gbc": {".gbc"}, "fds": {".fds"}, "gc": {".rvz", ".iso"},
    "n64": {".z64", ".n64", ".v64"}, "n64dd": {".ndd", ".d64"},
    "megadrive": {".md", ".gen", ".bin"}, "mastersystem": {".sms"},
    "gamegear": {".gg"}, "virtualboy": {".vb"}, "ngpc": {".ngc"},
}


ZIP_ZSTD = 93  # zstd, added to APPNOTE 6.3.7; zipfile can't read it before 3.14


def extract_zstd_member(src: Path, member: str, out: Path) -> None:
    """Extract a zstd-compressed zip member.

    Python's zipfile rejects method 93 and mainline 7-Zip can list but not
    decode it, so read the member's raw bytes and run them through zstandard.
    The payload is a bare zstd frame, which is self-terminating.  Because this
    bypasses zipfile's own integrity checking, verify CRC and size afterwards.
    """
    import zstandard

    with zipfile.ZipFile(src) as z:
        info = z.getinfo(member)
    if info.compress_type != ZIP_ZSTD:
        raise RuntimeError(
            f"{src.name}: unsupported compression method {info.compress_type}"
        )

    with open(src, "rb") as fh:
        fh.seek(info.header_offset)
        header = fh.read(30)
        if header[:4] != b"PK\x03\x04":
            raise RuntimeError(f"{src.name}: bad local header for {member}")
        name_len = int.from_bytes(header[26:28], "little")
        extra_len = int.from_bytes(header[28:30], "little")
        fh.seek(info.header_offset + 30 + name_len + extra_len)

        crc = 0
        written = 0
        reader = zstandard.ZstdDecompressor().stream_reader(fh)
        with open(out, "wb") as dest:
            # Stop at the recorded size rather than at EOF.  The zstd frame is
            # followed immediately by the zip's central directory, and reading
            # one chunk past the end makes the decompressor try to parse that
            # as another frame ("Unknown frame descriptor").
            while written < info.file_size:
                chunk = reader.read(min(1 << 20, info.file_size - written))
                if not chunk:
                    break
                dest.write(chunk)
                crc = zlib.crc32(chunk, crc)
                written += len(chunk)

    if written != info.file_size:
        raise RuntimeError(
            f"{member}: size mismatch {written} != {info.file_size}"
        )
    if crc != info.CRC:
        raise RuntimeError(f"{member}: CRC mismatch {crc:08x} != {info.CRC:08x}")


def load_supersede_map() -> dict[str, str]:
    """new filename -> path of the collection file it replaces."""
    out: dict[str, str] = {}
    path = ROOT / "en-roms2025-upgrades.csv"
    if not path.is_file():
        return out
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[row["new_filename"]] = row["old_path"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually write to disk")
    ap.add_argument("--list", type=Path, default=ROOT / "en-roms2025-download.txt")
    args = ap.parse_args()

    supersede = load_supersede_map()
    existing_targets = {d.name.lower() for d in DST.iterdir() if d.is_dir()}

    plan: list[tuple[Path, Path, str]] = []   # (src, dst, mode)
    skipped: list[tuple[str, str]] = []       # (system, relpath)
    already: list[Path] = []
    missing: list[str] = []

    for line in args.list.read_text(encoding="utf-8").splitlines():
        url = line.strip()
        if not url:
            continue
        parts = urllib.parse.unquote(url).split("/")
        system = parts[5]
        if system == "DATs":
            continue
        rel = "/".join(parts[5:])
        src = SRC / rel.replace("/", os.sep)
        if not src.is_file():
            missing.append(rel)
            continue

        folder = FOLDER_MAP.get(system)
        if not folder or folder not in existing_targets:
            skipped.append((system, rel))
            continue

        target_dir = DST / folder
        if folder in UNZIP_TARGETS and src.suffix.lower() == ".zip":
            with zipfile.ZipFile(src) as z:
                members = [m for m in z.namelist() if not m.endswith("/")]
            # A few zips carry emulator sidecars next to the ROM (bsnes .bml
            # manifests, .xml).  Keep only members the folder actually stores.
            roms = [m for m in members
                    if Path(m).suffix.lower() in FOLDER_EXT.get(folder, set())]
            if roms:
                members = roms
            for m in members:
                out = target_dir / Path(m).name
                if out.exists():
                    already.append(out)
                else:
                    plan.append((src, out, f"unzip:{m}"))
        else:
            out = target_dir / src.name
            if out.exists():
                already.append(out)
            else:
                plan.append((src, out, "copy"))

    # --- report ------------------------------------------------------------
    by_folder = Counter(p[1].parent.name for p in plan)
    bytes_needed = 0
    for src, out, mode in plan:
        bytes_needed += src.stat().st_size

    print(f"install plan      : {len(plan)} files into {len(by_folder)} folders")
    print(f"already present   : {len(already)}")
    print(f"skipped (no folder): {len(skipped)} across "
          f"{len({s for s, _ in skipped})} systems")
    if missing:
        print(f"MISSING on disk   : {len(missing)}")
        for m in missing[:5]:
            print(f"    {m}")
    print(f"source bytes      : {bytes_needed / 1e9:.1f} GB")
    print()
    for folder, n in sorted(by_folder.items(), key=lambda kv: -kv[1]):
        mode = "unzip" if folder in UNZIP_TARGETS else "copy "
        print(f"  {n:5d}  {mode}  -> Z:\\roms\\{folder}")

    # Quarantine every *translated* file in the collection that the incoming
    # picks supersede.  Matching on the upgrades CSV alone is not enough: it
    # only knows the pairs computed earlier, so a game whose CSV replacement
    # lost the dedup (but which is still being replaced by a newer build)
    # would leave its old copy behind as a duplicate.
    #
    # Match on game name within the target folder instead -- but only against
    # files that are themselves translations.  An untranslated original often
    # shares the exact base name ("Zelda no Densetsu - Kamigami no Triforce
    # (Japan)"), and must never be quarantined.
    def game_base(name: str) -> str:
        head = Path(name).stem.split("[")[0].strip()
        return head.lower()

    def is_translation(name: str) -> bool:
        return "[t-en" in name.lower()

    installing = {out.name for _, out, _ in plan}
    incoming: dict[tuple[str, str], set[str]] = defaultdict(set)
    for _, out, _ in plan:
        incoming[(out.parent.name, game_base(out.name))].add(out.name)

    # A name match alone is not grounds for deletion.  The dedup pass only
    # ranked download candidates against each other, so it never saw the file
    # already on the NAS -- and that file may well be the better one (it can
    # carry a bug fix, an AP fix or a completeness the incoming build lacks).
    # Re-run the same scoring across the pair and only retire the existing file
    # when it loses to a build from the *same* translator.  Anything else --
    # a different translator, or an existing file that scores higher -- is left
    # alone and reported, matching the manual-review policy.
    def entry_for(system_folder: str, filename: str):
        return dedup.Entry(f"https://x/x/x/x/x/{system_folder}/{filename}")

    to_quarantine: set[str] = set()
    conflicts: list[tuple[str, str, str]] = []   # (existing, incoming, why)
    for folder in {out.parent.name for _, out, _ in plan}:
        for existing in (DST / folder).iterdir():
            if not existing.is_file() or not is_translation(existing.name):
                continue
            key = (folder, game_base(existing.name))
            if key not in incoming or existing.name in incoming[key]:
                continue
            cur = entry_for(folder, existing.name)
            best_new = max(
                (entry_for(folder, n) for n in incoming[key]),
                key=lambda e: e.score(LOC_ORDER, False),
            )
            if cur.translator != best_new.translator:
                conflicts.append((f"{folder}/{existing.name}", best_new.filename,
                                  "different translator"))
            elif cur.score(LOC_ORDER, False) > best_new.score(LOC_ORDER, False):
                conflicts.append((f"{folder}/{existing.name}", best_new.filename,
                                  "existing build scores higher"))
            else:
                to_quarantine.add(f"{folder}/{existing.name}")

    # Union with the precomputed upgrade pairs, which also cover competing
    # editions that carry no [T-En] tag of their own.  These were verified
    # same-translator version bumps when the report was generated.
    conflicted = {c[0] for c in conflicts}
    for new, old in supersede.items():
        stem = Path(new).stem
        if old in conflicted:
            continue
        if new in installing or any(Path(n).stem == stem for n in installing):
            to_quarantine.add(old)

    if conflicts:
        with open(ROOT / "en-roms2025-install-conflicts.csv", "w",
                  encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["existing_on_nas", "incoming", "reason"])
            for row in sorted(conflicts):
                w.writerow(row)

    q_real = [p for p in sorted(to_quarantine) if (DST / p).is_file()]
    q_bytes = sum((DST / p).stat().st_size for p in q_real)
    print(f"\nquarantine        : {len(q_real)} superseded files "
          f"({q_bytes / 1e9:.1f} GB) -> {QUARANTINE}")
    print(f"kept, needs review: {len(conflicts)} existing files the incoming "
          f"build does not clearly beat")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        (ROOT / "en-roms2025-skipped.txt").write_text(
            "\n".join(f"{s}\t{r}" for s, r in sorted(skipped)) + "\n",
            encoding="utf-8", newline="\n",
        )
        print(f"skipped list written to en-roms2025-skipped.txt")
        return 0

    # --- execute -----------------------------------------------------------
    (ROOT / "en-roms2025-quarantined.txt").write_text(
        "\n".join(q_real) + "\n", encoding="utf-8", newline="\n"
    )
    for rel in q_real:
        src_old = DST / rel
        dest = QUARANTINE / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_old), str(dest))
    print(f"quarantined {len(q_real)} files -> en-roms2025-quarantined.txt")

    done = errors = 0
    for src, out, mode in plan:
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            # Write to a temp name and rename on success.  A partially written
            # or zero-byte file left behind by a failure would be picked up as
            # "already present" on the next run and silently never retried.
            tmp_out = out.with_name(out.name + ".part")
            if mode.startswith("unzip:"):
                member = mode.split(":", 1)[1]
                try:
                    with zipfile.ZipFile(src) as z, open(tmp_out, "wb") as fh:
                        with z.open(member) as m:
                            shutil.copyfileobj(m, fh)
                except NotImplementedError:
                    # Several GameCube archives are zstd-compressed zips
                    # (method 93), which zipfile cannot read before 3.14.
                    # 7-Zip handles them, so fall back to it.
                    tmp_out.unlink(missing_ok=True)
                    extract_zstd_member(src, member, tmp_out)
            else:
                shutil.copy2(src, tmp_out)
            tmp_out.replace(out)
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(plan)} ...", flush=True)
        except Exception as exc:  # keep going; report at the end
            errors += 1
            out.with_name(out.name + ".part").unlink(missing_ok=True)
            print(f"  ERROR {src.name}: {exc}", file=sys.stderr)
    print(f"\ninstalled {done} files, {errors} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
