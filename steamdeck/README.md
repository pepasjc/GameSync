# GameSync — Steam Deck Client

Python/PyQt6 client for syncing emulator save files with the GameSync server. Designed for Steam Deck Gaming Mode (full-screen, controller-driven) but also runs on any Linux desktop.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — used to manage the virtual environment and dependencies

Install `uv` on Steam Deck (Desktop Mode):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Run

```bash
cd steamdeck
uv run python3 main.py
```

`uv` automatically creates a virtual environment and installs dependencies (`PyQt6`, `requests`, `pygame`) on first run.

## Steam Deck Gaming Mode Setup

To launch from Gaming Mode, add `launch.sh` as a non-Steam game in Steam:

1. Switch to Desktop Mode
2. Open Steam → **Add a Non-Steam Game** → browse to `launch.sh`
3. In the game properties, set:
   - **Target:** `/bin/bash`
   - **Launch Options:** `-lc "/path/to/GameSync/steamdeck/launch.sh"`
4. The script auto-runs `git pull` on launch to keep the client up to date, then starts the app

## Supported Emulators

| Emulator | System(s) |
|---|---|
| RetroArch | Multi-system |
| Dolphin | GameCube, Wii |
| PCSX2 | PS2 |
| DuckStation | PS1 |
| PPSSPP | PSP |
| melonDS | NDS |
| RPCS3 | PS3 |

## Configuration

On first launch, open **Settings** (gear icon or Start button) and enter your server URL and API key:

```
Server URL:  http://192.168.1.100:8000
API Key:     your-secret-key
```

## Controls (Gaming Mode)

| Input | Action |
|---|---|
| Left stick / D-pad | Navigate save list |
| A (Cross) | Upload / confirm |
| B (Circle) | Download / back |
| Menu (Start) | Open settings |
