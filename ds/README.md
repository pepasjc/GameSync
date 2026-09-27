# GameSync — NDS Client

Homebrew client for Nintendo DS / DS Lite / DSi. Syncs save files stored on a flashcard with the GameSync server over WiFi.

Requires a flashcard (e.g. R4, DSTT, Acekard) — saves are read directly from the flashcard filesystem via libfat.

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
make dsi      # ndssync_dsi.nds  (DSi-enhanced)
make clean
```

**On Linux / macOS:**

```bash
make          # ndssync.nds
make dsi      # ndssync_dsi.nds  (DSi-enhanced)
make clean
```

### Output files

| File | Target |
|---|---|
| `ndssync.nds` | DS / DS Lite / DSi (standard) |
| `ndssync_dsi.nds` | DSi / DSi XL (extra RAM, enhanced build) |

Copy the `.nds` file to the flashcard SD and launch it from the flashcard menu.

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

The config can also be edited in-app: press **L** to toggle the config panel and use the on-screen keyboard.

## RetroAchievements (DSi + nds-bootstrap-ra)

For playing with achievements on real hardware through the nds-bootstrap-ra fork
(DSi, TWiLight Menu++ on the SD card). The GameSync server talks to
RetroAchievements; it needs `SYNC_RA_USERNAME` and a token from `ra_login.py`.
Open it from the config panel: **L**, then **Achievements**.

- **Update achievement sets**: scans `sd:/roms/nds` recursively (or `sd:/roms` if that doesn't exist; 4 folder
  levels deep, up to 1000 ROMs, `saves` folders skipped), computes each ROM's RetroAchievements hash, and saves the
  set for every ROM RA knows to `sd:/_nds/ra/sets/<ROM file name>.txt`, where nds-bootstrap loads it. ROMs RA doesn't
  know are skipped. Hashes are cached in `sd:/_nds/ra/hashes.txt` by file name and size, so later runs only
  download. Hold **B** to stop.
- **Upload unlocks**: first moves unlocks still sitting in `sd:/_nds/nds-bootstrap/ramDump.bin` into
  `sd:/_nds/ra/unlocks.log`, the same way nds-bootstrap does on the next game boot. Then it sends the new log lines
  to the server, one request per game. The unlock time comes from the DS clock. The screen shows how many were
  submitted, already awarded, duplicate, dry-run or failed. `sd:/_nds/ra/uploaded.txt` records how far the log was
  uploaded. That mark only moves when every unlock reached RetroAchievements, so failed uploads, and uploads to a
  server in dry-run mode (`SYNC_RA_SUBMIT` off), are sent again next time; the server skips repeats. Delete
  `uploaded.txt` to send the whole log again.

The files go under `sd:/`, or under `fat:/` if only a flashcard with `_nds` is present. nds-bootstrap only runs
achievements from the DSi SD card, though.

## Controls

| Button | Action |
|---|---|
| A | Upload selected save to server |
| B | Download selected save from server |
| Up / Down | Navigate save list |
| L | Toggle config editor panel (also holds Rescan, Connect WiFi, Check Updates, Achievements) |
| START | Exit |
