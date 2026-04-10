# GameSync — Server

FastAPI server that stores save files, manages history, and coordinates sync across all clients.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

## Setup

```bash
cd server
uv sync
uv run python run.py
```

The server starts on `http://0.0.0.0:8000` by default.

## Configuration

All settings are via environment variables with the `SYNC_` prefix:

| Variable | Default | Description |
|---|---|---|
| `SYNC_API_KEY` | `anything` | Key required in the `X-API-Key` header by all clients |
| `SYNC_SAVE_DIR` | `./saves` | Directory where save files and history are stored |
| `SYNC_ROM_DIR` | unset | ROM root directory for ROM browsing/scanning |
| `SYNC_ROM_SCAN_INTERVAL` | `300` | Background ROM rescan interval in seconds (`0` disables it) |
| `SYNC_HOST` | `0.0.0.0` | Bind address |
| `SYNC_PORT` | `8000` | Port |
| `SYNC_MAX_HISTORY_VERSIONS` | `10` | Number of previous save versions to keep per title |

```bash
# Linux / macOS
SYNC_API_KEY=your-secret-key uv run python run.py

# Windows
set SYNC_API_KEY=your-secret-key
uv run python run.py
```

The server also loads a local `.env` file from the `server/` directory, which is
often the simplest place to keep persistent settings on a Pi.

## Running as a Service (Linux / Raspberry Pi)

 A systemd unit template is included at `server/3dssync@.service`. Copy and enable it:

```bash
sudo cp 3dssync@.service /etc/systemd/system/3dssync@.service
sudo nano /etc/default/3dssync
sudo systemctl daemon-reload
sudo systemctl enable --now 3dssync@pi
```

Example `/etc/default/3dssync`:

```bash
APP_DIR=/home/pi/Documents/3ds_sync/server
UV_BIN=/home/pi/.local/bin/uv
```

The unit uses `uvicorn app.main:app` directly instead of `python run.py` because
`run.py` enables auto-reload for development.

Put your actual server settings either in `/etc/default/3dssync` or in
`server/.env`. Example `server/.env`:

```bash
SYNC_API_KEY=your-secret-key
SYNC_SAVE_DIR=/home/pi/Documents/3ds_sync/server/saves
SYNC_ROM_DIR=/mnt/emudeck/roms
SYNC_ROM_SCAN_INTERVAL=300
SYNC_HOST=0.0.0.0
SYNC_PORT=8000
SYNC_MAX_HISTORY_VERSIONS=10
```

Check logs:

```bash
sudo journalctl -u 3dssync@pi -f
```

## API Endpoints

All endpoints except `GET /api/v1/status` require the `X-API-Key` header.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/status` | Health check, returns save count |
| `GET` | `/api/v1/saves` | List all stored saves with metadata |
| `GET` | `/api/v1/saves/{title_id}` | Download save bundle |
| `POST` | `/api/v1/saves/{title_id}` | Upload save bundle |
| `DELETE` | `/api/v1/saves/{title_id}` | Delete a save |
| `GET` | `/api/v1/saves/{title_id}/history` | List previous versions |
| `GET` | `/api/v1/saves/{title_id}/raw` | Download raw save bytes (NDS clients) |
| `POST` | `/api/v1/saves/{title_id}/raw` | Upload raw save bytes (NDS clients) |
| `POST` | `/api/v1/sync` | Batch sync plan — returns what to upload/download/conflict |
| `POST` | `/api/v1/titles/names` | Resolve title IDs to game names |
| `GET` | `/api/v1/update/latest` | Proxy GitHub releases for in-app updates |

## Game Database (DAT Files)

The server uses No-Intro / Redump DAT files to identify ROMs and resolve canonical game names. Without them, ROM normalization and CRC32-based title matching won't work.

Download DAT files from the [libretro-database repository](https://github.com/libretro/libretro-database/tree/master/dat) and place them in `server/data/dats/`.

The filename is used to detect the system automatically — standard No-Intro and Redump filenames work as-is. See [`data/dats/README.md`](data/dats/README.md) for the full list of recognized filenames and how matching works.

## Tests

```bash
uv run pytest tests/ -v
```

Run a specific file or test:

```bash
uv run pytest tests/test_bundle.py -v
uv run pytest tests/test_api.py::TestUploadEndpoint -v
```

## Storage Layout

```
saves/
  <title_id>/
    current/          # active save files
    history/          # previous versions (up to SYNC_MAX_HISTORY_VERSIONS)
    metadata.json     # timestamps, hashes, platform info
```

Title IDs are 16-character uppercase hex for 3DS/NDS/PSP/Vita (`0004000000055D00`), or a `SYSTEM_slug` format for emulator saves (`GBA_zelda_the_minish_cap`).
