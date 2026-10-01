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
| `show_server_only` | List saves that only exist on the server (default `1`) |

Everything here can also be changed in the **Settings** tab.

## Interface

A dark, graphical interface drawn at the console's own output resolution
(720p, 1080p, ...). Every screen has the same layout:

- **Header** — GameSync logo and the current view, the tabs (L1 / R1
  cycle them), the app version and an Online / Offline server indicator.
- **Toolbar** — the tab's sub-tabs as chips (SELECT cycles them): All / PS3 /
  PS1 in Saves, PS3 / PS1 in the ROM Catalog.
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

The same scheme as every GameSync console client. Cross is always confirm and
Circle always cancel, even on a console set to the Japanese convention.

| Button | Everywhere |
|---|---|
| Up / Down | Move one row (hold to repeat) |
| Left / Right | Page up / down (hold to repeat) |
| L1 / R1 | Previous / next tab: **Saves → ROM Catalog → Downloads → Settings** (wraps) |
| SELECT | Next sub-tab of the current tab |
| Cross | Confirm / primary action of the selected row |
| Circle | Cancel / back; closes dialogs; pauses a running download |
| START | Exit GameSync (asks first) |

The PS button also leaves the app.

### Saves

| Button | Action |
|---|---|
| Cross | Smart sync (decides upload or download, asks first) |
| Square | Sync all saves (conflicts are skipped) |
| Triangle | Details and actions: force upload, force download, compare files with the server, refresh hash, rescan all saves |
| SELECT | Filter: All / PS3 / PS1 |

### ROM Catalog

| Button | Action |
|---|---|
| Cross | Download the selected game, or resume it (switches to Downloads) |
| Triangle | Details (file, size, install location, download state) |
| SELECT | System: PS3 / PS1 |

### Downloads

| Button | Action |
|---|---|
| Cross | Start / resume the selected download |
| Circle | Pause the running download (progress is kept) |
| Square | Clear finished entries |
| Triangle | Options: start / resume, remove the entry and its partial file |

While a download runs, the detail panel shows a progress bar with percentage,
bytes, speed and time remaining; it refreshes about four times a second so the
transfer itself is not slowed down.

### Settings

| Button | Action |
|---|---|
| Up / Down | Select a field |
| Left / Right | Change user / flip a switch |
| Cross | Edit text, toggle, or run Refresh catalog / Save and apply / Discard changes |
| Circle | Discard unsaved changes and go back to Saves |

The PS3 user (previously L2 / R2 in the Saves view) and showing server-only
saves (previously L1) are Settings fields now; **Save and apply** writes
`config.txt`, rescans and reconnects.

Text fields open an editor: Up / Down changes the character under the cursor,
Left / Right moves the cursor, Square inserts a space, Triangle deletes, Cross
accepts and Circle cancels.

## ROM catalog cache

The catalog is kept on the HDD between runs, one file per system:

```
/dev_hdd0/game/3DSSYNC00/USRDIR/catalog_PS3.dat
/dev_hdd0/game/3DSSYNC00/USRDIR/catalog_PS1.dat
```

Each is plain text: a `GSCATALOG <version>` line, `system=`, `fingerprint=`,
`count=`, then one tab-separated row per game (`rom_id`, `filename`, `name`,
`system`, `size`, `is_bundle`, `file_count`, `extract_format`). The first time
the catalog opens in a session the app asks the server for
`/api/v1/roms/fingerprints`; a system whose fingerprint is unchanged loads from
the file, a changed or missing one is fetched again and rewritten. With the
server unreachable the cached copy is shown (marked **Offline - cached**). A
server without the fingerprints route is used the old way, without a cache.

**Settings → Refresh catalog** asks the server to rescan its ROM folder (if the
rescan fails or is not allowed it carries on), deletes the cache, fetches every
system again and reports the counts.
