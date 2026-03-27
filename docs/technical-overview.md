# Technical Overview

This document keeps the implementation-level notes out of the top-level README.

## Server Role

The server is the central save store for all clients. It:

- accepts uploads
- serves downloads
- keeps save history
- compares client and server hashes for sync decisions
- stores metadata per title

The main code lives under:

- `server/app/routes`
- `server/app/services`

## Sync Model

The sync flow is metadata-first:

1. Client scans local saves
2. Client computes local hash
3. Client sends current hash plus last-synced hash
4. Server decides whether the correct action is upload, download, up-to-date, or conflict
5. Client executes that action

This is a three-way comparison model:

- local hash
- server hash
- last synced hash

That allows the server to distinguish:

- only client changed
- only server changed
- both changed

## Save Transfer Format

Most non-raw clients use the custom `3DSS` bundle format.

That bundle stores:

- title ID
- timestamp
- file table
- file hashes
- compressed file payload

Relevant code:

- `server/app/services/bundle.py`
- `psp/source/bundle.c`
- `vita/source/bundle.c`

## Title IDs and Names

The system uses a shared title ID per game/save slot.

Depending on platform that may be:

- 3DS 16-character hex title IDs
- DS-derived IDs
- PSP / Vita / PS1 product codes
- normalized IDs for emulator-oriented systems in desktop flows

Name and platform lookup logic lives in:

- `server/app/routes/titles.py`
- `server/app/services/game_names.py`

## Handheld Clients

### 3DS / DS

These clients focus on native handheld save access.

### PSP / Vita

These clients now merge server titles into the local title list, so a save can be downloaded even when it does not exist locally yet.

Relevant code:

- `psp/source/network.c`
- `psp/source/sync.c`
- `vita/source/network.c`
- `vita/source/sync.c`

Those server-only entries are represented as placeholders until the first successful download.

## Desktop Client

The desktop app is a management and bridge tool around the server API.

Main areas:

- `Server Saves`: browse and download server content
- `Sync Profiles`: define local folder/device layouts
- `Sync`: compare local folders against server saves
- `ROM Normalizer`: canonical naming via DATs
- `ROM Collection`: build curated ROM sets

Key files:

- `desktop/window.py`
- `desktop/sync_engine.py`
- `desktop/rom_normalizer.py`

## API Surface

Important endpoints:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/status` | GET | health check |
| `/api/v1/titles` | GET | list titles on server |
| `/api/v1/titles/names` | POST | name/platform lookup |
| `/api/v1/saves/{title_id}` | GET/POST | bundle download/upload |
| `/api/v1/saves/{title_id}/meta` | GET | metadata |
| `/api/v1/sync` | POST | batch sync plan |

For PS1-specific raw card handling, see [ps1-sync.md](ps1-sync.md).
