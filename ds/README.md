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

## Controls

| Button | Action |
|---|---|
| A | Upload selected save to server |
| B | Download selected save from server |
| Up / Down | Navigate save list |
| L | Toggle config editor panel |
| START | Exit |
