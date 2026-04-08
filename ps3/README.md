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

## Controls

| Button | Action |
|---|---|
| Cross | Upload selected save |
| Circle | Download selected save |
| Up / Down | Navigate save list |
| START | Exit |
