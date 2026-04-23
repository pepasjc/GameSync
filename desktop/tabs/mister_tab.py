"""MiSTer FPGA SSH sync tab.

Connects to a MiSTer over SSH/SFTP, scans /media/fat/saves/, compares
hashes with the 3dssync server, and uploads/downloads as needed using
the same three-way hash logic as the MiSTer bash script.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config import (
    STATUS_COLORS,
    STATUS_LABELS,
    get_api_headers,
    get_base_url,
    load_config,
    save_config,
)
from mister_ssh import (
    FOLDER_TO_SYSTEM,
    SYSTEM_TO_FOLDER,
    MiSTerSave,
    MiSTerSSH,
    determine_status,
)

# ---------------------------------------------------------------------------
# Column indices
# ---------------------------------------------------------------------------

COL_SYSTEM = 0
COL_GAME = 1
COL_TITLE_ID = 2
COL_SIZE = 3
COL_STATUS = 4
COLUMN_HEADERS = ["System", "Game", "Title ID", "Size", "Status"]

# Status label overrides for MiSTer context
_STATUS_LABELS = {
    **STATUS_LABELS,
    "not_on_server": "Not on server",
    "local_newer": "Upload",
    "server_newer": "Download",
    "up_to_date": "Up to date",
    "conflict": "Conflict",
    "error": "Error",
}


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# Worker: scan + compare
# ---------------------------------------------------------------------------


class ScanWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(str, int, int)  # message, current, total
    saves_found = pyqtSignal(list)         # list[MiSTerSave] with hashes + status
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, ssh: MiSTerSSH, base_url: str, headers: dict):
        super().__init__()
        self._ssh = ssh
        self._base_url = base_url
        self._headers = headers

    def run(self):
        try:
            self._ssh.connect()
            try:
                self._do_scan()
            finally:
                self._ssh.disconnect()
            self.finished_ok.emit()
        except Exception as exc:
            self.failed.emit(str(exc))

    def _do_scan(self):
        self.log.emit(f"[{_ts()}] Connected to {self._ssh.host}")

        # Load state (last-synced hashes)
        state = self._ssh.load_state()
        self.log.emit(f"[{_ts()}] Loaded sync state ({len(state)} entries)")

        # Discover save files
        self.log.emit(f"[{_ts()}] Scanning /media/fat/saves/ …")
        saves = self._ssh.scan_saves(
            progress_cb=lambda msg: self.progress.emit(msg, 0, 0)
        )
        self.log.emit(f"[{_ts()}] Found {len(saves)} save file(s)")

        if not saves:
            self.saves_found.emit([])
            return

        # Hash each file + attach last_synced_hash
        for i, sv in enumerate(saves):
            if self.isInterruptionRequested():
                return
            self.progress.emit(f"Hashing {sv.filename} …", i, len(saves))
            try:
                sv.local_hash = self._ssh.hash_file(sv.remote_path)
            except Exception as exc:
                sv.status = "error"
                sv.error_msg = str(exc)
            sv.last_synced_hash = state.get(sv.title_id, "")

        # Emit partial results so the table shows up quickly
        self.saves_found.emit(list(saves))

        # Query server metadata for each title
        self.progress.emit("Comparing with server …", 0, len(saves))
        self.log.emit(f"[{_ts()}] Querying server metadata …")
        for i, sv in enumerate(saves):
            if self.isInterruptionRequested():
                return
            if sv.status == "error":
                continue
            self.progress.emit(f"Checking {sv.title_id} …", i, len(saves))
            try:
                resp = requests.get(
                    f"{self._base_url}/api/v1/saves/{sv.title_id}/meta",
                    headers=self._headers,
                    timeout=10,
                )
                if resp.status_code == 200:
                    meta = resp.json()
                    sv.server_hash = meta.get("save_hash", "")
                    sv.server_timestamp = meta.get("updated_at", 0)
                    sv.game_name = meta.get("game_name", "")
                elif resp.status_code == 404:
                    sv.server_hash = ""
                else:
                    sv.status = "error"
                    sv.error_msg = f"HTTP {resp.status_code}"
                    continue
            except Exception as exc:
                sv.status = "error"
                sv.error_msg = str(exc)
                continue

            sv.status = determine_status(
                sv.local_hash, sv.server_hash, sv.last_synced_hash
            )

        self.log.emit(f"[{_ts()}] Scan complete.")
        self.saves_found.emit(list(saves))


# ---------------------------------------------------------------------------
# Worker: sync
# ---------------------------------------------------------------------------


class SyncWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(str, int, int)
    save_synced = pyqtSignal(str, str)  # title_id, new_status
    finished_ok = pyqtSignal(int, int, int, int)  # up, down, skipped, errors
    failed = pyqtSignal(str)

    def __init__(
        self,
        ssh: MiSTerSSH,
        saves: list[MiSTerSave],
        base_url: str,
        headers: dict,
    ):
        super().__init__()
        self._ssh = ssh
        self._saves = saves
        self._base_url = base_url
        self._headers = headers

    def run(self):
        try:
            self._ssh.connect()
            try:
                up, down, skipped, errors = self._do_sync()
            finally:
                self._ssh.disconnect()
            self.finished_ok.emit(up, down, skipped, errors)
        except Exception as exc:
            self.failed.emit(str(exc))

    def _do_sync(self):
        self.log.emit(f"[{_ts()}] Connected — starting sync …")
        state = self._ssh.load_state()

        to_upload = [s for s in self._saves if s.status in ("local_newer", "not_on_server")]
        to_download = [s for s in self._saves if s.status == "server_newer"]
        conflicts = [s for s in self._saves if s.status == "conflict"]

        total = len(to_upload) + len(to_download)
        done = 0
        uploaded = downloaded = skipped = errors = 0

        for sv in conflicts:
            self.log.emit(f"[{_ts()}] CONFLICT (skipped)  {sv.title_id}")
            skipped += 1

        # ── Uploads ────────────────────────────────────────────────────────
        for sv in to_upload:
            if self.isInterruptionRequested():
                break
            done += 1
            self.progress.emit(f"Uploading {sv.filename} …", done, total)
            try:
                data = self._ssh.read_file(sv.remote_path)
                resp = requests.post(
                    f"{self._base_url}/api/v1/saves/{sv.title_id}/raw",
                    headers=self._headers,
                    data=data,
                    timeout=30,
                )
                resp.raise_for_status()
                state[sv.title_id] = sv.local_hash
                sv.status = "up_to_date"
                sv.server_hash = sv.local_hash
                uploaded += 1
                self.log.emit(f"[{_ts()}] ↑ Uploaded  {sv.title_id}")
                self.save_synced.emit(sv.title_id, "up_to_date")
            except Exception as exc:
                errors += 1
                sv.status = "error"
                sv.error_msg = str(exc)
                self.log.emit(f"[{_ts()}] ERROR uploading {sv.title_id}: {exc}")
                self.save_synced.emit(sv.title_id, "error")

        # ── Downloads ──────────────────────────────────────────────────────
        for sv in to_download:
            if self.isInterruptionRequested():
                break
            done += 1
            self.progress.emit(f"Downloading {sv.filename} …", done, total)
            try:
                resp = requests.get(
                    f"{self._base_url}/api/v1/saves/{sv.title_id}/raw",
                    headers=self._headers,
                    timeout=30,
                )
                resp.raise_for_status()
                # Determine correct remote path (may need to create folder)
                dest_folder = SYSTEM_TO_FOLDER.get(sv.system, sv.folder)
                dest_path = f"/media/fat/saves/{dest_folder}/{sv.filename}"
                self._ssh.write_file(dest_path, resp.content)
                server_hash_hdr = resp.headers.get("X-Save-Hash", "")
                new_hash = server_hash_hdr if server_hash_hdr else sv.server_hash
                state[sv.title_id] = new_hash
                sv.status = "up_to_date"
                sv.local_hash = new_hash
                downloaded += 1
                self.log.emit(f"[{_ts()}] ↓ Downloaded {sv.title_id}")
                self.save_synced.emit(sv.title_id, "up_to_date")
            except Exception as exc:
                errors += 1
                sv.status = "error"
                sv.error_msg = str(exc)
                self.log.emit(f"[{_ts()}] ERROR downloading {sv.title_id}: {exc}")
                self.save_synced.emit(sv.title_id, "error")

        # Persist updated state to MiSTer
        try:
            self._ssh.save_state(state)
            self.log.emit(f"[{_ts()}] Sync state saved to MiSTer.")
        except Exception as exc:
            self.log.emit(f"[{_ts()}] WARNING: could not save state: {exc}")

        return uploaded, downloaded, skipped, errors


# ---------------------------------------------------------------------------
# Main tab widget
# ---------------------------------------------------------------------------


class MiSTerTab(QWidget):
    def __init__(self):
        super().__init__()
        self._saves: list[MiSTerSave] = []
        self._scan_worker: Optional[ScanWorker] = None
        self._sync_worker: Optional[SyncWorker] = None
        self._init_ui()
        self._load_connection_config()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(6)

        # ── Connection group ───────────────────────────────────────────
        conn_group = QGroupBox("MiSTer Connection")
        conn_layout = QVBoxLayout(conn_group)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Host:"))
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("192.168.1.x")
        self.host_edit.setFixedWidth(160)
        row1.addWidget(self.host_edit)

        row1.addWidget(QLabel("Port:"))
        self.port_edit = QLineEdit("22")
        self.port_edit.setFixedWidth(55)
        row1.addWidget(self.port_edit)

        row1.addWidget(QLabel("User:"))
        self.user_edit = QLineEdit("root")
        self.user_edit.setFixedWidth(90)
        row1.addWidget(self.user_edit)

        row1.addWidget(QLabel("Password:"))
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_edit.setPlaceholderText("(leave blank for key auth)")
        self.pass_edit.setFixedWidth(160)
        row1.addWidget(self.pass_edit)

        row1.addStretch()
        conn_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("SSH Key:"))
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("Optional — path to private key file")
        row2.addWidget(self.key_edit)
        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse_key)
        row2.addWidget(browse_btn)

        self.test_btn = QPushButton("Test Connection")
        self.test_btn.setFixedWidth(130)
        self.test_btn.clicked.connect(self._test_connection)
        row2.addWidget(self.test_btn)

        self.scan_btn = QPushButton("Scan Saves")
        self.scan_btn.setFixedWidth(110)
        self.scan_btn.clicked.connect(self._start_scan)
        row2.addWidget(self.scan_btn)

        conn_layout.addLayout(row2)
        root.addWidget(conn_group)

        # ── Filter bar ─────────────────────────────────────────────────
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("System:"))
        self.system_combo = QComboBox()
        self.system_combo.addItem("All")
        for sys in sorted(set(FOLDER_TO_SYSTEM.values())):
            self.system_combo.addItem(sys)
        self.system_combo.setFixedWidth(100)
        self.system_combo.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self.system_combo)

        filter_row.addWidget(QLabel("Status:"))
        self.status_combo = QComboBox()
        self.status_combo.addItems(["All", "Upload", "Download", "Conflict", "Up to date", "Not on server", "Error"])
        self.status_combo.setFixedWidth(130)
        self.status_combo.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self.status_combo)

        filter_row.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("game name / title ID …")
        self.search_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.search_edit)

        self.summary_label = QLabel("")
        filter_row.addStretch()
        filter_row.addWidget(self.summary_label)
        root.addLayout(filter_row)

        # ── Table ──────────────────────────────────────────────────────
        self.table = QTableWidget()
        self.table.setColumnCount(len(COLUMN_HEADERS))
        self.table.setHorizontalHeaderLabels(COLUMN_HEADERS)
        self.table.horizontalHeader().setSectionResizeMode(COL_GAME, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(COL_TITLE_ID, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)

        # ── Action row ─────────────────────────────────────────────────
        action_row = QHBoxLayout()
        self.sync_all_btn = QPushButton("Sync All")
        self.sync_all_btn.setFixedWidth(100)
        self.sync_all_btn.clicked.connect(self._sync_all)
        self.sync_all_btn.setEnabled(False)
        action_row.addWidget(self.sync_all_btn)

        self.sync_sel_btn = QPushButton("Sync Selected")
        self.sync_sel_btn.setFixedWidth(120)
        self.sync_sel_btn.clicked.connect(self._sync_selected)
        self.sync_sel_btn.setEnabled(False)
        action_row.addWidget(self.sync_sel_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedWidth(80)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        action_row.addWidget(self.cancel_btn)

        action_row.addStretch()

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedWidth(300)
        action_row.addWidget(self.progress_bar)

        # ── Splitter: table + log ───────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Vertical)

        table_container = QWidget()
        tc_layout = QVBoxLayout(table_container)
        tc_layout.setContentsMargins(0, 0, 0, 0)
        tc_layout.addWidget(self.table)
        tc_layout.addLayout(action_row)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(160)
        font = QFont("Courier New", 9)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.log_box.setFont(font)
        self.log_box.setPlaceholderText("Activity log…")

        splitter.addWidget(table_container)
        splitter.addWidget(self.log_box)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter)

    # ------------------------------------------------------------------
    # Config persistence
    # ------------------------------------------------------------------

    def _load_connection_config(self):
        cfg = load_config().get("mister_ssh", {})
        self.host_edit.setText(cfg.get("host", ""))
        self.port_edit.setText(str(cfg.get("port", 22)))
        self.user_edit.setText(cfg.get("username", "root"))
        self.pass_edit.setText(cfg.get("password", ""))
        self.key_edit.setText(cfg.get("key_path", ""))

    def _save_connection_config(self):
        cfg = load_config()
        cfg["mister_ssh"] = {
            "host": self.host_edit.text().strip(),
            "port": self._port(),
            "username": self.user_edit.text().strip() or "root",
            "password": self.pass_edit.text(),
            "key_path": self.key_edit.text().strip(),
        }
        save_config(cfg)

    def save_ui_state(self) -> dict:
        return {}

    def load_ui_state(self, state: dict):
        pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _port(self) -> int:
        try:
            return int(self.port_edit.text())
        except ValueError:
            return 22

    def _make_ssh(self) -> MiSTerSSH:
        return MiSTerSSH(
            host=self.host_edit.text().strip(),
            port=self._port(),
            username=self.user_edit.text().strip() or "root",
            password=self.pass_edit.text(),
            key_path=self.key_edit.text().strip(),
        )

    def _log(self, msg: str):
        self.log_box.append(msg)
        self.log_box.verticalScrollBar().setValue(
            self.log_box.verticalScrollBar().maximum()
        )

    def _browse_key(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select SSH Private Key", str(Path.home()), "All Files (*)"
        )
        if path:
            self.key_edit.setText(path)

    def _set_busy(self, busy: bool):
        self.scan_btn.setEnabled(not busy)
        self.test_btn.setEnabled(not busy)
        self.sync_all_btn.setEnabled(not busy and bool(self._saves))
        self.sync_sel_btn.setEnabled(not busy and bool(self._saves))
        self.cancel_btn.setEnabled(busy)
        self.progress_bar.setVisible(busy)
        if busy:
            self.progress_bar.setRange(0, 0)

    # ------------------------------------------------------------------
    # Test connection
    # ------------------------------------------------------------------

    def _test_connection(self):
        self._save_connection_config()
        ssh = self._make_ssh()
        self._set_busy(True)
        self._log(f"[{_ts()}] Testing connection to {ssh.host}:{ssh.port} …")
        ok, msg = ssh.test_connection()
        self._set_busy(False)
        self._log(f"[{_ts()}] {'OK:' if ok else 'FAILED:'} {msg}")
        if ok:
            QMessageBox.information(self, "Connection Test", msg)
        else:
            QMessageBox.critical(self, "Connection Failed", msg)

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def _start_scan(self):
        host = self.host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "No Host", "Enter the MiSTer hostname or IP address.")
            return
        self._save_connection_config()
        self._saves.clear()
        self.table.setRowCount(0)
        self.summary_label.setText("")
        self._set_busy(True)

        self._log(f"[{_ts()}] Connecting to {host} …")

        self._scan_worker = ScanWorker(
            self._make_ssh(),
            get_base_url(),
            get_api_headers(),
        )
        self._scan_worker.log.connect(self._log)
        self._scan_worker.progress.connect(self._on_progress)
        self._scan_worker.saves_found.connect(self._on_saves_found)
        self._scan_worker.finished_ok.connect(self._on_scan_done)
        self._scan_worker.failed.connect(self._on_worker_error)
        self._scan_worker.start()

    def _on_progress(self, msg: str, cur: int, total: int):
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(cur)
        else:
            self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat(msg[:60])

    def _on_saves_found(self, saves: list[MiSTerSave]):
        self._saves = saves
        self._populate_table(saves)

    def _on_scan_done(self):
        self._set_busy(False)
        self.sync_all_btn.setEnabled(bool(self._saves))
        self.sync_sel_btn.setEnabled(bool(self._saves))
        self._update_summary()

    def _on_worker_error(self, msg: str):
        self._set_busy(False)
        self._log(f"[{_ts()}] ERROR: {msg}")
        QMessageBox.critical(self, "Error", msg)

    # ------------------------------------------------------------------
    # Table population
    # ------------------------------------------------------------------

    def _populate_table(self, saves: list[MiSTerSave]):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        sys_f = self.system_combo.currentText()
        stat_f = self.status_combo.currentText()
        search = self.search_edit.text().strip().lower()

        for sv in saves:
            if sys_f != "All" and sv.system != sys_f:
                continue
            label = _STATUS_LABELS.get(sv.status, sv.status)
            if stat_f != "All" and label != stat_f:
                continue
            if search and search not in sv.filename.lower() and search not in sv.title_id.lower():
                continue

            row = self.table.rowCount()
            self.table.insertRow(row)

            display_name = sv.game_name if sv.game_name else sv.filename
            items = [
                QTableWidgetItem(sv.system),
                QTableWidgetItem(display_name),
                QTableWidgetItem(sv.title_id),
                QTableWidgetItem(_fmt_size(sv.size)),
                QTableWidgetItem(label),
            ]

            color = STATUS_COLORS.get(sv.status, QColor(180, 180, 180))
            items[COL_STATUS].setForeground(color)
            if sv.error_msg:
                items[COL_STATUS].setToolTip(sv.error_msg)

            for col, item in enumerate(items):
                item.setData(Qt.ItemDataRole.UserRole, sv)
                self.table.setItem(row, col, item)

        self.table.setSortingEnabled(True)
        self._update_summary()

    def _apply_filter(self):
        if self._saves:
            self._populate_table(self._saves)

    def _update_summary(self):
        if not self._saves:
            self.summary_label.setText("")
            return
        counts: dict[str, int] = {}
        for sv in self._saves:
            counts[sv.status] = counts.get(sv.status, 0) + 1
        parts = []
        for k in ("up_to_date", "local_newer", "server_newer", "not_on_server", "conflict", "error"):
            if counts.get(k, 0):
                parts.append(f"{_STATUS_LABELS.get(k, k)}: {counts[k]}")
        self.summary_label.setText("  |  ".join(parts))

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    def _get_sync_targets(self, selected_only: bool) -> list[MiSTerSave]:
        if selected_only:
            rows = {idx.row() for idx in self.table.selectedIndexes()}
            targets = []
            for row in rows:
                item = self.table.item(row, 0)
                if item:
                    sv: MiSTerSave = item.data(Qt.ItemDataRole.UserRole)
                    if sv and sv.status not in ("up_to_date", "error"):
                        targets.append(sv)
        else:
            targets = [s for s in self._saves if s.status not in ("up_to_date", "error")]
        return targets

    def _start_sync(self, selected_only: bool):
        targets = self._get_sync_targets(selected_only)
        if not targets:
            QMessageBox.information(self, "Nothing to sync", "All saves are already up to date.")
            return

        actionable = [s for s in targets if s.status in ("local_newer", "not_on_server", "server_newer")]
        conflicts = [s for s in targets if s.status == "conflict"]

        msg_parts = []
        if actionable:
            uploads = sum(1 for s in actionable if s.status in ("local_newer", "not_on_server"))
            downloads = sum(1 for s in actionable if s.status == "server_newer")
            if uploads:
                msg_parts.append(f"{uploads} upload(s)")
            if downloads:
                msg_parts.append(f"{downloads} download(s)")
        if conflicts:
            msg_parts.append(f"{len(conflicts)} conflict(s) will be skipped")

        reply = QMessageBox.question(
            self,
            "Confirm Sync",
            f"Proceed with: {', '.join(msg_parts)}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._set_busy(True)
        self._log(f"[{_ts()}] Starting sync ({len(targets)} saves) …")

        self._sync_worker = SyncWorker(
            self._make_ssh(),
            targets,
            get_base_url(),
            get_api_headers(),
        )
        self._sync_worker.log.connect(self._log)
        self._sync_worker.progress.connect(self._on_progress)
        self._sync_worker.save_synced.connect(self._on_save_synced)
        self._sync_worker.finished_ok.connect(self._on_sync_done)
        self._sync_worker.failed.connect(self._on_worker_error)
        self._sync_worker.start()

    def _sync_all(self):
        self._start_sync(selected_only=False)

    def _sync_selected(self):
        self._start_sync(selected_only=True)

    def _on_save_synced(self, title_id: str, new_status: str):
        """Update a row's status in-place after sync."""
        label = _STATUS_LABELS.get(new_status, new_status)
        color = STATUS_COLORS.get(new_status, QColor(180, 180, 180))
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_TITLE_ID)
            if item and item.text() == title_id:
                status_item = self.table.item(row, COL_STATUS)
                if status_item:
                    status_item.setText(label)
                    status_item.setForeground(color)
                break

    def _on_sync_done(self, uploaded: int, downloaded: int, skipped: int, errors: int):
        self._set_busy(False)
        msg = (
            f"Sync complete — "
            f"{uploaded} uploaded, {downloaded} downloaded, "
            f"{skipped} skipped (conflicts), {errors} error(s)"
        )
        self._log(f"[{_ts()}] {msg}")
        self._update_summary()
        if errors:
            QMessageBox.warning(self, "Sync Complete (with errors)", msg)
        else:
            QMessageBox.information(self, "Sync Complete", msg)

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def _cancel(self):
        for worker in (self._scan_worker, self._sync_worker):
            if worker and worker.isRunning():
                worker.requestInterruption()
                worker.wait(3000)
        self._set_busy(False)
        self._log(f"[{_ts()}] Cancelled.")
