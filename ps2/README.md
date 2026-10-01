# ps2sync — PS2 Save Sync client

PlayStation 2 homebrew client for the Save Sync server.  Phase 1 ships a ROM
catalog browser and installer for USB mass storage or classic PS2 fat internal
HDDs using APA/HDLoader partitions.  Phase 2 (planned) adds Memcard PRO 2 save
sync via the GameID protocol.

## Build environment (WSL)

Once-only install of the PS2 toolchain (~30-90 min on first build):

```bash
wsl bash -c "cd /mnt/e/projects/3dssync && bash install-ps2sdk-wsl.sh"
source ~/.bashrc
```

Builds `$PS2DEV` at `/usr/local/ps2dev`, sets `PS2SDK=$PS2DEV/ps2sdk`,
adds `ee-gcc` etc. to `PATH`.

## Build

```bash
wsl bash -c "export PS2DEV=/usr/local/ps2dev && \
  export PS2SDK=\$PS2DEV/ps2sdk && \
  export PATH=\$PS2DEV/bin:\$PS2DEV/ee/bin:\$PS2DEV/iop/bin:\$PS2SDK/bin:\$PATH && \
  cd /mnt/e/projects/3dssync/ps2 && make"
```

Output: `ps2sync.elf`.

The UI is drawn with [gsKit](https://github.com/ps2dev/gsKit), which the ps2dev
installer puts at `$PS2DEV/gsKit` next to the SDK (override with `GSKIT=...`).
The UI font is a glyph atlas baked from the Vegur typeface
(`xbox/assets/font.ttf`, public domain) into `source/font_data.c`; regenerate it
with `python ps2/tools/gen_font.py` (needs Pillow) after changing sizes.

## Install on PS2

1. Format a USB drive as FAT32 (MBR), or use Config → `TRIANGLE` twice on the
   PS2 to format a fat internal HDD as APA for OPL HDD mode.
2. Copy `ps2sync.elf` to the root, or to `mass:/3dssync/ps2sync.elf`.
3. Create `mc0:/3DSSYNC/CONFIG.TXT`, or let the client create one with
   default settings on first launch if the memory card is writable:

   ```
   server_url=http://192.168.1.201:8000
   api_key=anything
   use_static_ip=true
   static_ip=192.168.1.95
   static_netmask=255.255.255.0
   static_gateway=192.168.1.1
   storage=auto
   ```

4. Boot the ELF via uLaunchELF / FMCB / OPL ELF launcher.  Storage is only
   required for queue persistence and ROM downloads; catalog browsing can work
   without it once networking is up.

`storage` controls where ROM installs go:

- `auto` tries APA/HDLoader on `hdd0:` first, then falls back to USB or BDM
  mass storage if no APA HDD is ready.
- `hdd` uses the PS2 fat internal HDD in classic APA/HDLoader mode.
- `usb` uses folder-based OPL ISO installs on `mass:/DVD` and `mass:/CD`.

The Config view also has an internal HDD formatter: press `TRIANGLE` once for
the warning, then `TRIANGLE` again to format. This is destructive. It formats
the PS2 internal HDD as APA/PFS, creates the standard PS2 system partitions,
and creates a `+OPL` common partition if one is missing. This is the classic
PS2 HDD format used by OPL's HDD mode. HDLoader downloads are written as
`PP.<SERIAL>..<TITLE>` APA partitions with OPL-compatible game metadata.

## Screens

The client uses a dark GameSync theme shared with the 3DS and DS clients: a
header with the screen name, version and a network dot (green = IP up), a tab
strip of every screen, a list card with a detail card for the selected item,
a status banner (teal = info, blue = working, red = error) and a footer with
the buttons that apply to the current screen.

Boot shows a splash with a live log of IRX loading, memory card and network
bring-up, so a failing module is still visible on screen.

| Tab | Screen | Shows |
|---|---|---|
| Catalog | Game Catalog | PS2 games on the server; a check marks games already installed, a dot games in the queue |
| Installed | Installed Games | ISOs in `DVD/` / `CD/` or HDLoader partitions |
| Downloads | Downloads | The queue with per-entry progress and status |
| VMC | Virtual Memory Cards | Card images in `VMC/` |
| Slot 1 / Slot 2 | Memory Card 1 / 2 | Game saves on the physical (or MemCard Pro / SD2PSX) card |
| Server | Server Saves | PS1/PS2 saves on the server; a check marks saves present on a card |
| Settings | Settings | Server, network, storage, GameID device and the HDD formatter |

Downloads show a progress card (bar, size, speed, elapsed and remaining time)
over whatever screen started them. Confirmations appear as a card over the
dimmed screen.

## Controls

| Button | Action |
|---|---|
| L2 / R2 | Previous / next screen |
| D-Pad Up/Down | Move in the list |
| D-Pad Left/Right | Page up/down · Settings: install target (`auto`, `usb`, `hdd`) |
| CIRCLE | Exit · cancel a confirmation · hold during a download to pause it |
| CROSS | Catalog: refresh · Installed: rescan · Downloads: start/resume · VMC: upload card · Slot: upload save · Server: download to the source · confirm |
| SQUARE | Catalog: queue · Installed: delete · Downloads: remove · VMC / Slot: rescan · Server: refresh |
| TRIANGLE | Catalog: download now · VMC: pull all server saves · Slot: restore from server · Server: upload from the source · Settings: format APA HDD (press twice) |
| L1 | Server: upload every save on the source (VMC source: pull all) |
| R1 | Slot / Server: switch a MemCard Pro / SD2PSX to the selected game (GameID) |
| START | Server: cycle the sync source (VMC → Slot 1 → Slot 2) |
| SELECT | Slot / Server: cycle the GameID device (`off`, `auto`, `gen1`, `gen2`) |

## On-disk layout

```
mc0:/
└── 3DSSYNC/
    ├── CONFIG.TXT
    ├── CONSOLEID.TXT
    └── HDL_DOWNLOADS.DAT   (queue used by APA/HDLoader mode)

mass:/  (USB or internal HDD BDM FAT/exFAT)
├── 3dssync/
│   ├── downloads.dat
│   └── downloads/
├── DVD/
│   └── SLUS_213.71.God of War.iso
└── CD/
    └── SLUS_201.13.Some CD Game.iso

hdd0:  (APA/HDLoader mode)
└── PP.SLUS-21371..GOD_OF_WAR   (APA game partition)
```

OPL picks games up automatically in either mode.  USB uses `DVD/` and `CD/`
folders; HDD mode uses the HDLoader partition list.  HDLoader installs are
rewritten from the beginning if interrupted so the APA table is not left with a
half-valid game entry.

## Networking

- The PS2 ethernet adapter (or built-in NIC on slim) must be present.
- Static IP is enabled by default.  Set `use_static_ip=false` or `dhcp=true`
  in `CONFIG.TXT` to use DHCP instead.
- Server URL must be a dotted-IP address (lwIP gethostbyname is not
  shipped in our build).

## Troubleshooting

- `Storage not ready`: check `storage=` in `CONFIG.TXT`. For USB, use a FAT32
  drive with an MBR partition table and plug it in before booting the ELF. For
  internal HDD, use Config → `TRIANGLE` twice to format APA/PFS, then relaunch
  with `storage=hdd` or `storage=auto`. USB mode probes `mass:`, `mass0:`,
  `mass1:`, `mass2:`, and `mass3:` and creates the OPL folders on the detected
  root.
- `Config not found`: edit the generated `mc0:/3DSSYNC/CONFIG.TXT` and
  replace the sample IP/API key with your server values.
- `Network not ready (ip=no-link/no-dhcp/bad-static)`: the catalog fetch is
  blocked before HTTP; check the ethernet adapter, cable, and `CONFIG.TXT`
  static IP values.
- `Catalog fetch failed (HTTP 401, ...)`: the PS2 reached the server but
  `api_key` does not match `SYNC_API_KEY`.

## Status

- **Phase 1: ROM installer** — catalog browse, queue, resumable HTTP/1.0
  downloads to USB `DVD/`/`CD/` folders, plus classic APA/HDLoader partition
  installs on PS2 fat internal HDDs.
- **Phase 2: MCP2 save sync** — not yet implemented.  Will use the
  GameID broadcast protocol to switch the MCP2 channel, then read the
  per-channel virtual memcard via libmc and POST it as a `.bin` save
  bundle to the server.
- **Phase 3: APA/HDLoader HDD installs** — implemented for OPL-compatible
  partition naming, APA partition creation, local HDL scan/delete, and direct
  HTTP streaming into HDD sectors.

## File map

```
include/
  common.h        shared paths + SyncState
  config.h        config loader
  network.h       ps2ip + NetMan + static IP / DHCP
  http.h          BSD-socket HTTP/1.0 client
  roms.h          catalog model + path resolution
  downloads.h     pause/resume manager
  hdl.h           APA/HDLoader installer
  ui.h            GameSync screens (boot splash, views, dialogs)
  gui.h           gsKit drawing kit: shapes, text, pills, button glyphs
  font_data.h     generated glyph metrics (see tools/gen_font.py)
  sha256.h        hash helper
source/
  main.c          IRX bootstrap + menu loop
  config.c
  network.c
  http.c
  roms.c
  downloads.c
  hdl.c
  ui.c
  gui.c
  font_data.c     generated Vegur glyph atlas
  sha256.c
tools/
  gen_irx_mods.sh embeds IRX blobs into the ELF at build time
  gen_font.py     bakes the UI font atlas (Pillow)
```
