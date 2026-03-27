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


class CollectionScanWorker(QThread):
    finished = pyqtSignal(list, list, list)
    progress = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, folder: Path, system: str, no_intro: dict[str, str]):
        super().__init__()
        self.folder = folder
        self.system = system
        self.no_intro = no_intro

    def run(self):
        try:
            import rom_collection as rc

            entries, duplicates, unmatched = rc.scan_collection(
                self.folder,
                self.system,
                self.no_intro,
                progress_callback=self.progress.emit,
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


class RomCollectionTab(QWidget):
    def __init__(self):
        super().__init__()
        self._entries = []
        self._duplicates = []
        self._unmatched = []
        self._no_intro = {}
        self._loaded_dat_path: Path | None = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        source_layout = QGridLayout()
        source_layout.addWidget(QLabel("ROM Folder:"), 0, 0)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Select a ROM folder or load one from a profile...")
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
        self.unzip_check = QCheckBox("Unzip zipped ROMs when building collection")
        self.unzip_check.setToolTip("When checked, .zip sources are extracted to their ROM file. Otherwise the .zip is copied and renamed.")
        source_layout.addWidget(self.unzip_check, 1, 2, 1, 2)
        self.include_unmatched_check = QCheckBox("Include unmatched files in 'unmatched files' folder")
        self.include_unmatched_check.setToolTip(
            "When checked, unmatched ROMs/archives are copied as-is into an 'unmatched files' subfolder in the output."
        )
        self.include_unmatched_check.stateChanged.connect(self._refresh_build_button)
        source_layout.addWidget(self.include_unmatched_check, 2, 1, 1, 3)
        self.bucket_folders_check = QCheckBox("Split collection into letter-range folders")
        self.bucket_folders_check.setToolTip(
            "When checked, matched games are copied into auto-generated folders like A-G, H-N, O-T, U-Z."
        )
        self.bucket_folders_check.stateChanged.connect(self._on_bucket_toggle)
        source_layout.addWidget(self.bucket_folders_check, 3, 1, 1, 2)
        self.bucket_count_spin = QSpinBox()
        self.bucket_count_spin.setRange(2, 26)
        self.bucket_count_spin.setValue(4)
        self.bucket_count_spin.setEnabled(False)
        self.bucket_count_spin.setToolTip("How many letter-range folders to create.")
        source_layout.addWidget(self.bucket_count_spin, 3, 3)
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
        btn_row.addWidget(scan_btn)
        btn_row.addWidget(self.build_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Current Source", "Collection Name", "Type", "Match", "Region"])
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
        folder = QFileDialog.getExistingDirectory(self, "Select ROM Folder")
        if folder:
            self.folder_edit.setText(folder)

    def _load_from_profile(self):
        profiles = load_config().get("profiles", [])
        if not profiles:
            QMessageBox.information(self, "No Profiles", "No sync profiles configured yet.")
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
        path, _ = QFileDialog.getOpenFileName(self, "Select No-Intro DAT", "", "DAT Files (*.dat *.xml)")
        if path:
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
            self._loaded_dat_path = None
            self.dat_label.setText(f"No DAT found for {system} in dats/ folder — browse manually")

    def _load_dat(self, path: Path):
        import rom_normalizer as rn

        self._no_intro = rn.load_no_intro_dat(path)
        self._loaded_dat_path = path
        count = len(self._no_intro)
        self.dat_label.setText(f"{path.name}  ({count:,} entries)")

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

        self._scan_worker = CollectionScanWorker(folder, system, self._no_intro)
        self._scan_worker.progress.connect(self.status_label.setText)
        self._scan_worker.finished.connect(self._on_scan_done)
        self._scan_worker.error.connect(self._on_worker_error)
        self._scan_worker.start()

    def _on_scan_done(self, entries: list, duplicates: list, unmatched: list):
        self._entries = entries
        self._duplicates = duplicates
        self._unmatched = unmatched
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            source_name = entry.source_path.name if entry.archive_member is None else f"{entry.source_path.name} :: {Path(entry.archive_member).name}"
            self.table.setItem(row, 0, QTableWidgetItem(source_name))
            self.table.setItem(row, 1, QTableWidgetItem(entry.output_name))
            self.table.setItem(row, 2, QTableWidgetItem(entry.source_kind))
            self.table.setItem(row, 3, QTableWidgetItem(entry.match_source))
            if getattr(entry, "is_english_translation", False):
                region = "Translated"
            else:
                region = next((token for token in ("USA", "Europe", "Japan") if f"({token})" in entry.canonical_name), "Other")
            self.table.setItem(row, 4, QTableWidgetItem(region))

        self._refresh_build_button()
        self.status_label.setText(
            f"{len(entries)} unique game(s) selected, "
            f"{len(duplicates)} duplicate candidate(s) skipped, "
            f"{len(unmatched)} unmatched file(s)."
        )

    def _on_worker_error(self, msg: str):
        self._refresh_build_button()
        QMessageBox.critical(self, "ROM Collection Error", msg)
        self.status_label.setText("Operation failed")

    def _on_bucket_toggle(self):
        self.bucket_count_spin.setEnabled(self.bucket_folders_check.isChecked())

    def _refresh_build_button(self):
        self.build_btn.setEnabled(bool(self._entries) or (self.include_unmatched_check.isChecked() and bool(self._unmatched)))

    def _build(self):
        include_unmatched = self.include_unmatched_check.isChecked()
        if not self._entries and not (include_unmatched and self._unmatched):
            return
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder for Collection")
        if not folder:
            return
        output_folder = Path(folder)
        reply = QMessageBox.question(
            self,
            "Build ROM Collection",
            f"Copy {len(self._entries)} unique game(s) into:\n{output_folder}\n\n"
            f"{len(self._unmatched) if include_unmatched else 0} unmatched file(s) will "
            f"{'also be copied into' if include_unmatched else 'be skipped from'} "
            f"the 'unmatched files' subfolder.\n"
            f"Matched games will {'be split into ' + str(self.bucket_count_spin.value()) + ' letter-range folders' if self.bucket_folders_check.isChecked() else 'be copied flat into the output folder'}.\n"
            f"Zipped ROMs will be {'extracted' if self.unzip_check.isChecked() else 'copied as .zip files'}.\n"
            f"Proceed?",
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
            folder_count=self.bucket_count_spin.value() if self.bucket_folders_check.isChecked() else 1,
        )
        self._build_worker.progress.connect(self.status_label.setText)
        self._build_worker.finished.connect(self._on_build_done)
        self._build_worker.error.connect(self._on_worker_error)
        self._build_worker.start()

    def _on_build_done(self, written: list):
        self.build_btn.setEnabled(True)
        self.status_label.setText(f"Wrote {len(written)} file(s) to the collection folder.")
        QMessageBox.information(self, "ROM Collection Complete", f"Created {len(written)} file(s).")

    def save_ui_state(self) -> dict:
        return {
            "folder": self.folder_edit.text(),
            "system": self.system_combo.currentText(),
            "unzip": self.unzip_check.isChecked(),
            "include_unmatched": self.include_unmatched_check.isChecked(),
            "bucket_folders": self.bucket_folders_check.isChecked(),
            "bucket_count": self.bucket_count_spin.value(),
        }

    def load_ui_state(self, state: dict):
        self.folder_edit.setText(state.get("folder", ""))
        self.unzip_check.setChecked(bool(state.get("unzip", False)))
        self.include_unmatched_check.setChecked(bool(state.get("include_unmatched", False)))
        self.bucket_folders_check.setChecked(bool(state.get("bucket_folders", False)))
        self.bucket_count_spin.setValue(int(state.get("bucket_count", 4)))
        self._on_bucket_toggle()
        system = state.get("system", "")
        if system:
            self.system_combo.setCurrentText(system)
