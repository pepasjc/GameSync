"""Server API client for the Steam Deck SaveSync client."""

import hashlib
import io
import struct
import time
import zipfile
import zlib
from pathlib import Path
from typing import Optional

import requests

from scanner.models import GameEntry, SyncStatus
from config import load_sync_state, save_sync_state


# ──────────────────────────────────────────────────────────────────────────────
# 3DSS Bundle format (v4) — for PSP saves
# ──────────────────────────────────────────────────────────────────────────────

_BUNDLE_MAGIC = b"3DSS"
_BUNDLE_V4 = 4


def _create_psp_bundle(title_id: str, slot_dir: Path) -> bytes:
    """
    Create a 3DSS v4 bundle from a PSP SAVEDATA slot directory.
    All files in the directory are included, sorted by name.
    Matches Android's BundleUtils.createBundle() output.
    """
    files: list[tuple[str, bytes, bytes]] = []  # (name, data, sha256_hash)

    for fp in sorted(slot_dir.iterdir(), key=lambda f: f.name):
        if not fp.is_file():
            continue
        data = fp.read_bytes()
        h = hashlib.sha256(data).digest()
        files.append((fp.name, data, h))

    if not files:
        raise ValueError(f"No files found in {slot_dir}")

    timestamp = int(time.time())

    # Build file table + file data
    file_table = bytearray()
    file_data = bytearray()
    for name, data, sha in files:
        name_bytes = name.encode("utf-8")
        file_table += struct.pack("<H", len(name_bytes))
        file_table += name_bytes
        file_table += struct.pack("<I", len(data))
        file_table += sha
        file_data += data

    payload = bytes(file_table) + bytes(file_data)
    uncompressed_size = len(payload)
    compressed = zlib.compress(payload, 6)

    # Build v4 header
    header = bytearray()
    header += _BUNDLE_MAGIC
    header += struct.pack("<I", _BUNDLE_V4)
    # Title ID: 32 bytes, null-padded, uppercase ASCII
    tid_bytes = title_id.upper().encode("ascii")[:31]
    header += tid_bytes + b"\x00" * (32 - len(tid_bytes))
    header += struct.pack("<I", timestamp)
    header += struct.pack("<I", len(files))
    header += struct.pack("<I", uncompressed_size)

    return bytes(header) + compressed


def _parse_psp_bundle(data: bytes) -> list[tuple[str, bytes]]:
    """
    Parse a 3DSS v3 or v4 bundle, returning list of (filename, file_data).
    Used for downloading PSP saves from the server.
    """
    if len(data) < 4 or data[:4] != _BUNDLE_MAGIC:
        raise ValueError("Not a valid 3DSS bundle")

    version = struct.unpack_from("<I", data, 4)[0]

    if version == 4:
        # v4: 4 magic + 4 version + 32 title_id + 4 timestamp + 4 file_count + 4 uncompressed_size
        offset = 4 + 4 + 32 + 4
        file_count = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        uncompressed_size = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        payload = zlib.decompress(data[offset:])
    elif version == 3:
        # v3: 4 magic + 4 version + 16 title_id + 4 timestamp + 4 file_count + 4 uncompressed_size
        offset = 4 + 4 + 16 + 4
        file_count = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        uncompressed_size = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        payload = zlib.decompress(data[offset:])
    elif version == 2:
        # v2: 4 magic + 4 version + 8 title_id(u64) + 4 timestamp + 4 file_count + 4 uncompressed_size
        offset = 4 + 4 + 8 + 4
        file_count = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        uncompressed_size = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        payload = zlib.decompress(data[offset:])
    elif version == 1:
        # v1: 4 magic + 4 version + 8 title_id(u64) + 4 timestamp + 4 file_count + 4 total_size
        offset = 4 + 4 + 8 + 4
        file_count = struct.unpack_from("<I", data, offset)[0]
        offset += 4 + 4  # skip total_size
        payload = data[offset:]
    else:
        raise ValueError(f"Unknown bundle version: {version}")

    # Parse file table
    pos = 0
    file_entries: list[tuple[str, int]] = []
    for _ in range(file_count):
        path_len = struct.unpack_from("<H", payload, pos)[0]
        pos += 2
        path = payload[pos : pos + path_len].decode("utf-8")
        pos += path_len
        file_size = struct.unpack_from("<I", payload, pos)[0]
        pos += 4
        pos += 32  # skip sha256 hash
        file_entries.append((path, file_size))

    # Extract file data
    result: list[tuple[str, bytes]] = []
    for name, size in file_entries:
        file_data = payload[pos : pos + size]
        pos += size
        result.append((name, file_data))

    return result


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
                titles = data.get("titles", [])
                return {t["title_id"]: t for t in titles}
        except Exception:
            pass
        return {}

    # ------------------------------------------------------------------
    # ROM normalization / serial lookup
    # ------------------------------------------------------------------

    def normalize_batch(self, roms: list[dict[str, str]]) -> dict[str, str]:
        """
        Call POST /api/v1/normalize/batch to resolve ROM filenames to title_ids.

        Args:
            roms: list of {"system": "PS1", "filename": "Game Name (USA).chd"}

        Returns:
            dict mapping original_filename -> title_id (serial or slug)
        """
        if not roms:
            return {}
        try:
            r = requests.post(
                f"{self.base_url}/normalize/batch",
                json={"roms": roms},
                headers={**self.headers, "Content-Type": "application/json"},
                timeout=self._timeout,
            )
            if r.status_code == 200:
                data = r.json()
                result: dict[str, str] = {}
                for item in data.get("results", []):
                    result[item["original_filename"]] = item["title_id"]
                return result
        except Exception as exc:
            print(f"[Normalize] batch lookup failed: {exc}")
        return {}

    # ------------------------------------------------------------------
    # Card metadata (for three-way hash on PS1/PS2/GC)
    # ------------------------------------------------------------------

    def get_card_meta(self, title_id: str, system: str) -> Optional[dict]:
        """Fetch card metadata for PS1/PS2/GC saves."""
        try:
            if system == "PS1":
                url = f"{self.base_url}/saves/{title_id}/ps1-card/meta?slot=0"
            elif system == "PS2":
                url = f"{self.base_url}/saves/{title_id}/ps2-card/meta?format=ps2"
            elif system == "GC":
                url = f"{self.base_url}/saves/{title_id}/gc-card/meta"
            else:
                return None

            r = requests.get(url, headers=self.headers, timeout=self._timeout)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_save(self, entry: GameEntry, force: bool = False) -> bool:
        """Upload a save to the server. Returns True on success."""
        if entry.save_path is None:
            return False

        # PSP slot dirs might not exist yet (or might be empty)
        if entry.is_psp_slot:
            if not entry.save_path.is_dir():
                return False
        elif not entry.save_path.exists():
            return False

        title_id = entry.title_id
        system = entry.system
        save_path = entry.save_path

        try:
            if entry.is_psp_slot:
                # PSP: create a 3DSS v4 bundle and upload via bundle endpoint
                data = _create_psp_bundle(title_id, save_path)
                url = f"{self.base_url}/saves/{title_id}"
                params = {"source": "psp_emu"}
                if force:
                    params["force"] = "true"
                r = requests.post(
                    url,
                    params=params,
                    data=data,
                    headers={
                        **self.headers,
                        "Content-Type": "application/octet-stream",
                    },
                    timeout=30,
                )
            elif entry.is_multi_file and save_path.is_dir():
                # Generic multi-file: zip and upload as raw
                data = _zip_dir(save_path)
                url = f"{self.base_url}/saves/{title_id}/raw"
                if force:
                    url += "?force=true"
                r = requests.post(
                    url,
                    data=data,
                    headers={
                        **self.headers,
                        "Content-Type": "application/octet-stream",
                    },
                    timeout=30,
                )
            else:
                # Single file: route by system
                with open(save_path, "rb") as f:
                    data = f.read()

                if system == "PS1":
                    url = f"{self.base_url}/saves/{title_id}/ps1-card"
                elif system == "PS2":
                    url = f"{self.base_url}/saves/{title_id}/ps2-card"
                elif system == "GC":
                    url = f"{self.base_url}/saves/{title_id}/gc-card?format=gci"
                else:
                    url = f"{self.base_url}/saves/{title_id}/raw"

                if force:
                    url += "&force=true" if "?" in url else "?force=true"

                r = requests.post(
                    url,
                    data=data,
                    headers={
                        **self.headers,
                        "Content-Type": "application/octet-stream",
                    },
                    timeout=30,
                )

            if r.status_code in (200, 201):
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
            if entry.is_psp_slot:
                # PSP: download bundle and extract to slot directory
                url = f"{self.base_url}/saves/{title_id}"
                r = requests.get(url, headers=self.headers, timeout=30)
                if r.status_code != 200:
                    return False

                files = _parse_psp_bundle(r.content)
                save_path.mkdir(parents=True, exist_ok=True)
                # Clear existing files in slot dir
                for existing in save_path.iterdir():
                    if existing.is_file():
                        existing.unlink()
                # Write extracted files
                for name, data in files:
                    (save_path / name).write_bytes(data)

                server_hash = r.headers.get("X-Save-Hash", "")
                _update_state(title_id, server_hash)
                return True

            elif entry.is_multi_file and save_path.is_dir():
                # Generic multi-file: download raw and unzip
                url = f"{self.base_url}/saves/{title_id}/raw"
                r = requests.get(url, headers=self.headers, timeout=30)
                if r.status_code != 200:
                    return False
                _unzip_to_dir(r.content, save_path)
            else:
                # Single file: route by system
                if system == "PS1":
                    url = f"{self.base_url}/saves/{title_id}/ps1-card?slot=0"
                elif system == "PS2":
                    url = f"{self.base_url}/saves/{title_id}/ps2-card?format=ps2"
                elif system == "GC":
                    url = f"{self.base_url}/saves/{title_id}/gc-card?format=gci"
                else:
                    url = f"{self.base_url}/saves/{title_id}/raw"

                r = requests.get(url, headers=self.headers, timeout=30)
                if r.status_code != 200:
                    return False

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

    def compute_status(
        self, entry: GameEntry, server_saves: dict[str, dict]
    ) -> SyncStatus:
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
            # Never synced -> conflict (safe fallback)
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
