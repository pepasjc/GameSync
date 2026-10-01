# GameSync — 3DS Client

Homebrew client for Nintendo 3DS / 2DS. Syncs save files with the GameSync server over local WiFi using a three-way hash protocol to handle multiple consoles without conflicts.

## Requirements

- [devkitPro](https://devkitpro.org/wiki/Getting_Started) with 3DS support
- Required packages (install via `pacman` inside the devkitPro MSYS2 shell):

```bash
pacman -S 3ds-dev 3ds-zlib
```

## Build

**On Windows** — open the devkitPro MSYS2 shell (not Git Bash), then:

```bash
make          # produces 3dssync.3dsx
make cia      # produces 3dssync.cia
make clean
```

**On Linux / macOS:**

```bash
make          # produces 3dssync.3dsx
make cia      # produces 3dssync.cia
make clean
```

### Output files

| File | How to use |
|---|---|
| `3dssync.3dsx` | Run via [Homebrew Launcher](https://github.com/fincs/generic-hid) from the SD card |
| `3dssync.cia` | Install with [FBI](https://github.com/Steveice10/FBI) for a permanent home menu icon |

## Installation

### Homebrew Launcher (.3dsx)
Copy `3dssync.3dsx` to `sdmc:/3ds/3dssync/3dssync.3dsx` on the 3DS SD card.

### Home menu app (.cia)
Install `3dssync.cia` using FBI or trigger an in-app auto-update from the SELECT button.

## Configuration

On first launch a default config is created at:

```
sdmc:/3ds/3dssync/config.txt
```

Edit it with the details of your GameSync server:

```ini
server_url=http://192.168.1.100:8000
api_key=your-secret-key
```

The in-app config editor (L button) can also edit these values directly on the console using the system keyboard.

## Controls

| Button | Action |
|---|---|
| A | Smart sync the selected save (or upload all marked titles) |
| X | Sync all saves |
| Y | Save history (restore an older version) |
| SELECT | Mark / unmark the selected title for batch upload |
| R | Switch tab (All / 3DS / NDS) |
| L | Config menu (server, API key, NDS folder, rescan, updates, game catalog) |
| B | Game catalog |
| START | Exit |

## Game catalog

Press **B** on the save list (or pick *Game Catalog* in the L menu) to browse
the server's ROM catalog and install games straight from the 3DS. The list is
on the top screen, details and controls on the bottom.

| Button | Action |
|---|---|
| A | Install the selected game |
| SELECT | Switch system (3DS / NDS / DSi, whichever the server has) |
| Y | Only games with RetroAchievements / all games |
| X | Search (system keyboard) |
| START | Clear the search |
| Up/Down, Left/Right | Move, page |
| L / R | Jump 100 entries |
| B | Back (hold B to cancel a download) |

`RA NN` marks games with an achievement set (`RA?` when matched by name only);
a green `*` marks games that are already installed.

**3DS games** install as a CIA directly through the system's AM service — the
download is streamed into the install in 64 KB chunks, so nothing is staged on
the SD card and the game appears on the HOME Menu when it finishes. Requires
custom firmware (Luma3DS) like any CIA install.

- A `.cia` in the server's `n3ds` folder is installed as-is.
- A `.3ds` / `.cci` cart image (or a zip holding one) is converted on the
  server with `?extract=cia`. That conversion is an optional server tool: set
  `SYNC_ROM_3DS_CIA_COMMAND` (e.g. to `3dsconv`, see `server/README.md`).
  Big games take a few minutes to convert
  before the download starts; converted CIAs are cached on the server.
- If the server can't convert (HTTP 503), the client offers to save the raw
  `.3ds` to `sdmc:/roms/3ds/` instead; install it from there with GodMode9
  (*Build CIA from file*). Press **X** on the install prompt to pick that
  directly.

Titles installed from the catalog are remembered in
`sdmc:/3ds/3dssync/catalog_cia.txt` (rom id → title id) so the list can show
them as installed.

**DS / DSi games** are saved as `.nds` files in the TWiLight Menu++ layout:
`sdmc:/roms/nds/` (or the configured *NDS ROM Directory*) and
`sdmc:/roms/dsi/`. Zipped ROMs are unzipped by the server (`?extract=nds`).
Downloads go to a `.part` file that is renamed when complete, and the free
space is checked first. After a DS install the save list is rescanned so the
new game's save can be synced.

## Tests

The catalog's parsing and planning code (`source/catalog_data.c`) is plain C
and has host tests:

```bash
sh tests/run_host_tests.sh          # needs a host gcc (Linux / WSL)
```
