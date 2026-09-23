from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config import load_config, save_config
from rom_installer import (
    ROM_FORMAT_OPTIONS,
    available_systems_for_profiles,
    build_install_plan,
    build_install_plans,
    fetch_rom_catalog,
    group_multidisc_roms,
    install_rom,
    profile_rom_format,
    repair_installed_files,
    resolve_profile_rom_folder,
)

# Device types that support the "Sanitize / Repair Installed Files" action.
REPAIRABLE_DEVICE_TYPES = {"PSIO", "OPL", "GDEMU", "OPENMENU"}


def _fmt_size(num_bytes: int) -> str:
    if num_bytes <= 0:
        return ""
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.0f} KB"
    if num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"


class CatalogFetchWorker(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, system: str, search: str, parent=None):
        super().__init__(parent)
        self.system = system
        self.search = search

    def run(self):
        try:
            self.finished.emit(fetch_rom_catalog(self.system, self.search))
        except Exception as exc:
            self.error.emit(str(exc) or exc.__class__.__name__)


class InstallWorker(QThread):
    progress = pyqtSignal(int, int)            # bytes downloaded / total for current item
    item_started = pyqtSignal(int, int, str)   # index (1-based), total, display name
    item_error = pyqtSignal(int, int, str, str)  # index, total, display name, message
    finished = pyqtSignal(int, int, list)      # ok count, fail count, all written paths

    def __init__(self, plans, parent=None):
        super().__init__(parent)
        self.plans = plans

    def run(self):
        ok = 0
        fail = 0
        all_paths: list[str] = []
        total = len(self.plans)
        for idx, plan in enumerate(self.plans, 1):
            self.item_started.emit(idx, total, plan.display_name)
            try:
                paths = install_rom(plan, progress_callback=self.progress.emit)
                all_paths.extend(str(p) for p in paths)
                ok += 1
            except Exception as exc:
                fail += 1
                self.item_error.emit(idx, total, plan.display_name, str(exc) or exc.__class__.__name__)
        self.finished.emit(ok, fail, all_paths)


class SanitizeWorker(QThread):
    finished = pyqtSignal(list)   # list of (old_path, new_path) renames
    error = pyqtSignal(str)

    def __init__(self, profile: dict, system: str, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.system = system

    def run(self):
        try:
            self.finished.emit(repair_installed_files(self.profile, self.system))
        except Exception as exc:
            self.error.emit(str(exc) or exc.__class__.__name__)


class RomInstallerTab(QWidget):
    def __init__(self, profiles_tab):
        super().__init__()
        self.profiles_tab = profiles_tab
        self._profiles: list[dict] = []
        self._roms: list[dict] = []
        self._fetch_worker: CatalogFetchWorker | None = None
        self._install_worker: InstallWorker | None = None
        self._sanitize_worker: SanitizeWorker | None = None
        self._init_ui()
        self.refresh_profiles()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        top.addWidget(self.profile_combo, 2)

        top.addWidget(QLabel("System:"))
        self.system_combo = QComboBox()
        self.system_combo.currentIndexChanged.connect(self._on_system_changed)
        top.addWidget(self.system_combo)

        top.addWidget(QLabel("Format:"))
        self.format_combo = QComboBox()
        for value, label in ROM_FORMAT_OPTIONS:
            self.format_combo.addItem(label, value)
        self.format_combo.currentIndexChanged.connect(
            lambda _idx: self._populate_table(self._roms) if self._roms else None
        )
        top.addWidget(self.format_combo)
        layout.addLayout(top)

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search catalog by game name or filename...")
        self.search_edit.returnPressed.connect(self.load_catalog)
        search_row.addWidget(self.search_edit, 1)
        self.fetch_btn = QPushButton("Fetch Catalog")
        self.fetch_btn.clicked.connect(self.load_catalog)
        search_row.addWidget(self.fetch_btn)
        self.install_btn = QPushButton("Install Selected")
        self.install_btn.clicked.connect(self.install_selected)
        search_row.addWidget(self.install_btn)
        self.sanitize_btn = QPushButton("Sanitize Installed Files")
        self.sanitize_btn.setToolTip(
            "PSIO: rename installed files/folders that break PSIO's limits\n"
            "(filenames > 60 chars or non-ASCII), keeping .bin/.cu2 aligned.\n"
            "OPL: backfill POPStarter APPS/ app folders (launcher + title.cfg)\n"
            "for installed VCDs so PS1 games show on OPL's Apps page."
        )
        self.sanitize_btn.clicked.connect(self.sanitize_installed)
        search_row.addWidget(self.sanitize_btn)
        layout.addLayout(search_row)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["System", "Name", "Filename", "Size", "Install Format", "ROM ID"]
        )
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(0, 80)
        self.table.setColumnWidth(3, 90)
        self.table.setColumnWidth(4, 120)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(lambda _idx: self.install_selected())
        layout.addWidget(self.table, 1)

    def refresh_profiles(self):
        current = self.profile_combo.currentText() or load_config().get(
            "last_installer_profile", ""
        )
        self._profiles = self.profiles_tab.get_profiles()
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for profile in self._profiles:
            self.profile_combo.addItem(profile.get("name", "Profile"), profile)
        if current:
            idx = self.profile_combo.findText(current)
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)
        self.profile_combo.blockSignals(False)
        self._on_profile_changed()

    def _current_profile(self) -> dict | None:
        idx = self.profile_combo.currentIndex()
        if idx < 0:
            return None
        profile = self.profile_combo.itemData(idx)
        return profile if isinstance(profile, dict) else None

    def _on_profile_changed(self):
        profile = self._current_profile()
        name = self.profile_combo.currentText().strip()
        if name:
            cfg = load_config()
            if cfg.get("last_installer_profile") != name:
                cfg["last_installer_profile"] = name
                save_config(cfg)
        systems = available_systems_for_profiles([profile]) if profile else []
        current = self.system_combo.currentText()
        self.system_combo.blockSignals(True)
        self.system_combo.clear()
        self.system_combo.addItems(systems)
        if current:
            idx = self.system_combo.findText(current)
            if idx >= 0:
                self.system_combo.setCurrentIndex(idx)
        self.system_combo.blockSignals(False)
        self._on_system_changed()
        # The repair action is device-specific (PSIO filename limits, OPL
        # POPStarter backfill), so gate the button to supported devices.
        self.sanitize_btn.setEnabled(self._repair_supported(profile))

    def _on_system_changed(self):
        profile = self._current_profile()
        system = self.system_combo.currentText()
        fmt = profile_rom_format(profile or {}, system) if profile and system else "auto"
        idx = self.format_combo.findData(fmt)
        self.format_combo.setCurrentIndex(idx if idx >= 0 else 0)
        if self._roms:
            self._populate_table(self._roms)

    def load_catalog(self):
        profile = self._current_profile()
        system = self.system_combo.currentText()
        if not profile or not system:
            QMessageBox.warning(self, "ROM Installer", "Choose a profile and system first.")
            return
        if self._fetch_worker and self._fetch_worker.isRunning():
            return
        self.fetch_btn.setEnabled(False)
        self.status_label.setText("Fetching ROM catalog...")
        self._fetch_worker = CatalogFetchWorker(system, self.search_edit.text().strip(), self)
        self._fetch_worker.finished.connect(self._on_catalog_loaded)
        self._fetch_worker.error.connect(self._on_catalog_error)
        self._fetch_worker.start()

    def _on_catalog_loaded(self, roms: list[dict]):
        self.fetch_btn.setEnabled(True)
        self._roms = roms
        self._populate_table(roms)

    def _on_catalog_error(self, message: str):
        self.fetch_btn.setEnabled(True)
        self.status_label.setText("Catalog fetch failed.")
        QMessageBox.critical(self, "ROM Installer", message)

    def _populate_table(self, roms: list[dict]):
        profile = self._current_profile()
        system = self.system_combo.currentText()
        override = str(self.format_combo.currentData() or "auto")
        display_roms = group_multidisc_roms(profile or {}, roms, system, override)
        self.table.setRowCount(0)
        for rom in display_roms:
            row = self.table.rowCount()
            self.table.insertRow(row)
            try:
                plan = build_install_plan(profile or {}, rom, system, override)
                fmt = plan.format_label
            except Exception:
                plan = None
                fmt = ""

            members = rom.get("disc_members") or []
            name = rom.get("name") or rom.get("filename", "")
            filename = rom.get("filename", "")
            if len(members) > 1:
                name = f"{name} ({len(members)} discs)"
                filename = (
                    f"{len(members)} discs in one folder"
                    if rom.get("install_members")
                    else f"{len(members)} discs combined"
                )
            values = [
                rom.get("system", ""),
                name,
                filename,
                _fmt_size(int(rom.get("size") or 0)),
                fmt,
                rom.get("rom_id") or rom.get("title_id", ""),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, col, item)
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, rom)
        self.status_label.setText(f"{len(roms)} ROM(s)")

    def _selected_rows(self) -> list[int]:
        return sorted({idx.row() for idx in self.table.selectionModel().selectedRows()})

    def install_selected(self):
        profile = self._current_profile()
        if not profile:
            return
        rows = self._selected_rows()
        if not rows:
            return

        override = str(self.format_combo.currentData() or "auto")
        system = self.system_combo.currentText()
        plans = []
        errors = []
        for row in rows:
            rom_item = self.table.item(row, 0)
            rom = rom_item.data(Qt.ItemDataRole.UserRole) if rom_item else None
            if not isinstance(rom, dict):
                continue
            try:
                # One row can expand to several plans (multi-disc groups that
                # install disc-by-disc into a shared game folder).
                plans.extend(build_install_plans(profile, rom, system, override))
            except Exception as exc:
                errors.append(f"{rom.get('name') or rom.get('filename', '?')}: {exc}")
        if not plans:
            QMessageBox.critical(
                self, "ROM Installer", "No installable ROMs.\n" + "\n".join(errors)
            )
            return

        if len(plans) == 1:
            p = plans[0]
            prompt = f"Install {p.display_name} as {p.format_label} to:\n{p.target_path}"
        else:
            preview = "\n".join(f"  • {p.display_name} ({p.format_label})" for p in plans[:15])
            more = f"\n  … and {len(plans) - 15} more" if len(plans) > 15 else ""
            # All discs of one game land in the same folder — show it.
            parents = {str(p.target_path.parent) for p in plans}
            dest = f"\n\nInto:\n{parents.pop()}" if len(parents) == 1 else ""
            prompt = f"Install {len(plans)} ROMs (one at a time)?\n\n{preview}{more}{dest}"
        reply = QMessageBox.question(
            self,
            "Install ROM",
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._install_errors: list[str] = []
        self.install_btn.setEnabled(False)
        self.fetch_btn.setEnabled(False)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self._install_worker = InstallWorker(plans, self)
        self._install_worker.progress.connect(self._on_install_progress)
        self._install_worker.item_started.connect(self._on_install_item_started)
        self._install_worker.item_error.connect(self._on_install_item_error)
        self._install_worker.finished.connect(self._on_install_finished)
        self._install_worker.start()

    def _on_install_item_started(self, idx: int, total: int, name: str):
        self._install_prefix = f"[{idx}/{total}] " if total > 1 else ""
        # Reset to busy/indeterminate for each item — the server converts
        # CHD/RVZ -> ISO before any byte flows, so the animated bar shows work.
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setValue(0)
        self.status_label.setText(f"{self._install_prefix}Preparing {name}...")

    def _on_install_progress(self, downloaded: int, total: int):
        prefix = getattr(self, "_install_prefix", "")
        # First byte arrived -> conversion done, real download underway.
        if total > 0:
            pct = int(downloaded * 100 / total)
            if self.progress_bar.maximum() == 0:
                self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(pct)
            self.status_label.setText(
                f"{prefix}Downloading... {_fmt_size(downloaded)} / {_fmt_size(total)} ({pct}%)"
            )
        else:
            # Unknown total (no Content-Length) -> stay busy, show bytes.
            self.status_label.setText(f"{prefix}Downloading... {_fmt_size(downloaded)}")

    def _on_install_item_error(self, idx: int, total: int, name: str, message: str):
        self._install_errors.append(f"{name}: {message}")

    def _on_install_finished(self, ok: int, fail: int, paths: list[str]):
        self.install_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.progress_bar.hide()
        errors = getattr(self, "_install_errors", [])
        if fail:
            self.status_label.setText(f"Installed {ok}, failed {fail}.")
            body = f"Installed {ok} ROM(s), {fail} failed.\n\nFailures:\n" + "\n".join(errors[:20])
            QMessageBox.warning(self, "ROM Installer", body)
        else:
            self.status_label.setText(f"Installed {ok} ROM(s), {len(paths)} file(s).")
            QMessageBox.information(
                self,
                "ROM Installer",
                f"Installed {ok} ROM(s):\n" + "\n".join(paths[:20]),
            )

    def _repair_supported(self, profile: dict | None) -> bool:
        if not profile:
            return False
        return (
            str(profile.get("device_type", "")).strip().upper()
            in REPAIRABLE_DEVICE_TYPES
        )

    def sanitize_installed(self):
        profile = self._current_profile()
        system = self.system_combo.currentText()
        if not profile or not system:
            QMessageBox.warning(self, "ROM Installer", "Choose a profile and system first.")
            return
        device_type = str(profile.get("device_type", "")).strip().upper()
        if device_type not in REPAIRABLE_DEVICE_TYPES:
            QMessageBox.information(
                self,
                "ROM Installer",
                "Repair is available for PSIO (filename limits), OPL\n"
                "(POPStarter APPS/ app-folder backfill) and GDEMU / openMenu\n"
                "(folder layout + menu game list) profiles.",
            )
            return
        try:
            root = resolve_profile_rom_folder(profile, system)
        except Exception:
            root = None
        if not root or not Path(root).is_dir():
            QMessageBox.warning(
                self, "ROM Installer", "Profile ROM folder not found:\n" + str(root or "")
            )
            return

        if device_type == "OPL":
            prompt = (
                "Scan this profile's POPS/ folder and, for every installed PS1\n"
                ".VCD, create its APPS/ app folder (renamed POPSTARTER.ELF +\n"
                "title.cfg) so the game appears on OPL's Apps page?\n\n"
                "Requires POPSTARTER.ELF in POPS/.  Safe to run repeatedly.\n\n"
                f"Folder:\n{root}"
            )
        elif device_type in {"GDEMU", "OPENMENU"}:
            prompt = (
                "Bring every game folder on this card up to the standard GDEMU\n"
                "layout (disc.gdi + trackNN.* filenames, name.txt and the other\n"
                "metadata caches), renumber the game folders so 02, 03, ... run\n"
                "in alphabetical order, then rebuild the menu's game list?\n\n"
                "Folder 01 keeps its place — only game folders are renumbered.\n"
                "The list is also patched into the menu image in folder 01 — that\n"
                "is the copy the console reads, and without it a newly installed\n"
                "game never appears in the menu.  The original bytes are backed\n"
                "up first, and folder 01 is otherwise left alone.\n\n"
                f"Folder:\n{root}"
            )
        else:
            prompt = (
                "Scan this profile's ROM folder and rename any files or folders\n"
                "that break PSIO's limits (filenames > 60 chars or non-ASCII)?\n\n"
                "Each game's .bin/.cu2 pair is kept on a matching stem and\n"
                "MULTIDISC.LST is rewritten to match.\n\n"
                f"Folder:\n{root}"
            )
        reply = QMessageBox.question(
            self,
            "Sanitize Installed Files",
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.sanitize_btn.setEnabled(False)
        self.status_label.setText("Sanitizing installed files...")
        self._sanitize_worker = SanitizeWorker(profile, system, self)
        self._sanitize_worker.finished.connect(self._on_sanitize_finished)
        self._sanitize_worker.error.connect(self._on_sanitize_error)
        self._sanitize_worker.start()

    def _on_sanitize_finished(self, renames: list):
        profile = self._current_profile()
        self.sanitize_btn.setEnabled(self._repair_supported(profile))
        is_opl = (
            str((profile or {}).get("device_type", "")).strip().upper() == "OPL"
        )
        count = len(renames)
        if count:
            if is_opl:
                self.status_label.setText(f"Repaired {count} item(s).")
                preview = "\n".join(
                    f"{Path(vcd).name} -> {Path(elf).name}" for vcd, elf in renames[:25]
                )
                more = f"\n... and {count - 25} more" if count > 25 else ""
                QMessageBox.information(
                    self,
                    "ROM Installer",
                    f"Updated {count} item(s) (added APPS/ app folders, "
                    f"removed stale ones):\n\n{preview}{more}",
                )
            else:
                self.status_label.setText(f"Sanitized {count} item(s).")
                preview = "\n".join(
                    f"{Path(old).name} -> {Path(new).name}" for old, new in renames[:25]
                )
                more = f"\n... and {count - 25} more" if count > 25 else ""
                QMessageBox.information(
                    self,
                    "ROM Installer",
                    f"Renamed {count} item(s):\n\n{preview}{more}",
                )
        else:
            if is_opl:
                self.status_label.setText("All PS1 games already registered.")
                QMessageBox.information(
                    self,
                    "ROM Installer",
                    "Every VCD already has its POPStarter APPS/ app folder.",
                )
            else:
                self.status_label.setText("No problematic files found.")
                QMessageBox.information(
                    self, "ROM Installer", "No files needed renaming. Everything is PSIO-safe."
                )

    def _on_sanitize_error(self, message: str):
        self.sanitize_btn.setEnabled(self._repair_supported(self._current_profile()))
        self.status_label.setText("Sanitize failed.")
        QMessageBox.critical(self, "ROM Installer", message)
