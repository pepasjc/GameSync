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
current view, view pager, version, WiFi bars and a server status dot), a list
on the left, a detail panel for the selected row on the right, and a footer
with the PlayStation button hints. Dialogs are cards over the dimmed view.

- **Saves** - every PSP/PS1 save on the Memory Stick plus saves that only exist
  on the server (tagged `SERVER`). The detail panel shows the game ID, size,
  file count and folder, plus the server URL, console ID and access point.
- **Catalog** - the server's ROM library for PSP or PS1 (switch with L/R). Rows
  show the download state (queued, paused, done, error) and size; the detail
  panel shows the file, how it installs (CSO / EBOOT.PBP) and where it goes.
- **Downloads** - the resumable download queue. While a transfer runs the
  detail panel shows a progress bar, size, speed and ETA.

Sync actions open a compare dialog (this PSP vs. server: size, files, date)
with an arrow for the direction; a conflict asks you to pick a side with
Square or Triangle. Sync all ends with a summary of uploads, downloads,
up-to-date saves, conflicts and failures.

The text is the Vegur typeface (public domain), pre-rendered into
`source/font_data.c` by `tools/make_font.py` (needs Pillow and fontTools) -
rerun it after changing sizes or the glyph set.

## Controls

START cycles **Saves -> Catalog -> Downloads** from any view.

**Saves**

| Button | Action |
|---|---|
| Up / Down | Select a save |
| Left / Right | Page up / down |
| Cross | Sync: compare with the server and confirm the suggested action |
| Square | Upload the selected save |
| Triangle | Download the selected save |
| SELECT | Sync all saves |

**Catalog**

| Button | Action |
|---|---|
| Up / Down, Left / Right | Select / page |
| L / R | Switch system (PSP, PS1) |
| Cross | Download the selected game |
| Triangle | Resume a paused or failed download |
| Circle | Ask the server to rescan its ROMs and reload |

**Downloads**

| Button | Action |
|---|---|
| Up / Down | Select an entry |
| Cross | Start or resume the selected download |
| Square | Pause the running download (resume later) |
| Circle | Remove the selected entry (pause it first if running) |
| Triangle | Clear finished downloads |

In dialogs Cross confirms and Circle cancels. HOME exits.
