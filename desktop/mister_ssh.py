"""MiSTer FPGA SSH/SFTP sync helper.

Uses paramiko to connect to a MiSTer over SSH and sync saves with the
3dssync server.  The state file stored on the MiSTer
(/media/fat/3dssync_state.json) is compatible with the standalone
mister/sync_saves.sh script so both tools can be used interchangeably.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

try:
    import paramiko

    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False

from systems import (  # noqa: E402 (after conditional import above)
    MISTER_FOLDER_TO_SYSTEM,
    MISTER_SYSTEM_TO_FOLDER,
    SAVE_EXTENSIONS,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MISTER_SAVES_DIR = "/media/fat/saves"
MISTER_STATE_FILE = "/media/fat/3dssync_state.json"

FOLDER_TO_SYSTEM = MISTER_FOLDER_TO_SYSTEM
SYSTEM_TO_FOLDER = MISTER_SYSTEM_TO_FOLDER


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class MiSTerSave:
    system: str       # e.g. "GBA"
    folder: str       # MiSTer subfolder, e.g. "GBA"
    filename: str     # e.g. "Zelda - Minish Cap (USA).sav"
    remote_path: str  # full SFTP path
    title_id: str     # e.g. "GBA_zelda_the_minish_cap_usa"
    size: int = 0

    # Populated during scan
    local_hash: str = ""
    last_synced_hash: str = ""

    # Populated during server compare
    server_hash: str = ""
    server_timestamp: int = 0
    game_name: str = ""

    # Determined after compare
    status: str = "unknown"  # up_to_date | local_newer | server_newer | conflict | not_on_server | error
    error_msg: str = ""


# ---------------------------------------------------------------------------
# SSH client
# ---------------------------------------------------------------------------


class MiSTerSSH:
    """Thin wrapper around paramiko SSHClient + SFTPClient."""

    def __init__(
        self,
        host: str,
        port: int = 22,
        username: str = "root",
        password: str = "",
        key_path: str = "",
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.key_path = key_path
        self._client: Optional[object] = None
        self._sftp: Optional[object] = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        if not PARAMIKO_AVAILABLE:
            raise RuntimeError(
                "paramiko is not installed.\n"
                "Run: pip install paramiko"
            )
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: dict = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
            "timeout": 15,
            "banner_timeout": 15,
            "auth_timeout": 15,
        }
        if self.key_path:
            kwargs["key_filename"] = self.key_path
        if self.password:
            kwargs["password"] = self.password
        client.connect(**kwargs)
        self._client = client
        self._sftp = client.open_sftp()

    def disconnect(self) -> None:
        if self._sftp:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def __enter__(self) -> "MiSTerSSH":
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.disconnect()

    def test_connection(self) -> tuple[bool, str]:
        """Try to connect and immediately disconnect. Returns (ok, message)."""
        try:
            self.connect()
            # Quick sanity check: verify saves dir exists
            self._sftp.stat(MISTER_SAVES_DIR)
            self.disconnect()
            return True, f"Connected to {self.host} — saves dir found."
        except FileNotFoundError:
            self.disconnect()
            return True, f"Connected to {self.host} (saves dir not yet present — will be created on first download)."
        except Exception as exc:
            self.disconnect()
            return False, str(exc)

    # ------------------------------------------------------------------
    # Save discovery
    # ------------------------------------------------------------------

    def scan_saves(
        self, progress_cb: Optional[Callable[[str], None]] = None
    ) -> list[MiSTerSave]:
        """Walk /media/fat/saves/ and return all recognised save files."""
        assert self._sftp is not None, "Not connected"

        from sync_engine import make_title_id  # local import to avoid hard dep at module load

        saves: list[MiSTerSave] = []
        try:
            folders = self._sftp.listdir(MISTER_SAVES_DIR)
        except FileNotFoundError:
            return []

        for folder in sorted(folders):
            system = FOLDER_TO_SYSTEM.get(folder)
            if not system:
                continue
            system_path = f"{MISTER_SAVES_DIR}/{folder}"
            try:
                attrs = self._sftp.listdir_attr(system_path)
            except Exception:
                continue

            for attr in sorted(attrs, key=lambda a: a.filename):
                fname = attr.filename
                if Path(fname).suffix.lower() not in SAVE_EXTENSIONS:
                    continue
                remote_path = f"{system_path}/{fname}"
                try:
                    title_id = make_title_id(system, fname)
                except Exception:
                    continue

                saves.append(
                    MiSTerSave(
                        system=system,
                        folder=folder,
                        filename=fname,
                        remote_path=remote_path,
                        title_id=title_id,
                        size=attr.st_size or 0,
                    )
                )
                if progress_cb:
                    progress_cb(f"Found {system}/{fname}")

        return saves

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def hash_file(self, remote_path: str) -> str:
        """Compute SHA-256 of a remote file (streaming, no full read into RAM)."""
        assert self._sftp is not None
        h = hashlib.sha256()
        with self._sftp.open(remote_path, "rb") as fh:
            fh.prefetch()
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    def read_file(self, remote_path: str) -> bytes:
        assert self._sftp is not None
        with self._sftp.open(remote_path, "rb") as fh:
            fh.prefetch()
            return fh.read()

    def write_file(self, remote_path: str, data: bytes) -> None:
        assert self._sftp is not None
        # Ensure parent directory exists
        parent = str(PurePosixPath(remote_path).parent)
        try:
            self._sftp.stat(parent)
        except FileNotFoundError:
            self._sftp.mkdir(parent)
        with self._sftp.open(remote_path, "wb") as fh:
            fh.write(data)

    # ------------------------------------------------------------------
    # State file (last-synced hashes, one per title_id)
    # ------------------------------------------------------------------

    def load_state(self) -> dict[str, str]:
        """Load sync state from MiSTer. Returns {} if missing or corrupt."""
        assert self._sftp is not None
        try:
            with self._sftp.open(MISTER_STATE_FILE, "r") as fh:
                return json.loads(fh.read())
        except Exception:
            return {}

    def save_state(self, state: dict[str, str]) -> None:
        """Persist sync state to MiSTer."""
        assert self._sftp is not None
        data = json.dumps(state, indent=2)
        with self._sftp.open(MISTER_STATE_FILE, "w") as fh:
            fh.write(data)


# ---------------------------------------------------------------------------
# Three-way hash comparison (same logic as sync_saves.sh and server sync.py)
# ---------------------------------------------------------------------------


def determine_status(
    local_hash: str,
    server_hash: str,
    last_synced_hash: str,
) -> str:
    """Return a status string based on the three-way hash comparison."""
    if not server_hash:
        return "not_on_server"
    if local_hash == server_hash:
        return "up_to_date"
    if not last_synced_hash:
        # No history — treat as conflict (safe default: don't overwrite either)
        return "conflict"
    if last_synced_hash == server_hash:
        # Server unchanged since last sync → local is newer
        return "local_newer"
    if last_synced_hash == local_hash:
        # Local unchanged since last sync → server is newer
        return "server_newer"
    # Both changed → conflict
    return "conflict"
