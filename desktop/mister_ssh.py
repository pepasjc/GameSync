"""MiSTer FPGA SSH/SFTP sync helper.

Uses paramiko to connect to a MiSTer over SSH and sync saves with the
GameSync server.  The state file lives in the MiSTer script convention
directory (``/media/fat/Scripts/.config/gamesync/state.json``) and is shared
with the on-device client and the standalone ``mister/sync_saves.sh``, so all
three can be used interchangeably.  The pre-0.5.4 path is still read when the
current one is absent, so an existing install keeps its sync state.
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

from shared import mister as _shared_mister  # noqa: E402
from shared.mister_scan import system_for_save  # noqa: E402
from systems import (  # noqa: E402 (after conditional import above)
    MISTER_FOLDER_TO_SYSTEM,
    MISTER_SYSTEM_TO_FOLDER,
    SAVE_EXTENSIONS,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MISTER_SAVES_DIR = "/media/fat/saves"

# Device paths live in shared/mister.py because the on-device client and the
# legacy shell script write the same state file.
MISTER_CONFIG_DIR = _shared_mister.MISTER_CONFIG_DIR
MISTER_STATE_FILE = _shared_mister.MISTER_STATE_FILE
LEGACY_MISTER_STATE_FILE = _shared_mister.LEGACY_MISTER_STATE_FILE

FOLDER_TO_SYSTEM = MISTER_FOLDER_TO_SYSTEM
SYSTEM_TO_FOLDER = MISTER_SYSTEM_TO_FOLDER


class _SftpProvider:
    """The shared file-provider interface over an open SFTP channel.

    Lets ``shared/mister_scan.py`` rules run against a remote MiSTer exactly as
    they run against a local one on the device.
    """

    def __init__(self, sftp):
        self._sftp = sftp
        self._listdir_cache: dict[str, list[str]] = {}
        self._isdir_cache: dict[str, bool] = {}

    def listdir(self, path: str) -> list[str]:
        if path not in self._listdir_cache:
            try:
                self._listdir_cache[path] = self._sftp.listdir(path)
            except Exception:
                self._listdir_cache[path] = []
        return list(self._listdir_cache[path])

    def is_dir(self, path: str) -> bool:
        if path not in self._isdir_cache:
            try:
                import stat as _stat

                self._isdir_cache[path] = _stat.S_ISDIR(
                    self._sftp.stat(path).st_mode
                )
            except Exception:
                self._isdir_cache[path] = False
        return self._isdir_cache[path]

    def stat(self, path: str):
        try:
            info = self._sftp.stat(path)
            return int(info.st_size or 0), float(info.st_mtime or 0)
        except Exception:
            return 0, 0.0

    def read(self, path: str) -> bytes:
        with self._sftp.open(path, "rb") as handle:
            return handle.read()


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
    mtime: float = 0.0

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

    def provider(self) -> "_SftpProvider":
        """A shared-rules file provider backed by this SFTP connection."""
        assert self._sftp is not None, "Not connected"
        return _SftpProvider(self._sftp)

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
                # A folder can serve two systems - the TurboGrafx-16 core keeps
                # HuCard and CD saves together in saves/TGFX16 - so the
                # installed game decides which this is.
                resolved = system_for_save(
                    self.provider(), folder, system, Path(fname).stem
                )
                try:
                    title_id = make_title_id(resolved, fname)
                except Exception:
                    continue

                saves.append(
                    MiSTerSave(
                        system=resolved,
                        folder=folder,
                        filename=fname,
                        remote_path=remote_path,
                        title_id=title_id,
                        size=attr.st_size or 0,
                        mtime=float(attr.st_mtime or 0),
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

    def exec(self, command: str) -> tuple[int, str, str]:
        """Run a shell command on the MiSTer. Returns (exit code, stdout, stderr)."""
        assert self._client is not None, "Not connected"
        _, out, err = self._client.exec_command(command)
        rc = out.channel.recv_exit_status()
        return rc, out.read().decode(errors="replace"), err.read().decode(errors="replace")

    def makedirs(self, remote_dir: str) -> None:
        """mkdir -p over SFTP (write_file only creates one parent level)."""
        assert self._sftp is not None
        path = PurePosixPath(remote_dir)
        current = PurePosixPath("/") if path.is_absolute() else PurePosixPath(".")
        for part in path.parts:
            if part == "/":
                continue
            current = current / part
            try:
                self._sftp.stat(str(current))
            except FileNotFoundError:
                self._sftp.mkdir(str(current))

    def upload_file(
        self,
        local_path: str | Path,
        remote_path: str,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Stream a local file to the MiSTer, creating parent dirs.

        Uploads to a ``.part`` name first so an interrupted transfer never
        leaves a truncated file where a core would find it.
        """
        assert self._sftp is not None
        self.makedirs(str(PurePosixPath(remote_path).parent))
        tmp_remote = f"{remote_path}.part"
        self._sftp.put(str(local_path), tmp_remote, callback=progress_cb)
        try:
            self._sftp.posix_rename(tmp_remote, remote_path)
        except Exception:
            # SFTP rename refuses to overwrite — drop the old file first.
            try:
                self._sftp.remove(remote_path)
            except FileNotFoundError:
                pass
            self._sftp.rename(tmp_remote, remote_path)

    # ------------------------------------------------------------------
    # State file (last-synced hashes, one per title_id)
    # ------------------------------------------------------------------

    def load_state(self) -> dict[str, str]:
        """Load sync state from MiSTer. Returns {} if missing or corrupt.

        Falls back to the pre-0.5.4 path so an existing install does not lose
        its last-synced hashes and see every save as a conflict.
        """
        assert self._sftp is not None
        for path in (MISTER_STATE_FILE, LEGACY_MISTER_STATE_FILE):
            try:
                with self._sftp.open(path, "r") as fh:
                    return json.loads(fh.read())
            except Exception:
                continue
        return {}

    def save_state(self, state: dict[str, str]) -> None:
        """Persist sync state to MiSTer, in the script-convention directory."""
        assert self._sftp is not None
        data = json.dumps(state, indent=2)
        self.makedirs(MISTER_CONFIG_DIR)
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
