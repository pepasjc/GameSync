# GameSync

Sync save files between consoles, handhelds, and emulators through a self-hosted local server.

## Repository Structure

| Folder | Description |
|---|---|
| `server/` | FastAPI server — stores saves and history |
| `3ds/` | Nintendo 3DS homebrew client |
| `ds/` | Nintendo DS / DSi homebrew client |
| `psp/` | PSP homebrew client |
| `vita/` | PS Vita homebrew client |
| `ps3/` | PS3 homebrew client |
| `android/` | Android app |
| `steamdeck/` | Steam Deck / Linux desktop client |
| `desktop/` | Windows/macOS desktop client (PyQt6) |

Each client folder has its own README with build instructions and setup details.

## Server Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
cd server
uv sync
uv run python run.py
```

Configuration is done via environment variables:

| Variable | Default | Description |
|---|---|---|
| `SYNC_API_KEY` | `dev-key-change-me` | API key required by all clients |
| `SYNC_SAVE_DIR` | `./saves` | Directory where saves are stored |
| `SYNC_HOST` | `0.0.0.0` | Bind address |
| `SYNC_PORT` | `8000` | Server port |
| `SYNC_MAX_HISTORY_VERSIONS` | `5` | Number of previous save versions to keep |

```bash
# Linux / macOS
SYNC_API_KEY=your-secret-key uv run python run.py

# Windows
set SYNC_API_KEY=your-secret-key
uv run python run.py
```

## How It Works

1. Start the server on any PC on your local network.
2. Configure each client with the server URL and API key.
3. Clients upload saves to the server and download them on other devices.
4. A three-way hash protocol detects which side changed since the last sync, avoiding conflicts across multiple consoles.
5. The server keeps a configurable history of previous save versions.

## Documentation

- [docs/sync-profiles.md](docs/sync-profiles.md)
- [docs/technical-overview.md](docs/technical-overview.md)
- [docs/ps1-sync.md](docs/ps1-sync.md)
- [docs/ps3-sync.md](docs/ps3-sync.md)

## License

MIT
