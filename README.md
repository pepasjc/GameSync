# 3DS Save Sync

Sync Nintendo 3DS and DS save files between multiple CFW consoles via a PC server over WiFi.

## Features

- **Multi-console sync**: Keep saves in sync across multiple 3DS consoles
- **Three-way hash sync**: Automatically detects which side changed to avoid conflicts
- **3DS cartridge support**: Sync saves from physical 3DS game cards
- **NDS support**: Sync DS games via nds-bootstrap (SD), physical NDS cartridges (SPI), or the PC sync tool
- **Compression**: zlib compression allows syncing saves up to ~1-2MB
- **Game name lookup**: Shows actual game names instead of title IDs (4500+ 3DS games, 7000+ DS games)
- **Conflict detection**: Highlights conflicting saves in red for manual resolution
- **Batch operations**: Mark multiple titles with SELECT and upload/download them together
- **Tab filtering**: Cycle between All / 3DS / NDS views with R
- **Save details**: Press Y to view local and server save metadata, hashes, sync status
- **Save history**: Server keeps previous versions of saves for recovery
- **In-app config editor**: Edit server URL, API key, and NDS path without removing your SD card
- **Auto-update**: Check for and install updates directly from the 3DS
- **PC DS sync tool**: Python script to sync DS saves from SD cards, flashcards, or TWiLight Menu++

## Requirements

### Server (PC)
- Python 3.11+
- [uv](https://github.com/astral-sh/uv) package manager

### Client (3DS)
- Custom firmware (Luma3DS recommended)
- Homebrew Launcher or CIA installer
- WiFi connection to the same network as the server

## Quick Start

### 1. Start the Server

```bash
cd server
uv sync                    # Install dependencies
uv run python run.py       # Start server on port 8000
```

The server will display how many 3DS and DS game names it loaded from the database.

### 2. Configure the 3DS Client

Create `sdmc:/3ds/3dssync/config.txt` on your SD card:

```
server_url=http://192.168.1.100:8000
api_key=your-secret-key
```

Replace `192.168.1.100` with your PC's local IP address.

To also sync **NDS games** installed via nds-bootstrap on your SD card, add the path to your NDS ROM directory:

```
server_url=http://192.168.1.100:8000
api_key=your-secret-key
nds_dir=sdmc:/roms/nds
```

The client will scan this directory for `.nds` ROMs and look for matching `.sav` files next to them. You can also edit all config values in-app by pressing L.

### 3. Set Server API Key

Set the same API key on the server via environment variable:

```bash
# Windows
set SYNC_API_KEY=your-secret-key
uv run python run.py

# Linux/Mac
SYNC_API_KEY=your-secret-key uv run python run.py
```

### 4. Install the Client

**Homebrew Launcher**: Copy `client/3dssync.3dsx` to `sdmc:/3ds/` on your SD card.

**CIA (Home Menu)**: Install `client/3dssync.cia` using FBI or another CIA installer.

## Usage

### Controls

| Button | Action |
|--------|--------|
| D-Pad Up/Down | Navigate title list |
| D-Pad Left/Right | Page up / Page down |
| A | Upload save to server (or batch upload if titles are marked) |
| B | Download save from server (or batch download if marked) |
| X | Sync all SD titles automatically |
| Y | Show save details (local/server metadata, hashes) |
| SELECT | Toggle mark on current title (for batch operations) |
| R | Cycle view filter: All / 3DS / NDS |
| L | Open config menu (edit settings, rescan titles, check updates) |
| START | Exit |

### Title List Colors

| Color | Meaning |
|-------|---------|
| White | Normal SD game |
| Yellow | Currently selected |
| Green | Marked for batch operation |
| Cyan | Physical cartridge (3DS or NDS game card) |
| Magenta | NDS game on SD (nds-bootstrap) |
| Red | Save conflict (needs manual resolution) |

### Syncing Workflow

**First time setup:**
1. On your "main" 3DS, press X to sync all — this uploads all saves to the server

**Regular use:**
1. Play games on any 3DS
2. Before switching consoles, press X to sync all
3. On the other console, press X to sync all — changed saves download automatically

**Resolving conflicts:**
If both consoles changed the same save:
1. The save shows in red and gets auto-marked
2. Press A to keep local version, or B to use server version
3. With multiple conflicts, use batch download (B) to accept all server versions

### Cartridge Saves

Physical cartridge games (3DS and NDS) appear in cyan and are excluded from "Sync All" to prevent accidental overwrites. Use A/B buttons for manual upload/download.

This lets you transfer saves between:
- Physical cartridge on one console <-> another console
- Physical cartridge <-> Digital copy (same title ID)

NDS physical cartridges are accessed via SPI and support Flash, EEPROM, and FRAM save types.

### NDS Games on SD (nds-bootstrap)

If you use nds-bootstrap to run DS games from your SD card:

1. Set `nds_dir` in your config to point to your NDS ROM directory (e.g., `sdmc:/roms/nds`)
2. The client scans for `.nds` files and their matching `.sav` files
3. NDS games appear in magenta in the title list
4. They sync like any other title — the server stores saves using a title ID derived from the game code

### Batch Operations

1. Press SELECT on titles to mark them (shown in green with an asterisk)
2. Press A to batch upload all marked titles, or B to batch download
3. Marks are cleared after the batch completes

This is useful for resolving multiple conflicts at once or syncing specific titles.

### DS PC Sync Tool

A Python script (`tools/ds_sync.py`) syncs DS `.sav` files from a PC — useful for flashcard saves, TWiLight Menu++ on DSi, or any other DS save files:

```bash
# Sync from SD card (auto-detects TWiLight Menu++ paths)
python tools/ds_sync.py --sd-path E:\ --server http://192.168.1.100:8000 --api-key mykey

# Sync from specific directories
python tools/ds_sync.py --roms-dir E:\nds --saves-dir E:\nds\saves --server ... --api-key ...

# Dry run (show what would happen)
python tools/ds_sync.py --sd-path E:\ --server ... --api-key ... --dry-run
```

### Auto-Update

Press L to open the config menu, then select "Check for updates". If an update is available:
1. Press A to download and install directly (no FBI needed)
2. The app restarts automatically after installation

Updates are downloaded from the server, which proxies GitHub releases.

## Server Configuration

Environment variables (prefix with `SYNC_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNC_API_KEY` | `dev-key-change-me` | API key for authentication |
| `SYNC_SAVE_DIR` | `./saves` | Directory to store saves |
| `SYNC_HOST` | `0.0.0.0` | Server bind address |
| `SYNC_PORT` | `8000` | Server port |
| `SYNC_MAX_HISTORY_VERSIONS` | `5` | Number of save versions to keep |

## Building from Source

### Server

```bash
cd server
uv sync
uv run pytest tests/ -v  # Run tests
```

### Client (.3dsx)

Requires [devkitPro](https://devkitpro.org/) with 3DS development tools.

```bash
# Install zlib for 3DS (one time)
pacman -S 3ds-zlib

# Build
cd client
make
```

The output is `3dssync.3dsx` (launch via Homebrew Launcher).

### Client (.cia)

To build an installable CIA (appears on home menu):

1. Download additional tools and place in `C:\devkitPro\tools\bin\` (or `client/` directory):
   - [makerom](https://github.com/3DSGuy/Project_CTR/releases) - get `makerom-*-win_x86_64.zip`
   - [bannertool](https://github.com/Steveice10/bannertool/releases) - get `bannertool.zip`

2. Build:
```bash
cd client
make cia
```

The output is `3dssync.cia` — install with FBI or other CIA installer.

## Technical Details

### Sync Protocol

1. Client scans local saves and computes SHA-256 hashes
2. Client sends metadata to `POST /api/v1/sync` with:
   - Current hash
   - Last synced hash (stored locally per title)
   - Save size and console ID
3. Server compares using three-way logic:
   - Hashes match -> up to date
   - Only client changed (last_synced == server) -> upload
   - Only server changed (last_synced == client) -> download
   - Both changed -> conflict
4. Client executes the sync plan (uploads/downloads as needed)
5. After successful sync, client stores the new hash as "last synced"

### Bundle Format

Saves are transferred as compressed binary bundles:

```
Header (28 bytes):
  [4B]  Magic: "3DSS"
  [4B]  Version: 2
  [8B]  Title ID (big-endian)
  [4B]  Timestamp (unix epoch)
  [4B]  File count
  [4B]  Uncompressed payload size

### PS1 / PSone Classics Notes

PS1 saves are handled in two formats:

- Raw memory cards for desktop emulators / DuckStation (`.mcd` / `.mcr`)
- PSone Classics memory cards for PSP / Vita / Adrenaline (`SCEVMC0.VMP`, `SCEVMC1.VMP`)

The server stores raw PS1 card images (`slot0.mcd`, `slot1.mcd`) and regenerates PSP/Vita-compatible
`SCEVMC*.VMP` files when needed. For that regeneration it uses the upstream `mcr2vmp` reference
implementation from:

- [chrisbrasington/psp_psx_save_sync](https://github.com/chrisbrasington/psp_psx_save_sync)
- originally based on `vita-mcr2vmp` by `@dots_tb`

The bundled helper source lives under [server/third_party/mcr2vmp](/E:/projects/3dssync/server/third_party/mcr2vmp).
That third-party code is included with its original license notice.

Payload (zlib compressed):
  For each file:
    [2B]  Path length
    [NB]  Path (UTF-8)
    [4B]  File size
    [32B] SHA-256 hash
  For each file:
    [NB]  File data
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/status` | GET | Health check (no auth required) |
| `/api/v1/titles` | GET | List all titles on server |
| `/api/v1/titles/names` | POST | Look up game names by product code |
| `/api/v1/saves/{title_id}` | GET | Download save bundle |
| `/api/v1/saves/{title_id}` | POST | Upload save bundle |
| `/api/v1/saves/{title_id}/meta` | GET | Get save metadata |
| `/api/v1/sync` | POST | Get sync plan for multiple titles |

All endpoints except `/status` require `X-API-Key` header.

## License

MIT

## Acknowledgments

- [devkitPro](https://devkitpro.org/) for the 3DS development toolchain
- [libctru](https://github.com/devkitPro/libctru) for 3DS homebrew libraries
- [3dstdb](https://www.3dsdb.com/) for the 3DS game names database
- [advanscene](https://www.advanscene.com/) for the DS game names database
