import re
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor

from config import load_config, SYSTEM_CHOICES
from systems import CD_ALL_EXTENSIONS, CD_DATA_EXTENSIONS, CD_FOLDER_SYSTEMS


# Regex to extract a disc number from a folder name — used to restore the
# correct disc tag after a disc-agnostic fuzzy match.
_DISC_NUMBER_RE = re.compile(r"\((?:Disc|Disk|CD)\s*(\d+)\)", re.IGNORECASE)

# Aliases kept for readability inside this module
_CD_DATA_EXTS = CD_DATA_EXTENSIONS
_CD_ALL_EXTS = CD_ALL_EXTENSIONS


def _find_cd_data_file(game_dir: Path) -> "Path | None":
    """Return the best file to CRC-fingerprint for a CD game folder.

    Preference order:
      1. Track 01 .bin referenced by the first .cue file (most accurate for Redump)
      2. First .iso file
      3. First .bin file (fallback when no .cue present)

    Returns None if no usable file is found.
    """
    try:
        files = list(game_dir.iterdir())
    except OSError:
        return None

    # Look for a .cue and parse its first FILE reference
    for cue in sorted(f for f in files if f.suffix.lower() == ".cue"):
        try:
            for line in cue.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if stripped.upper().startswith("FILE"):
                    # FILE "name.bin" BINARY  or  FILE name.bin BINARY
                    name = (
                        stripped.split('"')[1]
                        if '"' in stripped
                        else stripped.split()[1]
                    )
                    bin_path = game_dir / name
                    if bin_path.exists():
                        return bin_path
        except Exception:
            pass

    # Fallback: first .iso
    for f in sorted(files):
        if f.is_file() and f.suffix.lower() == ".iso":
            return f

    # Fallback: first .bin
    for f in sorted(files):
        if f.is_file() and f.suffix.lower() == ".bin":
            return f

    # Fallback: PSP single-file formats (.cso, .pbp)
    for ext in (".cso", ".pbp"):
        for f in sorted(files):
            if f.is_file() and f.suffix.lower() == ext:
                return f

    return None


class NormalizeScanWorker(QThread):
    finished = pyqtSignal(list)  # list of dicts: old, new, source, subfolder
    progress = pyqtSignal(str)

    def __init__(
        self,
        folder: Path,
        no_intro: dict,
        system: str,
        save_folder: Path | None = None,
        device_type: str = "",
        normalize_fallback: bool = False,
        use_crc: bool = True,
        serial_map: dict | None = None,
    ):
        super().__init__()
        self.folder = folder
        self.no_intro = no_intro
        self.system = system
        self.save_folder = save_folder
        self.device_type = device_type
        self.normalize_fallback = normalize_fallback
        self.use_crc = use_crc
        # Optional {serial: canonical_name} map — only populated for systems
        # whose DAT carries Sony disc serials (PS1 / PS2 / PSP).
        self.serial_map = serial_map or {}

    def run(self):
        import rom_normalizer as rn

        # Build name-based index for header matching (patched ROMs)
        name_index = rn.build_name_index(self.no_intro) if self.no_intro else {}

        # Serial-based lookup applies only to Sony disc systems.  When the
        # DAT didn't include serial info we simply skip the step.
        use_serial_lookup = bool(self.serial_map) and rn.supports_serial_lookup(
            self.system
        )

        is_mega_everdrive = self.device_type == "MEGA EverDrive"
        is_cd_folder = self.device_type == "CD Folder"

        # CD-based systems where games always live in subfolders (one dir per disc).
        # When the device is MEGA EverDrive and the system is one of these, we must
        # use folder-based scanning instead of individual-file scanning.
        is_cd_system = self.system in CD_FOLDER_SYSTEMS

        # ── CD Folder mode (or MEGA EverDrive + CD system): scan game subdirectories ──
        if is_cd_folder or (is_mega_everdrive and is_cd_system):
            try:
                subfolders = sorted(d for d in self.folder.iterdir() if d.is_dir())
            except OSError:
                self.finished.emit([])
                return

            results = []
            total = len(subfolders)
            for i, game_dir in enumerate(subfolders):
                self.progress.emit(f"Scanning {i + 1}/{total}: {game_dir.name}")

                # Skip folders with no CD image files
                try:
                    has_cd = any(
                        f.is_file() and f.suffix.lower() in _CD_ALL_EXTS
                        for f in game_dir.iterdir()
                    )
                except OSError:
                    has_cd = False
                if not has_cd:
                    continue

                source = "filename"
                new_stem = game_dir.name  # default: no rename

                if self.no_intro:
                    # Step 1: CRC match on the data track file
                    if self.use_crc:
                        data_file = _find_cd_data_file(game_dir)
                        if data_file:
                            self.progress.emit(
                                f"CRC: {i + 1}/{total}: {game_dir.name} ({data_file.name})"
                            )
                            try:
                                crc = rn._crc32_file(data_file)
                                canonical = self.no_intro.get(crc)
                                if canonical:
                                    new_stem = canonical
                                    source = "No-Intro"
                            except Exception:
                                pass

                    # Step 1b: Sony disc serial embedded in the folder name
                    # (e.g. "SCES_538.51.007 Agent Under Fire").  Most reliable
                    # signal for PS2 Redump collections.
                    if source == "filename" and use_serial_lookup:
                        serial = rn.extract_ps_serial(game_dir.name)
                        if serial:
                            canonical = rn.lookup_serial(serial, self.serial_map)
                            if canonical:
                                new_stem = canonical
                                source = "Serial"

                    # Step 2: fuzzy folder-name match (fallback)
                    if source == "filename":
                        canonical = rn.fuzzy_filename_search(game_dir.name, name_index)
                        if canonical:
                            # Restore the correct disc number from the actual folder name
                            disc_m = _DISC_NUMBER_RE.search(game_dir.name)
                            if disc_m:
                                disc_num = disc_m.group(1)
                                canonical = _DISC_NUMBER_RE.sub(
                                    f"(Disc {disc_num})", canonical
                                )
                            new_stem = canonical
                            source = "Fuzzy"

                    # Step 3: normalize fallback (if enabled)
                    if source == "filename" and self.normalize_fallback:
                        new_stem = rn.normalize_name(game_dir.name)
                        # source stays "filename" (yellow)

                new_dir = game_dir.parent / new_stem
                if new_dir != game_dir:
                    entry = {
                        "old": game_dir,
                        "new": new_dir,
                        "source": source,
                        "subfolder": "",
                        "companions": [],
                        "has_save": False,
                    }
                    # MEGA EverDrive + CD system: the EverDrive names gamedata
                    # folders after the .cue filename (including extension), e.g.
                    # gamedata/Sonic CD (USA).cue/cd-bram.brm.  Find the .cue inside
                    # the game dir and use its name for the save-folder lookup.
                    # Sega CD uses cd-bram.brm; other CD systems use bram.srm.
                    if is_mega_everdrive and self.save_folder:
                        save_filename = (
                            "cd-bram.brm" if self.system == "SEGACD" else "bram.srm"
                        )
                        cue_files = sorted(
                            f
                            for f in game_dir.iterdir()
                            if f.is_file() and f.suffix.lower() == ".cue"
                        )
                        for cue_file in cue_files:
                            game_save_dir = self.save_folder / cue_file.name
                            if (
                                game_save_dir.is_dir()
                                and (game_save_dir / save_filename).exists()
                            ):
                                entry["has_save"] = True
                                new_save_dir = self.save_folder / (
                                    new_stem + cue_file.suffix
                                )
                                if new_save_dir != game_save_dir:
                                    entry["save_dir_old"] = game_save_dir
                                    entry["save_dir_new"] = new_save_dir
                                    # Store the extension so _apply() can re-derive
                                    # the name from user-edited values correctly.
                                    entry["save_dir_ext"] = cue_file.suffix
                                break
                    results.append(entry)

            # Also scan flat ROM files that sit directly in the root (not inside
            # a game subfolder).  This handles PSP ISO/CSO collections stored as
            # individual files rather than one-folder-per-game.
            try:
                flat_roms = sorted(
                    f
                    for f in self.folder.iterdir()
                    if f.is_file() and f.suffix.lower() in rn.ROM_EXTENSIONS
                )
            except OSError:
                flat_roms = []

            for rom in flat_roms:
                ext = rom.suffix.lower()
                source = "filename"
                new_stem = rom.stem

                if self.no_intro:
                    if self.use_crc:
                        try:
                            crc = rn._crc32_file(rom)
                            canonical = self.no_intro.get(crc)
                            if canonical:
                                new_stem = canonical
                                source = "No-Intro"
                        except Exception:
                            canonical = None

                    # Sony disc-serial match — great for PS2 ROMs named like
                    # "SCES_538.51.game name.iso".
                    if source == "filename" and use_serial_lookup:
                        serial = rn.extract_ps_serial(rom.name)
                        if serial:
                            canonical = rn.lookup_serial(serial, self.serial_map)
                            if canonical:
                                new_stem = canonical
                                source = "Serial"

                    if source == "filename":
                        header_title = rn.read_rom_header_title(rom, self.system)
                        if header_title:
                            canonical = rn.lookup_header_in_index(header_title, name_index)
                            if canonical:
                                new_stem = canonical
                                source = "Header"

                    if source == "filename":
                        canonical = rn.fuzzy_filename_search(rom.name, name_index)
                        if canonical:
                            new_stem = canonical
                            source = "Fuzzy"

                    if source in ("Header", "Fuzzy"):
                        region_hint = rn.extract_region_hint(rom.name)
                        if region_hint:
                            new_stem = rn.find_region_preferred(
                                new_stem, self.no_intro, region_hint
                            )

                    if source == "filename" and self.normalize_fallback:
                        bracket_idx = rom.stem.find("[")
                        if bracket_idx > 0:
                            new_stem = rom.stem[:bracket_idx].strip()
                            source = "Bracket"
                        else:
                            new_stem = rn.normalize_name(rom.name)

                new_rom = rom.parent / (new_stem + ext)
                if new_rom != rom:
                    results.append(
                        {
                            "old": rom,
                            "new": new_rom,
                            "source": source,
                            "subfolder": "",
                            "companions": [],
                            "has_save": False,
                        }
                    )

            self.finished.emit(results)
            return

        # Pre-index save files from the save folder (stem → list of Path).
        # Using rglob so nested structures (e.g. Pocket's snes/common/all/A-F/) are
        # found regardless of how the save folder root aligns with the ROM folder root.
        # MEGA EverDrive uses subdirs (bram.srm inside each game folder) so we skip
        # the flat index for it and do a per-ROM directory lookup instead.
        save_index: dict[str, list[Path]] = {}
        if self.save_folder and self.save_folder.exists() and not is_mega_everdrive:
            self.progress.emit("Indexing save files…")
            for f in self.save_folder.rglob("*"):
                if f.is_file() and f.suffix.lower() in rn.SAVE_EXTENSIONS:
                    save_index.setdefault(f.stem, []).append(f)

        roms = rn.find_roms(self.folder)
        results = []
        for i, rom in enumerate(roms):
            self.progress.emit(f"Scanning {i + 1}/{len(roms)}: {rom.name}")
            ext = rom.suffix.lower()
            source = "filename"
            new_stem = rom.stem  # default: keep as-is

            if self.no_intro:
                # Step 1: exact CRC32 match → use canonical No-Intro name with region
                canonical = None
                if self.use_crc:
                    crc = rn._crc32_file(rom)
                    canonical = self.no_intro.get(crc)
                if canonical:
                    new_stem = canonical  # e.g. "Bahamut Lagoon (Japan)"
                    source = "No-Intro"
                else:
                    # Step 1b: Sony disc serial embedded in the filename.
                    # Runs before header-matching because it's both faster
                    # (no disc I/O) and more reliable for PS2 ISOs where
                    # the filename looks like "SCES_538.51.game.iso".
                    if use_serial_lookup:
                        serial = rn.extract_ps_serial(rom.name)
                        if serial:
                            canonical = rn.lookup_serial(serial, self.serial_map)
                            if canonical:
                                new_stem = canonical
                                source = "Serial"
                if source == "filename" and self.no_intro:
                    # Step 2: read ROM header, match via No-Intro index
                    # Handles translated ROMs ("Bahamut Lagoon Eng v31" → "bahamut_lagoon")
                    # and roman/arabic mismatches ("FINAL FANTASY 5" → "Final Fantasy V")
                    header_title = rn.read_rom_header_title(rom, self.system)
                    if header_title:
                        canonical = rn.lookup_header_in_index(header_title, name_index)
                        if canonical:
                            new_stem = canonical  # e.g. "Final Fantasy V (Japan)"
                            source = "Header"
                    # Step 3: fuzzy filename prefix search — finds games like
                    # "Chaos Seed.sfc" → "Chaos Seed - Fuusui Kairoki (Japan)"
                    # when the filename slug is a unique prefix of a No-Intro key.
                    if source == "filename":
                        canonical = rn.fuzzy_filename_search(rom.name, name_index)
                        if canonical:
                            new_stem = canonical
                            source = "Fuzzy"
                    # Step 4: normalize fallback (opt-in) — only if "Enable Normalize ROMs"
                    # is checked. Prefers bracket-trim over full slug normalization.
                    if source == "filename" and self.normalize_fallback:
                        bracket_idx = rom.stem.find("[")
                        if bracket_idx > 0:
                            new_stem = rom.stem[:bracket_idx].strip()
                            source = "Bracket"
                        else:
                            new_stem = rn.normalize_name(rom.name)
                            # source stays "filename"

                    # Step 5: parent folder name lookup — for MSU packs and other games
                    # where the ROM uses a shorthand filename (e.g. "ys5_msu.sfc") but
                    # lives in a properly named subfolder ("Ys V - Ushinawareta Suna…").
                    # Only tried when all other steps failed and ROM is in a subfolder.
                    if source == "filename" and rom.parent != self.folder:
                        canonical = rn.fuzzy_filename_search(
                            rom.parent.name, name_index
                        )
                        if canonical:
                            new_stem = canonical
                            source = "Folder"

                    # Region correction: if the filename (or folder name) has a region tag
                    # and the matched canonical has a different region, prefer the matching
                    # region's No-Intro entry (e.g. "Final Fight 2 (Europe)" → "(USA)").
                    if source in ("Header", "Fuzzy", "Folder"):
                        region_hint = rn.extract_region_hint(
                            rom.name
                        ) or rn.extract_region_hint(rom.parent.name)
                        if region_hint:
                            new_stem = rn.find_region_preferred(
                                new_stem, self.no_intro, region_hint
                            )

            new_rom = rom.parent / (new_stem + ext)
            subfolder = (
                str(rom.parent.relative_to(self.folder))
                if rom.parent != self.folder
                else ""
            )

            # Companion files (MSU-1 tracks, CUE sheets): store as (old_path, suffix)
            # so apply-time can derive the new name from whatever the user typed.
            # suffix = everything after the rom stem in the companion filename,
            # e.g. ".msu", "-1.pcm", ".cue"
            companions: list[tuple[Path, str]] = []
            for comp_old, _ in rn.find_companion_files(rom, new_stem):
                suffix = comp_old.name[len(rom.stem) :]  # e.g. "-1.pcm", ".msu"
                companions.append((comp_old, suffix))

            rom_entry_idx: int | None = None
            if new_rom != rom:
                rom_entry_idx = len(results)
                results.append(
                    {
                        "old": rom,
                        "new": new_rom,
                        "source": source,
                        "subfolder": subfolder,
                        "companions": companions,
                        "has_save": False,
                    }
                )

            # Matching save files — shown as separate visible rows so the user
            # can review, edit, or uncheck them independently.
            # Search order:
            #   1. ROM's own folder (co-located saves)
            #   2. pre-built save_index from save_folder (handles any depth/structure)
            seen_saves: set[Path] = set()
            candidate_saves: list[Path] = []
            for save_ext in rn.SAVE_EXTENSIONS:
                co_located = rom.parent / (rom.stem + save_ext)
                if co_located.exists():
                    candidate_saves.append(co_located)
            if save_index:
                for sp in save_index.get(rom.stem, []):
                    if sp not in {c for c in candidate_saves}:
                        candidate_saves.append(sp)

            for save_file in candidate_saves:
                if save_file in seen_saves:
                    continue
                seen_saves.add(save_file)
                if rom_entry_idx is not None:
                    results[rom_entry_idx]["has_save"] = True
                new_save = save_file.parent / (new_stem + save_file.suffix)
                if new_save != save_file:
                    save_subfolder = ""
                    try:
                        root = self.save_folder or self.folder
                        save_subfolder = str(save_file.parent.relative_to(root))
                    except ValueError:
                        save_subfolder = str(save_file.parent)
                    results.append(
                        {
                            "old": save_file,
                            "new": new_save,
                            "source": "Save",
                            "subfolder": save_subfolder,
                            "companions": [],
                            "rom_idx": rom_entry_idx,  # link back to parent ROM entry
                        }
                    )

            # MEGA EverDrive Pro: save is at gamedata/<rom_stem>/bram.srm.
            # Renaming means renaming the subdirectory, not the file inside it.
            # Embed this as metadata on the ROM entry so it applies automatically
            # when the ROM row is checked — no separate unchecked row needed.
            if is_mega_everdrive and self.save_folder and rom_entry_idx is not None:
                # EverDrive creates gamedata folders named after the full ROM filename
                # INCLUDING the extension (e.g. "Sonic (USA).md/bram.srm").
                game_dir = self.save_folder / rom.name
                if game_dir.is_dir() and (game_dir / "bram.srm").exists():
                    results[rom_entry_idx]["has_save"] = True
                    new_game_dir = self.save_folder / (new_stem + ext)
                    if new_game_dir != game_dir:
                        results[rom_entry_idx]["save_dir_old"] = game_dir
                        results[rom_entry_idx]["save_dir_new"] = new_game_dir

        self.finished.emit(results)


class RomNormalizerTab(QWidget):
    def __init__(self):
        super().__init__()
        self._renames: list[dict] = []
        self._no_intro: dict = {}
        self._serial_map: dict = {}
        self._worker = None
        self._loaded_dat_path: Path | None = None
        self._device_type: str = ""
        self._last_rom_folder: Path | None = None
        self._last_save_folder: Path | None = None
        self._last_dat_folder: Path | None = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Folder row
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("ROM Folder:"))
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText(
            "Path to ROM/save folder (searched recursively)..."
        )
        folder_row.addWidget(self.folder_edit, 4)
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse_btn)
        load_profile_btn = QPushButton("Load from Profile…")
        load_profile_btn.setToolTip(
            "Populate folders and system from a saved sync profile"
        )
        load_profile_btn.clicked.connect(self._load_from_profile)
        folder_row.addWidget(load_profile_btn)
        folder_row.addWidget(QLabel("System:"))
        self.system_combo = QComboBox()
        self.system_combo.addItems([""] + SYSTEM_CHOICES)
        self.system_combo.currentTextChanged.connect(self._on_system_changed)
        folder_row.addWidget(self.system_combo)
        layout.addLayout(folder_row)

        # Save folder row (optional — for devices like Everdrive where saves live elsewhere)
        save_folder_row = QHBoxLayout()
        self.save_folder_label = QLabel("Save Folder:")
        save_folder_row.addWidget(self.save_folder_label)
        self.save_folder_edit = QLineEdit()
        self.save_folder_edit.setPlaceholderText(
            "Optional — separate save folder (e.g. Everdrive SAVE/ dir)..."
        )
        save_folder_row.addWidget(self.save_folder_edit, 4)
        browse_save_btn = QPushButton("Browse")
        browse_save_btn.clicked.connect(self._browse_save_folder)
        save_folder_row.addWidget(browse_save_btn)
        clear_save_btn = QPushButton("Clear")
        clear_save_btn.clicked.connect(self.save_folder_edit.clear)
        save_folder_row.addWidget(clear_save_btn)
        save_folder_row.addWidget(QLabel("Device:"))
        self.device_combo = QComboBox()
        self.device_combo.addItems(["Standard", "MEGA EverDrive", "CD Folder"])
        self.device_combo.currentTextChanged.connect(self._on_device_changed)
        save_folder_row.addWidget(self.device_combo)
        layout.addLayout(save_folder_row)

        # DAT row
        dat_row = QHBoxLayout()
        dat_row.addWidget(QLabel("DAT:"))
        self.dat_label = QLabel("No DAT loaded — select a system or browse manually")
        self.dat_label.setStyleSheet("color: gray;")
        dat_row.addWidget(self.dat_label, 4)
        browse_dat_btn = QPushButton("Browse DAT...")
        browse_dat_btn.clicked.connect(self._browse_dat)
        dat_row.addWidget(browse_dat_btn)
        layout.addLayout(dat_row)

        # Buttons
        btn_row = QHBoxLayout()
        scan_btn = QPushButton("Scan / Preview")
        scan_btn.clicked.connect(self._scan)
        check_all_btn = QPushButton("Check All")
        check_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        uncheck_all_btn = QPushButton("Uncheck All")
        uncheck_all_btn.clicked.connect(lambda: self._set_all_checked(False))
        self.normalize_fallback_check = QCheckBox("Enable Normalize ROMs")
        self.normalize_fallback_check.setToolTip(
            "When checked, ROMs that don't match any DAT entry are renamed using a normalized slug\n"
            "(or the portion before '[' if the filename contains one).\n"
            "When unchecked, unmatched ROMs are left as-is."
        )
        self.nointro_only_check = QCheckBox("No-Intro / Redump matches only")
        self.nointro_only_check.setToolTip(
            "When checked, only renames matched via DAT (CRC, header, fuzzy, or folder name) are applied.\n"
            "Filename-normalized renames (yellow) are skipped."
        )
        self.nointro_only_check.stateChanged.connect(self._update_row_highlighting)
        self.crc_check = QCheckBox("CRC matching")
        self.crc_check.setChecked(True)
        self.crc_check.setToolTip(
            "When checked, the normalizer fingerprints ROM data and matches by DAT CRC first.\n"
            "Uncheck to skip CRC lookups and rely on header, folder, and fuzzy filename matching only."
        )
        self.apply_btn = QPushButton("Apply Renames")
        self.apply_btn.clicked.connect(self._apply)
        self.apply_btn.setEnabled(False)
        btn_row.addWidget(scan_btn)
        btn_row.addWidget(check_all_btn)
        btn_row.addWidget(uncheck_all_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.crc_check)
        btn_row.addWidget(self.normalize_fallback_check)
        btn_row.addWidget(self.nointro_only_check)
        btn_row.addWidget(self.apply_btn)
        layout.addLayout(btn_row)

        # Filter row
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Search name or subfolder…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_edit, 4)
        filter_row.addWidget(QLabel("Source:"))
        self.source_filter_combo = QComboBox()
        self.source_filter_combo.addItems(
            [
                "All",
                "No-Intro",
                "Serial",
                "Header",
                "Fuzzy",
                "Folder",
                "Bracket",
                "filename",
                "Save",
            ]
        )
        self.source_filter_combo.currentTextChanged.connect(self._apply_filter)
        filter_row.addWidget(self.source_filter_combo)
        self.has_save_check = QCheckBox("Has Save Only")
        self.has_save_check.stateChanged.connect(self._apply_filter)
        filter_row.addWidget(self.has_save_check)
        layout.addLayout(filter_row)

        # Results table — col 0 items have checkboxes for per-row opt-out
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Current Name", "New Name", "Subfolder", "Source", "Save"]
        )
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(2, 160)
        hdr.resizeSection(3, 90)
        hdr.resizeSection(4, 48)
        self.table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table)

        self.status_label = QLabel("Select a folder and system, then click Scan.")
        layout.addWidget(self.status_label)

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select ROM Folder", str(self._last_rom_folder or "")
        )
        if folder:
            self._last_rom_folder = Path(folder)
            self.folder_edit.setText(folder)

    def _load_from_profile(self):
        profiles = load_config().get("profiles", [])
        if not profiles:
            QMessageBox.information(
                self,
                "No Profiles",
                "No sync profiles configured yet.\nAdd profiles in the Sync Profiles tab first.",
            )
            return

        menu = QMenu(self)
        for p in profiles:
            name = p.get("name", "")
            system = p.get("system", "")
            label = f"{name}  [{system}]" if system else name
            action = menu.addAction(label)
            action.setData(p)

        btn = self.sender()
        action = menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
        if action is None:
            return

        profile = action.data()
        device_type = profile.get("device_type", "")

        # Set device combo — triggers _on_device_changed which updates label/placeholder
        if device_type == "MEGA EverDrive":
            combo_device = "MEGA EverDrive"
        elif device_type == "CD Folder":
            combo_device = "CD Folder"
        else:
            combo_device = "Standard"
        self.device_combo.setCurrentText(combo_device)

        self.folder_edit.setText(profile.get("path", ""))
        save_folder = profile.get("save_folder", "")
        self.save_folder_edit.setText(save_folder)

        system = profile.get("system", "")
        if system:
            idx = self.system_combo.findText(system)
            if idx >= 0:
                self.system_combo.setCurrentIndex(idx)

        # For CD Folder profiles, auto-load the Redump DAT if one is configured
        if device_type == "CD Folder":
            dat_path_str = profile.get("dat_path", "")
            if dat_path_str:
                dat_path = Path(dat_path_str)
                if dat_path.exists():
                    self._load_dat(dat_path)

    def _on_device_changed(self, device: str):
        if device == "MEGA EverDrive":
            self.save_folder_label.setText("Gamedata Folder:")
            self.save_folder_edit.setPlaceholderText(
                "Path to gamedata/ folder (e.g. J:/MEGA/gamedata)"
            )
        elif device == "CD Folder":
            self.save_folder_label.setText("Save Folder:")
            self.save_folder_edit.setPlaceholderText(
                "Optional — separate save folder (e.g. DuckStation memcards/)"
            )
            self.folder_edit.setPlaceholderText(
                "CD game folder — each subfolder is one disc (e.g. J:/PS1)"
            )
        else:
            self.save_folder_label.setText("Save Folder:")
            self.save_folder_edit.setPlaceholderText(
                "Optional — separate save folder (e.g. Everdrive SAVE/ dir)..."
            )
            self.folder_edit.setPlaceholderText(
                "Path to ROM/save folder (searched recursively)..."
            )
        self._device_type = device

    def _browse_save_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Save Folder", str(self._last_save_folder or "")
        )
        if folder:
            self._last_save_folder = Path(folder)
            self.save_folder_edit.setText(folder)

    def _browse_dat(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select No-Intro DAT",
            str(self._last_dat_folder or ""),
            "DAT Files (*.dat *.xml)",
        )
        if path:
            self._last_dat_folder = Path(path).parent
            self._load_dat(Path(path))

    def _on_system_changed(self, system: str):
        if not system:
            return
        import rom_normalizer as rn

        dat_path = rn.find_dat_for_system(system)
        if dat_path:
            self._load_dat(dat_path)
        else:
            self.dat_label.setText(
                f"No DAT found for {system} in dats/ folder — browse manually"
            )
            self.dat_label.setStyleSheet("color: orange;")
            self._no_intro = {}

    def _load_dat(self, path: Path):
        import rom_normalizer as rn

        self._no_intro = rn.load_no_intro_dat(path)
        # Libretro clrmamepro DATs (PS1 / PS2 / PSP) carry Sony serials.
        # Failures are silent so systems with no serial info just skip the step.
        self._serial_map = rn.load_serial_map(path)
        self._loaded_dat_path = path
        count = len(self._no_intro)
        serial_count = len(self._serial_map)
        label = f"{path.name}  ({count:,} entries"
        if serial_count:
            label += f", {serial_count:,} serials"
        label += ")"
        self.dat_label.setText(label)
        self.dat_label.setStyleSheet("color: green;" if count > 0 else "color: red;")

    def _scan(self):
        folder = Path(self.folder_edit.text().strip())
        if not folder.exists():
            QMessageBox.warning(self, "Error", "ROM folder not found.")
            return
        self.apply_btn.setEnabled(False)
        self.table.setRowCount(0)
        self._renames = []
        self.filter_edit.clear()
        self.source_filter_combo.setCurrentIndex(0)
        self.status_label.setText("Scanning...")
        save_folder_text = self.save_folder_edit.text().strip()
        save_folder = Path(save_folder_text) if save_folder_text else None
        if save_folder and not save_folder.exists():
            QMessageBox.warning(self, "Error", "Save folder not found.")
            return
        self._worker = NormalizeScanWorker(
            folder,
            self._no_intro,
            self.system_combo.currentText(),
            save_folder,
            self.device_combo.currentText(),
            self.normalize_fallback_check.isChecked(),
            self.crc_check.isChecked(),
            serial_map=self._serial_map,
        )
        self._worker.finished.connect(self._on_scan_done)
        self._worker.progress.connect(self.status_label.setText)
        self._worker.start()

    def _on_scan_done(self, renames: list):
        self._renames = renames
        self.table.setRowCount(len(renames))

        roms_only = [r for r in renames if r["source"] != "Save"]
        saves_only = [r for r in renames if r["source"] == "Save"]
        nointro = sum(1 for r in roms_only if r["source"] == "No-Intro")
        serial = sum(1 for r in roms_only if r["source"] == "Serial")
        header = sum(1 for r in roms_only if r["source"] == "Header")
        fuzzy = sum(1 for r in roms_only if r["source"] == "Fuzzy")
        folder = sum(1 for r in roms_only if r["source"] == "Folder")
        bracket = sum(1 for r in roms_only if r["source"] == "Bracket")
        filename = sum(1 for r in roms_only if r["source"] == "filename")
        companion = sum(len(r.get("companions", [])) for r in renames)

        SOURCE_COLORS = {
            "No-Intro": QColor(0, 200, 0),
            "Serial": QColor(0, 200, 160),
            "Header": QColor(80, 160, 255),
            "Fuzzy": QColor(200, 100, 255),
            "Folder": QColor(255, 160, 50),
            "Bracket": QColor(255, 220, 100),
            "filename": QColor(255, 200, 0),
            "Save": QColor(255, 140, 0),
        }
        for row, r in enumerate(renames):
            ro = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
            name_item = QTableWidgetItem(r["old"].name)
            name_item.setFlags(ro | Qt.ItemFlag.ItemIsUserCheckable)
            name_item.setCheckState(Qt.CheckState.Unchecked)
            tip_lines = []
            comps = r.get("companions", [])
            if comps:
                tip_lines.append(
                    "Companion files (renamed with ROM):\n"
                    + "\n".join(f"  {c.name}  →  {{new stem}}{s}" for c, s in comps)
                )
            if r.get("save_dir_old"):
                tip_lines.append(
                    f"Save folder also renamed:\n"
                    f"  {r['save_dir_old'].name}/\n"
                    f"  → {r['save_dir_new'].name}/"
                )
            if tip_lines:
                name_item.setToolTip("\n\n".join(tip_lines))
            self.table.setItem(row, 0, name_item)
            new_item = QTableWidgetItem(r["new"].name)
            if comps:
                new_item.setToolTip(
                    f"+{len(comps)} companion file(s) will follow this name"
                )
            self.table.setItem(row, 1, new_item)  # editable
            subfolder_item = QTableWidgetItem(r["subfolder"])
            subfolder_item.setFlags(ro)
            self.table.setItem(row, 2, subfolder_item)
            src_item = QTableWidgetItem(r["source"])
            src_item.setFlags(ro)
            src_item.setForeground(
                SOURCE_COLORS.get(r["source"], QColor(255, 255, 255))
            )
            self.table.setItem(row, 3, src_item)
            if r["source"] != "Save":
                has_save = r.get("has_save", False)
                save_col_item = QTableWidgetItem("✓" if has_save else "—")
                save_col_item.setFlags(ro)
                save_col_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                save_col_item.setForeground(
                    QColor(0, 200, 0) if has_save else QColor(100, 100, 100)
                )
                self.table.setItem(row, 4, save_col_item)
        self._update_row_highlighting()
        if renames:
            self.apply_btn.setEnabled(True)
            parts = []
            if nointro:
                parts.append(f"{nointro} No-Intro CRC")
            if serial:
                parts.append(f"{serial} PS serial match")
            if header:
                parts.append(f"{header} header match")
            if fuzzy:
                parts.append(f"{fuzzy} fuzzy name match")
            if folder:
                parts.append(f"{folder} folder name match")
            if bracket:
                parts.append(f"{bracket} bracket trim")
            if filename:
                parts.append(f"{filename} filename only")
            comp_note = f" (+{companion} companion files)" if companion else ""
            paired_save_dirs = sum(1 for r in roms_only if r.get("save_dir_old"))
            save_parts = []
            if saves_only:
                save_parts.append(
                    f"{len(saves_only)} save file{'s' if len(saves_only) != 1 else ''}"
                )
            if paired_save_dirs:
                save_parts.append(
                    f"{paired_save_dirs} save folder{'s' if paired_save_dirs != 1 else ''}"
                )
            save_note = f" (+{', '.join(save_parts)})" if save_parts else ""
            self.status_label.setText(
                f"{len(roms_only)} ROM rename(s) needed — {', '.join(parts)}{comp_note}{save_note}. "
                f"Review above, then click Apply Renames."
            )
        else:
            self.status_label.setText(
                "All files already normalized — no renames needed."
            )

    def _update_row_highlighting(self):
        """Grey out non-No-Intro rows when 'No-Intro only' is checked."""
        SOURCE_COLORS = {
            "No-Intro": QColor(0, 200, 0),
            "Serial": QColor(0, 200, 160),
            "Header": QColor(80, 160, 255),
            "Fuzzy": QColor(200, 100, 255),
            "Folder": QColor(255, 160, 50),
            "Bracket": QColor(255, 220, 100),
            "filename": QColor(255, 200, 0),
            "Save": QColor(255, 140, 0),
        }
        nointro_only = self.nointro_only_check.isChecked()
        dim = QColor(100, 100, 100)
        for row in range(self.table.rowCount()):
            src_item = self.table.item(row, 3)
            if src_item is None:
                continue
            source = src_item.text()
            excluded = nointro_only and source not in (
                "No-Intro",
                "Serial",
                "Header",
                "Fuzzy",
                "Folder",
                "Save",
            )
            for col in range(self.table.columnCount()):
                cell = self.table.item(row, col)
                if cell:
                    if excluded:
                        cell.setForeground(dim)
                    elif col == 3:
                        cell.setForeground(
                            SOURCE_COLORS.get(source, QColor(255, 255, 255))
                        )
                    else:
                        cell.setForeground(QColor(255, 255, 255))

    def _apply_filter(self):
        text = self.filter_edit.text().strip().lower()
        source_filter = self.source_filter_combo.currentText()
        has_save_only = self.has_save_check.isChecked()
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            sub_item = self.table.item(row, 2)
            src_item = self.table.item(row, 3)
            save_item = self.table.item(row, 4)
            name = (name_item.text() if name_item else "").lower()
            sub = (sub_item.text() if sub_item else "").lower()
            src = src_item.text() if src_item else ""
            text_match = not text or text in name or text in sub
            source_match = source_filter == "All" or src == source_filter
            # Save rows (the .sav/.srm rename entries) always count as "has save"
            save_val = save_item.text() if save_item else ""
            save_match = not has_save_only or src == "Save" or save_val == "✓"
            self.table.setRowHidden(
                row, not (text_match and source_match and save_match)
            )

    def _on_item_changed(self, item: QTableWidgetItem):
        """When a checkbox in col 0 is toggled and its row is selected, apply to all selected rows."""
        if item.column() != 0:
            return
        selected_rows = {idx.row() for idx in self.table.selectedIndexes()}
        if item.row() not in selected_rows or len(selected_rows) < 2:
            return
        state = item.checkState()
        self.table.blockSignals(True)
        for row in selected_rows:
            if row == item.row():
                continue
            cell = self.table.item(row, 0)
            if cell:
                cell.setCheckState(state)
        self.table.blockSignals(False)

    def _on_cell_double_clicked(self, row: int, col: int):
        """Double-click on any column except New Name toggles all rows in the same subfolder."""
        if col == 1:
            return  # let the editor open normally for New Name
        subfolder_item = self.table.item(row, 2)
        if subfolder_item is None:
            return
        subfolder = subfolder_item.text()

        # Collect visible row indices that share this subfolder
        rows_in_folder = [
            r
            for r in range(self.table.rowCount())
            if not self.table.isRowHidden(r)
            and (self.table.item(r, 2) or QTableWidgetItem("")).text() == subfolder
        ]

        # If every visible row in the group is checked, uncheck all; otherwise check all
        all_checked = all(
            (self.table.item(r, 0) or QTableWidgetItem("")).checkState()
            == Qt.CheckState.Checked
            for r in rows_in_folder
        )
        new_state = Qt.CheckState.Unchecked if all_checked else Qt.CheckState.Checked
        for r in rows_in_folder:
            item = self.table.item(r, 0)
            if item:
                item.setCheckState(new_state)

    def _set_all_checked(self, checked: bool):
        """Check/uncheck all currently visible rows."""
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row):
                continue
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(state)

    def _apply(self):
        if not self._renames:
            return
        nointro_only = self.nointro_only_check.isChecked()
        to_apply = []
        applied_rom_indices: set[int] = set()
        for row, r in enumerate(self._renames):
            item = self.table.item(row, 0)
            if item and item.checkState() != Qt.CheckState.Checked:
                continue
            if nointro_only and r["source"] not in (
                "No-Intro",
                "Serial",
                "Header",
                "Fuzzy",
                "Folder",
                "Save",
            ):
                continue
            to_apply.append((row, r))
            if r["source"] != "Save":
                applied_rom_indices.add(row)

        # Auto-include Save rows linked to an applied ROM, even if unchecked.
        # This ensures save files always follow their ROM rename automatically.
        applied_rows = {row for row, _ in to_apply}
        for row, r in enumerate(self._renames):
            if row in applied_rows:
                continue
            if r["source"] != "Save":
                continue
            rom_idx = r.get("rom_idx")
            if rom_idx is not None and rom_idx in applied_rom_indices:
                to_apply.append((row, r))
        to_apply.sort(key=lambda x: x[0])  # keep original row order
        if not to_apply:
            QMessageBox.information(
                self,
                "Nothing to apply",
                "No renames to apply — all rows are unchecked or filtered out.",
            )
            return
        filter_note = " (No-Intro/Redump matches only)" if nointro_only else ""
        rom_count = sum(1 for _, r in to_apply if r["source"] != "Save")
        save_count = sum(1 for _, r in to_apply if r["source"] == "Save")
        save_dir_count = sum(1 for _, r in to_apply if r.get("save_dir_old"))
        parts = []
        if rom_count:
            parts.append(f"{rom_count} ROM(s)")
        if save_count:
            parts.append(f"{save_count} save file(s)")
        if save_dir_count:
            parts.append(f"{save_dir_count} save folder(s)")
        reply = QMessageBox.question(
            self,
            "Apply Renames",
            f"Rename {' + '.join(parts)}{filter_note}? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        import rom_normalizer as rn

        done_rows = []  # table row indices that were successfully renamed
        skipped = 0
        log_entries: list[str] = []  # (old_path, new_path) pairs for the undo log

        for row, r in to_apply:
            old = r["old"]
            # Use the (possibly user-edited) name from the table cell
            new_name = (self.table.item(row, 1) or QTableWidgetItem("")).text().strip()
            if not new_name:
                skipped += 1
                continue
            new = old.parent / new_name
            if new.exists() and new != old:
                skipped += 1
                continue
            try:
                old.rename(new)
                log_entries.append(f"{new}\t{old}")
                # Rename companion files (MSU tracks, CUE sheets) using the new stem
                for comp_old, comp_suffix in r.get("companions", []):
                    comp_new = comp_old.parent / (new.stem + comp_suffix)
                    if (
                        comp_new != comp_old
                        and not comp_new.exists()
                        and comp_old.exists()
                    ):
                        comp_old.rename(comp_new)
                        if comp_new.suffix.lower() == ".cue":
                            rn.patch_cue_references(
                                comp_new, comp_old.stem, comp_new.stem
                            )
                        log_entries.append(f"{comp_new}\t{comp_old}")
                # MEGA EverDrive: rename paired save folder alongside the ROM.
                # Derive the new folder name from the actual renamed file (new), not
                # from the pre-scanned value, so user-edited names are respected.
                # For CD games the save dir uses a .cue extension (save_dir_ext),
                # while for ROM files it uses the full filename (new.name).
                save_dir_old = r.get("save_dir_old")
                if save_dir_old and save_dir_old.exists():
                    save_dir_ext = r.get("save_dir_ext")
                    if save_dir_ext:
                        # CD folder: new is a directory, derive from its name + .cue ext
                        save_dir_new = save_dir_old.parent / (new.name + save_dir_ext)
                    else:
                        # ROM file: new already has an extension (e.g. "Sonic.md")
                        save_dir_new = save_dir_old.parent / new.name
                    if save_dir_new != save_dir_old and not save_dir_new.exists():
                        save_dir_old.rename(save_dir_new)
                        log_entries.append(f"{save_dir_new}\t{save_dir_old}")
                done_rows.append(row)
            except Exception as e:
                QMessageBox.warning(
                    self, "Rename Error", f"Could not rename {old.name}:\n{e}"
                )
                break

        # Write undo log — tab-separated "new_path<TAB>old_path" per line so a script
        # can reverse the renames by reading each line and renaming new→old.
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if log_entries:
            logs_dir = Path(__file__).parent.parent / "logs"
            logs_dir.mkdir(exist_ok=True)
            log_path = logs_dir / f"renames_{ts}.txt"
            try:
                with open(log_path, "w", encoding="utf-8") as lf:
                    lf.write(
                        f"# ROM Normalizer rename log — {datetime.now().isoformat()}\n"
                    )
                    lf.write(
                        "# Format: new_path<TAB>old_path  (rename new→old to undo)\n"
                    )
                    lf.write(
                        "# To undo: for each line, rename the first path back to the second.\n\n"
                    )
                    lf.write("\n".join(log_entries) + "\n")
            except Exception as e:
                QMessageBox.warning(
                    self, "Log Error", f"Could not write undo log:\n{e}"
                )

        # Remove successfully renamed rows from the table and _renames list.
        # Iterate in reverse so that removing a row doesn't shift subsequent indices.
        self.table.blockSignals(True)
        for row in sorted(done_rows, reverse=True):
            self.table.removeRow(row)
            self._renames.pop(row)
        self.table.blockSignals(False)

        remaining = self.table.rowCount()
        done = len(done_rows)
        log_note = f" — log saved to logs/renames_{ts}.txt" if log_entries else ""
        if remaining == 0:
            self.apply_btn.setEnabled(False)
            self.status_label.setText(
                f"Done: {done} renamed, {skipped} skipped — all renames applied.{log_note}"
            )
        else:
            self.status_label.setText(
                f"Done: {done} renamed, {skipped} skipped — {remaining} item(s) still pending.{log_note}"
            )

    def save_ui_state(self) -> dict:
        return {
            "rom_folder": self.folder_edit.text(),
            "save_folder": self.save_folder_edit.text(),
            "system": self.system_combo.currentText(),
            "device": self.device_combo.currentText(),
            "dat_path": str(self._loaded_dat_path) if self._loaded_dat_path else "",
            "nointro_only": self.nointro_only_check.isChecked(),
            "normalize_fallback": self.normalize_fallback_check.isChecked(),
            "has_save_only": self.has_save_check.isChecked(),
            "last_rom_folder": str(self._last_rom_folder)
            if self._last_rom_folder
            else "",
            "last_save_folder": str(self._last_save_folder)
            if self._last_save_folder
            else "",
            "last_dat_folder": str(self._last_dat_folder)
            if self._last_dat_folder
            else "",
        }

    def load_ui_state(self, state: dict):
        if "rom_folder" in state:
            self.folder_edit.setText(state["rom_folder"])
        if "save_folder" in state:
            self.save_folder_edit.setText(state["save_folder"])
        if "system" in state:
            idx = self.system_combo.findText(state["system"])
            if idx >= 0:
                self.system_combo.setCurrentIndex(idx)
        if "device" in state:
            idx = self.device_combo.findText(state["device"])
            if idx >= 0:
                self.device_combo.setCurrentIndex(idx)
        if state.get("nointro_only"):
            self.nointro_only_check.setChecked(True)
        if state.get("normalize_fallback"):
            self.normalize_fallback_check.setChecked(True)
        if state.get("has_save_only"):
            self.has_save_check.setChecked(True)
        dat_path_str = state.get("dat_path", "")
        if dat_path_str:
            dat_path = Path(dat_path_str)
            if dat_path.exists():
                self._load_dat(dat_path)
        last_rom = state.get("last_rom_folder", "")
        self._last_rom_folder = Path(last_rom) if last_rom else None
        last_save = state.get("last_save_folder", "")
        self._last_save_folder = Path(last_save) if last_save else None
        last_dat = state.get("last_dat_folder", "")
        self._last_dat_folder = Path(last_dat) if last_dat else None
