# GameSync — PS Vita Client

Homebrew client for PlayStation Vita. Syncs Vita and PSP-emu save files with the GameSync server over WiFi.

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
```

| Key | Description |
|---|---|
| `scan_vita` | Scan native Vita save folders (`ux0:user/00/savedata/`) |
| `scan_psp_emu` | Scan PSP saves running under the Vita's PSP emulator |

## Controls

| Button | Action |
|---|---|
| Cross | Upload selected save |
| Circle | Download selected save |
| Up / Down | Navigate save list |
| START | Exit |
