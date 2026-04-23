from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
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
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config import SYSTEM_CHOICES, load_config


def format_build_confirmation_message(
    output_folder: Path,
    matched_count: int,
    unmatched_found_count: int,
    include_unmatched: bool,
    split_into_ranges: bool,
    bucket_count: int,
    unzip_archives: bool,
    one_game_one_rom: bool,
) -> str:
    unmatched_copied_count = unmatched_found_count if include_unmatched else 0
    total_written = matched_count + unmatched_copied_count
    if split_into_ranges:
        matched_line = (
            f"Matched games: {matched_count} file(s), split into {bucket_count} "
            "letter-range folders."
        )
    else:
        matched_line = f"Matched games: {matched_count} file(s), copied flat into the output folder."

    unmatched_line = (
        f"Unmatched files: {unmatched_found_count} found, "
        f"{unmatched_copied_count} will be copied into the 'unmatched files' subfolder."
        if include_unmatched
        else f"Unmatched files: {unmatched_found_count} found, 0 will be copied."
    )

    zip_line = (
        "ZIP/7z ROM archives will be extracted."
        if unzip_archives
        else "ZIP ROMs stay .zip and 7z ROMs will be converted to .zip."
    )
    mode_line = (
        "Mode: 1G1R preferred set."
        if one_game_one_rom
        else "Mode: complete collection (all matched variants)."
    )

    return (
        f"Build collection in:\n{output_folder}\n\n"
        f"Total files to write: {total_written}\n"
        f"{mode_line}\n"
        f"{matched_line}\n"
        f"{unmatched_line}\n"
        f"{zip_line}\n"
        "Proceed?"
    )


class CollectionScanWorker(QThread):
    finished = pyqtSignal(list, list, list)
    progress = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        folder: Path,
        system: str,
        no_intro: dict[str, str],
        clone_map: dict[str, str] | None = None,
        skip_crc: bool = False,
        one_game_one_rom: bool = True,
    ):
        super().__init__()
        self.folder = folder
        self.system = system
        self.no_intro = no_intro
        self.clone_map = clone_map or {}
        self.skip_crc = skip_crc
        self.one_game_one_rom = one_game_one_rom

    def run(self):
        try:
            import rom_collection as rc

            entries, duplicates, unmatched = rc.scan_collection(
                self.folder,
                self.system,
                self.no_intro,
                progress_callback=self.progress.emit,
                clone_map=self.clone_map,
                skip_crc=self.skip_crc,
                one_game_one_rom=self.one_game_one_rom,
            )
            self.finished.emit(entries, duplicates, unmatched)
        except Exception as exc:
            import traceback

            self.error.emit(traceback.format_exc() or str(exc))


class CollectionBuildWorker(QThread):
    finished = pyqtSignal(list)
    progress = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        entries: list,
        output_folder: Path,
        unzip_archives: bool,
        unmatched_files: list[Path] | None = None,
        folder_count: int = 1,
    ):
        super().__init__()
        self.entries = entries
        self.output_folder = output_folder
        self.unzip_archives = unzip_archives
        self.unmatched_files = unmatched_files or []
        self.folder_count = folder_count

    def run(self):
        try:
            import rom_collection as rc

            written = rc.build_collection(
                self.entries,
                self.output_folder,
                self.unzip_archives,
                unmatched_files=self.unmatched_files,
                folder_count=self.folder_count,
                progress_callback=self.progress.emit,
            )
            self.finished.emit(written)
        except Exception as exc:
            import traceback

            self.error.emit(traceback.format_exc() or str(exc))


class CollectionValidateWorker(QThread):
    finished = pyqtSignal(object)
    progress = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        folder: Path,
        system: str,
        no_intro: dict[str, str],
        clone_map: dict[str, str] | None = None,
        skip_crc: bool = False,
        one_game_one_rom: bool = True,
        enabled_regions: set[str] | None = None,
    ):
        super().__init__()
        self.folder = folder
        self.system = system
        self.no_intro = no_intro
        self.clone_map = clone_map or {}
        self.skip_crc = skip_crc
        self.one_game_one_rom = one_game_one_rom
        self.enabled_regions = enabled_regions

    def run(self):
        try:
            import rom_collection as rc

            report = rc.validate_collection(
                self.folder,
                self.system,
                self.no_intro,
                progress_callback=self.progress.emit,
                clone_map=self.clone_map,
                skip_crc=self.skip_crc,
                one_game_one_rom=self.one_game_one_rom,
                enabled_regions=self.enabled_regions,
            )
            self.finished.emit(report)
        except Exception as exc:
            import traceback

            self.error.emit(traceback.format_exc() or str(exc))


class RomCollectionTab(QWidget):
    def __init__(self):
        super().__init__()
        self._entries = []
        self._duplicates = []
        self._unmatched = []
        self._no_intro = {}
        self._clone_map: dict[str, str] = {}
        self._loaded_dat_path: Path | None = None
        self._last_rom_folder: Path | None = None
        self._last_dat_folder: Path | None = None
        self._last_output_folder: Path | None = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        source_layout = QGridLayout()
        source_layout.addWidget(QLabel("ROM Folder:"), 0, 0)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText(
            "Select a ROM folder or load one from a profile..."
        )
        source_layout.addWidget(self.folder_edit, 0, 1)
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse_folder)
        source_layout.addWidget(browse_btn, 0, 2)
        load_profile_btn = QPushButton("Load from Profile…")
        load_profile_btn.clicked.connect(self._load_from_profile)
        source_layout.addWidget(load_profile_btn, 0, 3)

        source_layout.addWidget(QLabel("System:"), 1, 0)
        self.system_combo = QComboBox()
        self.system_combo.addItems([""] + SYSTEM_CHOICES)
        self.system_combo.currentTextChanged.connect(self._on_system_changed)
        source_layout.addWidget(self.system_combo, 1, 1)
        self.unzip_check = QCheckBox(
            "Extract .zip/.7z ROM archives when building collection"
        )
        self.unzip_check.setToolTip(
            "When checked, .zip and .7z sources are extracted to their ROM file. "
            "Otherwise .zip files stay zipped and .7z files are converted to .zip."
        )
        source_layout.addWidget(self.unzip_check, 1, 2, 1, 2)
        self.one_game_one_rom_check = QCheckBox(
            "1G1R (keep one preferred copy per game)"
        )
        self.one_game_one_rom_check.setChecked(True)
        self.one_game_one_rom_check.setToolTip(
            "When checked, keeps one preferred variant per game using the collection priority rules. Disable to include all matched variants."
        )
        self.one_game_one_rom_check.stateChanged.connect(
            self._on_collection_mode_changed
        )
        source_layout.addWidget(self.one_game_one_rom_check, 2, 1, 1, 3)
        self.include_unmatched_check = QCheckBox(
            "Include unmatched files in 'unmatched files' folder"
        )
        self.include_unmatched_check.setToolTip(
            "When checked, unmatched ROMs/archives are copied as-is into an 'unmatched files' subfolder in the output."
        )
        self.include_unmatched_check.stateChanged.connect(self._refresh_build_button)
        source_layout.addWidget(self.include_unmatched_check, 3, 1, 1, 3)
        self.bucket_folders_check = QCheckBox(
            "Split collection into letter-range folders"
        )
        self.bucket_folders_check.setToolTip(
            "When checked, matched games are copied into auto-generated folders like A-G, H-N, O-T, U-Z."
        )
        self.bucket_folders_check.stateChanged.connect(self._on_bucket_toggle)
        source_layout.addWidget(self.bucket_folders_check, 4, 1, 1, 2)
        self.bucket_count_spin = QSpinBox()
        self.bucket_count_spin.setRange(2, 26)
        self.bucket_count_spin.setValue(4)
        self.bucket_count_spin.setEnabled(False)
        self.bucket_count_spin.setToolTip("How many letter-range folders to create.")
        source_layout.addWidget(self.bucket_count_spin, 4, 3)

        source_layout.addWidget(QLabel("Regions:"), 5, 0)
        region_box = QHBoxLayout()
        self.region_usa_check = QCheckBox("USA")
        self.region_usa_check.setChecked(True)
        self.region_europe_check = QCheckBox("Europe")
        self.region_europe_check.setChecked(True)
        self.region_japan_check = QCheckBox("Japan")
        self.region_japan_check.setChecked(True)
        self.region_other_check = QCheckBox("Other")
        self.region_other_check.setChecked(True)
        self.region_other_check.setToolTip(
            "Includes translated ROMs and all non-USA/Europe/Japan regions."
        )
        for cb in (
            self.region_usa_check,
            self.region_europe_check,
            self.region_japan_check,
            self.region_other_check,
        ):
            cb.stateChanged.connect(self._on_region_filter_changed)
            region_box.addWidget(cb)
        region_box.addStretch()
        region_widget = QWidget()
        region_widget.setLayout(region_box)
        source_layout.addWidget(region_widget, 5, 1, 1, 3)

        self.crc_check = QCheckBox("CRC matching")
        self.crc_check.setChecked(True)
        self.crc_check.setToolTip(
            "When enabled, computes CRC32 of each ROM to match against the DAT.\n"
            "Accurate but slow for large collections. Disable to use only\n"
            "filename / header matching (much faster)."
        )
        source_layout.addWidget(QLabel("Options:"), 6, 0)
        source_layout.addWidget(self.crc_check, 6, 1, 1, 3)

        layout.addLayout(source_layout)

        dat_row = QHBoxLayout()
        dat_row.addWidget(QLabel("DAT:"))
        self.dat_label = QLabel("No DAT loaded — select a system or browse manually")
        dat_row.addWidget(self.dat_label, 1)
        browse_dat_btn = QPushButton("Browse DAT…")
        browse_dat_btn.clicked.connect(self._browse_dat)
        dat_row.addWidget(browse_dat_btn)
        layout.addLayout(dat_row)

        btn_row = QHBoxLayout()
        scan_btn = QPushButton("Scan / Preview")
        scan_btn.clicked.connect(self._scan)
        self.build_btn = QPushButton("Build Collection…")
        self.build_btn.clicked.connect(self._build)
        self.build_btn.setEnabled(False)
        validate_btn = QPushButton("Validate Collection…")
        validate_btn.clicked.connect(self._validate)
        btn_row.addWidget(scan_btn)
        btn_row.addWidget(self.build_btn)
        btn_row.addWidget(validate_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Current Source", "Collection Name", "Type", "Match", "Region"]
        )
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        self.status_label = QLabel("Select a ROM folder and system, then scan.")
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
                self, "No Profiles", "No sync profiles configured yet."
            )
            return

        menu = QMenu(self)
        for profile in profiles:
            name = profile.get("name", "")
            system = profile.get("system", "")
            if not system and "systems" in profile:
                enabled = [s for s in profile["systems"] if s.get("enabled", True)]
                if len(enabled) == 1:
                    system = enabled[0].get("system", "")
            label = f"{name}  [{system}]" if system else name
            action = menu.addAction(label)
            action.setData(profile)

        btn = self.sender()
        action = menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
        if action is None:
            return

        profile = action.data()
        self.folder_edit.setText(profile.get("path", ""))
        system = profile.get("system", "")
        if not system and "systems" in profile:
            enabled = [s for s in profile["systems"] if s.get("enabled", True)]
            if len(enabled) == 1:
                system = enabled[0].get("system", "")
        if system:
            self.system_combo.setCurrentText(system)

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
            self._no_intro = {}
            self._clone_map = {}
            self._loaded_dat_path = None
            self.dat_label.setText(
                f"No DAT found for {system} in dats/ folder — browse manually"
            )

    def _load_dat(self, path: Path):
        import rom_normalizer as rn

        self._no_intro = rn.load_no_intro_dat(path)
        self._clone_map = rn.load_cloneof_map(path)
        self._loaded_dat_path = path
        count = len(self._no_intro)
        clones = len(self._clone_map)
        label = f"{path.name}  ({count:,} entries"
        if clones:
            label += f", {clones:,} clone links"
        label += ")"
        self.dat_label.setText(label)

    def _scan(self):
        folder = Path(self.folder_edit.text().strip())
        system = self.system_combo.currentText().strip()
        if not folder.exists():
            QMessageBox.warning(self, "Error", "ROM folder not found.")
            return
        if not system:
            QMessageBox.warning(self, "Error", "Select a system first.")
            return
        if not self._no_intro:
            QMessageBox.warning(self, "Error", "Load a DAT first.")
            return

        self._entries = []
        self._duplicates = []
        self._unmatched = []
        self.table.setRowCount(0)
        self._refresh_build_button()
        self.status_label.setText("Scanning collection candidates...")

        self._scan_worker = CollectionScanWorker(
            folder,
            system,
            self._no_intro,
            clone_map=self._clone_map,
            skip_crc=not self.crc_check.isChecked(),
            one_game_one_rom=self.one_game_one_rom_check.isChecked(),
        )
        self._scan_worker.progress.connect(self.status_label.setText)
        self._scan_worker.finished.connect(self._on_scan_done)
        self._scan_worker.error.connect(self._on_worker_error)
        self._scan_worker.start()

    def _on_scan_done(self, entries: list, duplicates: list, unmatched: list):
        self._all_entries = entries
        self._duplicates = duplicates
        self._unmatched = unmatched
        self._apply_region_filter()

    def _enabled_regions(self) -> set[str]:
        regions: set[str] = set()
        if self.region_usa_check.isChecked():
            regions.add("USA")
        if self.region_europe_check.isChecked():
            regions.add("Europe")
        if self.region_japan_check.isChecked():
            regions.add("Japan")
        if self.region_other_check.isChecked():
            regions.add("Other")
        return regions

    def _apply_region_filter(self):
        import rom_collection as rc

        all_entries = getattr(self, "_all_entries", [])
        self._entries = rc.filter_by_regions(all_entries, self._enabled_regions())
        self._refresh_table()

    def _refresh_table(self):
        entries = self._entries
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            source_name = (
                entry.source_path.name
                if entry.archive_member is None
                else f"{entry.source_path.name} :: {Path(entry.archive_member).name}"
            )
            self.table.setItem(row, 0, QTableWidgetItem(source_name))
            self.table.setItem(row, 1, QTableWidgetItem(entry.output_name))
            self.table.setItem(row, 2, QTableWidgetItem(entry.source_kind))
            self.table.setItem(row, 3, QTableWidgetItem(entry.match_source))
            self.table.setItem(row, 4, QTableWidgetItem(entry.region))

        self._refresh_build_button()
        all_count = len(getattr(self, "_all_entries", []))
        filtered_count = len(entries)
        dup_count = len(self._duplicates)
        unmatched_count = len(self._unmatched)
        parts = [f"{filtered_count} game(s) shown"]
        if filtered_count < all_count:
            parts.append(f"{all_count - filtered_count} filtered out by region")
        parts.append(f"{dup_count} duplicate(s) skipped")
        parts.append(f"{unmatched_count} unmatched file(s)")
        self.status_label.setText(", ".join(parts) + ".")

    def _on_region_filter_changed(self):
        if getattr(self, "_all_entries", None):
            self._apply_region_filter()

    def _on_collection_mode_changed(self):
        self._entries = []
        self._duplicates = []
        self._unmatched = []
        self._all_entries = []
        self.table.setRowCount(0)
        self._refresh_build_button()
        self.status_label.setText("Collection mode changed. Scan again.")

    def _on_worker_error(self, msg: str):
        self._refresh_build_button()
        QMessageBox.critical(self, "ROM Collection Error", msg)
        self.status_label.setText("Operation failed")

    def _on_bucket_toggle(self):
        self.bucket_count_spin.setEnabled(self.bucket_folders_check.isChecked())

    def _refresh_build_button(self):
        self.build_btn.setEnabled(
            bool(self._entries)
            or (self.include_unmatched_check.isChecked() and bool(self._unmatched))
        )

    def _build(self):
        include_unmatched = self.include_unmatched_check.isChecked()
        if not self._entries and not (include_unmatched and self._unmatched):
            return
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Output Folder for Collection",
            str(self._last_output_folder or ""),
        )
        if not folder:
            return
        self._last_output_folder = Path(folder)
        output_folder = self._last_output_folder
        reply = QMessageBox.question(
            self,
            "Build ROM Collection",
            format_build_confirmation_message(
                output_folder=output_folder,
                matched_count=len(self._entries),
                unmatched_found_count=len(self._unmatched),
                include_unmatched=include_unmatched,
                split_into_ranges=self.bucket_folders_check.isChecked(),
                bucket_count=self.bucket_count_spin.value(),
                unzip_archives=self.unzip_check.isChecked(),
                one_game_one_rom=self.one_game_one_rom_check.isChecked(),
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.build_btn.setEnabled(False)
        self.status_label.setText("Building collection...")
        self._build_worker = CollectionBuildWorker(
            self._entries,
            output_folder,
            self.unzip_check.isChecked(),
            unmatched_files=self._unmatched if include_unmatched else [],
            folder_count=self.bucket_count_spin.value()
            if self.bucket_folders_check.isChecked()
            else 1,
        )
        self._build_worker.progress.connect(self.status_label.setText)
        self._build_worker.finished.connect(self._on_build_done)
        self._build_worker.error.connect(self._on_worker_error)
        self._build_worker.start()

    def _on_build_done(self, written: list):
        self.build_btn.setEnabled(True)
        self.status_label.setText(
            f"Wrote {len(written)} file(s) to the collection folder."
        )
        QMessageBox.information(
            self, "ROM Collection Complete", f"Created {len(written)} file(s)."
        )

    def _validate(self):
        system = self.system_combo.currentText().strip()
        if not system:
            QMessageBox.warning(self, "Error", "Select a system first.")
            return
        if not self._no_intro:
            QMessageBox.warning(self, "Error", "Load a DAT first.")
            return

        folder = self.folder_edit.text().strip()
        if not folder:
            QMessageBox.warning(self, "Error", "Set a ROM folder first.")
            return
        if not Path(folder).exists():
            QMessageBox.warning(self, "Error", f"ROM folder not found:\n{folder}")
            return

        self.status_label.setText("Validating collection...")
        self.build_btn.setEnabled(False)
        self._validate_worker = CollectionValidateWorker(
            folder=Path(folder),
            system=system,
            no_intro=self._no_intro,
            clone_map=self._clone_map,
            skip_crc=not self.crc_check.isChecked(),
            one_game_one_rom=self.one_game_one_rom_check.isChecked(),
            enabled_regions=self._enabled_regions(),
        )
        self._validate_worker.progress.connect(self.status_label.setText)
        self._validate_worker.finished.connect(
            lambda report: self._on_validate_done(Path(folder), system, report)
        )
        self._validate_worker.error.connect(self._on_worker_error)
        self._validate_worker.start()

    def _on_validate_done(self, folder: Path, system: str, report):
        import rom_collection as rc

        self._refresh_build_button()
        report_text = rc.format_validation_report(
            report,
            folder,
            system,
            self.one_game_one_rom_check.isChecked(),
            self._enabled_regions(),
        )
        default_name = f"{system.lower()}_collection_validation.txt"
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Validation Report",
            str((folder / default_name)),
            "Text Files (*.txt)",
        )
        if save_path:
            Path(save_path).write_text(report_text, encoding="utf-8")
        self.status_label.setText(
            f"Validation complete: {len(report.present)} present, {len(report.wrong_region)} incorrect region, {len(report.missing)} missing."
        )
        message = (
            f"Present: {len(report.present)}\n"
            f"Incorrect region: {len(report.wrong_region)}\n"
            f"Missing: {len(report.missing)}\n"
            f"Unmatched: {len(report.unmatched)}"
        )
        if save_path:
            message += f"\n\nReport saved to:\n{save_path}"
        QMessageBox.information(self, "Collection Validation Complete", message)

    def save_ui_state(self) -> dict:
        return {
            "folder": self.folder_edit.text(),
            "system": self.system_combo.currentText(),
            "unzip": self.unzip_check.isChecked(),
            "one_game_one_rom": self.one_game_one_rom_check.isChecked(),
            "include_unmatched": self.include_unmatched_check.isChecked(),
            "bucket_folders": self.bucket_folders_check.isChecked(),
            "bucket_count": self.bucket_count_spin.value(),
            "region_usa": self.region_usa_check.isChecked(),
            "region_europe": self.region_europe_check.isChecked(),
            "region_japan": self.region_japan_check.isChecked(),
            "region_other": self.region_other_check.isChecked(),
            "crc_matching": self.crc_check.isChecked(),
            "last_rom_folder": str(self._last_rom_folder)
            if self._last_rom_folder
            else "",
            "last_dat_folder": str(self._last_dat_folder)
            if self._last_dat_folder
            else "",
            "last_output_folder": str(self._last_output_folder)
            if self._last_output_folder
            else "",
        }

    def load_ui_state(self, state: dict):
        self.folder_edit.setText(state.get("folder", ""))
        self.unzip_check.setChecked(bool(state.get("unzip", False)))
        self.one_game_one_rom_check.setChecked(
            bool(state.get("one_game_one_rom", True))
        )
        self.include_unmatched_check.setChecked(
            bool(state.get("include_unmatched", False))
        )
        self.bucket_folders_check.setChecked(bool(state.get("bucket_folders", False)))
        self.bucket_count_spin.setValue(int(state.get("bucket_count", 4)))
        self.region_usa_check.setChecked(bool(state.get("region_usa", True)))
        self.region_europe_check.setChecked(bool(state.get("region_europe", True)))
        self.region_japan_check.setChecked(bool(state.get("region_japan", True)))
        self.region_other_check.setChecked(bool(state.get("region_other", True)))
        self.crc_check.setChecked(bool(state.get("crc_matching", True)))
        self._on_bucket_toggle()
        system = state.get("system", "")
        if system:
            self.system_combo.setCurrentText(system)
        last_rom = state.get("last_rom_folder", "")
        self._last_rom_folder = Path(last_rom) if last_rom else None
        last_dat = state.get("last_dat_folder", "")
        self._last_dat_folder = Path(last_dat) if last_dat else None
        last_out = state.get("last_output_folder", "")
        self._last_output_folder = Path(last_out) if last_out else None
