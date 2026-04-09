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

## Running as a Service (Linux / Raspberry Pi)

A systemd unit file is included at `server/3dssync.service`. Copy and enable it:

```bash
sudo cp 3dssync.service /etc/systemd/system/gamesync.service
# Edit the file to set the correct user and working directory
sudo systemctl daemon-reload
sudo systemctl enable gamesync
sudo systemctl start gamesync
```

Check logs:

```bash
sudo journalctl -u gamesync -f
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
