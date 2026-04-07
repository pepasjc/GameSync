# PS3 Client

This folder will contain the native PS3 Save Sync client.

The client is planned around Apollo Save Tool rather than implementing PS3 save
resigning logic from scratch.

## Local Responsibilities

- read config for server URL and API key
- enumerate PS3 save folders from Apollo-visible locations
- enumerate PS1 VM1 cards for PS1 interoperability
- compute deterministic hashes for local saves
- upload/download saves from the Save Sync server
- persist last-synced hashes for three-way conflict detection

## Current Status

The native scaffold now has:

- portable config and sync-state helpers
- debug config auto-creation for first-run RPCS3 testing
- Apollo path helpers for PS3 save folders and PS1 VMC roots
- local scanning for PS3 HDD save directories
- local scanning for HDD and USB `.VM1` files whose filenames expose a PS1 serial
- cache-backed SHA-256 hashing for PS3 save directories and `.VM1` files
- a simple controller-driven text/debug UI for early testing

Still missing:

- HTTP sync
- save download/apply flows
- Apollo-assisted import/export actions

## Apollo Integration

Apollo already knows how to:

- discover PS3 HDD saves
- resign and import/export protected saves
- manage PS1 VM1/VMP/MCR/MCD memory cards

The Save Sync client should assume Apollo is installed and use Apollo-compatible
locations and formats.

## Planned Layout

- `include/` - headers
- `source/` - application code
- `assets/` - icons and package assets

## Planned Config

Config file target:

- `dev_hdd0/game/3DSSYNC00/USRDIR/config.txt`

Suggested keys:

- `server_url=http://192.168.1.100:8000`
- `api_key=your-secret-key`
- `ps3_user=00000001`
- `scan_ps3=1`
- `scan_ps1=1`

If `config.txt` is missing, the current debug build auto-creates one using:

- `server_url=http://192.168.1.201:8000`
- `api_key=anything`
