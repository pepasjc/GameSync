# GameSync — 3DS Client

Homebrew client for Nintendo 3DS / 2DS. Syncs save files with the GameSync server over local WiFi using a three-way hash protocol to handle multiple consoles without conflicts.

## Requirements

- [devkitPro](https://devkitpro.org/wiki/Getting_Started) with 3DS support
- Required packages (install via `pacman` inside the devkitPro MSYS2 shell):

```bash
pacman -S 3ds-dev 3ds-zlib
```

`3ds-dev` includes citro2d and citro3d, which draw the GUI.

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
Install `3dssync.cia` using FBI, or update in-app from *Settings > Check for updates*.

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

The in-app **Settings** tab can also edit these values directly on the console using the system keyboard (or a D-pad editor if the keyboard applet can't start).

## Screens

The GUI is drawn with citro2d in a dark theme shared with the DS client. The
app has three top-level tabs, **Saves | Catalog | Settings**, shown in the top
screen's header between the `L` and `R` glyphs. The header also shows the WiFi
strength and a server status dot (green = the server answered, red = it
didn't), and every screen has a footer with button hints.

- **Saves**: the list is on the bottom screen, with All / 3DS / NDS sub-tabs, a
  mark box per row, a system tag (`3DS`, `NDS`, cyan `CART` for game cards) and
  a status dot. The top screen shows the selected title: name, title id,
  product code, storage, its sync state (up to date, needs upload / download,
  conflict, failed, or *manual sync* for game cards, which Sync All skips) and
  the result of the last compare with the server. The bar above the footer
  shows the last action's result.
- **Smart Sync / details**: this console's and the server's copy side by side
  (size, files, hash, last sync), with the verdict below and a confirmation
  card on the bottom screen.
- **Progress**: syncs, restores, updates and catalog installs show a card with
  a progress bar (with speed, elapsed and remaining time for downloads).
- **Catalog**, **Settings** and **History** use the same layout: a list on the
  bottom screen, details of the highlighted entry on the top.

Rows can also be picked by tapping them on the touch screen. Holding the D-pad
repeats.

## Controls

The same scheme on every screen (shared by all GameSync console clients):

| Button | Everywhere |
|---|---|
| D-pad Up / Down | Move one row |
| D-pad Left / Right | Page up / page down |
| L / R | Previous / next tab (Saves, Catalog, Settings; wraps around) |
| SELECT | Next sub-tab (All / 3DS / NDS on Saves, 3DS / NDS / DSi on Catalog) |
| A | Confirm / the highlighted row's main action |
| B | Cancel / back; closes dialogs (never starts anything) |
| START | Exit (asks first: START again or A exits, B stays) |

### Saves

| Button | Action |
|---|---|
| A | Smart sync the selected save (or upload all marked titles) |
| X | Sync all saves |
| Y | Details: compare with the server, upload, download, history (restore an older version), mark / unmark |
| B | Unmark all marked titles |
| SELECT | All / 3DS / NDS |
| Touch | Tap a row to select it; tap its mark box to mark / unmark it |

Marking for a batch upload is on the touch screen (the mark box) or in the
**Y** menu. When both copies of a save changed, the Smart Sync card offers
**A** upload (the server keeps the old copy in its history), **X** download,
**B** cancel.

### Catalog

| Button | Action |
|---|---|
| A | Install the selected game (try again after an error) |
| X | Only games with RetroAchievements / all games |
| Y | Search (system keyboard; an empty search shows everything) |
| B | Clear the search; hold B to cancel a download |
| SELECT | 3DS / NDS / DSi, whichever the server has |

On the install prompt, **X** saves a 3DS cart as a `.3ds` file instead of
installing a CIA.

### Settings

| Entry | What A does |
|---|---|
| Server URL, API key, NDS ROM folder | Edit (system keyboard); saved to `config.txt` right away |
| Rescan titles | Look for installed titles and DS saves again |
| Refresh catalog | Ask the server to rescan its ROM folder, throw the cached catalog away and download it again |
| Check for updates | Download and install the latest GameSync CIA |

If the system keyboard can't start, a D-pad editor takes its place: Left /
Right move the cursor, Up / Down change the character, Y inserts, X deletes,
A confirms, B cancels.

## Game catalog

The **Catalog** tab browses the server's ROM catalog and installs games
straight from the 3DS. The list is on the bottom screen, details of the
highlighted game on the top.

The catalog is cached on the SD card in `sdmc:/3ds/3dssync/cache/`, one
`catalog_<SYSTEM>.txt` per system (3DS, NDS, DSI): a tab-separated text file
with a header carrying the cache format version and the server's fingerprint
for that system, one row per game (only the fields the client uses), and an
`END` line with the row count, so a file cut short is ignored. On open the
client asks `GET /api/v1/roms/fingerprints` and downloads again only the
systems whose fingerprint changed (same strategy as the MiSTer client); the
rest come from the SD card. Search and the RetroAchievements filter then run
on the cached rows. If the server can't be reached the cached copy is shown
with an `OFFLINE` tag. A server without the fingerprints route is browsed live
like before (paged from the server, which also does search and the RA
filter). *Settings > Refresh catalog* forces a full reload. The server's
`?extract=` support is cached with the rows: if the server operator enables
CIA conversion later, refresh the catalog to pick it up.

A gold `RA NN` badge marks games with an achievement set (an outlined `RA?`
when matched by name only); a green check marks games that are already
installed.

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
