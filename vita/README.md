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

START cycles **Saves → ROM Catalog → Downloads**. The PS button exits (close from LiveArea).

### Saves

| Button | Action |
|---|---|
| Cross | Smart sync selected save (three-way hash) |
| Square | Upload selected save |
| Triangle | Download selected save |
| SELECT | Auto sync all saves |
| Up / Down, Left / Right | Navigate / page |

### ROM Catalog

Lists the server's PSP and PS1 games and installs them where Adrenaline looks:

| System | Server does | Lands at |
|---|---|---|
| PSP ISO / CSO / PBP | nothing (raw download) | `<pspemu_root>/ISO/<name>` |
| PSP CHD | converts to CSO (`?extract=cso`) | `<pspemu_root>/ISO/<name>.cso` |
| PS1 CHD / CUE / BIN / ISO / IMG | converts to `EBOOT.PBP` via pop-fe (`?extract=eboot`) | `<pspemu_root>/PSP/GAME/<serial>/EBOOT.PBP` |

Multi-disc PS1 games show as one row (`… (3 discs)`); the server packs every disc into a single EBOOT and POPS swaps discs in-game. PS1 games stored on the server as a folder of loose files show as `N/A` — the server has no EBOOT route for those. A PS1 download needs `SYNC_ROM_PS1_EBOOT_COMMAND` (pop-fe) configured on the server; the client reports the 503 if it isn't.

Downloads stream to `<target>.part` and resume with an HTTP Range request, so a large game can be paused (Square) and picked up later. The Vita is kept awake while a transfer runs.

| Button | Action |
|---|---|
| Cross | Queue and start download |
| Triangle | Resume a paused / failed download |
| Circle | Rescan the server's ROM folder and refetch |
| L / R | Switch system (PSP ↔ PS1) |

### Downloads

| Button | Action |
|---|---|
| Cross | Start / resume selected |
| Square | Pause active download |
| Circle | Cancel selected (pause first if active) |
| Triangle | Clear completed entries |
