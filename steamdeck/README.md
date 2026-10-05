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
| Cemu | Wii U (`mlc01/usr/save/00050000/`) |

## Configuration

On first launch, open the **Settings** tab (R1 until it is highlighted), pick *Server and folders* and enter your server URL and API key:

```
Server URL:  http://192.168.1.100:8000
API Key:     your-secret-key
```

### Cemu folder

Cemu is usually installed outside the EmuDeck `Emulation` folder — a Proton
prefix, the flatpak data dir, or a second install on an SD card — so it is the
one emulator auto-detection can miss.  Settings → **Saves** → *Cemu folder*
pins it: point at `mlc01` or the folder holding it, and a `settings.xml` there
that relocates the MLC is followed too.  Leave it empty to auto-detect
(`<mlc_path>` from any settings.xml found, then the usual locations).

## Screens

The UI is drawn in the dark theme shared by every GameSync client (the same
slate-and-teal palette as the console apps). Five top-level tabs, **Saves |
Catalog | Installed | Downloads | Settings**, sit in the header between the
`L1` and `R1` glyphs, next to a server status dot (green = the server
answered, red = it didn't). Under the header a row of chips is the sub-tab
(the system filter), the list fills the left side and a detail panel on the
right describes the highlighted row. A one-line status banner and a footer of
button hints for the current tab run along the bottom.

## Controls (Gaming Mode)

The same scheme on every screen (shared by all GameSync clients):

| Input | Everywhere |
|---|---|
| D-pad / left stick Up / Down | Move one row (hold repeats) |
| D-pad Left / Right | Page up / page down (hold to speed up, then jump by first letter) |
| L1 / R1 | Previous / next tab (wraps around) |
| SELECT (View) | Next sub-tab: the system filter |
| A | Confirm / the highlighted row's main action |
| B | Cancel / back: closes dialogs and the search (never starts anything, never exits) |
| X | The tab's secondary action |
| Y | Details (Saves) or search (Catalog, Installed) |
| START (Menu) | Exit (asks first) |

| Tab | A | X | Y | B |
|---|---|---|---|---|
| Saves | Smart sync the save (upload or download; a conflict opens the comparison) | Sync all | Details: compare, upload, download, get the ROM | Clear the search |
| Catalog | Queue the download | RetroAchievements games only / all | Search | Clear the search |
| Installed | Delete the ROM (asks first) | Rescan the ROM folders | Search | Clear the search |
| Downloads | Pause / resume / retry the row | Clear finished | Remove the row (asks first) | Pause the running download |
| Settings | Open the highlighted entry | | | |

On **Saves**, L2 / R2 step the status filter (needs action, upload, download,
conflict, synced, …). **Sync all** uploads every save that changed here and
downloads every save that changed on the server; conflicts are skipped (open
them with Y), and a save that exists only on the server is fetched only when
the game's ROM is on this machine.

**Settings** has *Server and folders* (the connection and path editor),
*Rescan saves*, *Refresh catalog*, *Rescan installed games* and *Exit*.

A keyboard works too: arrows / W S to move, Left / Right or Page Up / Down to
page, Enter = A, Esc or Backspace = B, X, Y, Tab / Shift+Tab (or Q / E) for
tabs, `[` `]` for the system filter, F3 / F4 for the status filter, F10 to
exit.

## Catalog cache

The server's ROM catalog is kept in `~/.config/savesync/steamdeck_catalog.json`
per system, together with the fingerprint the server published for it
(`GET /api/v1/roms/fingerprints`) — the same strategy as the MiSTer and console
clients, using the shared `shared/catalog_cache.py`. On start only the systems
whose fingerprint moved are downloaded again, search and the RA filter run
locally, and when the server can't be reached the cached copy is shown (the
banner says so). A server without the fingerprints route is browsed live.
*Settings > Refresh catalog* asks the server to rescan its ROM folder, throws
the cached copy away and fetches everything again.
