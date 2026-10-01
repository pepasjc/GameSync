# GameSync — PSP Client

Homebrew client for PlayStation Portable. Syncs save files with the GameSync server over WiFi.

## Requirements

- [pspdev toolchain](https://github.com/pspdev/pspdev) (includes psp-gcc, pspsdk, psp-build)

### Install pspdev

**Linux / macOS (x86-64):** download a pre-built release from the [pspdev releases page](https://github.com/pspdev/pspdev/releases) and extract to `/usr/local/pspdev`.

**Raspberry Pi / ARM Linux:** pre-built binaries are x86-64 only — build from source using the included helper script:

```bash
bash install-pspsdk-rpi.sh   # from the repo root (~30-90 min)
```

After installing, add to your shell environment:

```bash
export PSPDEV="$HOME/pspdev"       # or wherever you installed it
export PSPSDK="$PSPDEV/psp/sdk"
export PATH="$PSPDEV/bin:$PATH"
```

## Build

```bash
cd psp
make          # produces EBOOT.PBP
make clean
```

### Output

`EBOOT.PBP` — copy the entire folder to the PSP memory stick:

```
ms0:/PSP/GAME/pspsync/EBOOT.PBP
```

Launch from the PSP XMB under **Game → Memory Stick**.

## Configuration

Create a config file on the memory stick at:

```
ms0:/PSP/GAME/pspsync/config.txt
```

```ini
server_url=http://192.168.1.100:8000
api_key=your-secret-key
wifi_ap=0
```

`wifi_ap` selects which saved WiFi access point to use (0–2, matching the PSP's network settings).

## Screens

The UI is drawn with the GE (sceGu): a dark theme with a header (GameSync,
the four views as a tab strip between the L and R glyphs, version, WiFi bars
and a server status dot), a list on the left, a detail panel for the selected
row on the right, and a footer with the PlayStation button hints. Dialogs are
cards over the dimmed view.

- **Saves** - every PSP/PS1 save on the Memory Stick plus saves that only exist
  on the server (tagged `SERVER`). The detail panel shows the game ID, size,
  file count and folder, plus the server URL, console ID and access point.
- **Catalog** - the server's ROM library, with PSP and PS1 as sub-tabs (chips
  at the top of the list, switched with SELECT). Rows show the download state
  (queued, paused, done, error) and size; the detail panel shows the file, how
  it installs (CSO / EBOOT.PBP) and where it goes.
- **Downloads** - the resumable download queue. While a transfer runs the
  detail panel shows a progress bar, size, speed and ETA.
- **Settings** - server URL, connection state, console ID, access point, what
  the catalog cache holds, the version, and **Refresh catalog**.

Sync opens a compare dialog (this PSP vs. server: size, files, date) with an
arrow for the suggested direction: Cross confirms it, Square forces an upload,
Triangle forces a download (the only way to pick a side on a conflict).
Sync all ends with a summary of uploads, downloads, up-to-date saves,
conflicts and failures.

The text is the Vegur typeface (public domain), pre-rendered into
`source/font_data.c` by `tools/make_font.py` (needs Pillow and fontTools) -
rerun it after changing sizes or the glyph set.

## Catalog cache

The catalog is kept on the Memory Stick in
`ms0:/PSP/GAME/pspsync/catalog_cache.bin`, one section per system (PSP, PS1)
holding the rows the client uses and the server fingerprint they were fetched
under (`GET /api/v1/roms/fingerprints`). On the first visit to the Catalog in
a session the client asks for the fingerprints and refetches only a system
whose fingerprint moved (or that isn't cached yet); the rest come from the
file. With the server unreachable the cached copy is shown with an
"Offline - cached" tag; a server without the fingerprints route is fetched
live every time and nothing is cached. **Settings > Refresh catalog** asks the
server to rescan its ROM folder (carrying on if that is refused), throws the
cache away and refetches every system.

The format and code (`source/catcache.c`) are shared with the Vita client;
`sh psp/tests/run_host_tests.sh` runs its host tests with the PC's gcc.

## Controls

Cross confirms and Circle cancels everywhere (also on Japanese consoles).

**Every view**

| Button | Action |
|---|---|
| L / R | Previous / next view: Saves, Catalog, Downloads, Settings (wraps) |
| Up / Down | Move one row (hold to repeat) |
| Left / Right | Page up / down (hold to repeat) |
| START | Exit GameSync (asks first) |

**Saves**

| Button | Action |
|---|---|
| Cross | Sync: compare with the server; in the dialog Cross takes the suggested direction, Square uploads, Triangle downloads, Circle cancels |
| Square | Sync all saves |
| Triangle | Save details |

**Catalog**

| Button | Action |
|---|---|
| Cross | Download the selected game (resumes a paused or failed one) |
| Triangle | Game details |
| SELECT | Switch sub-tab: PSP / PS1 |

**Downloads**

| Button | Action |
|---|---|
| Cross | Start or resume the selected download |
| Circle | Pause the running download (resume later with Cross) |
| Square | Remove the selected entry, or clear every finished one |
| Triangle | Download details |

**Settings**

| Button | Action |
|---|---|
| Cross | Run the selected action (Refresh catalog) |

HOME also leaves the app through the system menu.
