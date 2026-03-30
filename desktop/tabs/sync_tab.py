import re
from pathlib import Path

import requests
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QProgressDialog,
    QHeaderView,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor

from config import (
    STATUS_COLORS,
    STATUS_LABELS,
    format_display_game_name,
    get_api_headers,
    get_base_url,
)


_PS_TITLE_ID_RE = re.compile(r"^[A-Z]{4}\d{5}$")


class ScanWorker(QThread):
    result_ready = pyqtSignal(list)
    error = pyqtSignal(str)
    progress = pyqtSignal(str, int, int)

    def __init__(self, profiles: list[dict], base_url: str, headers: dict, enable_auto_normalize: bool = True):
        super().__init__()
        self.profiles = profiles
        self.base_url = base_url
        self.headers = headers
        self.enable_auto_normalize = enable_auto_normalize

    def _emit_progress(self, message: str, current=None, total=None):
        if self.isInterruptionRequested():
            raise InterruptedError("Scan cancelled by user.")
        cur = 0 if current is None else int(current)
        tot = 0 if total is None else int(total)
        self.progress.emit(message, cur, tot)

    def run(self):
        try:
            from sync_engine import scan_profile, compare_with_server
            all_saves = []
            systems_filter: set[str] = set()
            for profile in self.profiles:
                self._emit_progress(f"Scanning profile '{profile.get('name', '')}'…")
                all_saves.extend(scan_profile(
                    profile,
                    progress_callback=self._emit_progress,
                    enable_auto_normalize=self.enable_auto_normalize,
                ))
                # Single-system profiles (Generic / Everdrive / MEGA EverDrive)
                # store their system as profile["system"] (a plain string).
                single = profile.get("system", "").strip().upper()
                if single:
                    systems_filter.add(single)
                elif "systems" in profile:
                    # New multi-system format: list of {system, enabled, ...}
                    for s in profile["systems"]:
                        if s.get("enabled", True):
                            systems_filter.add(s["system"].upper())
                else:
                    # Old format: systems_filter is a plain list of system codes
                    pf = set(s.upper() for s in (profile.get("systems_filter") or []))
                    systems_filter |= pf
            self._emit_progress("Comparing with server…", 0, max(len(all_saves), 1))
            statuses = compare_with_server(
                all_saves, self.base_url, self.headers,
                systems_filter=systems_filter or None,
                progress_callback=self._emit_progress,
            )
            self.result_ready.emit(statuses)
        except InterruptedError:
            self.error.emit("__SCAN_CANCELLED__")
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())


class SyncTab(QWidget):
    def __init__(self, profiles_tab):
        super().__init__()
        self.profiles_tab = profiles_tab
        self._statuses: list = []
        self._saved_profile_name = ""
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Profile selector row
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(220)
        self.profile_combo.setToolTip("Select which profile to scan and sync")
        self.profile_combo.currentTextChanged.connect(self._on_profile_changed)
        profile_row.addWidget(self.profile_combo)
        refresh_profiles_btn = QPushButton("↺")
        refresh_profiles_btn.setFixedWidth(30)
        refresh_profiles_btn.setToolTip("Refresh profile list")
        refresh_profiles_btn.clicked.connect(self._refresh_profile_list)
        profile_row.addWidget(refresh_profiles_btn)
        profile_row.addStretch()
        layout.addLayout(profile_row)

        # Action buttons row
        btn_row = QHBoxLayout()
        self.scan_btn = QPushButton("Scan Profile")
        self.scan_btn.clicked.connect(self._scan)
        self.sync_all_btn = QPushButton("Sync All")
        self.sync_all_btn.clicked.connect(self._sync_all)
        self.sync_all_btn.setEnabled(False)
        self.sync_sel_btn = QPushButton("Sync Selected")
        self.sync_sel_btn.clicked.connect(self._sync_selected)
        self.sync_sel_btn.setEnabled(False)
        btn_row.addWidget(self.scan_btn)
        btn_row.addWidget(self.sync_all_btn)
        btn_row.addWidget(self.sync_sel_btn)
        self.auto_normalize_check = QCheckBox("Auto normalize names for sync")
        self.auto_normalize_check.setChecked(True)
        self.auto_normalize_check.setToolTip(
            "When checked, scan uses the ROM Normalizer matching logic to choose the server slot\n"
            "without renaming local files. When unchecked, sync uses raw filename-based title IDs."
        )
        btn_row.addWidget(self.auto_normalize_check)
        self.clear_mappings_btn = QPushButton("Clear Sync Mappings")
        self.clear_mappings_btn.setToolTip(
            "Forget locally stored legacy/canonical slot decisions and recompute them on the next scan."
        )
        self.clear_mappings_btn.clicked.connect(self._clear_sync_mappings)
        btn_row.addWidget(self.clear_mappings_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Filter row
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("System:"))
        self.system_filter_combo = QComboBox()
        self.system_filter_combo.addItem("All")
        self.system_filter_combo.setMinimumWidth(100)
        self.system_filter_combo.currentTextChanged.connect(self._apply_filter)
        filter_row.addWidget(self.system_filter_combo)
        filter_row.addWidget(QLabel("Status:"))
        self.status_filter_combo = QComboBox()
        self.status_filter_combo.addItems(["All", "Local newer", "Server newer",
                                           "Not on server", "Server only", "Conflict",
                                           "Mapping conflict", "Local duplicates differ",
                                           "Up to date", "Error"])
        self.status_filter_combo.currentTextChanged.connect(self._apply_filter)
        filter_row.addWidget(self.status_filter_combo)
        filter_row.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter by game name or title ID…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.search_edit, 1)
        layout.addLayout(filter_row)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["System", "Game", "Title ID", "Local File", "Server Status", "Action"]
        )
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.table)

        self.status_label = QLabel("Select a profile and click Scan to begin.")
        layout.addWidget(self.status_label)

        # Populate on first show
        self._refresh_profile_list()

    def _refresh_profile_list(self):
        """Reload profiles from ProfilesTab into the dropdown."""
        profiles = self.profiles_tab.get_profiles()
        current = self.profile_combo.currentText().strip() or self._saved_profile_name
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for p in profiles:
            self.profile_combo.addItem(p.get("name", ""), userData=p)
        # Restore previous selection if still present
        idx = self.profile_combo.findText(current)
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
            self._saved_profile_name = current
        elif self.profile_combo.count() > 0:
            self._saved_profile_name = self.profile_combo.currentText()
        self.profile_combo.blockSignals(False)
        if not profiles:
            self.status_label.setText("No profiles configured — add profiles in the Sync Profiles tab.")

    def _on_profile_changed(self, name: str):
        self._saved_profile_name = name.strip()

    def _scan(self):
        self._refresh_profile_list()
        if self.profile_combo.count() == 0:
            QMessageBox.information(
                self, "No Profiles",
                "Add sync profiles in the 'Sync Profiles' tab first."
            )
            return

        profile = self.profile_combo.currentData()
        if not profile:
            return

        self.scan_btn.setEnabled(False)
        self.sync_all_btn.setEnabled(False)
        self.sync_sel_btn.setEnabled(False)
        self.status_label.setText(f"Scanning profile '{profile.get('name', '')}'…")
        self.table.setRowCount(0)
        self._scan_progress = QProgressDialog("Scanning profile…", "Cancel", 0, 0, self)
        self._scan_progress.setWindowTitle("Scanning")
        self._scan_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._scan_progress.setMinimumDuration(0)
        self._scan_progress.setAutoClose(False)
        self._scan_progress.setAutoReset(False)
        self._scan_progress.canceled.connect(self._cancel_scan)
        self._scan_progress.show()

        self._worker = ScanWorker(
            [profile],
            get_base_url(),
            get_api_headers(),
            enable_auto_normalize=self.auto_normalize_check.isChecked(),
        )
        self._worker.result_ready.connect(self._on_scan_done)
        self._worker.error.connect(self._on_scan_error)
        self._worker.progress.connect(self._on_scan_progress)
        self._worker.start()

    def _cancel_scan(self):
        worker = getattr(self, "_worker", None)
        if worker and worker.isRunning():
            worker.requestInterruption()
            self.status_label.setText("Cancelling scan…")
            if hasattr(self, "_scan_progress"):
                self._scan_progress.setLabelText("Cancelling scan…")

    def _clear_sync_mappings(self):
        from sync_engine import clear_scan_cache, clear_slot_mappings

        reply = QMessageBox.question(
            self,
            "Clear Sync Cache",
            "Clear stored sync slot mappings and scan cache?\n\n"
            "This does not delete server saves. It only forgets the local decisions and cached\n"
            "ROM match results used during scanning. Re-scan after clearing.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        clear_slot_mappings()
        clear_scan_cache()
        self.status_label.setText("Sync mappings and scan cache cleared. Re-scan the profile.")

    def _on_scan_done(self, statuses: list):
        self._apply_display_names(statuses)
        self._statuses = statuses
        self._populate_table(statuses)
        self.scan_btn.setEnabled(True)
        self.sync_all_btn.setEnabled(True)
        self.sync_sel_btn.setEnabled(True)
        if hasattr(self, "_scan_progress"):
            self._scan_progress.close()
        local_count = sum(1 for s in statuses if s.status != "server_only")
        server_only_count = sum(1 for s in statuses if s.status == "server_only")
        if local_count == 0 and server_only_count > 0:
            profile = self.profile_combo.currentData() or {}
            folder = profile.get("save_folder") or profile.get("path", "")
            self.status_label.setText(
                f"⚠ No local saves found in profile folder — is the device connected? "
                f"({server_only_count} saves exist on server only)"
            )
        else:
            parts = [f"{local_count} local saves"]
            if server_only_count:
                parts.append(f"{server_only_count} server-only")
            self.status_label.setText("Found " + ", ".join(parts))

    def _apply_display_names(self, statuses: list) -> None:
        """Replace raw product-code labels with readable names when possible.

        MemCard Pro and some emulator profiles discover local saves by product
        code only (for example ``SCUS-94403``). The Sync tab should still show
        the human-readable game name, so we batch-resolve those codes through
        the server's existing `/titles/names` lookup endpoint after scan.
        """
        codes: list[str] = []
        seen: set[str] = set()
        for st in statuses:
            save = st.save
            is_ps_code = _PS_TITLE_ID_RE.match(save.title_id)
            is_gc_code = save.system == "GC" and save.title_id.startswith("GC_")
            if not is_ps_code and not is_gc_code:
                continue
            if is_ps_code and not self._looks_like_raw_code_label(save.game_name, save.title_id):
                continue
            if save.title_id not in seen:
                seen.add(save.title_id)
                codes.append(save.title_id)

        if not codes:
            return

        try:
            resp = requests.post(
                f"{get_base_url()}/api/v1/titles/names",
                headers=get_api_headers(),
                json={"codes": codes},
                timeout=10,
            )
            resp.raise_for_status()
            names = resp.json().get("names", {})
        except requests.RequestException:
            return

        for st in statuses:
            resolved = names.get(st.save.title_id, "").strip()
            if resolved:
                st.save.game_name = format_display_game_name(resolved, st.save.system)

    def _looks_like_raw_code_label(self, label: str, title_id: str) -> bool:
        """Return True when the current display label is just the product code."""
        compact = re.sub(r"[^A-Z0-9]", "", label.upper())
        return compact == title_id.upper()

    def _on_scan_error(self, msg: str):
        self.scan_btn.setEnabled(True)
        self.sync_all_btn.setEnabled(False)
        self.sync_sel_btn.setEnabled(False)
        if hasattr(self, "_scan_progress"):
            self._scan_progress.close()
        if msg == "__SCAN_CANCELLED__":
            self.status_label.setText("Scan cancelled")
            return
        QMessageBox.critical(self, "Scan Error", msg)
        self.status_label.setText("Scan failed")

    def _on_scan_progress(self, message: str, current: int, total: int):
        self.status_label.setText(message)
        if not hasattr(self, "_scan_progress"):
            return
        self._scan_progress.setLabelText(message)
        if total and total > 0:
            if self._scan_progress.maximum() == 0:
                self._scan_progress.setRange(0, total)
            else:
                self._scan_progress.setMaximum(total)
            self._scan_progress.setValue(max(0, min(current, total)))
        else:
            if self._scan_progress.maximum() != 0:
                self._scan_progress.setRange(0, 0)

    def _populate_table(self, statuses: list):
        # Sort by system first, then game name — keeps all SNES/GBA/etc. together
        sorted_statuses = sorted(
            enumerate(statuses),
            key=lambda x: (x[1].save.system.upper(), x[1].save.game_name.lower()),
        )

        # Rebuild system filter combo from actual results (preserve selection)
        prev_system = self.system_filter_combo.currentText()
        self.system_filter_combo.blockSignals(True)
        self.system_filter_combo.clear()
        self.system_filter_combo.addItem("All")
        seen_systems: list[str] = []
        for _, st in sorted_statuses:
            s = st.save.system
            if s and s not in seen_systems:
                seen_systems.append(s)
                self.system_filter_combo.addItem(s)
        idx = self.system_filter_combo.findText(prev_system)
        self.system_filter_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.system_filter_combo.blockSignals(False)

        self.table.setRowCount(0)
        for i, st in sorted_statuses:
            save = st.save
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(save.system))
            self.table.setItem(
                row,
                1,
                QTableWidgetItem(format_display_game_name(save.game_name, save.system)),
            )
            title_item = QTableWidgetItem(save.title_id)
            details = []
            if getattr(save, "legacy_title_id", "") and save.legacy_title_id != save.title_id:
                details.append(f"Legacy slot: {save.legacy_title_id}")
            if getattr(save, "canonical_title_id", "") and save.canonical_title_id != save.title_id:
                details.append(f"Canonical slot: {save.canonical_title_id}")
            if getattr(st, "mapping_note", ""):
                details.append(st.mapping_note)
            if details:
                title_item.setToolTip("\n".join(details))
            self.table.setItem(row, 2, title_item)
            # Local file column
            save_exists = getattr(save, "save_exists", True)
            if save.path is None:
                local_name = "(server only)"
                local_tooltip = "This save exists on the server but has no local file in any profile."
                local_color = QColor(140, 140, 140)
            elif not save_exists:
                local_name = "(no local save)"
                local_tooltip = f"ROM present on device — save will be placed at:\n{save.path}"
                local_color = QColor(160, 120, 40)
            else:
                local_name = save.path.name
                all_paths = [save.path, *getattr(save, "alternate_paths", [])]
                unique_paths = []
                for path in all_paths:
                    if path is not None and path not in unique_paths:
                        unique_paths.append(path)
                local_tooltip = "\n".join(str(path) for path in unique_paths)
                local_color = None
            local_item = QTableWidgetItem(local_name)
            if local_color:
                local_item.setForeground(local_color)
            if local_tooltip:
                local_item.setToolTip(local_tooltip)
            self.table.setItem(row, 3, local_item)

            status_label = STATUS_LABELS.get(st.status, st.status)
            status_item = QTableWidgetItem(status_label)
            color = STATUS_COLORS.get(st.status)
            if color:
                status_item.setForeground(color)
            self.table.setItem(row, 4, status_item)

            # Action buttons
            # Upload: only when local save actually exists
            can_upload = save_exists and save.path is not None
            # Download: server_only, conflict (keep server), server_newer without a local
            # save, or local_newer (force-download overwriting the newer local copy)
            needs_download_btn = st.status in ("conflict", "server_only", "local_newer", "local_duplicate_conflict") or (
                st.status == "server_newer" and not save_exists
            )
            action_needed = (
                st.status in ("conflict", "server_only", "not_on_server", "local_newer", "local_duplicate_conflict")
                or needs_download_btn
            )
            if action_needed:
                action_widget = QWidget()
                action_layout = QHBoxLayout(action_widget)
                action_layout.setContentsMargins(2, 2, 2, 2)
                action_layout.setSpacing(4)

                if st.status in ("conflict", "not_on_server") and can_upload:
                    lbl = "Keep Local" if st.status == "conflict" else "Upload"
                    upload_btn = QPushButton(lbl)
                    upload_btn.setFixedHeight(22)
                    upload_btn.clicked.connect(lambda _, idx=i: self._keep_local(idx))
                    action_layout.addWidget(upload_btn)

                if needs_download_btn and (st.server_hash or st.status == "server_only"):
                    lbl = "Keep Server" if st.status == "conflict" else "Download"
                    download_btn = QPushButton(lbl)
                    download_btn.setFixedHeight(22)
                    download_btn.clicked.connect(lambda _, idx=i: self._keep_server(idx))
                    action_layout.addWidget(download_btn)

                self.table.setCellWidget(row, 5, action_widget)

            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, i)

        self._apply_filter()

    def _apply_filter(self):
        system_filter = self.system_filter_combo.currentText()
        status_filter = self.status_filter_combo.currentText()
        search = self.search_edit.text().strip().lower()
        for row in range(self.table.rowCount()):
            system_item = self.table.item(row, 0)
            game_item   = self.table.item(row, 1)
            tid_item    = self.table.item(row, 2)
            status_item = self.table.item(row, 4)
            system = system_item.text() if system_item else ""
            game   = game_item.text()   if game_item   else ""
            tid    = tid_item.text()    if tid_item    else ""
            status = status_item.text() if status_item else ""
            match_system = system_filter == "All" or system == system_filter
            match_status = status_filter == "All" or status == status_filter
            match_search = not search or search in game.lower() or search in tid.lower()
            self.table.setRowHidden(row, not (match_system and match_status and match_search))

    def _selected_status_indices(self) -> list[int]:
        rows = sorted(set(idx.row() for idx in self.table.selectedIndexes()))
        result = []
        for row in rows:
            item = self.table.item(row, 0)
            if item:
                result.append(item.data(Qt.ItemDataRole.UserRole))
        return result

    def _show_context_menu(self, pos):
        item = self.table.itemAt(pos)
        if item is None:
            return

        row = item.row()
        row_item = self.table.item(row, 0)
        if row_item is None:
            return

        status_idx = row_item.data(Qt.ItemDataRole.UserRole)
        if status_idx is None:
            return

        # Right-clicking a row should operate on that row even if it was not
        # already part of the current selection.
        self.table.selectRow(row)
        st = self._statuses[status_idx]
        save_exists = getattr(st.save, "save_exists", True)
        has_local = st.save.path is not None and save_exists
        has_server = bool(st.server_hash) or st.status == "server_only"

        menu = QMenu(self)
        force_upload = menu.addAction("Force Upload")
        force_upload.setEnabled(has_local)
        force_download = menu.addAction("Force Download")
        force_download.setEnabled(has_server)

        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == force_upload and has_local:
            self._force_upload(status_idx)
        elif chosen == force_download and has_server:
            self._force_download(status_idx)

    def _sync_all(self):
        # Only sync rows currently visible (respects system/status/search filters)
        indices = []
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                item = self.table.item(row, 0)
                if item:
                    indices.append(item.data(Qt.ItemDataRole.UserRole))
        self._do_sync(indices)

    def _sync_selected(self):
        self._do_sync(self._selected_status_indices())

    def _do_sync(self, indices):
        from sync_engine import upload_save, download_save
        base_url = get_base_url()
        headers = get_api_headers()
        errors = []
        synced = 0
        skipped = 0

        indices = list(indices)
        progress = QProgressDialog("Syncing saves...", "Cancel", 0, len(indices), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()

        for n, idx in enumerate(indices):
            progress.setValue(n)
            if progress.wasCanceled():
                break
            st = self._statuses[idx]
            try:
                save_exists = getattr(st.save, "save_exists", True)
                if st.status in ("local_newer", "not_on_server") and st.save.path and save_exists:
                    upload_save(st.save.title_id, st.save.path, base_url, headers, system=st.save.system)
                    self._update_row_status(idx, "up_to_date")
                    synced += 1
                elif st.status == "server_newer" and st.save.path:
                    self._download_to_paths(st.save.title_id, [st.save.path, *getattr(st.save, "alternate_paths", [])], base_url, headers)
                    self._update_row_status(idx, "up_to_date", new_path=st.save.path)
                    synced += 1
                elif st.status == "server_only":
                    dest_path = self._resolve_download_path(st)
                    if dest_path:
                        self._download_to_paths(st.save.title_id, [dest_path], base_url, headers)
                        self._update_row_status(idx, "up_to_date", new_path=dest_path)
                        synced += 1
                    else:
                        skipped += 1  # can't resolve destination — use Download button
                elif st.status in ("conflict", "local_duplicate_conflict"):
                    skipped += 1  # conflicts need manual resolution
                # up_to_date / error: skip
            except Exception as e:
                errors.append(f"{st.save.title_id}: {e}")

        progress.setValue(len(indices))
        progress.close()

        msg = f"Synced {synced} saves."
        if skipped:
            msg += f"\n{skipped} item(s) skipped (conflicts / unresolvable server-only — use the action buttons)."
        if errors:
            msg += f"\n\nErrors:\n" + "\n".join(errors)
        QMessageBox.information(self, "Sync Complete", msg)

    def _update_row_status(self, status_idx: int, new_status: str, new_path: Path | None = None):
        """Update a single table row in-place after an upload or download.

        Mutates self._statuses[status_idx] and refreshes the corresponding
        table row without triggering a full re-scan.
        """
        st = self._statuses[status_idx]
        st.status = new_status
        if new_path is not None:
            st.save.path = new_path
            st.save.save_exists = True

        # Find the table row whose UserRole matches status_idx
        target_row = None
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == status_idx:
                target_row = row
                break
        if target_row is None:
            return

        # Update status column
        status_label = STATUS_LABELS.get(new_status, new_status)
        status_item = QTableWidgetItem(status_label)
        color = STATUS_COLORS.get(new_status)
        if color:
            status_item.setForeground(color)
        self.table.setItem(target_row, 4, status_item)

        # Update local file column
        save = st.save
        save_exists = getattr(save, "save_exists", True)
        if save.path is None:
            local_name = "(server only)"
            local_color = QColor(140, 140, 140)
        elif not save_exists:
            local_name = "(no local save)"
            local_color = QColor(160, 120, 40)
        else:
            local_name = save.path.name
            local_color = None
        local_item = QTableWidgetItem(local_name)
        if local_color:
            local_item.setForeground(local_color)
        self.table.setItem(target_row, 3, local_item)

        # Clear action buttons — row is now up_to_date (no actions needed)
        self.table.setCellWidget(target_row, 5, None)

    def _download_to_paths(
        self,
        title_id: str,
        paths: list[Path],
        base_url: str,
        headers: dict,
        system: str | None = None,
    ):
        from sync_engine import download_save

        unique_paths: list[Path] = []
        for path in paths:
            if path is not None and path not in unique_paths:
                unique_paths.append(path)
        if not unique_paths:
            raise ValueError("No destination paths resolved for download.")

        server_hash = None
        for i, path in enumerate(unique_paths):
            if i == 0:
                server_hash = download_save(title_id, path, base_url, headers, system=system)
            else:
                # Avoid re-downloading from the server for duplicate ROM locations.
                data = unique_paths[0].read_bytes()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        return server_hash

    def _keep_local(self, status_idx: int):
        from sync_engine import upload_save
        st = self._statuses[status_idx]
        if not st.save.path:
            QMessageBox.warning(self, "No Local File", "No local file found for this save.")
            return
        try:
            upload_save(
                st.save.title_id,
                st.save.path,
                get_base_url(),
                get_api_headers(),
                system=st.save.system,
                force=True,
            )
            self._update_row_status(status_idx, "up_to_date")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _force_upload(self, status_idx: int):
        """Upload the local save regardless of the current sync status."""
        st = self._statuses[status_idx]
        save_exists = getattr(st.save, "save_exists", True)
        if not st.save.path or not save_exists:
            QMessageBox.warning(self, "No Local File", "No local save exists for this row.")
            return
        reply = QMessageBox.question(
            self,
            "Force Upload",
            f"Force upload local save for '{st.save.game_name}' and overwrite the server copy if needed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._keep_local(status_idx)

    def _ps1_memcard_serial_dirname(self, title_id: str) -> str:
        """Convert a compact PS1 title ID like ``SLUS00594`` to ``SLUS-00594``."""
        compact = re.sub(r"[^A-Z0-9]", "", (title_id or "").upper())
        if _PS_TITLE_ID_RE.match(compact):
            return f"{compact[:4]}-{compact[4:]}"
        return compact or "UNKNOWN"

    def _finalize_memcard_pro_download(self, path: Path, game_name: str):
        """Write MemCard Pro companion metadata after a server download."""
        serial_dir = path.parent.name
        if not serial_dir:
            return
        if path.suffix.lower() == ".mc2":
            txt_path = path.parent / "name.txt"
        else:
            txt_name = re.sub(r'[<>:"/\\|?*]', "_", (game_name or "").strip()) or serial_dir
            txt_path = path.parent / f"{txt_name}.txt"
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.write_text((game_name or serial_dir).strip() + "\n", encoding="utf-8")

    def _find_rom_file(self, rom_folder: Path, game_name: str) -> Path | None:
        """Search rom_folder recursively for a ROM whose normalized stem matches game_name.

        Returns the full ROM file Path if found, or None.
        Used by Pocket profiles to get the exact ROM stem so the save filename matches.
        """
        import rom_normalizer as rn
        target = rn.normalize_name(game_name)
        if not target:
            return None
        for f in sorted(rom_folder.rglob("*")):
            if f.is_file() and f.suffix.lower() in rn.ROM_EXTENSIONS:
                if rn.normalize_name(f.stem) == target:
                    return f
        return None

    def _resolve_download_path(self, st) -> Path | None:
        """Return the correct local path for downloading a server-only save into the active profile.

        Uses the profile's device type and folder structure to mirror where
        the device would store this save file natively.
        Falls back to None if no profile is selected or path can't be resolved.
        """
        from sync_engine import (
            MISTER_FOLDER_MAP, POCKET_FOLDER_MAP,
            POCKET_OPENFPGA_FOLDER_MAP, RETROARCH_CORE_MAP,
        )

        profile = self.profile_combo.currentData()
        if not profile:
            return None

        device_type = profile.get("device_type", "Generic")
        save_root_str = profile.get("save_folder") or profile.get("path", "")
        if not save_root_str:
            return None
        save_root = Path(save_root_str)

        system = (st.save.system or "").upper()

        # Check per-system save folder override (new multi-system format)
        if "systems" in profile:
            sys_info = next(
                (s for s in profile["systems"] if s.get("system") == system), {}
            )
            override = sys_info.get("save_folder", "")
            if override:
                save_root = Path(override)

        # Build a clean filename from the server name or title_id slug
        raw_name = st.save.game_name or st.save.title_id
        filename_stem = re.sub(r'[<>:"/\\|?*]', "_", raw_name).strip()
        if not filename_stem:
            # Fall back to slug portion of title_id
            if "_" in st.save.title_id and not st.save.title_id[0].isdigit():
                filename_stem = st.save.title_id[len(system) + 1:]
            else:
                filename_stem = st.save.title_id
        # Use per-system save_ext if available (new format), else default .sav
        save_ext = ".sav"
        if "systems" in profile:
            sys_info = next(
                (s for s in profile["systems"] if s.get("system") == system), {}
            )
            save_ext = sys_info.get("save_ext", ".sav") or ".sav"
        filename = filename_stem + save_ext

        if device_type == "MemCard Pro":
            if system == "PS1":
                card_root = save_root / "MemoryCards" if (save_root / "MemoryCards").is_dir() else save_root
                serial_dir = self._ps1_memcard_serial_dirname(st.save.title_id)
                return card_root / serial_dir / f"{serial_dir}-1.mcd"
            if system == "PS2":
                card_root = save_root / "PS2" if (save_root / "PS2").is_dir() else save_root
                serial_dir = self._ps1_memcard_serial_dirname(st.save.title_id)
                return card_root / serial_dir / f"{serial_dir}-1.mc2"
            if system == "GC":
                # Title ID is GC_xxxx; reconstruct the DL-DOL-XXXX-USA folder.
                # We can't know the exact region suffix from the title_id alone,
                # so we look for an existing matching folder, or default to -USA.
                gc_code = st.save.title_id[3:].upper()  # e.g. "GBZE"
                existing = next(
                    (d for d in save_root.iterdir()
                     if d.is_dir() and d.name.upper().startswith(f"DL-DOL-{gc_code}-")),
                    None,
                ) if save_root.exists() else None
                if existing:
                    folder_name = existing.name
                else:
                    folder_name = f"DL-DOL-{gc_code}-USA"
                return save_root / folder_name / f"{folder_name}-1.raw"
            return None

        if device_type in ("Generic", "Everdrive"):
            return save_root / filename

        elif device_type == "MEGA EverDrive":
            # gamedata/<Game Name>/bram.srm — subfolder named after the game
            game_folder = re.sub(r'[<>:"/\\|?*]', "_", raw_name).strip() or filename_stem
            return save_root / game_folder / "bram.srm"

        elif device_type == "MiSTer":
            folder = next((k for k, v in MISTER_FOLDER_MAP.items() if v == system), system)
            return save_root / folder / filename

        elif device_type in ("Pocket", "Pocket (openFPGA)", "Analogue Pocket"):
            # Pocket saves mirror the ROM's subfolder inside the Assets tree.
            # E.g. ROM at  Assets/snes/common/all/A-F/game.sfc
            #      Save at Saves/snes/common/all/A-F/game.sav
            if device_type in ("Pocket", "Analogue Pocket"):
                sys_folder = next((k for k, v in POCKET_FOLDER_MAP.items() if v == system), system)
            else:
                sys_folder = next((k for k, v in POCKET_OPENFPGA_FOLDER_MAP.items() if v == system), system.lower())

            # Try to locate the matching ROM in the Assets folder.
            # When found, use the ROM's actual stem — not the server's game_name — so the
            # save filename exactly matches what the core expects.
            rom_folder_str = profile.get("path", "")
            direct_root = False
            if rom_folder_str:
                if "systems" in profile:
                    enabled = [s for s in profile["systems"] if s.get("enabled", True)]
                    if len(enabled) == 1 and enabled[0].get("system") == system:
                        search_root = Path(rom_folder_str)
                        direct_root = True
                    else:
                        search_root = Path(rom_folder_str) / sys_folder
                else:
                    search_root = Path(rom_folder_str) / sys_folder
                if search_root.exists():
                    rom_file = self._find_rom_file(search_root, raw_name)
                    if rom_file is not None:
                        try:
                            rel_subdir = rom_file.parent.relative_to(search_root)
                        except ValueError:
                            rel_subdir = Path()
                        if direct_root:
                            return save_root / rel_subdir / (rom_file.stem + save_ext)
                        return save_root / sys_folder / rel_subdir / (rom_file.stem + save_ext)

            # ROM not found on card — place save flat under system folder with sanitised name
            if direct_root:
                return save_root / filename
            return save_root / sys_folder / filename

        elif device_type == "RetroArch":
            core = next((k for k, v in RETROARCH_CORE_MAP.items() if v == system), system)
            return save_root / core / filename

        else:
            return save_root / filename

    def _keep_server(self, status_idx: int):
        from sync_engine import download_save
        st = self._statuses[status_idx]
        dest_path = st.save.path

        if dest_path is None:
            # Truly server-only (no local ROM/path) — resolve destination from profile structure
            dest_path = self._resolve_download_path(st)
            if dest_path is None:
                # Fall back to file dialog if resolution failed
                suggested = f"{st.save.title_id}.sav"
                dest_str, _ = QFileDialog.getSaveFileName(
                    self, f"Download {st.save.title_id}", suggested,
                    "Save Files (*.sav *.srm *.bin);;All Files (*)"
                )
                if not dest_str:
                    return
                dest_path = Path(dest_str)
            else:
                # Show the resolved path and let user confirm
                reply = QMessageBox.question(
                    self, "Download Save",
                    f"Download to:\n{dest_path}\n\nProceed?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
        else:
            # dest_path is already computed from the ROM scan (correct name + location)
            save_exists = getattr(st.save, "save_exists", True)
            dest_paths = [dest_path, *getattr(st.save, "alternate_paths", [])]
            if st.status == "local_newer":
                reply = QMessageBox.question(
                    self, "Overwrite Newer Local Save",
                    f"Your local save for {st.save.game_name} is newer than the server.\n"
                    f"Download from server anyway? This will overwrite your local save.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            elif st.status == "local_duplicate_conflict":
                unique_paths = []
                for p in dest_paths:
                    if p is not None and p not in unique_paths:
                        unique_paths.append(p)
                path_msg = "\n".join(str(p) for p in unique_paths)
                reply = QMessageBox.question(
                    self, "Overwrite Diverged Local Saves",
                    f"Local save copies for {st.save.game_name} do not match.\n\n"
                    f"Download from server and overwrite all copies at:\n{path_msg}\n\nProceed?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            elif not save_exists:
                # New download — confirm destination once
                unique_paths = []
                for p in dest_paths:
                    if p is not None and p not in unique_paths:
                        unique_paths.append(p)
                path_msg = "\n".join(str(p) for p in unique_paths)
                reply = QMessageBox.question(
                    self, "Download Save",
                    f"Download save for {st.save.game_name} to:\n{path_msg}\n\nProceed?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

        try:
            target_paths = [dest_path, *getattr(st.save, "alternate_paths", [])]
            self._download_to_paths(
                st.save.title_id,
                target_paths,
                get_base_url(),
                get_api_headers(),
                system=st.save.system,
            )
            profile = self.profile_combo.currentData() or {}
            if profile.get("device_type") == "MemCard Pro" and (st.save.system or "").upper() in {"PS1", "PS2"}:
                for path in target_paths:
                    if path is not None:
                        self._finalize_memcard_pro_download(path, st.save.game_name)
            self._update_row_status(status_idx, "up_to_date", new_path=dest_path)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _force_download(self, status_idx: int):
        """Download the server copy regardless of the current sync status."""
        st = self._statuses[status_idx]
        if not (st.server_hash or st.status == "server_only"):
            QMessageBox.warning(self, "No Server Save", "No server save is available for this row.")
            return
        reply = QMessageBox.question(
            self,
            "Force Download",
            f"Force download the server save for '{st.save.game_name}' and overwrite the local copy if it exists?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._keep_server(status_idx)

    def save_ui_state(self) -> dict:
        return {
            "auto_normalize_sync": self.auto_normalize_check.isChecked(),
            "selected_profile": self.profile_combo.currentText(),
        }

    def load_ui_state(self, state: dict):
        if "auto_normalize_sync" in state:
            self.auto_normalize_check.setChecked(bool(state["auto_normalize_sync"]))
        self._saved_profile_name = state.get("selected_profile", "")
