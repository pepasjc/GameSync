"""Server API client for the Steam Deck SaveSync client."""

import hashlib
import io
import re
import shutil
import struct
import time
import zipfile
import zlib
from pathlib import Path
from typing import Callable, Optional

# Matches PS3/PSP/PS1/PS2 product codes: 4 uppercase letters + 5 digits (+ optional suffix)
_PS3_CODE_RE = re.compile(r"^([A-Z]{4}\d{5})")


def _ps3_base_code(title_id: str) -> str | None:
    m = _PS3_CODE_RE.match(title_id)
    return m.group(1) if m else None


def _find_server_save(server_saves: dict, title_id: str) -> "dict | None":
    """
    Look up a server save entry for *title_id*, with fallback prefix matching
    for legacy PS3-style IDs where one side uses only the 9-char product code
    and the other uses a full save-folder name.

    This does not match two different suffixed save-folder names for the same
    base product code because those are distinct PS3 save slots.
    """
    info = server_saves.get(title_id)
    if info is not None:
        return info
    code9 = _ps3_base_code(title_id)
    if not code9:
        return None

    is_bare_local = title_id == code9
    if is_bare_local:
        # Legacy local bare-code entry: match any server save rooted in this code.
        info = server_saves.get(code9)
        if info is not None:
            return info
        for sid, sinfo in server_saves.items():
            if _ps3_base_code(sid) == code9:
                return sinfo
        return None

    # Full local save-folder name: only fall back to the exact bare-code server
    # entry for legacy data, never to a different suffixed folder.
    return server_saves.get(code9)

import requests

from scanner.models import GameEntry, SyncStatus
from config import (
    load_saturn_archive_state,
    load_sync_state,
    save_saturn_archive_state,
    save_sync_state,
)
from saturn_format import (
    convert_saturn_save_format,
    extract_saturn_save_set,
    list_saturn_archive_names,
    merge_saturn_save_set,
    normalize_saturn_save,
)


# ──────────────────────────────────────────────────────────────────────────────
# 3DSS Bundle format (v4) — for PSP saves
# ──────────────────────────────────────────────────────────────────────────────

_BUNDLE_MAGIC = b"3DSS"
_BUNDLE_V4 = 4
_BUNDLE_V5 = 5
_PS3_BUNDLE_SKIP = {"PARAM.PFD"}


def _is_shared_saturn_backup(path: Path | None) -> bool:
    return path is not None and path.name.strip().lower() == "backup.bin"


def _saturn_format_for_path(path: Path | None) -> str:
    if path is None:
        return "mednafen"
    if _is_shared_saturn_backup(path) or path.suffix.lower() == ".bin":
        return "yabasanshiro"
    if path.suffix.lower() == ".srm":
        return "yabause"
    return "mednafen"


def _get_saturn_archive_names(title_id: str) -> list[str]:
    state = load_saturn_archive_state()
    values = state.get((title_id or "").upper(), [])
    return [str(value).strip().upper() for value in values if str(value).strip()]


def _set_saturn_archive_names(title_id: str, archive_names: list[str]) -> None:
    state = load_saturn_archive_state()
    key = (title_id or "").upper()
    values = sorted(
        {
            str(name).strip().upper()
            for name in archive_names
            if str(name).strip()
        }
    )
    if values:
        state[key] = values
    else:
        state.pop(key, None)
    save_saturn_archive_state(state)


def _lookup_saturn_archive_candidates(
    base_url: str,
    headers: dict[str, str],
    title_id: str,
    archive_names: list[str],
    timeout: int = 30,
) -> list[dict]:
    r = requests.post(
        f"{base_url}/titles/saturn-archives",
        json={"title_id": title_id, "archive_names": archive_names},
        headers={**headers, "Content-Type": "application/json"},
        timeout=timeout,
    )
    r.raise_for_status()
    body = r.json()
    return body.get("results", []) if isinstance(body, dict) else []


def _resolve_saturn_archive_selection(
    base_url: str,
    headers: dict[str, str],
    title_id: str,
    path: Path,
    timeout: int = 30,
) -> list[str]:
    archive_names = list_saturn_archive_names(path.read_bytes())
    persisted = [
        name
        for name in _get_saturn_archive_names(title_id)
        if name in {archive.upper() for archive in archive_names}
    ]
    if persisted:
        return persisted

    results = _lookup_saturn_archive_candidates(
        base_url, headers, title_id, archive_names, timeout=timeout
    )
    exact_selected: list[str] = []
    includes_selected: list[str] = []
    has_unknown = False
    for result in results:
        status = str(result.get("status") or "").strip().lower()
        if status == "unknown":
            has_unknown = True
        for archive_name in result.get("archive_names", []):
            normalized = str(archive_name).strip().upper()
            if not normalized:
                continue
            if status == "exact_current" and normalized not in exact_selected:
                exact_selected.append(normalized)
            elif status == "includes_current" and normalized not in includes_selected:
                includes_selected.append(normalized)

    selected: list[str] = []
    if exact_selected and not includes_selected:
        selected = exact_selected
    elif exact_selected and has_unknown:
        # Mirror Android's safer behavior: keep exact matches even if the shared
        # container has unrelated unknown families, but don't silently absorb
        # broader "includes_current" matches in that case.
        selected = exact_selected
    elif not has_unknown and includes_selected:
        selected = exact_selected + [
            name for name in includes_selected if name not in exact_selected
        ]

    if selected:
        _set_saturn_archive_names(title_id, selected)
    return selected


def _canonical_saturn_payload(
    title_id: str,
    path: Path,
    base_url: str,
    headers: dict[str, str],
    timeout: int = 30,
) -> tuple[bytes, list[str] | None]:
    data = path.read_bytes()
    if _is_shared_saturn_backup(path):
        archive_names = _get_saturn_archive_names(title_id)
        if not archive_names:
            archive_names = _resolve_saturn_archive_selection(
                base_url, headers, title_id, path, timeout=timeout
            )
        if not archive_names:
            return b"", None
        return extract_saturn_save_set(data, archive_names), archive_names
    canonical = normalize_saturn_save(data)
    return canonical, [name.upper() for name in list_saturn_archive_names(canonical)]


def _refresh_saturn_entry_metadata(
    entry: GameEntry,
    canonical_data: bytes,
    archive_names: list[str] | None = None,
) -> None:
    if not canonical_data:
        return
    entry.save_hash = hashlib.sha256(canonical_data).hexdigest()
    entry.save_size = len(canonical_data)
    entry.save_mtime = time.time()
    if archive_names:
        _set_saturn_archive_names(entry.title_id, archive_names)


def _create_dir_bundle(
    title_id: str,
    slot_dir: Path,
    skip_names: set[str] | None = None,
) -> bytes:
    """
    Create a 3DSS bundle from a save directory.
    Files are included recursively, sorted by relative path.
    """
    files: list[tuple[str, bytes, bytes]] = []  # (name, data, sha256_hash)
    skip = {name.upper() for name in (skip_names or set())}

    for fp in sorted(slot_dir.rglob("*")):
        if not fp.is_file():
            continue
        if fp.name.upper() in skip:
            continue
        data = fp.read_bytes()
        h = hashlib.sha256(data).digest()
        files.append((fp.relative_to(slot_dir).as_posix(), data, h))

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

    # Build v4/v5 header
    header = bytearray()
    header += _BUNDLE_MAGIC
    raw_tid = title_id.upper().encode("ascii")
    if len(raw_tid) <= 31:
        header += struct.pack("<I", _BUNDLE_V4)
        header += raw_tid[:31].ljust(32, b"\x00")
    else:
        header += struct.pack("<I", _BUNDLE_V5)
        header += raw_tid[:63].ljust(64, b"\x00")
    header += struct.pack("<I", timestamp)
    header += struct.pack("<I", len(files))
    header += struct.pack("<I", uncompressed_size)

    return bytes(header) + compressed


def _parse_dir_bundle(data: bytes) -> list[tuple[str, bytes]]:
    """
    Parse a 3DSS v3/v4/v5 bundle, returning list of (filename, file_data).
    Used for downloading PSP/PS3 directory saves from the server.
    """
    if len(data) < 4 or data[:4] != _BUNDLE_MAGIC:
        raise ValueError("Not a valid 3DSS bundle")

    version = struct.unpack_from("<I", data, 4)[0]

    if version == 5:
        offset = 4 + 4 + 64 + 4
        file_count = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        uncompressed_size = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        payload = zlib.decompress(data[offset:])
    elif version == 4:
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
        # ROM downloads need a much longer read timeout than the generic
        # API calls: CHD/RVZ extraction runs server-side (chdman /
        # DolphinTool) *before* the first byte hits the wire, so the
        # client has to wait through the whole extraction.  Split into
        # (connect, read) so connection problems still fail fast.
        self._download_timeout = (30, 900)
        # Populated by :meth:`download_rom` so the caller can surface a
        # useful message in the failure dialog (HTTP status, extraction
        # stderr the server returned in the body, timeout, ...).
        self.last_download_error: str = ""

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
        Returns {title_id: {save_hash, last_sync, save_size, name, ...}}.
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
    # ROM catalog (server-hosted ROMs available for download)
    # ------------------------------------------------------------------

    def list_roms(self, system: Optional[str] = None) -> list[dict]:
        """
        Return the server's ROM catalog.  Each entry is a dict with at least
        ``rom_id``, ``title_id``, ``system``, ``name``, ``filename``, ``size``
        and may carry an ``extract_format`` hint for CHD/RVZ discs.

        Passing ``system`` narrows the server-side filter so we aren't paging
        through unrelated catalogs when the caller only cares about one
        console.
        """
        params: dict[str, str] = {}
        if system:
            params["system"] = system.upper()
        try:
            r = requests.get(
                f"{self.base_url}/roms",
                params=params,
                headers=self.headers,
                timeout=self._timeout,
            )
            if r.status_code == 200:
                return list(r.json().get("roms", []))
        except Exception as exc:
            print(f"[ROMs] list failed: {exc}")
        return []

    def find_roms_for_title(self, title_id: str, system: str) -> list[dict]:
        """
        Return ROM catalog entries whose ``title_id`` matches ``title_id``.

        Multi-disc games yield multiple rows (same ``title_id``, distinct
        ``rom_id``); the UI can surface them individually so the user can
        pick a specific disc.
        """
        if not title_id:
            return []
        return [r for r in self.list_roms(system) if r.get("title_id") == title_id]

    def download_rom(
        self,
        rom_id: str,
        target_path: Path,
        extract_format: Optional[str] = None,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """
        Stream a ROM to ``target_path``.

        If the catalog entry carries an ``extract_format`` hint (e.g. ``cue``
        for CHD→CUE/BIN, ``gdi``, ``iso`` / ``cso`` for PSP, ``rvz`` for
        Dolphin), pass it through so the server returns the extracted payload.
        ``progress_cb(downloaded, total)`` is invoked after each chunk; total
        is 0 when the server did not advertise ``Content-Length``.
        """
        self.last_download_error = ""
        if not rom_id:
            self.last_download_error = "No ROM id supplied."
            return False

        params: dict[str, str] = {}
        if extract_format:
            params["extract"] = extract_format

        try:
            with requests.get(
                f"{self.base_url}/roms/{rom_id}",
                params=params,
                headers=self.headers,
                stream=True,
                # ROMs can be multi-GB and CHD/RVZ extraction runs in a
                # server-side subprocess (chdman, DolphinTool) before the
                # first byte arrives.  Use a long read timeout so we wait
                # for extraction to finish; keep the connect timeout
                # small so unreachable servers still fail fast.
                timeout=self._download_timeout,
            ) as r:
                if r.status_code != 200:
                    # Surface the server's error body — the /roms endpoint
                    # uses plain-text responses for extraction failures
                    # ("chdman not installed", "Extraction failed: ..."),
                    # and those are what the user actually needs to see.
                    detail = ""
                    try:
                        detail = r.text.strip()
                    except Exception:
                        pass
                    self.last_download_error = (
                        f"HTTP {r.status_code}" + (f": {detail}" if detail else "")
                    )
                    print(f"[ROMs] download HTTP {r.status_code} for {rom_id}: {detail}")
                    return False
                total = int(r.headers.get("Content-Length", "0") or 0)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = target_path.with_suffix(target_path.suffix + ".part")
                downloaded = 0
                try:
                    with open(tmp_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=65536):
                            if not chunk:
                                continue
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_cb is not None:
                                try:
                                    progress_cb(downloaded, total)
                                except Exception:
                                    pass
                    # Atomic rename so a cancelled/partial download never
                    # leaves a half-written ROM the scanner would pick up.
                    tmp_path.replace(target_path)
                except Exception:
                    try:
                        tmp_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    raise
            return True
        except requests.exceptions.Timeout:
            self.last_download_error = (
                "Timed out waiting for the server.  For CHD/RVZ files the "
                "server has to extract them before sending — very large "
                "games can take several minutes."
            )
            print(f"[ROMs] download timed out for {rom_id}")
            return False
        except Exception as exc:
            self.last_download_error = str(exc) or exc.__class__.__name__
            print(f"[ROMs] download failed for {rom_id}: {exc}")
            return False

    # ------------------------------------------------------------------
    # ROM normalization / serial lookup
    # ------------------------------------------------------------------

    def normalize_batch(self, roms: list[dict[str, str]]) -> dict[tuple[str, str], str]:
        """
        Call POST /api/v1/normalize/batch to resolve ROM filenames to title_ids.

        Args:
            roms: list of {"system": "PS1", "filename": "Game Name (USA).chd"}

        Returns:
            dict mapping (system, original_filename) -> title_id
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
                result: dict[tuple[str, str], str] = {}
                for item in data.get("results", []):
                    key = (str(item["system"]).upper(), item["original_filename"])
                    result[key] = item["title_id"]
                return result
        except Exception as exc:
            print(f"[Normalize] batch lookup failed: {exc}")
        return {}

    # ------------------------------------------------------------------
    # Game name / platform lookup
    # ------------------------------------------------------------------

    def lookup_names(self, codes: list[str]) -> dict:
        """
        Call POST /api/v1/titles/names to resolve product codes to game names.

        Args:
            codes: list of product codes (e.g. ["ULUS10567", "0004000000055D00"])

        Returns:
            {"names": {"CODE": "Game Name", ...},
             "types": {"CODE": "PSP", ...},
             "retail_serials": {"PSN_CODE": "RETAIL_SERIAL", ...}}
        """
        if not codes:
            return {"names": {}, "types": {}, "retail_serials": {}}
        try:
            r = requests.post(
                f"{self.base_url}/titles/names",
                json={"codes": codes},
                headers={**self.headers, "Content-Type": "application/json"},
                timeout=self._timeout,
            )
            if r.status_code == 200:
                return r.json()
        except Exception as exc:
            print(f"[LookupNames] batch lookup failed: {exc}")
        return {"names": {}, "types": {}, "retail_serials": {}}

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
        remote_title_id = entry.server_title_id or title_id
        system = entry.system
        save_path = entry.save_path

        try:
            if entry.is_psp_slot:
                # PSP: create a 3DSS v4 bundle and upload via bundle endpoint
                data = _create_dir_bundle(title_id, save_path)
                url = f"{self.base_url}/saves/{remote_title_id}"
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
                # Multi-file directory saves (for example RPCS3) use the bundle endpoint.
                skip = _PS3_BUNDLE_SKIP if system == "PS3" else None
                data = _create_dir_bundle(title_id, save_path, skip_names=skip)
                url = f"{self.base_url}/saves/{remote_title_id}"
                params = {"source": "ps3_emu"} if system == "PS3" else {}
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
            else:
                # Single file: route by system
                if system == "SAT":
                    data, archive_names = _canonical_saturn_payload(
                        title_id,
                        save_path,
                        self.base_url,
                        self.headers,
                        timeout=30,
                    )
                    if not data:
                        return False
                    _refresh_saturn_entry_metadata(entry, data, archive_names)
                else:
                    with open(save_path, "rb") as f:
                        data = f.read()

                if system == "PS1":
                    url = f"{self.base_url}/saves/{remote_title_id}/ps1-card"
                elif system == "PS2":
                    url = f"{self.base_url}/saves/{remote_title_id}/ps2-card"
                elif system == "GC":
                    url = f"{self.base_url}/saves/{remote_title_id}/gc-card?format=gci"
                else:
                    url = f"{self.base_url}/saves/{remote_title_id}/raw"

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
                local_hash = (
                    hashlib.sha256(data).hexdigest()
                    if not entry.is_multi_file and not entry.is_psp_slot
                    else entry.save_hash or ""
                )
                if system == "SAT":
                    entry.save_hash = local_hash
                    entry.save_size = len(data)
                    entry.save_mtime = time.time()
                _update_state(title_id, local_hash)
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
        remote_title_id = entry.server_title_id or title_id
        system = entry.system
        save_path = entry.save_path

        try:
            if entry.is_psp_slot:
                # PSP: download bundle and extract to slot directory
                url = f"{self.base_url}/saves/{remote_title_id}"
                r = requests.get(url, headers=self.headers, timeout=30)
                if r.status_code != 200:
                    return False

                files = _parse_dir_bundle(r.content)
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

            elif entry.is_multi_file:
                # Multi-file directory saves (for example RPCS3) use the bundle endpoint.
                url = f"{self.base_url}/saves/{remote_title_id}"
                r = requests.get(url, headers=self.headers, timeout=30)
                if r.status_code != 200:
                    return False
                files = _parse_dir_bundle(r.content)
                if save_path.exists():
                    shutil.rmtree(save_path)
                save_path.mkdir(parents=True, exist_ok=True)
                for name, data in files:
                    target = save_path / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            else:
                # Single file: route by system
                if system == "PS1":
                    url = f"{self.base_url}/saves/{remote_title_id}/ps1-card?slot=0"
                elif system == "PS2":
                    url = f"{self.base_url}/saves/{remote_title_id}/ps2-card?format=ps2"
                elif system == "GC":
                    url = f"{self.base_url}/saves/{remote_title_id}/gc-card?format=gci"
                else:
                    url = f"{self.base_url}/saves/{remote_title_id}/raw"

                r = requests.get(url, headers=self.headers, timeout=30)
                if r.status_code != 200:
                    return False

                save_path.parent.mkdir(parents=True, exist_ok=True)
                if system == "SAT":
                    archive_names = [
                        name.upper() for name in list_saturn_archive_names(r.content)
                    ]
                    saturn_format = _saturn_format_for_path(save_path)
                    if saturn_format == "yabasanshiro":
                        existing_data = save_path.read_bytes() if save_path.exists() else None
                        data = merge_saturn_save_set(
                            existing_data,
                            r.content,
                            "yabasanshiro",
                        )
                    else:
                        data = convert_saturn_save_format(r.content, saturn_format)
                    with open(save_path, "wb") as f:
                        f.write(data)
                    _refresh_saturn_entry_metadata(entry, r.content, archive_names)
                else:
                    with open(save_path, "wb") as f:
                        f.write(r.content)
                    entry.save_hash = hashlib.sha256(r.content).hexdigest()
                    entry.save_size = len(r.content)
                    entry.save_mtime = time.time()

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

        server_info = _find_server_save(server_saves, entry.title_id)
        server_hash = server_info.get("save_hash") if server_info else None

        local_hash = entry.save_hash
        if (
            entry.system == "SAT"
            and entry.save_path is not None
            and entry.save_path.exists()
        ):
            try:
                canonical_saturn, archive_names = _canonical_saturn_payload(
                    entry.title_id,
                    entry.save_path,
                    self.base_url,
                    self.headers,
                    timeout=30,
                )
                if canonical_saturn:
                    _refresh_saturn_entry_metadata(entry, canonical_saturn, archive_names)
                    local_hash = entry.save_hash
            except Exception:
                pass

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
