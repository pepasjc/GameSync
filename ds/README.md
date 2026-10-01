# GameSync — NDS Client

Homebrew client for Nintendo DS / DS Lite / DSi. Syncs save files stored on a flashcard with the GameSync server over WiFi.

Requires a flashcard (e.g. R4, DSTT, Acekard) — saves are read directly from the flashcard filesystem via libfat.

![Save list](docs/screenshots/main.png) ![Smart Sync](docs/screenshots/smart_sync.png) ![Game catalog](docs/screenshots/catalog.png)

## Screens

Both screens are 16-bit bitmaps drawn in software (`source/gfx.c`, `theme.c`, `views.c`): a dark slate theme with a
teal accent, a header (app name, screen, WiFi state, version), cards, status pills and a footer showing the buttons
each screen accepts. The top screen shows details, the bottom screen the list or dialog you are working in.

- **Save list** (bottom): a coloured dot per save (grey = not checked yet, green = up to date, amber = needs
  upload, blue = needs download, red = conflict or check failed) and a cloud for saves the server has. The header
  counts each status after a scan. The top screen shows the selected save: status, what A would do, size, title
  ID, file, and the server address with an online/offline badge.
- **Settings & tools** (top, **L**): the connection settings with their values and the tools (Rescan, Connect
  WiFi, Check Updates, Achievements, Game Catalog).
- **Smart Sync / Upload**: this DS's save and the server's side by side on the top screen (size, SHA-256 prefix,
  last sync), the suggested action and its buttons on the bottom.
- **Work in progress** (scan, upload, download, WiFi, updates, achievement sets): a status card with a progress
  bar where the length is known, over an activity log of everything the client prints, so error details stay
  readable. The result is shown in the same card.
- **Editor**: the D-pad text editor shows the field in a box with the cursor highlighted and a strip of the
  characters Up/Down step through.

The UI can be rendered on a PC, without a DS, for review: `sh ds/tests/render_screens.sh [OUT_DIR]` (needs gcc;
Pillow for PNGs) draws every screen from mock data.

## Requirements

- [devkitPro](https://devkitpro.org/wiki/Getting_Started) with NDS support
- Required package (install via `pacman` inside the devkitPro MSYS2 shell):

```bash
pacman -S nds-dev
```

## Build

**On Windows** — open the devkitPro MSYS2 shell (not Git Bash), then:

```bash
make          # ndssync.nds
make dsi      # ndssync_dsi.nds  (DSi build, faster WiFi)
make both     # both
make clean
```

**On Linux / macOS:**

```bash
make          # ndssync.nds
make dsi      # ndssync_dsi.nds  (DSi build, faster WiFi)
make both     # both
make clean
```

### Host tests

The plain-C parts (catalog JSON parsing and paging, file names, the batched RA set parser) build and run on a PC:

```bash
# from the repo root, e.g. in a Debian container
docker run --rm -v "$PWD":/src -w /src debian:bookworm-slim sh ds/tests/run_host_tests.sh
# the real http.c + catalog code against this repo's server on a fixture library
docker run --rm -v "$PWD":/src -w /src python:3.12-slim sh ds/tests/run_e2e.sh
```

### Output files

| File | Target |
|---|---|
| `ndssync.nds` | DS / DS Lite, or a DSi running it from a flashcard (DS mode) |
| `ndssync_dsi.nds` | DSi / DSi XL in DSi mode (SD card, TWiLight Menu++ / Unlaunch / hiyaCFW): faster WiFi |

Copy the `.nds` file to the flashcard SD and launch it from the flashcard menu. On a DSi, put `ndssync_dsi.nds`
on the console's SD card and launch it in DSi mode.

### DSi build: faster WiFi

Both builds use the DSi's own WiFi chip (WPA2, firmware access points 4–6) when they run in DSi mode, but the
stock dswifi TCP stack (sgIP) caps every download at one packet per round trip: it never advertises a receive
window above 1400 bytes, and sends no MSS option, so the server falls back to 536-byte segments.

`ndssync_dsi.nds` links a patched copy of dswifi (`third_party/dswifi`, see the notes at the top of
`sgIP_Config.h`) instead of `-ldswifi9`:

- a 64 KB TCP receive buffer and a real receive window (32 KB by default) in DSi mode, so many segments are in
  flight at once
- an MSS option on SYN, so the server sends full-size (1420-byte) segments
- 48 extra 2 KB WiFi packet buffers so a full window has somewhere to land, and the ARM9 at 134 MHz
- `recv()` copies with `memcpy` instead of a byte loop

Started in DS mode (from a flashcard) the same file behaves like the standard build (1400-byte window, plus the MSS
option) and says so when WiFi starts. The window can be tuned in `config.txt`; the catalog's progress screen shows
the resulting KB/s:

```ini
tcp_window=32768   # DSi build only: 1400..65535, default 32768
```

## Configuration

On first launch a default config file is created on the flashcard:

```
fat:/dssync/config.txt
```

Edit it with your server details and WiFi credentials:

```ini
server_url=http://192.168.1.100:8000
api_key=your-secret-key

# WiFi (required for DS / DS Lite — WEP only)
wifi_ssid=YourNetwork
wifi_wep_key=your-wep-key
```

> **DSi note:** The DSi can use firmware WiFi settings; leave `wifi_ssid` and `wifi_wep_key` blank to skip manual WiFi config.

> **WEP only:** The DS WiFi chip only supports WEP encryption. DS Lite has the same limitation. DSi supports WPA via its firmware.

The config can also be edited in-app: press **L** for the Settings & tools menu and **A** on a field. The D-pad editor:
Left/Right move the cursor, Up/Down change the letter under it (or add one at the end), **A** inserts, **B** deletes,
**Y** saves, **X** cancels.

## RetroAchievements (DSi + nds-bootstrap-ra)

For playing with achievements on real hardware through the nds-bootstrap-ra fork
(DSi, TWiLight Menu++ on the SD card). The GameSync server talks to
RetroAchievements; it needs `SYNC_RA_USERNAME` and a token from `ra_login.py`.
Open it from the config panel: **L**, then **Achievements**.

- **Update achievement sets**: scans `sd:/roms/nds` recursively (or `sd:/roms` if that doesn't exist; 4 folder
  levels deep, up to 1000 ROMs, `saves` folders skipped) and computes each ROM's RetroAchievements hash first.
  Hashes are cached in `sd:/_nds/ra/hashes.txt` by file name and size, so later runs only download. Then it asks the
  server for the sets in batches (`POST /api/v1/ra/sets`, 32 hashes per request on a DSi, 12 in DS mode) and saves
  the set for every ROM RA knows to `sd:/_nds/ra/sets/<ROM file name>.txt`, where nds-bootstrap loads it. ROMs RA
  doesn't know are skipped. Batching matters: the DSi network stack stops opening connections after a few dozen,
  so one request per ROM used to fail part way through a big library. Hold **B** to stop.

The files go under `sd:/`, or under `fat:/` if only a flashcard with `_nds` is present. nds-bootstrap only runs
achievements from the DSi SD card, though.

## Game catalog

Browse the DS games on the server and install them to the SD card. Open it with **SELECT** from the save list,
or **L**, then **Game Catalog**. It needs WiFi; it works even when no saves were found.

The bottom screen lists the games (the server pages and filters the list, so a catalog of thousands of games never
has to fit on the DS); the top screen shows the selected game's full name, size, RetroAchievements status and
whether it is already on the SD.

- **RA NN** (gold badge) marks games with a RetroAchievements set of NN achievements. **RA?** (outlined) means the
  server matched the game by name only, so the set may not fit this dump.
- A green **check mark** marks games already on the SD: a `.nds`/`.dsi` file with the same name (extension and case
  ignored) somewhere under the install folder.
- Installing downloads the ROM to `sd:/roms/nds/<name>.nds` (TWiLight Menu++'s folder; `roms/dsi` for DSiWare if
  the server has a `DSI` system). The server keeps DS ROMs zipped and unzips them while sending
  (`?extract=nds`), so the DS writes the plain `.nds` straight to the SD: nothing is held in RAM and nothing is
  unzipped on the DS. The download goes to `<name>.nds.part` first and is renamed when complete. The progress screen
  shows a progress bar, size, percentage, speed (KB/s, current and average) and time left, and the summary shows the
  average WiFi throughput. Free space is checked before writing.
- After installing a game that has achievements, its set is fetched right away (like **Update achievement sets**
  does), so it is ready to play with nds-bootstrap-ra.

| Button | Action |
|---|---|
| Up / Down | Move (hold to repeat; wraps around) |
| Left / Right | Page up / down (10 games) |
| L / R | Jump 100 games |
| A | Install the selected game (asks first; replaces the file if it is already there). After an error: try again |
| Y | Toggle "only games with RetroAchievements" |
| X | Search game names (D-pad text editor; confirm with Y, empty = all) |
| START | Clear the search |
| SELECT | Next system, if the server has more than one DS system |
| B | Back (during a download: hold to cancel) |

Needs a server with `has_ra` and `?extract=nds` support on `/api/v1/roms` (older servers are reported as such).

## Controls

| Button | Action |
|---|---|
| A | Smart sync of the selected save (suggests upload or download) |
| R | Upload the selected save |
| X | Scan all saves against the server (out-of-sync ones turn red) |
| Y | Save details |
| Up / Down | Navigate save list |
| Left / Right | Page up / down (11 saves) |
| SELECT | Game catalog |
| L | Toggle the Settings & tools menu on the top screen (server/WiFi settings, Rescan, Connect WiFi, Check Updates, Achievements, Game Catalog); Up/Down and A use it while it has the focus |
| START | Exit |
