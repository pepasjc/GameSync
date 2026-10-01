# GameSync — PS Vita Client

Homebrew client for PlayStation Vita. Syncs Vita and PSP-emu save files with the GameSync server over WiFi, and installs PSP and PS1 games from the server's ROM catalog straight into Adrenaline's PSP tree.

Requires a CFW (e.g. Ensō / HENkaku) and [VitaShell](https://github.com/TheOfficialFloW/VitaShell) to install the VPK.

## Requirements

- [VitaSDK](https://vitasdk.org/)

### Install VitaSDK

**Linux / macOS (x86-64):** follow the [VitaSDK quickstart](https://vitasdk.org/) to download the pre-built toolchain.

**Raspberry Pi / ARM Linux:** pre-built binaries are x86-64 only — build from source using the included helper script:

```bash
bash install-vitasdk-rpi.sh   # from the repo root (~1-2 hours)
```

After installing, add to your shell environment:

```bash
export VITASDK=/usr/local/vitasdk    # adjust if you installed elsewhere
export PATH="$VITASDK/bin:$PATH"
```

## Build

```bash
cd vita
bash build.sh        # produces build/vitasync.vpk
```

Or manually with CMake:

```bash
mkdir -p vita/build && cd vita/build
cmake ..
make
```

### Output

`build/vitasync.vpk` — transfer to the Vita via FTP or QCMA and install with VitaShell.

## Configuration

On first launch a config file is created at:

```
ux0:data/vitasync/config.txt
```

Edit it with your server details:

```ini
server_url=http://192.168.1.100:8000
api_key=your-secret-key
scan_vita=1
scan_psp_emu=1
pspemu_root=ux0:pspemu
```

| Key | Description |
|---|---|
| `scan_vita` | Scan native Vita save folders (`ux0:user/00/savedata/`) |
| `scan_psp_emu` | Scan PSP saves running under the Vita's PSP emulator |
| `pspemu_root` | Where Adrenaline keeps its PSP tree (`ISO/`, `PSP/GAME/`). Default `ux0:pspemu`; set to `ur0:pspemu` or `uma0:pspemu` if Adrenaline is redirected there |

## Views

The GUI is drawn with [vita2d](https://github.com/xerpi/libvita2d) using the console's own system font, in the same dark theme as the other GameSync clients. Every view has the same frame: a header (GameSync, the current view, the version and an Online / Offline server chip), a tab strip, a list on the left with the selected item's details on the right, and a footer showing the buttons for that view.

### Controls

Every GameSync console client uses the same scheme. Cross / Circle are read as physical buttons, so a Japanese-region Vita set to "Circle = enter" still confirms with Cross here.

| Button | Everywhere |
|---|---|
| Up / Down | Move one row (hold to repeat) |
| Left / Right | Page up / page down (hold to repeat) |
| L / R | Previous / next tab: **Saves → ROM Catalog → Downloads → Settings**, wrapping |
| SELECT | Switch the sub-tab (PSP / PS1 in the ROM Catalog) |
| Cross | Confirm / the primary action of the selected row |
| Circle | Cancel: closes cards, pauses a running download |
| Square | Secondary action of the view |
| Triangle | Details of the selected row |
| START | Exit (asks first) |

Touch input is not used.

### Saves

Each row has a platform tag (VITA / PSP / PS1) and a sync marker: **Synced** (local save matches the last sync), **Changed**, **Synced before** (local hash not checked yet), **Local** (never synced) or **On server** (server-only, not on this Vita yet). The detail panel shows the game ID, platform, size, file count and save folder.

Cross compares the save with the server and opens a compare card with this Vita's copy and the server's copy (size, file count, server save date) side by side, plus the action the three-way hash recommends. On that card Cross runs the recommendation, Square forces an upload (keep this Vita's save) and Triangle forces a download (keep the server's) — on a **conflict** those two are how you pick a side — and Circle cancels. Sync all asks first, then ends with a summary card (uploaded / downloaded / up to date / conflicts / failed).

| Button | Action |
|---|---|
| Cross | Sync selected save (compare card: Cross recommended, Square upload, Triangle download, Circle cancel) |
| Square | Sync all saves |
| Triangle | Details: game ID, platform, local size / folder, the server's copy and whether it was synced before |

### ROM Catalog

Lists the server's PSP and PS1 games and installs them where Adrenaline looks:

| System | Server does | Lands at |
|---|---|---|
| PSP ISO / CSO / PBP | nothing (raw download) | `<pspemu_root>/ISO/<name>` |
| PSP CHD | converts to CSO (`?extract=cso`) | `<pspemu_root>/ISO/<name>.cso` |
| PS1 CHD / CUE / BIN / ISO / IMG | converts to `EBOOT.PBP` via pop-fe (`?extract=eboot`) | `<pspemu_root>/PSP/GAME/<serial>/EBOOT.PBP` |

Multi-disc PS1 games show as one row (`… (3 discs)`); the server packs every disc into a single EBOOT and POPS swaps discs in-game. PS1 games stored on the server as a folder of loose files show as `N/A` — the server has no EBOOT route for those. A PS1 download needs `SYNC_ROM_PS1_EBOOT_COMMAND` (pop-fe) configured on the server; the client reports the 503 if it isn't.

Rows carry a status tag once a game is queued (Queued, Downloading, Paused, Failed, Installed). The detail panel shows the file, what it installs as (raw, CSO or EBOOT.PBP), the exact target path and the queue state.

Downloads stream to `<target>.part` and resume with an HTTP Range request, so a large game can be paused (Circle) and picked up later. The Vita is kept awake while a transfer runs.

| Button | Action |
|---|---|
| Cross | Install: queue and start, or resume a paused / failed download |
| Triangle | Details: file, size, serial, install format, target path, status |
| SELECT | Switch system (PSP ↔ PS1) |

The catalog has no search or RetroAchievements filter on the Vita, so Square is unused here.

#### Catalog cache

The catalog is kept on the memory card in `ux0:data/vitasync/catalog_cache.bin`, together with the per-system fingerprint the server publishes at `GET /api/v1/roms/fingerprints` (the same scheme as the MiSTer client). The first visit to the ROM Catalog in a session fetches the fingerprints and refetches only the systems whose fingerprint changed or that aren't cached yet; the rest come from the file, and switching PSP ↔ PS1 just reads the file. When the server can't be reached the cached copy is shown with a "Cached copy - server offline" notice. A server too old to have the fingerprints route (404) is handled the old way: each system is fetched live and nothing is cached.

The file holds only the fields the client uses (rom id, file name, name, system, serial, size, bundle flag / file count, server conversion hint, disc index / total) as length-prefixed binary records, one section per system, and is rewritten through a temp file. A format version in its header is bumped whenever a row field changes, so an old file is simply refetched.

### Downloads

Starting a download switches here. While it runs, the detail panel shows a large progress bar with bytes done / total, percentage, speed and time remaining (redrawn about four times a second, without waiting for the display, so the transfer is never slowed by the UI). Each queue row has its own status tag and progress bar.

| Button | Action |
|---|---|
| Cross | Start / resume selected |
| Circle | While downloading: pause (resume later with Cross). Otherwise: cancel the selected download (asks first when a partial file would be deleted) |
| Square | Clear finished entries |

### Settings

Shows the configuration read from `config.txt` (server, API key, console ID, connection, scan options, Adrenaline root, config path), what the catalog cache holds, and the version. The values are read-only — edit `config.txt` (e.g. over VitaShell FTP) and restart to change them.

| Button | Action |
|---|---|
| Cross on **Refresh catalog** | Asks the server to rescan its ROM folder (`GET /api/v1/roms/scan`; a refusal or failure is reported and the refresh carries on), wipes the catalog cache, refetches every system and reports the result. If the server can't be reached the cached copy is kept. |

## Tests

The catalog cache is plain C and has host tests:

```bash
sh vita/tests/run_host_tests.sh          # host gcc; SAN=none for MinGW
```
