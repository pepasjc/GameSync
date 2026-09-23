"""Interactive test script for ROM and save API endpoints.

Usage:
    python test_api.py                  # list saves + ROMs
    python test_api.py --titles         # list saves only
    python test_api.py --roms           # list ROMs only
    python test_api.py --download TITLE_ID --out save.bin
    python test_api.py --download-rom TITLE_ID --out game.gba

Config via env vars (or .env file):
    SYNC_HOST   (default 192.168.1.201)
    SYNC_PORT   (default 8000)
    SYNC_API_KEY (default "anything")
"""

import argparse
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import requests


HOST = os.environ.get("SYNC_HOST", "192.168.1.201")
PORT = os.environ.get("SYNC_PORT", "8000")
API_KEY = os.environ.get("SYNC_API_KEY", "anything")


def _base():
    return f"http://{HOST}:{PORT}/api/v1"


def get_json(path: str, params: dict | None = None) -> dict:
    r = requests.get(
        f"{_base()}{path}", headers={"X-API-Key": API_KEY}, params=params, timeout=30
    )
    r.raise_for_status()
    return r.json()


def download_file(path: str, out: str) -> None:
    r = requests.get(
        f"{_base()}{path}", headers={"X-API-Key": API_KEY}, stream=True, timeout=120
    )
    if r.status_code != 200:
        print(f"Error {r.status_code}: {r.text[:200]}")
        sys.exit(1)
    total = int(r.headers.get("Content-Length", 0))
    downloaded = 0
    with open(out, "wb") as f:
        for chunk in r.iter_content(chunk_size=65536):
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded * 100 // total
                bar = "#" * (pct // 2) + "-" * (50 - pct // 2)
                print(f"\r  [{bar}] {pct}% ({downloaded}/{total})", end="", flush=True)
    print(f"\n  Saved to {out} ({downloaded} bytes)")


def print_titles(titles: list[dict], label: str = "Saves") -> None:
    if not titles:
        print(f"\n  No {label.lower()} found.")
        return
    print(f"\n  {label} ({len(titles)}):")
    print(
        f"  {'#':<4} {'Console':<8} {'Title ID':<35} {'Name':<40} {'Size':>8} {'ROM':>4}"
    )
    print(f"  {'─' * 4} {'─' * 8} {'─' * 35} {'─' * 40} {'─' * 8} {'─' * 4}")
    for i, t in enumerate(titles):
        name = t.get("game_name") or t.get("name") or t.get("title_id", "?")
        if len(name) > 38:
            name = name[:36] + ".."
        console = t.get("console_type") or t.get("system") or "?"
        tid = t.get("title_id", "?")
        size = t.get("save_size", 0)
        size_str = f"{size // 1024}K" if size else "-"
        print(f"  {i:<4} {console:<8} {tid:<35} {name:<40} {size_str:>8}")


def print_roms(roms: list[dict]) -> None:
    if not roms:
        print("\n  No ROMs found.")
        return
    print(f"\n  ROMs ({len(roms)}):")
    print(f"  {'#':<4} {'System':<8} {'Title ID':<35} {'Name':<40} {'Size':>8}")
    print(f"  {'─' * 4} {'─' * 8} {'─' * 35} {'─' * 40} {'─' * 8}")
    for i, r in enumerate(roms):
        name = r.get("name") or r.get("filename", "?")
        if len(name) > 38:
            name = name[:36] + ".."
        sys_code = r.get("system", "?")
        tid = r.get("title_id", "?")
        size = r.get("size", 0)
        if size >= 1024 * 1024 * 1024:
            size_str = f"{size / (1024 * 1024 * 1024):.1f}G"
        elif size >= 1024 * 1024:
            size_str = f"{size / (1024 * 1024):.1f}M"
        elif size >= 1024:
            size_str = f"{size // 1024}K"
        else:
            size_str = f"{size}B"
        print(f"  {i:<4} {sys_code:<8} {tid:<35} {name:<40} {size_str:>8}")


def cmd_titles(args: argparse.Namespace) -> None:
    data = get_json("/titles", {"console_type": args.filter} if args.filter else None)
    titles = data.get("titles", [])
    print_titles(titles, "Saves")


def cmd_roms(args: argparse.Namespace) -> None:
    params = {}
    if args.system:
        params["system"] = args.system
    if args.search:
        params["search"] = args.search
    data = get_json("/roms", params or None)
    roms = data.get("roms", [])
    print_roms(roms)


def cmd_all(args: argparse.Namespace) -> None:
    data = get_json("/titles")
    titles = data.get("titles", [])

    rom_data = get_json("/roms")
    rom_list = rom_data.get("roms", [])

    rom_by_tid = {r["title_id"]: r for r in rom_list}

    enriched = []
    for t in titles:
        tid = t.get("title_id", "")
        t["has_rom"] = "Y" if tid in rom_by_tid else ""
        enriched.append(t)

    print_titles(enriched, "Server Saves")

    server_only_roms = [
        r
        for r in rom_list
        if not any(t.get("title_id") == r["title_id"] for t in titles)
    ]
    if server_only_roms:
        print_roms(server_only_roms)
        print(f"\n  ({len(server_only_roms)} ROMs with no save on server)")


def cmd_download(args: argparse.Namespace) -> None:
    out = args.out or f"{args.title_id}.bin"
    print(f"Downloading save for {args.title_id}...")
    download_file(f"/saves/{args.title_id}", out)


def cmd_download_rom(args: argparse.Namespace) -> None:
    out = args.out
    if not out:
        roms_data = get_json("/roms")
        rom = next(
            (r for r in roms_data.get("roms", []) if r["title_id"] == args.title_id),
            None,
        )
        out = rom["filename"] if rom else f"{args.title_id}.rom"
    print(f"Downloading ROM for {args.title_id}...")
    download_file(f"/roms/{args.title_id}", out)


def cmd_scan(args: argparse.Namespace) -> None:
    params = {}
    if args.crc32:
        params["use_crc32"] = "true"
    r = requests.get(
        f"{_base()}/roms/scan",
        headers={"X-API-Key": API_KEY},
        params=params,
        timeout=300,
    )
    r.raise_for_status()
    print(f"Scan result: {r.json()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Save Sync API test tool")
    parser.add_argument("--host", default=HOST, help="Server host")
    parser.add_argument("--port", default=PORT, help="Server port")
    parser.add_argument("--api-key", default=API_KEY, help="API key")

    sub = parser.add_subparsers(dest="command")

    p_titles = sub.add_parser("titles", help="List saves on server")
    p_titles.add_argument("--filter", help="Filter by console type (3DS, PSP, etc.)")

    p_roms = sub.add_parser("roms", help="List ROMs in catalog")
    p_roms.add_argument("--system", help="Filter by system (GBA, SNES, etc.)")
    p_roms.add_argument("--search", help="Search by name")

    sub.add_parser("all", help="List saves + ROMs (default)")

    p_dl = sub.add_parser("download", help="Download a save by title_id")
    p_dl.add_argument("title_id", help="Title ID to download")
    p_dl.add_argument("--out", help="Output filename")

    p_drom = sub.add_parser("download-rom", help="Download a ROM by title_id")
    p_drom.add_argument("title_id", help="Title ID of the ROM")
    p_drom.add_argument("--out", help="Output filename (auto-detected if omitted)")

    p_scan = sub.add_parser("scan", help="Trigger ROM rescan")
    p_scan.add_argument(
        "--crc32", action="store_true", help="Use CRC32 matching (slow)"
    )

    args = parser.parse_args()

    import __main__ as _self

    _self.HOST = args.host
    _self.PORT = args.port
    _self.API_KEY = args.api_key

    print(f"Server: {_base()}")

    cmd = args.command or "all"
    dispatch = {
        "titles": cmd_titles,
        "roms": cmd_roms,
        "all": cmd_all,
        "download": cmd_download,
        "download-rom": cmd_download_rom,
        "scan": cmd_scan,
    }
    dispatch[cmd](args)


if __name__ == "__main__":
    main()
