# Save Sync

Sync save files between handhelds, desktop tools, and emulators through a local server.

This repo contains:

- `server/`: FastAPI server that stores saves and history
- `3ds/`: Nintendo 3DS client
- `ds/`: Nintendo DS / DSi client
- `desktop/`: PyQt desktop client
- `psp/`: PSP client
- `ps3/`: PS3 client scaffold (Apollo-based design in progress)
- `vita/`: PS Vita client

## What It Does

- Syncs saves through a single local server on your network
- Keeps save history on the server
- Supports multiple platforms and clients sharing the same save slot where appropriate
- Includes desktop tools for server browsing, profile sync, and ROM normalization

## Server Setup

Requirements:

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)

Start the server from `server/`:

```bash
cd server
uv sync
uv run python run.py
```

Set the API key before starting:

```bash
# Windows
set SYNC_API_KEY=your-secret-key
uv run python run.py

# Linux / macOS
SYNC_API_KEY=your-secret-key uv run python run.py
```

Important environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNC_API_KEY` | `dev-key-change-me` | API key required by clients |
| `SYNC_SAVE_DIR` | `./saves` | Save storage directory |
| `SYNC_HOST` | `0.0.0.0` | Bind address |
| `SYNC_PORT` | `8000` | Server port |
| `SYNC_MAX_HISTORY_VERSIONS` | `5` | Number of old save versions kept |

## Building

### Server

```bash
cd server
uv sync
uv run pytest tests/ -v
```

### 3DS Client

Requires devkitPro / devkitARM:

```bash
cd 3ds
make
```

### DS Client

Requires devkitPro / devkitARM:

```bash
cd ds
make
```

DSi-enhanced build:

```bash
cd ds
make dsi
```

### Desktop Client

```bash
cd desktop
pip install -r requirements.txt
python main.py
```

### PSP Client

Requires `pspdev` and PSP zlib support:

```bash
cd psp
make
```

### Vita Client

Requires VitaSDK:

```bash
cd vita
mkdir build
cd build
cmake ..
make
```

## Client Setup

All clients need:

- the same `server_url`
- the same `api_key`
- access to the same local network as the server

### 3DS

Config file:

- `sdmc:/3ds/3dssync/config.txt`

Example:

```ini
server_url=http://192.168.1.100:8000
api_key=your-secret-key
```

Optional for nds-bootstrap saves on SD:

```ini
server_url=http://192.168.1.100:8000
api_key=your-secret-key
nds_dir=sdmc:/roms/nds
```

Install:

- `.3dsx`: copy to `sdmc:/3ds/`
- `.cia`: install with FBI

### Desktop

The desktop app uses the same server settings and provides:

- Server Saves browser
- Sync Profiles
- Sync tab for local folders / devices
- ROM Normalizer
- ROM Collection

### PSP

Config file:

- `ms0:/PSP/GAME/pspsync/config.txt`

Example:

```ini
server_url=http://192.168.1.100:8000
api_key=your-secret-key
wifi_ap=0
```

### Vita

Config file:

- `ux0:data/vitasync/config.txt`

Example:

```ini
server_url=http://192.168.1.100:8000
api_key=your-secret-key
scan_vita=1
scan_psp_emu=1
```

## Using the Clients

### Basic Flow

1. Start the server.
2. Configure the client with `server_url` and `api_key`.
3. Scan local saves.
4. Upload from the device that already has progress.
5. On the other device, download or run sync.

### 3DS Controls

| Button | Action |
|--------|--------|
| D-Pad | Navigate |
| A | Upload |
| B | Download |
| X | Auto sync |
| Y | View details |
| SELECT | Mark for batch actions |
| R | Filter view |
| L | Config / update menu |
| START | Exit |

### PSP / Vita Controls

| Button | Action |
|--------|--------|
| Up / Down | Navigate |
| Left / Right | Page |
| X / Cross | Smart sync |
| Square | Upload |
| Triangle | Download |
| Select | Auto sync all |
| Start | Exit |

Server-only saves are shown in the handheld lists and can be downloaded even if no local save exists yet.

### Desktop Usage

Typical flow:

1. Open `desktop/main.py`
2. Check the `Server Saves` tab to inspect what is stored on the server
3. Create or select a sync profile in `Sync Profiles`
4. Use `Sync` to scan and upload/download saves
5. Use `ROM Normalizer` or `ROM Collection` when preparing ROM sets

For sync profile details, see [docs/sync-profiles.md](docs/sync-profiles.md).

## Documentation

More technical details live in `docs/`:

- [docs/sync-profiles.md](docs/sync-profiles.md)
- [docs/technical-overview.md](docs/technical-overview.md)
- [docs/ps1-sync.md](docs/ps1-sync.md)
- [docs/ps3-sync.md](docs/ps3-sync.md)

## License

MIT
