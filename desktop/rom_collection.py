from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import zipfile
import zlib

import rom_normalizer as rn


ZIP_EXTENSIONS = {".zip"}
MATCH_PRIORITY = {
    "crc": 0,
    "header": 1,
    "fuzzy": 2,
    "folder": 3,
    "filename": 4,
}
REGION_PRIORITY = {
    "USA": 0,
    "Europe": 2,
    "Japan": 3,
}
_ENGLISH_TRANSLATION_MARKERS = (
    "[t-en",
    "[t+eng",
    "[t+en",
    "[translation",
    "[translated",
    " english translation",
)
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# No-Intro tags that indicate a non-retail / pre-release ROM.  Lower rank = better.
_STATUS_PENALTIES = (
    "(Beta",
    "(Proto",
    "(Sample",
    "(Demo",
    "(Kiosk",
    "(Promo",
    "(Preview",
    "(Pirate",
    "(Virtual Console",
    "(Unl)",
)


@dataclass
class CollectionCandidate:
    source_path: Path
    canonical_name: str
    source_kind: str
    extension: str
    match_source: str
    archive_member: str | None = None

    @property
    def source_label(self) -> str:
        return self.archive_member or self.source_path.name

    @property
    def base_key(self) -> str:
        return rn.normalize_name(self.canonical_name)

    @property
    def output_name(self) -> str:
        return f"{self.canonical_name}{self.extension}"

    @property
    def region_rank(self) -> int:
        if self.is_english_translation:
            return 1
        for region, rank in REGION_PRIORITY.items():
            if f"({region})" in self.canonical_name:
                return rank
        return len(REGION_PRIORITY)

    @property
    def match_rank(self) -> int:
        return MATCH_PRIORITY.get(self.match_source, len(MATCH_PRIORITY))

    @property
    def status_rank(self) -> int:
        """0 for retail releases, 1 for pre-release / non-retail ROMs."""
        name = self.canonical_name
        for tag in _STATUS_PENALTIES:
            if tag in name:
                return 1
        return 0

    @property
    def fastrom_rank(self) -> int:
        """0 when the source file is tagged ``[FastROM ...]``, 1 otherwise.

        FastROM SNES/SFC ROMs are preferred over SlowROM variants.  The tag
        lives in the original filename, not in the canonical DAT name.
        Matches both ``[FastROM]`` and longer forms like
        ``[FastROM hack by someone v1.0]``.
        """
        if "[fastrom" in self.source_label.lower():
            return 0
        return 1

    @property
    def is_english_translation(self) -> bool:
        label = self.source_label.lower()
        return any(marker in label for marker in _ENGLISH_TRANSLATION_MARKERS)

    @property
    def source_region_match_rank(self) -> int:
        """0 when the source filename's region matches the canonical region, 1 otherwise.

        Prevents a France-sourced ROM that was upgraded to a ``(USA)`` canonical
        name from beating an actual USA-sourced ROM for the same game.
        """
        source_region = rn.extract_region_hint(self.source_label)
        canonical_region = rn.extract_region_hint(self.canonical_name)
        if source_region and canonical_region and source_region == canonical_region:
            return 0
        if not source_region and not canonical_region:
            return 0
        return 1

    @property
    def region(self) -> str:
        """Return a human-readable region label for this entry."""
        if self.is_english_translation:
            return "Translated"
        for token in ("USA", "Europe", "Japan"):
            if f"({token})" in self.canonical_name:
                return token
        return "Other"

    @property
    def bucket_letter(self) -> str:
        slug = self.base_key
        for ch in slug:
            upper = ch.upper()
            if "A" <= upper <= "Z":
                return upper
        return "#"


def _iter_source_files(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file()
        and (
            path.suffix.lower() in rn.ROM_EXTENSIONS
            or path.suffix.lower() in ZIP_EXTENSIONS
        )
    )


def _read_member_header_title(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, system: str
) -> str | None:
    # Cartridge systems need only a small leading slice for header matching.
    max_len = 0x80000 if system.upper() in ("PSP", "PS3") else 0x10200
    try:
        with zf.open(info) as member:
            data = member.read(max_len)
    except Exception:
        return None

    # Reuse path-based logic by writing the bytes to an ad-hoc parser isn't worth it here;
    # support the common cartridge systems we care about with a small in-memory mirror.
    system = system.upper()
    file_size = info.file_size
    title_bytes = None
    if system == "GBA" and len(data) >= 0x00AC:
        title_bytes = data[0x00A0:0x00AC]
    elif system in ("GB", "GBC") and len(data) >= 0x0144:
        title_bytes = data[0x0134:0x0144]
    elif system == "SNES":
        offset = 512 if file_size % 1024 == 512 else 0
        data = data[offset:]
        candidates = []
        for addr in (0x7FC0, 0xFFC0):
            if len(data) >= addr + 21:
                chunk = data[addr : addr + 21]
                printable = sum(1 for b in chunk if 0x20 <= b <= 0x7E)
                candidates.append((printable, chunk))
        if candidates:
            title_bytes = max(candidates, key=lambda x: x[0])[1]
    elif system in ("MD", "GEN") and len(data) >= 0x0150:
        title_bytes = data[0x0120:0x0150]
    elif system == "N64" and len(data) >= 0x0034:
        order = rn.detect_n64_byte_order(data[:4])
        if order and order != "z64":
            data = rn.n64_to_z64(data[: max(0x40, 0x0034)], order)
        title_bytes = data[0x0020:0x0034]

    if title_bytes is None:
        return None
    title = title_bytes.decode("ascii", errors="ignore")
    title = "".join(ch if " " <= ch <= "~" else " " for ch in title)
    title = " ".join(title.split()).strip()
    return title if len(title) >= 2 else None


def _resolve_canonical_name_for_file(
    path: Path,
    system: str,
    no_intro: dict[str, str],
    name_index: dict[str, str],
    skip_crc: bool = False,
) -> tuple[str | None, str]:
    canonical = None
    match_source = "filename"
    if no_intro:
        if not skip_crc:
            try:
                crc = rn._crc32_file(path)
            except Exception:
                crc = ""
            if crc:
                canonical = no_intro.get(crc)
                if canonical:
                    return canonical, "crc"

        header_title = rn.read_rom_header_title(path, system)
        if header_title:
            canonical = rn.lookup_header_in_index(header_title, name_index)
            if canonical:
                region_hint = rn.extract_region_hint(
                    path.name
                ) or rn.extract_region_hint(path.parent.name)
                if region_hint:
                    canonical = rn.find_region_preferred(
                        canonical, no_intro, region_hint
                    )
                return canonical, "header"

        canonical = rn.fuzzy_filename_search(path.name, name_index)
        if canonical:
            region_hint = rn.extract_region_hint(path.name) or rn.extract_region_hint(
                path.parent.name
            )
            if region_hint:
                canonical = rn.find_region_preferred(canonical, no_intro, region_hint)
            return canonical, "fuzzy"

        if path.parent.name:
            canonical = rn.fuzzy_filename_search(path.parent.name, name_index)
            if canonical:
                region_hint = rn.extract_region_hint(
                    path.name
                ) or rn.extract_region_hint(path.parent.name)
                if region_hint:
                    canonical = rn.find_region_preferred(
                        canonical, no_intro, region_hint
                    )
                return canonical, "folder"

    return None, match_source


def _resolve_canonical_name_for_zip(
    path: Path,
    system: str,
    no_intro: dict[str, str],
    name_index: dict[str, str],
    skip_crc: bool = False,
) -> list[CollectionCandidate]:
    candidates: list[CollectionCandidate] = []
    try:
        with zipfile.ZipFile(path) as zf:
            infos = sorted(
                (
                    info
                    for info in zf.infolist()
                    if not info.is_dir()
                    and Path(info.filename).suffix.lower() in rn.ROM_EXTENSIONS
                ),
                key=lambda info: info.filename.lower(),
            )
            for info in infos:
                member_path = Path(info.filename)
                canonical = None
                match_source = "filename"
                if no_intro:
                    if not skip_crc:
                        crc = f"{info.CRC & 0xFFFFFFFF:08X}"
                        # For N64 zip members in non-native byte order, the zip
                        # CRC is for the stored bytes (which may be .n64/.v64
                        # order).  Try the raw CRC first; if no match and it's an
                        # N64 extension, read + byte-swap + recompute.
                        canonical = no_intro.get(crc)
                        if (
                            not canonical
                            and member_path.suffix.lower() in rn._N64_EXTENSIONS
                        ):
                            try:
                                with zf.open(info) as member:
                                    raw = member.read()
                                order = rn.detect_n64_byte_order(raw[:4])
                                if order and order != "z64":
                                    converted = rn.n64_to_z64(raw, order)
                                    crc = f"{zlib.crc32(converted) & 0xFFFFFFFF:08X}"
                                    canonical = no_intro.get(crc)
                            except Exception:
                                pass
                        if canonical:
                            match_source = "crc"
                    if canonical is None:
                        header_title = _read_member_header_title(zf, info, system)
                        if header_title:
                            canonical = rn.lookup_header_in_index(
                                header_title, name_index
                            )
                            if canonical:
                                region_hint = rn.extract_region_hint(
                                    member_path.name
                                ) or rn.extract_region_hint(member_path.parent.name)
                                if region_hint:
                                    canonical = rn.find_region_preferred(
                                        canonical, no_intro, region_hint
                                    )
                                match_source = "header"
                        if canonical is None:
                            canonical = rn.fuzzy_filename_search(
                                member_path.name, name_index
                            )
                            if canonical:
                                region_hint = rn.extract_region_hint(
                                    member_path.name
                                ) or rn.extract_region_hint(member_path.parent.name)
                                if region_hint:
                                    canonical = rn.find_region_preferred(
                                        canonical, no_intro, region_hint
                                    )
                                match_source = "fuzzy"
                        if canonical is None and member_path.parent.name:
                            canonical = rn.fuzzy_filename_search(
                                member_path.parent.name, name_index
                            )
                            if canonical:
                                region_hint = rn.extract_region_hint(
                                    member_path.name
                                ) or rn.extract_region_hint(member_path.parent.name)
                                if region_hint:
                                    canonical = rn.find_region_preferred(
                                        canonical, no_intro, region_hint
                                    )
                                match_source = "folder"

                if canonical:
                    candidates.append(
                        CollectionCandidate(
                            source_path=path,
                            canonical_name=canonical,
                            source_kind="zip",
                            extension=member_path.suffix.lower(),
                            match_source=match_source,
                            archive_member=info.filename,
                        )
                    )
    except zipfile.BadZipFile:
        return []
    return candidates


def scan_collection(
    folder: Path,
    system: str,
    no_intro: dict[str, str],
    progress_callback=None,
    clone_map: dict[str, str] | None = None,
    skip_crc: bool = False,
) -> tuple[list[CollectionCandidate], list[CollectionCandidate], list[Path]]:
    name_index = rn.build_name_index(no_intro) if no_intro else {}
    selected: dict[str, CollectionCandidate] = {}
    duplicates: list[CollectionCandidate] = []
    unmatched: list[Path] = []
    source_files = _iter_source_files(folder)
    clone_map = clone_map or {}

    for idx, path in enumerate(source_files, start=1):
        if progress_callback:
            progress_callback(f"Scanning {idx}/{len(source_files)}: {path.name}")
        candidates: list[CollectionCandidate] = []
        if path.suffix.lower() in ZIP_EXTENSIONS:
            candidates = _resolve_canonical_name_for_zip(
                path, system, no_intro, name_index, skip_crc=skip_crc
            )
            if not candidates:
                unmatched.append(path)
                continue
        else:
            canonical_name, match_source = _resolve_canonical_name_for_file(
                path, system, no_intro, name_index, skip_crc=skip_crc
            )
            if canonical_name is None:
                unmatched.append(path)
                continue
            candidates = [
                CollectionCandidate(
                    source_path=path,
                    canonical_name=canonical_name,
                    source_kind="file",
                    extension=path.suffix.lower(),
                    match_source=match_source,
                )
            ]

        for candidate in candidates:
            # Use cloneof leader's base_key when available so cross-language
            # variants (e.g. Japanese name vs USA name) share the same dedup
            # bucket.
            dedup_key = _dedup_key(candidate, clone_map)
            existing = selected.get(dedup_key)
            if existing is None or _is_better_candidate(candidate, existing):
                if existing is not None:
                    duplicates.append(existing)
                selected[dedup_key] = candidate
            else:
                duplicates.append(candidate)

    return (
        sorted(selected.values(), key=lambda c: c.canonical_name.lower()),
        duplicates,
        unmatched,
    )


def _dedup_key(candidate: CollectionCandidate, clone_map: dict[str, str]) -> str:
    """Return the dedup key for *candidate*.

    If the candidate's canonical name appears in *clone_map* (i.e. it has a
    ``cloneof`` leader), use the leader's normalised slug so that all members
    of the same clone group share a single dedup bucket.  Otherwise fall back
    to the candidate's own ``base_key``.
    """
    leader = clone_map.get(candidate.canonical_name)
    if leader:
        return rn.normalize_name(leader)
    return candidate.base_key


def filter_by_regions(
    entries: list[CollectionCandidate],
    enabled_regions: set[str],
) -> list[CollectionCandidate]:
    """Return only entries whose region is in *enabled_regions*.

    Region labels are: ``"USA"``, ``"Europe"``, ``"Japan"``, ``"Translated"``,
    ``"Other"``.  ``"Translated"`` is kept when ``"Other"`` is enabled.
    """
    if not enabled_regions:
        return []
    kept: list[CollectionCandidate] = []
    for entry in entries:
        region = entry.region
        if region in enabled_regions:
            kept.append(entry)
        elif region == "Translated" and "Other" in enabled_regions:
            kept.append(entry)
    return kept


def _is_better_candidate(
    candidate: CollectionCandidate, existing: CollectionCandidate
) -> bool:
    left = (
        candidate.fastrom_rank,
        candidate.region_rank,
        candidate.status_rank,
        candidate.source_region_match_rank,
        candidate.match_rank,
        0 if candidate.source_kind == "file" else 1,
        candidate.canonical_name.lower(),
        str(candidate.source_path).lower(),
    )
    right = (
        existing.fastrom_rank,
        existing.region_rank,
        existing.status_rank,
        existing.source_region_match_rank,
        existing.match_rank,
        0 if existing.source_kind == "file" else 1,
        existing.canonical_name.lower(),
        str(existing.source_path).lower(),
    )
    return left < right


def build_letter_buckets(folder_count: int) -> list[tuple[str, str]]:
    if folder_count <= 1:
        return [("A", "Z")]
    folder_count = min(folder_count, len(_ALPHABET))
    base_size, remainder = divmod(len(_ALPHABET), folder_count)
    buckets: list[tuple[str, str]] = []
    index = 0
    for bucket_idx in range(folder_count):
        size = base_size + (1 if bucket_idx < remainder else 0)
        start = _ALPHABET[index]
        end = _ALPHABET[index + size - 1]
        buckets.append((start, end))
        index += size
    return buckets


def bucket_name_for_letter(letter: str, folder_count: int) -> str:
    if letter == "#":
        return "0-9"
    for start, end in build_letter_buckets(folder_count):
        if start <= letter <= end:
            return f"{start}-{end}"
    start, end = build_letter_buckets(folder_count)[-1]
    return f"{start}-{end}"


def build_collection(
    entries: list[CollectionCandidate],
    output_folder: Path,
    unzip_archives: bool,
    unmatched_files: list[Path] | None = None,
    folder_count: int = 1,
    progress_callback=None,
) -> list[Path]:
    output_folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    unmatched_files = unmatched_files or []
    total = len(entries) + len(unmatched_files)
    for idx, entry in enumerate(entries, start=1):
        if progress_callback:
            progress_callback(f"Copying {idx}/{total}: {entry.output_name}")
        target_dir = (
            output_folder / bucket_name_for_letter(entry.bucket_letter, folder_count)
            if folder_count > 1
            else output_folder
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        if entry.source_kind == "zip" and entry.archive_member:
            if unzip_archives:
                target = target_dir / entry.output_name
                with zipfile.ZipFile(entry.source_path) as zf:
                    with (
                        zf.open(entry.archive_member) as src,
                        open(target, "wb") as dst,
                    ):
                        shutil.copyfileobj(src, dst)
            else:
                target = target_dir / f"{entry.canonical_name}.zip"
                shutil.copy2(entry.source_path, target)
        else:
            target = target_dir / entry.output_name
            shutil.copy2(entry.source_path, target)
        written.append(target)

    if unmatched_files:
        unmatched_folder = output_folder / "unmatched files"
        unmatched_folder.mkdir(parents=True, exist_ok=True)
        for offset, source_path in enumerate(unmatched_files, start=len(entries) + 1):
            if progress_callback:
                progress_callback(
                    f"Copying {offset}/{total}: {source_path.name} -> unmatched files"
                )
            target = unmatched_folder / source_path.name
            shutil.copy2(source_path, target)
            written.append(target)
    return written
