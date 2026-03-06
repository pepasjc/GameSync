#!/usr/bin/env python3
"""PSP Save Sync - Sync PSP SAVEDATA with the Save Sync server.

Supports saves from PSP connected via USB, or from a Memory Stick mounted on PC.
PSP saves live in /PSP/SAVEDATA/<GAMEID>/ on the Memory Stick.

The title ID used on the server is the save directory name (product code),
e.g. ULUS10272, ELES01234, NPUH10001.

The PSP can be accessed in two ways:
  1. USB mode: Connect PSP to PC via USB and put it into USB mode.
     The Memory Stick appears as a removable drive.
  2. Memory Stick adapter: Remove the Memory Stick and insert it directly.

Usage:
    python psp_sync.py --ms-path /media/user/PSP --server http://192.168.1.201:8000 --api-key mykey
    python psp_sync.py --ms-path E:\\ --server http://192.168.1.201:8000 --api-key mykey --dry-run
    python psp_sync.py --ms-path /mnt/psp --server ... --api-key ... --game-id ULUS10272
"""

import argparse
import hashlib
import json
import os
import random
import re
import struct
import sys
import time
import zlib
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# --- Constants ---

BUNDLE_MAGIC = b"3DSS"
BUNDLE_VERSION_V3 = 3        # String title_id format for PSP/Vita
SYNC_DIR_NAME = ".psp_sync"  # Hidden folder on Memory Stick for sync data
SAVEDATA_DIR = "PSP/SAVEDATA"

# PSP product code pattern
_PSP_CODE_RE = re.compile(r"^[A-Z]{4}\d{5}$")


# --- Title ID validation ---

def is_valid_psp_code(code: str) -> bool:
    """Return True if the string looks like a PSP product code (e.g. ULUS10272)."""
    return bool(_PSP_CODE_RE.match(code.upper()))


# --- PSP save scanning ---

def scan_saves(ms_root: Path) -> list[dict]:
    """Scan the Memory Stick for PSP save directories.

    Each directory under PSP/SAVEDATA/ whose name matches the product code
    pattern is considered a game save.

    Returns list of dicts with: game_id, save_dir, name, files, total_size
    """
    savedata_dir = ms_root / SAVEDATA_DIR
    if not savedata_dir.exists():
        return []

    found = []
    for save_dir in sorted(savedata_dir.iterdir()):
        if not save_dir.is_dir():
            continue

        game_id = save_dir.name.upper()
        if not is_valid_psp_code(game_id):
            continue

        # Collect all files in this save directory
        files = []
        total_size = 0
        for f in sorted(save_dir.rglob("*")):
            if f.is_file():
                size = f.stat().st_size
                files.append(f)
                total_size += size

        if not files:
            continue

        found.append({
            "game_id": game_id,
            "save_dir": save_dir,
            "name": game_id,  # Will be updated by server lookup
            "files": files,
            "total_size": total_size,
        })

    return found


# --- Game name lookup ---

def load_name_database(db_path: Path) -> dict[str, str]:
    """Load game names from psptdb.txt. Returns dict of product code -> name."""
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

def hash_save_dir(files: list[Path], save_dir: Path) -> str:
    """Compute SHA-256 hash of all files in a save directory.

    Hash is computed over: sorted relative paths + file contents,
    matching the server's bundle hash computation.
    """
    h = hashlib.sha256()
    for f in sorted(files, key=lambda p: p.relative_to(save_dir).as_posix()):
        h.update(f.read_bytes())
    return h.hexdigest()


# --- Bundle v3 format ---

def create_bundle_v3(game_id: str, files: list[Path], save_dir: Path) -> bytes:
    """Create a v3 bundle from a PSP save directory (multiple files supported)."""
    timestamp = int(time.time())

    # Build payload: file table + file data
    payload_parts = []
    file_infos = []

    for file_path in sorted(files, key=lambda p: p.relative_to(save_dir).as_posix()):
        rel_path = file_path.relative_to(save_dir).as_posix()
        data = file_path.read_bytes()
        file_hash = hashlib.sha256(data).digest()
        file_infos.append((rel_path, data, file_hash))

    # File table
    for rel_path, data, _ in file_infos:
        path_bytes = rel_path.encode("utf-8")
        payload_parts.append(struct.pack("<H", len(path_bytes)))
        payload_parts.append(path_bytes)
        payload_parts.append(struct.pack("<I", len(data)))
        payload_parts.append(hashlib.sha256(data).digest())

    # File data
    for _, data, _ in file_infos:
        payload_parts.append(data)

    payload = b"".join(payload_parts)
    compressed = zlib.compress(payload, level=6)

    # v3 header: magic + version(3) + title_id[16] + timestamp + file_count + uncompressed_size
    game_id_bytes = game_id.upper().encode("ascii")[:15].ljust(16, b"\x00")
    header = []
    header.append(BUNDLE_MAGIC)
    header.append(struct.pack("<I", BUNDLE_VERSION_V3))
    header.append(game_id_bytes)
    header.append(struct.pack("<I", timestamp))
    header.append(struct.pack("<I", len(file_infos)))
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

    # title_id string at offset 8, 16 bytes
    file_count = struct.unpack_from("<I", data, 28)[0]
    uncompressed_size = struct.unpack_from("<I", data, 32)[0]

    payload = zlib.decompress(data[36:])
    if len(payload) != uncompressed_size:
        raise ValueError("Decompressed size mismatch")

    # Parse file table
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

    # Parse file data
    result = {}
    for path, size in files_info:
        result[path] = payload[offset:offset + size]
        offset += size

    return result


# --- SD card identity & sync state ---

def get_sync_dir(ms_root: Path) -> Path:
    sync_dir = ms_root / SYNC_DIR_NAME
    sync_dir.mkdir(exist_ok=True)
    return sync_dir


def get_console_id(sync_dir: Path) -> str:
    """Get or generate a unique console ID for this Memory Stick."""
    id_file = sync_dir / "console_id"
    if id_file.exists():
        cid = id_file.read_text().strip()
        if cid:
            return cid

    rand_hex = "%08x" % random.getrandbits(32)
    cid = f"psp_{rand_hex}"
    id_file.write_text(cid)
    print(f"Generated new console ID for this Memory Stick: {cid}")
    return cid


def load_state(sync_dir: Path) -> dict:
    state_file = sync_dir / "state.json"
    if state_file.exists():
        return json.loads(state_file.read_text())
    return {}


def save_state(sync_dir: Path, state: dict):
    state_file = sync_dir / "state.json"
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
        with urlopen(req, timeout=30) as resp:
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


# --- Sync logic ---

def do_sync(games: list[dict], server: str, api_key: str, console_id: str,
            state: dict, ms_root: Path, dry_run: bool = False) -> dict:
    """Run the sync protocol against the server."""
    if not games:
        print("No PSP saves found.")
        return state

    print(f"\nPreparing sync for {len(games)} save(s)...")

    # Step 1: Build sync request
    titles_meta = []
    for g in games:
        save_hash = hash_save_dir(g["files"], g["save_dir"])
        g["save_hash"] = save_hash
        meta = {
            "title_id": g["game_id"],
            "save_hash": save_hash,
            "timestamp": int(time.time()),
            "size": g["total_size"],
        }
        last_hash = state.get(g["game_id"])
        if last_hash:
            meta["last_synced_hash"] = last_hash
        titles_meta.append(meta)

    sync_request = {"console_id": "psp", "titles": titles_meta}

    # Step 2: Send sync request
    print("Sending sync request to server...")
    status, resp = api_post_json(server, "/sync", api_key, console_id, sync_request)
    if status != 200:
        print(f"Sync request failed (HTTP {status})")
        if resp:
            print(f"  {resp.decode('utf-8', errors='replace')[:200]}")
        return state

    plan = json.loads(resp)
    upload_ids = set(plan.get("upload", []))
    download_ids = set(plan.get("download", []))
    conflict_ids = set(plan.get("conflict", []))
    up_to_date_ids = set(plan.get("up_to_date", []))
    server_only_ids = set(plan.get("server_only", []))

    games_by_id = {g["game_id"]: g for g in games}

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
        for tid in download_ids:
            g = games_by_id.get(tid)
            print(f"  Would download: {g['name'] if g else tid} ({tid})")
        for tid in conflict_ids:
            g = games_by_id.get(tid)
            print(f"  Conflict:       {g['name'] if g else tid} ({tid})")
        return state

    # Step 3: Uploads
    for tid in upload_ids:
        g = games_by_id.get(tid)
        if not g:
            continue
        print(f"  Uploading: {g['name']} ({tid})...")
        bundle = create_bundle_v3(tid, g["files"], g["save_dir"])
        s, r = api_post_bundle(server, f"/saves/{tid}?force=true&source=psp",
                               api_key, console_id, bundle)
        if s == 200:
            state[tid] = g["save_hash"]
            print(f"    OK ({g['total_size'] // 1024} KB)")
        else:
            print(f"    Failed (HTTP {s}): {r.decode('utf-8', errors='replace')[:100]}")

    # Step 4: Downloads
    for tid in list(download_ids) + list(server_only_ids):
        g = games_by_id.get(tid)
        save_dir = (ms_root / SAVEDATA_DIR / tid) if g is None else g["save_dir"]
        name = g["name"] if g else tid
        print(f"  Downloading: {name} ({tid})...")
        s, r = api_get(server, f"/saves/{tid}", api_key, console_id)
        if s == 200:
            try:
                file_data = parse_bundle_v3(r)
                save_dir.mkdir(parents=True, exist_ok=True)
                for rel_path, data in file_data.items():
                    out_path = save_dir / rel_path
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(data)
                # Recompute hash after writing
                written_files = [save_dir / p for p in file_data]
                new_hash = hash_save_dir(written_files, save_dir)
                state[tid] = new_hash
                total = sum(len(d) for d in file_data.values())
                print(f"    OK ({total // 1024} KB, {len(file_data)} file(s))")
            except Exception as e:
                print(f"    Bundle parse error: {e}")
        else:
            print(f"    Failed (HTTP {s})")

    # Step 5: Conflicts
    for tid in conflict_ids:
        g = games_by_id.get(tid)
        name = g["name"] if g else tid
        print(f"\n  CONFLICT: {name} ({tid})")
        print(f"    Local hash:  {g['save_hash'][:16]}...")
        print(f"    Choose action:")
        print(f"      [u] Upload local save to server")
        print(f"      [d] Download server save (overwrites local)")
        print(f"      [s] Skip")
        choice = input(f"    > ").strip().lower()

        if choice == "u" and g:
            print(f"    Uploading...")
            bundle = create_bundle_v3(tid, g["files"], g["save_dir"])
            s, r = api_post_bundle(server, f"/saves/{tid}?force=true&source=psp",
                                   api_key, console_id, bundle)
            if s == 200:
                state[tid] = g["save_hash"]
                print(f"    OK")
            else:
                print(f"    Failed (HTTP {s})")
        elif choice == "d":
            print(f"    Downloading...")
            save_dir = g["save_dir"] if g else ms_root / SAVEDATA_DIR / tid
            s, r = api_get(server, f"/saves/{tid}", api_key, console_id)
            if s == 200:
                try:
                    file_data = parse_bundle_v3(r)
                    save_dir.mkdir(parents=True, exist_ok=True)
                    for rel_path, data in file_data.items():
                        out_path = save_dir / rel_path
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        out_path.write_bytes(data)
                    written_files = [save_dir / p for p in file_data]
                    new_hash = hash_save_dir(written_files, save_dir)
                    state[tid] = new_hash
                    print(f"    OK")
                except Exception as e:
                    print(f"    Bundle parse error: {e}")
            else:
                print(f"    Failed (HTTP {s})")
        else:
            print(f"    Skipped")

    # Update state for up-to-date titles
    for tid in up_to_date_ids:
        g = games_by_id.get(tid)
        if g and "save_hash" in g:
            state[tid] = g["save_hash"]

    return state


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        description="Sync PSP SAVEDATA with the Save Sync server.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # PSP in USB mode (Windows)
  python psp_sync.py --ms-path E:\\ --server http://192.168.1.201:8000 --api-key mykey

  # PSP Memory Stick on Linux
  python psp_sync.py --ms-path /media/user/PSP --server http://192.168.1.201:8000 --api-key mykey

  # Sync only one game
  python psp_sync.py --ms-path /mnt/psp --server ... --api-key ... --game-id ULUS10272

  # Dry run (show what would be synced)
  python psp_sync.py --ms-path /mnt/psp --server ... --api-key ... --dry-run
        """,
    )
    parser.add_argument("--ms-path", type=Path, required=True,
                        help="Path to PSP Memory Stick root (contains PSP/ directory)")
    parser.add_argument("--server", required=True,
                        help="Server URL (e.g. http://192.168.1.201:8000)")
    parser.add_argument("--api-key", required=True,
                        help="API key for server authentication")
    parser.add_argument("--console-id", default=None,
                        help="Console ID override (default: auto-generated per Memory Stick)")
    parser.add_argument("--game-id", default=None,
                        help="Only sync this specific game ID (e.g. ULUS10272)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be synced without making changes")

    args = parser.parse_args()

    ms_root = args.ms_path
    if not ms_root.exists():
        print(f"Error: Memory Stick path does not exist: {ms_root}")
        sys.exit(1)

    savedata_dir = ms_root / SAVEDATA_DIR
    if not savedata_dir.exists():
        print(f"Error: No PSP/SAVEDATA directory found at: {ms_root}")
        print("Make sure the PSP is connected via USB and in USB mode,")
        print("or that the correct Memory Stick root is specified.")
        sys.exit(1)

    server = args.server.rstrip("/")

    sync_dir = get_sync_dir(ms_root)
    console_id = args.console_id or get_console_id(sync_dir)

    # Load game name database
    db_path = Path(__file__).parent.parent / "server" / "data" / "psptdb.txt"
    name_db = load_name_database(db_path)

    print("PSP Save Sync")
    print(f"Server:     {server}")
    print(f"Console ID: {console_id}")
    print(f"MS root:    {ms_root}")

    # Check server connectivity
    status, resp = api_get(server, "/status", args.api_key, console_id)
    if status != 200:
        print(f"Error: Cannot reach server (HTTP {status})")
        sys.exit(1)
    print("Server OK\n")

    # Scan for saves
    print("Scanning PSP/SAVEDATA/...")
    games = scan_saves(ms_root)

    if args.game_id:
        game_id = args.game_id.upper()
        games = [g for g in games if g["game_id"] == game_id]
        if not games:
            print(f"No save found for game ID: {game_id}")
            sys.exit(0)

    if not games:
        print("No PSP save directories found.")
        sys.exit(0)

    # Apply game names from database
    for g in games:
        g["name"] = name_db.get(g["game_id"], g["game_id"])

    print(f"Found {len(games)} save(s):")
    for g in games:
        size_kb = g["total_size"] / 1024
        file_count = len(g["files"])
        print(f"  {g['name']:<40s} {g['game_id']}  {size_kb:>7.1f} KB  ({file_count} file(s))")

    state = load_state(sync_dir)
    state = do_sync(games, server, args.api_key, console_id, state, ms_root, args.dry_run)

    if not args.dry_run:
        save_state(sync_dir, state)

    print("\nDone.")


if __name__ == "__main__":
    main()
