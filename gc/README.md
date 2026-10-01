# GameSync — GameCube client

Homebrew GameCube app (`gcsync.dol`) that syncs memory card saves with a
GameSync server and installs games from the server's catalog to an SD card.
Needs a Broadband Adapter (DOL-015) for the network and an SD card in an
SD2SP2 (Serial Port 2) or SD Gecko (memory card slot A/B) for settings,
installs and card images.

## Build

devkitPPC + libogc + libfat + libbba, from devkitPro's MSYS2 login shell (not
Git Bash, where recursive make breaks):

```bash
/c/devkitpro/msys2/usr/bin/bash.exe --login -c 'make -C /e/projects/3dssync/gc'
```

or `build_all.bat gc` from the repo root. Output: `gc/gcsync.dol`. Load it
with Swiss, an SD2SP2 / GC Loader / FlippyDrive boot setup, or any DOL loader.

## Setup

Settings live in `sd:/3dssync/config.txt` (written with defaults on first boot)
and can be edited on the console in the **Settings** view: server URL (a
dotted LAN IP, e.g. `http://192.168.1.100:8000`), API key, DHCP or static IP,
SD device, games folder, and GameID per memory card slot.

## Screens and controls

The screen is a header (current view, version, network address and SD status),
a tab strip of the eight views, a list on the left with a detail panel for the
selected item on the right, a status banner and a footer with the buttons that
apply. **L / R** switch views; **L + R + START** quits. On every list, the
D-pad **Up / Down** moves and **Left / Right** pages.

| View | A | X | Y | Z | START |
|---|---|---|---|---|---|
| **Catalog** — server's GameCube games | Fetch catalog | Queue download | Download now | | |
| **Installed** — ISOs on the SD card | Rescan | Delete (asks first) | | | |
| **Queue** — downloads (resumable) | Start / resume selected | Remove | Run whole queue | | |
| **VMC** — saves inside card images on SD | Upload save | Rescan images | Restore save into image | Next card image | Import whole image |
| **Slot A / Slot B** — physical memory cards | Upload save | Rescan card | Restore from server | Send GameID | |
| **Server** — GameCube saves on the server | Restore to slot A | Refresh | Restore to slot B | Send GameID | |
| **Settings** | Edit / toggle / run | | | | |

In **Settings**, Left / Right also change toggles (network mode, SD device,
GameID). Text fields open an on-screen editor: **Up / Down** pick the letter
under the cursor (the strip shows what comes next), **Left / Right** move,
**Z** inserts a space, **X** deletes, **A** (or START) accepts, **B** cancels.

While a download runs, a progress card shows the bar, size done / total,
percentage, speed, elapsed and remaining time; while the server is still
converting an RVZ to ISO it shows how long it has been waiting. **B** pauses
(the download resumes from the same offset with A later).

Uploads, restores, deletes and whole-image imports ask for confirmation
(**A** yes, **B** no).

Notes:

- Card scans run before the network comes up — the BBA shares EXI channel 0
  with memory card slot A.
- GameID (MemCard Pro GC, GCMCE / FlipperMCE) needs the slot's GameID setting
  on; the app waits for the device to swap cards and rescans the slot.
- Card images are found in `sd:/swiss/saves/` (Swiss), `sd:/MemoryCards/GC/*/`
  (GCMCE / FlipperMCE channel cards) and `sd:/VMC/`.

## UI implementation

The interface is drawn with libogc's GX directly (`source/gui.c`): rounded
panels, pills, GameCube button glyphs and progress bars are vertex-coloured
triangles, and text uses the console's own IPL ROM font, read at runtime with
`SYS_InitFont` — no font is bundled. If the ROM font can't be read, libogc's
built-in 8x16 console font is used instead. Frames are synchronous (one frame
in flight at most) and drawn only on input, so the Flipper FIFO can't be
overrun; progress redraws during a transfer are limited to two per second so
the single-segment TCP window keeps streaming.

Layout is a 640x480 logical space with text kept inside a CRT title-safe area.
