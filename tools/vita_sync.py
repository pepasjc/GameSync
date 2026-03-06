#!/usr/bin/env python3
"""PS Vita Save Sync - Sync PS Vita and PSP-on-Vita saves via VitaShell FTP.

Connects to the PS Vita FTP server (started from VitaShell) to sync saves.

Supported save types:
  1. Native PS Vita saves:   ux0:user/00/savedata/<TITLEID>/
  2. PSP emu saves on Vita:  ux0:pspemu/PSP/SAVEDATA/<GAMEID>/

Title IDs:
  - PS Vita native: PCSE00082, PCSB12345, PCSG00001, etc. (9 chars)
  - PSP emu:        ULUS10272, ELES01234, etc. (9 chars, same as native PSP)

Requirements:
  - VitaShell installed on PS Vita
  - FTP server started in VitaShell (Select button)
  - Network connection on both PC and Vita

Usage:
    python vita_sync.py --vita-ip 192.168.1.150 --server http://192.168.1.201:8000 --api-key mykey
    python vita_sync.py --vita-ip 192.168.1.150 --vita-port 1337 --server ... --api-key ... --dry-run
    python vita_sync.py --vita-ip 192.168.1.150 --server ... --api-key ... --psp-emu-only
    python vita_sync.py --vita-ip 192.168.1.150 --server ... --api-key ... --native-only
"""

import argparse
import ftplib
import hashlib
import io
import json
import random
import re
import struct
import sys
import time
import zlib
from pathlib import Path, PurePosixPath
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# --- Constants ---

BUNDLE_MAGIC = b"3DSS"
BUNDLE_VERSION_V3 = 3
SYNC_DIR_NAME = ".vita_sync"

# Vita save paths (FTP paths)
VITA_SAVEDATA_PATH = "ux0:/user/00/savedata"
PSP_SAVEDATA_PATH = "ux0:/pspemu/PSP/SAVEDATA"

_VITA_CODE_RE = re.compile(r"^PCS[A-Z]\d{5}$")   # PCSE00082, PCSB12345, etc.
_PSP_CODE_RE = re.compile(r"^[A-Z]{4}\d{5}$")     # ULUS10272, ELES01234, etc.

VITA_FTP_PORT = 1337


# --- FTP client ---

class VitaFTP:
    """Simple FTP client for PS Vita (VitaShell FTP server)."""

    def __init__(self, host: str, port: int = VITA_FTP_PORT, timeout: int = 30):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.ftp: ftplib.FTP | None = None

    def connect(self):
        self.ftp = ftplib.FTP()
        self.ftp.connect(self.host, self.port, timeout=self.timeout)
        self.ftp.login()  # VitaShell FTP has no authentication
        self.ftp.set_pasv(True)

    def disconnect(self):
        if self.ftp:
            try:
                self.ftp.quit()
            except Exception:
                pass
            self.ftp = None

    def list_dirs(self, path: str) -> list[str]:
        """List directory names under the given FTP path."""
        dirs = []
        try:
            entries = []
            self.ftp.retrlines(f"LIST {path}", entries.append)
            for entry in entries:
                parts = entry.split(None, 8)
                if len(parts) >= 9 and entry.startswith("d"):
                    name = parts[8]
                    if name not in (".", ".."):
                        dirs.append(name)
        except ftplib.error_perm:
            pass
        return dirs

    def list_files(self, path: str) -> list[tuple[str, int]]:
        """List files (name, size) under the given FTP path (non-recursive)."""
        files = []
        try:
            entries = []
            self.ftp.retrlines(f"LIST {path}", entries.append)
            for entry in entries:
                parts = entry.split(None, 8)
                if len(parts) >= 9 and not entry.startswith("d"):
                    name = parts[8]
                    try:
                        size = int(parts[4])
                    except ValueError:
                        size = 0
                    files.append((name, size))
        except ftplib.error_perm:
            pass
        return files

    def list_files_recursive(self, path: str) -> list[tuple[str, int]]:
        """Recursively list all files under an FTP path as (relative_path, size)."""
        results = []
        self._walk(path, path, results)
        return results

    def _walk(self, base: str, current: str, results: list):
        try:
            entries = []
            self.ftp.retrlines(f"LIST {current}", entries.append)
        except ftplib.error_perm:
            return

        for entry in entries:
            parts = entry.split(None, 8)
            if len(parts) < 9:
                continue
            name = parts[8]
            if name in (".", ".."):
                continue
            full_path = f"{current}/{name}"
            if entry.startswith("d"):
                self._walk(base, full_path, results)
            else:
                try:
                    size = int(parts[4])
                except ValueError:
                    size = 0
                rel = full_path[len(base):].lstrip("/")
                results.append((rel, size))

    def download_file(self, ftp_path: str) -> bytes:
        """Download a file from the Vita and return its contents."""
        buf = io.BytesIO()
        self.ftp.retrbinary(f"RETR {ftp_path}", buf.write)
        return buf.getvalue()

    def upload_file(self, ftp_path: str, data: bytes):
        """Upload data to a file on the Vita."""
        # Ensure parent directory exists
        parent = ftp_path.rsplit("/", 1)[0]
        self._mkdirs(parent)
        self.ftp.storbinary(f"STOR {ftp_path}", io.BytesIO(data))

    def _mkdirs(self, path: str):
        """Recursively create directories on the Vita."""
        parts = [p for p in path.split("/") if p]
        current = ""
        for part in parts:
            current = f"{current}/{part}"
            try:
                self.ftp.mkd(current)
            except ftplib.error_perm:
                pass  # Directory probably already exists


# --- Save scanning ---

def scan_vita_saves(ftp: VitaFTP) -> list[dict]:
    """Scan native Vita save directories."""
    saves = []
    dirs = ftp.list_dirs(VITA_SAVEDATA_PATH)
    for name in dirs:
        code = name.upper()
        if not _VITA_CODE_RE.match(code):
            continue
        ftp_path = f"{VITA_SAVEDATA_PATH}/{name}"
        files = ftp.list_files_recursive(ftp_path)
        if not files:
            continue
        total_size = sum(s for _, s in files)
        saves.append({
            "game_id": code,
            "ftp_base": ftp_path,
            "name": code,
            "files": files,  # list of (rel_path, size)
            "total_size": total_size,
            "platform": "vita",
        })
    return saves


def scan_psp_emu_saves(ftp: VitaFTP) -> list[dict]:
    """Scan PSP emulation save directories on Vita."""
    saves = []
    dirs = ftp.list_dirs(PSP_SAVEDATA_PATH)
    for name in dirs:
        code = name.upper()
        if not _PSP_CODE_RE.match(code):
            continue
        ftp_path = f"{PSP_SAVEDATA_PATH}/{name}"
        files = ftp.list_files_recursive(ftp_path)
        if not files:
            continue
        total_size = sum(s for _, s in files)
        saves.append({
            "game_id": code,
            "ftp_base": ftp_path,
            "name": code,
            "files": files,
            "total_size": total_size,
            "platform": "psp_emu",
        })
    return saves


# --- Game name lookup ---

def load_name_database(db_path: Path) -> dict[str, str]:
    names = {}
    if not db_path.exists():
        return names
    with open(db_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "," not in line:
                continue
            parts = line.split(",", 1)
            if len(parts) == 2:
                code = parts[0].strip().upper()
                name = parts[1].strip()
                if code and name:
                    names[code] = name
    return names


# --- Hash computation ---

def hash_ftp_save(ftp: VitaFTP, ftp_base: str, files: list[tuple[str, int]]) -> str:
    """Download all files and compute SHA-256 hash over their contents (sorted by path)."""
    h = hashlib.sha256()
    for rel_path, _ in sorted(files, key=lambda x: x[0]):
        data = ftp.download_file(f"{ftp_base}/{rel_path}")
        h.update(data)
    return h.hexdigest()


# --- Bundle v3 format ---

def create_bundle_v3(game_id: str, ftp: VitaFTP, ftp_base: str,
                     files: list[tuple[str, int]]) -> bytes:
    """Create a v3 bundle from Vita FTP save files."""
    timestamp = int(time.time())
    file_data_cache = {}

    # Download all files
    for rel_path, _ in files:
        data = ftp.download_file(f"{ftp_base}/{rel_path}")
        file_data_cache[rel_path] = data

    sorted_files = sorted(file_data_cache.items())

    payload_parts = []
    # File table
    for rel_path, data in sorted_files:
        path_bytes = rel_path.encode("utf-8")
        payload_parts.append(struct.pack("<H", len(path_bytes)))
        payload_parts.append(path_bytes)
        payload_parts.append(struct.pack("<I", len(data)))
        payload_parts.append(hashlib.sha256(data).digest())
    # File data
    for _, data in sorted_files:
        payload_parts.append(data)

    payload = b"".join(payload_parts)
    compressed = zlib.compress(payload, level=6)

    game_id_bytes = game_id.upper().encode("ascii")[:15].ljust(16, b"\x00")
    header = []
    header.append(BUNDLE_MAGIC)
    header.append(struct.pack("<I", BUNDLE_VERSION_V3))
    header.append(game_id_bytes)
    header.append(struct.pack("<I", timestamp))
    header.append(struct.pack("<I", len(sorted_files)))
    header.append(struct.pack("<I", len(payload)))
    header.append(compressed)

    return b"".join(header)


def parse_bundle_v3(data: bytes) -> dict[str, bytes]:
    """Parse a v3 bundle and return dict of {relative_path: file_data}."""
    if len(data) < 36:
        raise ValueError("Bundle too small for v3 header")

    magic = data[0:4]
    if magic != BUNDLE_MAGIC:
        raise ValueError(f"Invalid magic: {magic!r}")

    version = struct.unpack_from("<I", data, 4)[0]
    if version != BUNDLE_VERSION_V3:
        raise ValueError(f"Expected v3 bundle, got version {version}")

    file_count = struct.unpack_from("<I", data, 28)[0]
    uncompressed_size = struct.unpack_from("<I", data, 32)[0]

    payload = zlib.decompress(data[36:])
    if len(payload) != uncompressed_size:
        raise ValueError("Decompressed size mismatch")

    offset = 0
    files_info = []
    for _ in range(file_count):
        path_len = struct.unpack_from("<H", payload, offset)[0]
        offset += 2
        path = payload[offset:offset + path_len].decode("utf-8")
        offset += path_len
        file_size = struct.unpack_from("<I", payload, offset)[0]
        offset += 4
        offset += 32  # skip SHA-256
        files_info.append((path, file_size))

    result = {}
    for path, size in files_info:
        result[path] = payload[offset:offset + size]
        offset += size

    return result


# --- Sync state ---

def get_sync_dir() -> Path:
    """Get the local sync state directory (stored on PC)."""
    sync_dir = Path.home() / ".vita_sync"
    sync_dir.mkdir(exist_ok=True)
    return sync_dir


def get_console_id(vita_ip: str, sync_dir: Path) -> str:
    """Get or generate a console ID keyed by Vita IP address."""
    safe_ip = vita_ip.replace(".", "_")
    id_file = sync_dir / f"console_id_{safe_ip}"
    if id_file.exists():
        cid = id_file.read_text().strip()
        if cid:
            return cid

    rand_hex = "%08x" % random.getrandbits(32)
    cid = f"vita_{rand_hex}"
    id_file.write_text(cid)
    print(f"Generated new console ID for this Vita ({vita_ip}): {cid}")
    return cid


def load_state(sync_dir: Path, console_id: str) -> dict:
    state_file = sync_dir / f"state_{console_id}.json"
    if state_file.exists():
        return json.loads(state_file.read_text())
    return {}


def save_state(sync_dir: Path, console_id: str, state: dict):
    state_file = sync_dir / f"state_{console_id}.json"
    state_file.write_text(json.dumps(state, indent=2))


# --- HTTP client ---

def api_request(server: str, path: str, api_key: str, console_id: str,
                method: str = "GET", data: bytes | None = None,
                content_type: str = "application/octet-stream") -> tuple[int, bytes]:
    url = f"{server}/api/v1{path}"
    headers = {
        "X-API-Key": api_key,
        "X-Console-ID": console_id,
    }
    if data is not None:
        headers["Content-Type"] = content_type
        req = Request(url, data=data, headers=headers, method=method)
    else:
        req = Request(url, headers=headers, method=method)

    try:
        with urlopen(req, timeout=60) as resp:
            return resp.status, resp.read()
    except HTTPError as e:
        return e.code, e.read()
    except URLError as e:
        print(f"  Connection error: {e.reason}")
        return 0, b""


def api_get(server: str, path: str, api_key: str, console_id: str) -> tuple[int, bytes]:
    return api_request(server, path, api_key, console_id, method="GET")


def api_post_bundle(server: str, path: str, api_key: str, console_id: str,
                    bundle: bytes) -> tuple[int, bytes]:
    return api_request(server, path, api_key, console_id, method="POST", data=bundle)


def api_post_json(server: str, path: str, api_key: str, console_id: str,
                  payload: dict) -> tuple[int, bytes]:
    data = json.dumps(payload).encode("utf-8")
    return api_request(server, path, api_key, console_id, method="POST",
                        data=data, content_type="application/json")


# --- Main sync logic ---

def _build_sync_plan(games: list[dict], server: str, api_key: str,
                     sync_console_id: str, state: dict) -> tuple[set, set, set, set, set]:
    """Send a sync request for the given games and return the plan sets.

    sync_console_id is the console_id used in the JSON body (may differ from
    the X-Console-ID header; for PSP saves we always use "psp").
    """
    titles_meta = []
    for g in games:
        meta = {
            "title_id": g["game_id"],
            "save_hash": g["save_hash"],
            "timestamp": int(time.time()),
            "size": g["total_size"],
        }
        last_hash = state.get(g["game_id"])
        if last_hash:
            meta["last_synced_hash"] = last_hash
        titles_meta.append(meta)

    sync_request = {"console_id": sync_console_id, "titles": titles_meta}
    status, resp = api_post_json(server, "/sync", api_key, sync_console_id, sync_request)
    if status != 200:
        print(f"Sync request failed (HTTP {status})")
        if resp:
            print(f"  {resp.decode('utf-8', errors='replace')[:200]}")
        return set(), set(), set(), set(), set()

    plan = json.loads(resp)
    return (
        set(plan.get("upload", [])),
        set(plan.get("download", [])),
        set(plan.get("conflict", [])),
        set(plan.get("up_to_date", [])),
        set(plan.get("server_only", [])),
    )


def do_sync(games: list[dict], ftp: VitaFTP, server: str, api_key: str,
            console_id: str, state: dict, dry_run: bool = False) -> dict:
    if not games:
        print("No saves found.")
        return state

    print(f"\nComputing hashes for {len(games)} save(s) (downloading files)...")
    for g in games:
        print(f"  Hashing: {g['name']}...")
        g["save_hash"] = hash_ftp_save(ftp, g["ftp_base"], g["files"])

    # Split into PSP and Vita groups — they use different server console slots.
    # PSP saves (from the Vita's PSP emulator) share the canonical "psp" slot
    # with native PSP hardware and the psp_sync.py tool.
    psp_games  = [g for g in games if g["platform"] != "vita"]
    vita_games = [g for g in games if g["platform"] == "vita"]

    games_by_id = {g["game_id"]: g for g in games}

    upload_ids:     set[str] = set()
    download_ids:   set[str] = set()
    conflict_ids:   set[str] = set()
    up_to_date_ids: set[str] = set()
    server_only_ids: set[str] = set()

    if vita_games:
        print("Sending sync request (Vita saves)...")
        u, d, c, ok, so = _build_sync_plan(vita_games, server, api_key, console_id, state)
        upload_ids |= u; download_ids |= d; conflict_ids |= c
        up_to_date_ids |= ok; server_only_ids |= so

    if psp_games:
        print("Sending sync request (PSP saves)...")
        u, d, c, ok, so = _build_sync_plan(psp_games, server, api_key, "psp", state)
        upload_ids |= u; download_ids |= d; conflict_ids |= c
        up_to_date_ids |= ok; server_only_ids |= so

    print(f"\nSync plan:")
    print(f"  Upload:     {len(upload_ids)}")
    print(f"  Download:   {len(download_ids)}")
    print(f"  Up to date: {len(up_to_date_ids)}")
    print(f"  Conflicts:  {len(conflict_ids)}")
    print(f"  Server only:{len(server_only_ids)}")

    if dry_run:
        for tid in upload_ids:
            g = games_by_id.get(tid)
            print(f"  Would upload:   {g['name'] if g else tid} ({tid})")
        for tid in download_ids | server_only_ids:
            g = games_by_id.get(tid)
            print(f"  Would download: {g['name'] if g else tid} ({tid})")
        return state

    # Uploads
    for tid in upload_ids:
        g = games_by_id.get(tid)
        if not g:
            continue
        print(f"  Uploading: {g['name']} ({tid})...")
        bundle = create_bundle_v3(tid, ftp, g["ftp_base"], g["files"])
        source = "vita" if g["platform"] == "vita" else "psp_emu"
        s, r = api_post_bundle(server, f"/saves/{tid}?force=true&source={source}",
                               api_key, console_id, bundle)
        if s == 200:
            state[tid] = g["save_hash"]
            print(f"    OK ({g['total_size'] // 1024} KB)")
        else:
            print(f"    Failed (HTTP {s}): {r.decode('utf-8', errors='replace')[:100]}")

    # Downloads
    for tid in list(download_ids) + list(server_only_ids):
        g = games_by_id.get(tid)
        name = g["name"] if g else tid
        # Determine target FTP path
        if g:
            ftp_base = g["ftp_base"]
        else:
            # Server-only: guess path based on title ID format
            if _VITA_CODE_RE.match(tid):
                ftp_base = f"{VITA_SAVEDATA_PATH}/{tid}"
            else:
                ftp_base = f"{PSP_SAVEDATA_PATH}/{tid}"

        print(f"  Downloading: {name} ({tid})...")
        s, r = api_get(server, f"/saves/{tid}", api_key, console_id)
        if s == 200:
            try:
                file_data = parse_bundle_v3(r)
                for rel_path, data in file_data.items():
                    ftp.upload_file(f"{ftp_base}/{rel_path}", data)
                # Recompute hash
                written = [(p, len(d)) for p, d in file_data.items()]
                new_hash = hash_ftp_save(ftp, ftp_base, written)
                state[tid] = new_hash
                total = sum(len(d) for d in file_data.values())
                print(f"    OK ({total // 1024} KB, {len(file_data)} file(s))")
            except Exception as e:
                print(f"    Error: {e}")
        else:
            print(f"    Failed (HTTP {s})")

    # Conflicts
    for tid in conflict_ids:
        g = games_by_id.get(tid)
        name = g["name"] if g else tid
        print(f"\n  CONFLICT: {name} ({tid})")
        print(f"    Local hash:  {g['save_hash'][:16]}...")
        print(f"    [u] Upload  [d] Download  [s] Skip")
        choice = input(f"    > ").strip().lower()

        if choice == "u" and g:
            bundle = create_bundle_v3(tid, ftp, g["ftp_base"], g["files"])
            source = "vita" if g["platform"] == "vita" else "psp_emu"
            s, r = api_post_bundle(server, f"/saves/{tid}?force=true&source={source}",
                                   api_key, console_id, bundle)
            if s == 200:
                state[tid] = g["save_hash"]
                print(f"    Uploaded OK")
            else:
                print(f"    Failed (HTTP {s})")
        elif choice == "d":
            ftp_base = g["ftp_base"] if g else (
                f"{VITA_SAVEDATA_PATH}/{tid}" if _VITA_CODE_RE.match(tid)
                else f"{PSP_SAVEDATA_PATH}/{tid}"
            )
            s, r = api_get(server, f"/saves/{tid}", api_key, console_id)
            if s == 200:
                try:
                    file_data = parse_bundle_v3(r)
                    for rel_path, data in file_data.items():
                        ftp.upload_file(f"{ftp_base}/{rel_path}", data)
                    written = [(p, len(d)) for p, d in file_data.items()]
                    state[tid] = hash_ftp_save(ftp, ftp_base, written)
                    print(f"    Downloaded OK")
                except Exception as e:
                    print(f"    Error: {e}")
            else:
                print(f"    Failed (HTTP {s})")
        else:
            print(f"    Skipped")

    for tid in up_to_date_ids:
        g = games_by_id.get(tid)
        if g:
            state[tid] = g["save_hash"]

    return state


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        description="Sync PS Vita and PSP-on-Vita saves via VitaShell FTP.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Steps to use:
  1. Install VitaShell on your PS Vita (via HENkaku/Ensō)
  2. Open VitaShell and press SELECT to start FTP server
  3. Note the IP address shown (e.g. 192.168.1.150:1337)
  4. Run this script with --vita-ip <IP>

Examples:
  python vita_sync.py --vita-ip 192.168.1.150 --server http://192.168.1.201:8000 --api-key mykey
  python vita_sync.py --vita-ip 192.168.1.150 --server ... --api-key ... --native-only
  python vita_sync.py --vita-ip 192.168.1.150 --server ... --api-key ... --psp-emu-only
  python vita_sync.py --vita-ip 192.168.1.150 --server ... --api-key ... --dry-run
        """,
    )
    parser.add_argument("--vita-ip", required=True,
                        help="PS Vita IP address (shown in VitaShell FTP)")
    parser.add_argument("--vita-port", type=int, default=VITA_FTP_PORT,
                        help=f"VitaShell FTP port (default: {VITA_FTP_PORT})")
    parser.add_argument("--server", required=True,
                        help="Server URL (e.g. http://192.168.1.201:8000)")
    parser.add_argument("--api-key", required=True,
                        help="API key for server authentication")
    parser.add_argument("--console-id", default=None,
                        help="Console ID override (default: auto-generated per Vita)")
    parser.add_argument("--native-only", action="store_true",
                        help="Only sync native PS Vita saves (skip PSP emu)")
    parser.add_argument("--psp-emu-only", action="store_true",
                        help="Only sync PSP emulation saves (skip native Vita)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be synced without making changes")

    args = parser.parse_args()

    server = args.server.rstrip("/")
    sync_dir = get_sync_dir()
    console_id = args.console_id or get_console_id(args.vita_ip, sync_dir)

    # Load game name databases
    data_dir = Path(__file__).parent.parent / "server" / "data"
    vita_names = load_name_database(data_dir / "vitatdb.txt")
    psp_names = load_name_database(data_dir / "psptdb.txt")

    print("PS Vita Save Sync")
    print(f"Vita:       {args.vita_ip}:{args.vita_port}")
    print(f"Server:     {server}")
    print(f"Console ID: {console_id}")

    # Check server connectivity
    status, resp = api_get(server, "/status", args.api_key, console_id)
    if status != 200:
        print(f"Error: Cannot reach server (HTTP {status})")
        sys.exit(1)
    print("Server OK")

    # Connect to Vita FTP
    print(f"\nConnecting to Vita FTP ({args.vita_ip}:{args.vita_port})...")
    print("Make sure VitaShell FTP is running (SELECT button in VitaShell)")
    ftp = VitaFTP(args.vita_ip, args.vita_port)
    try:
        ftp.connect()
    except Exception as e:
        print(f"Error: Cannot connect to Vita FTP: {e}")
        print("Make sure:")
        print("  - PS Vita is on the same network")
        print("  - VitaShell is open and FTP server is running (press SELECT)")
        sys.exit(1)
    print("Vita FTP connected\n")

    games = []
    try:
        if not args.psp_emu_only:
            print("Scanning native Vita saves...")
            vita_saves = scan_vita_saves(ftp)
            for g in vita_saves:
                g["name"] = vita_names.get(g["game_id"], g["game_id"])
            games.extend(vita_saves)
            print(f"  Found {len(vita_saves)} native Vita save(s)")

        if not args.native_only:
            print("Scanning PSP emu saves...")
            psp_saves = scan_psp_emu_saves(ftp)
            for g in psp_saves:
                g["name"] = psp_names.get(g["game_id"], g["game_id"])
            games.extend(psp_saves)
            print(f"  Found {len(psp_saves)} PSP emu save(s)")

        if not games:
            print("\nNo saves found on Vita.")
            ftp.disconnect()
            sys.exit(0)

        print(f"\nTotal: {len(games)} save(s):")
        for g in games:
            size_kb = g["total_size"] / 1024
            platform = "Vita" if g["platform"] == "vita" else "PSP emu"
            print(f"  [{platform}] {g['name']:<40s} {g['game_id']}  {size_kb:>7.1f} KB")

        state = load_state(sync_dir, console_id)
        state = do_sync(games, ftp, server, args.api_key, console_id, state, args.dry_run)

        if not args.dry_run:
            save_state(sync_dir, console_id, state)

    finally:
        ftp.disconnect()

    print("\nDone.")


if __name__ == "__main__":
    main()
