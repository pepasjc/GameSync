# GameSync — PS3 Client

Native PS3 homebrew client. Syncs PS3 save folders and PS1 memory card images with the GameSync server over WiFi.

Requires a PS3 running CFW (e.g. Rebug, EVILNAT) or HFW + HEN.

## Requirements

- [PS3Dev / PSL1GHT toolchain](https://github.com/ps3dev/ps3dev)

### Install PS3Dev

**Linux (x86-64):** follow the [ps3dev setup guide](https://github.com/ps3dev/ps3dev) to build and install the toolchain. After installing, add to your shell environment:

```bash
export PS3DEV=/usr/local/ps3dev
export PSL1GHT=$PS3DEV
export PATH="$PS3DEV/bin:$PS3DEV/ppu/bin:$PS3DEV/spu/bin:$PATH"
```

**Windows:** the toolchain is Linux-only — build inside WSL. Open a WSL terminal, navigate to the `ps3/` folder and run `make`.

## Build

```bash
cd ps3
make          # produces ps3sync.pkg
make clean
```

### Output

`ps3sync.pkg` — install on the PS3 via:
- **XMB:** Game → Install Package Files (USB or internal HDD)
- **webMAN / multiMAN:** FTP the `.pkg` to `/dev_hdd0/packages/` then install from the XMB

App ID: `3DSSYNC00`

## Configuration

A default config is created on first launch at:

```
/dev_hdd0/game/3DSSYNC00/USRDIR/config.txt
```

Edit it with your server details:

```ini
server_url=http://192.168.1.100:8000
api_key=your-secret-key
ps3_user=00000001
scan_ps3=1
scan_ps1=1
```

| Key | Description |
|---|---|
| `ps3_user` | PS3 user ID to scan saves for (default `00000001`) |
| `scan_ps3` | Scan PS3 HDD save folders |
| `scan_ps1` | Scan PS1 `.VM1` memory card images |

## Interface

A dark, graphical interface drawn at the console's own output resolution
(720p, 1080p, ...). Every screen has the same layout:

- **Header** — GameSync logo and the current view, the view tabs (SELECT
  cycles them), the app version and an Online / Offline server indicator.
- **List** — the saves, catalog games or downloads, with a teal selection bar,
  coloured status pills and a scrollbar.
- **Detail panel** — everything about the selected row: save location, local
  and server hashes, install path, download progress, and so on.
- **Status banner** — the last result message plus the server address.
- **Footer** — the buttons that work on this screen, as PlayStation glyphs.

Confirmations, results and "working" messages appear as cards over the dimmed
screen. Text uses the PS3's own system font (Rodin, read from `/dev_flash`); if
that can't be loaded the app falls back to a built-in bitmap font.

Status pills in the Saves view: **SYNCED** (green, up to date), **UPLOAD**
(amber, changed here), **DOWNLOAD** (blue, newer on the server), **CONFLICT**
(red, both changed), **LOCAL** / **SERVER** (only on one side) and
**UNCHECKED** (not compared yet).

## Controls

SELECT cycles **Saves → ROM Catalog → Downloads**. The PS button exits.

### Saves

| Button | Action |
|---|---|
| Up / Down | Move through the list |
| Left / Right | Page up / down |
| Cross | Smart sync (decides upload or download, asks first) |
| Square | Force upload to the server |
| Triangle | Force download from the server |
| Circle | Rescan saves and refresh server status |
| R1 | Compare local files with the server copy |
| R3 | Sync all saves (conflicts are skipped) |
| L3 | Hash the selected save now |
| L1 | Show / hide saves that only exist on the server |
| L2 / R2 | Switch PS3 user |
| START | Settings |

### ROM Catalog

| Button | Action |
|---|---|
| Up / Down, Left / Right | Move / page |
| L1 / R1 | Switch system (PS3, PS1) |
| Cross | Download the selected game (switches to Downloads) |
| Triangle | Resume a paused or failed download |
| Circle | Ask the server to rescan its ROM folder and reload |

### Downloads

| Button | Action |
|---|---|
| Up / Down | Move through the queue |
| Cross | Start / resume the selected download |
| Square | Pause the running download (progress is kept) |
| Circle | Remove the selected entry and its partial file |
| Triangle | Clear finished entries |

While a download runs, the detail panel shows a progress bar with percentage,
bytes, speed and time remaining; it refreshes about four times a second so the
transfer itself is not slowed down.

### Settings (START)

| Button | Action |
|---|---|
| Up / Down | Select a field |
| Left / Right | Change user / flip a switch |
| Cross | Edit text, toggle, or run Save and apply / Cancel |
| Circle | Leave without saving |

Text fields open an editor: Up / Down changes the character under the cursor,
Left / Right moves the cursor, Square inserts a space, Triangle deletes, Cross
accepts and Circle cancels.
