"""Server API client for the Steam Deck SaveSync client."""

import hashlib
import io
import struct
import zipfile
from pathlib import Path
from typing import Optional

import requests

from scanner.models import GameEntry, SyncStatus
from config import load_sync_state, save_sync_state


class SyncClient:
    def __init__(self, host: str, port: int, api_key: str):
        self.base_url = f"http://{host}:{port}/api/v1"
        self.headers = {"X-API-Key": api_key}
        self._timeout = 10

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def check_connection(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/status", timeout=self._timeout)
            return r.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Server save listing
    # ------------------------------------------------------------------

    def get_server_saves(self) -> dict[str, dict]:
        """
        Returns {title_id: {hash, timestamp, size, game_name, ...}}.
        """
        try:
            r = requests.get(
                f"{self.base_url}/titles",
                headers=self.headers,
                timeout=self._timeout,
            )
            if r.status_code == 200:
                data = r.json()
                # Response is {"titles": [...]}
                titles = data.get("titles", [])
                return {t["title_id"]: t for t in titles}
        except Exception:
            pass
        return {}

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_save(self, entry: GameEntry, force: bool = False) -> bool:
        """Upload a save to the server. Returns True on success."""
        if entry.save_path is None or not entry.save_path.exists():
            return False

        title_id = entry.title_id
        system = entry.system
        save_path = entry.save_path

        try:
            if entry.is_multi_file and save_path.is_dir():
                # Zip the folder and upload as raw bundle
                data = _zip_dir(save_path)
            else:
                with open(save_path, "rb") as f:
                    data = f.read()

            if system == "PS1":
                url = f"{self.base_url}/saves/{title_id}/ps1-card"
            elif system == "PS2":
                url = f"{self.base_url}/saves/{title_id}/ps2-card"
            elif system == "GC":
                url = f"{self.base_url}/saves/{title_id}/gc-card"
            else:
                url = f"{self.base_url}/saves/{title_id}/raw"

            if force:
                url += "?force=true"

            r = requests.post(
                url,
                data=data,
                headers={**self.headers, "Content-Type": "application/octet-stream"},
                timeout=30,
            )
            if r.status_code in (200, 201):
                # Record last-synced hash
                _update_state(title_id, entry.save_hash or "")
                return True
        except Exception as exc:
            print(f"[Upload] {title_id}: {exc}")
        return False

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download_save(self, entry: GameEntry, force: bool = False) -> bool:
        """Download a save from the server, write to entry.save_path. Returns True on success."""
        if entry.save_path is None:
            return False

        title_id = entry.title_id
        system = entry.system
        save_path = entry.save_path

        try:
            if system == "PS1":
                url = f"{self.base_url}/saves/{title_id}/ps1-card?slot=0"
            elif system == "PS2":
                url = f"{self.base_url}/saves/{title_id}/ps2-card"
            elif system == "GC":
                url = f"{self.base_url}/saves/{title_id}/gc-card?format=raw"
            else:
                url = f"{self.base_url}/saves/{title_id}/raw"

            r = requests.get(url, headers=self.headers, timeout=30)
            if r.status_code != 200:
                return False

            # Write the file
            if entry.is_multi_file and save_path.is_dir():
                _unzip_to_dir(r.content, save_path)
            else:
                save_path.parent.mkdir(parents=True, exist_ok=True)
                with open(save_path, "wb") as f:
                    f.write(r.content)

            # Get server hash from header
            server_hash = r.headers.get("X-Save-Hash", "")
            _update_state(title_id, server_hash)
            return True

        except Exception as exc:
            print(f"[Download] {title_id}: {exc}")
        return False

    # ------------------------------------------------------------------
    # Sync status computation
    # ------------------------------------------------------------------

    def compute_status(self, entry: GameEntry, server_saves: dict[str, dict]) -> SyncStatus:
        """Three-way hash comparison to determine sync status."""
        state = load_sync_state()
        last_synced_hash = state.get(entry.title_id)

        server_info = server_saves.get(entry.title_id)
        server_hash = server_info["hash"] if server_info else None

        local_hash = entry.save_hash

        # No local save
        if local_hash is None:
            if server_hash:
                return SyncStatus.SERVER_ONLY
            return SyncStatus.NO_SAVE

        # No server save
        if server_hash is None:
            return SyncStatus.LOCAL_ONLY

        # Both exist
        if local_hash == server_hash:
            return SyncStatus.SYNCED

        if not last_synced_hash:
            # Never synced → conflict (safe fallback)
            return SyncStatus.CONFLICT

        if last_synced_hash == server_hash:
            return SyncStatus.LOCAL_NEWER  # only local changed

        if last_synced_hash == local_hash:
            return SyncStatus.SERVER_NEWER  # only server changed

        return SyncStatus.CONFLICT  # both changed


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _update_state(title_id: str, hash_value: str) -> None:
    state = load_sync_state()
    state[title_id] = hash_value
    save_sync_state(state)


def _zip_dir(directory: Path) -> bytes:
    """Zip a directory into bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in sorted(directory.rglob("*")):
            if fp.is_file():
                zf.write(fp, fp.relative_to(directory))
    return buf.getvalue()


def _unzip_to_dir(data: bytes, directory: Path) -> None:
    """Unzip bytes into a directory."""
    directory.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO(data)
    with zipfile.ZipFile(buf, "r") as zf:
        zf.extractall(directory)
