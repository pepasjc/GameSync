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

## Controls

| Button | Action |
|---|---|
| A (Cross) | Upload selected save |
| B (Circle) | Download selected save |
| Up / Down | Navigate save list |
| START | Exit |
