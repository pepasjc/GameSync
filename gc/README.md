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
a tab strip of the seven views, a list on the left with a detail panel for the
selected item on the right, a status banner and a footer with the buttons that
apply. The controls are the shared GameSync scheme, the same on every view:

| Button | Does |
|---|---|
| D-pad **Up / Down** | Move one row (hold to repeat) |
| D-pad **Left / Right** | Page up / page down in the list (hold to repeat) |
| **L / R** triggers | Previous / next view: Catalog, Installed, Queue, VMC, Cards, Server, Settings (wraps around) |
| **Z** | Sub-tab: next card image (VMC), slot A / slot B (Cards) |
| **A** | Act on the focused row; rows with several actions open a menu |
| **B** | Cancel: closes menus and dialogs, stops (pauses) a running download |
| **X** | The view's secondary action (see below) |
| **Y** | Details of the focused row |
| **START** | Exit GameSync (asks first) |

| View | A | X | Z |
|---|---|---|---|
| **Catalog**: the server's GameCube games | Menu: Download now / Add to download queue | | |
| **Installed**: ISOs on the SD card | Delete (asks first) | Rescan | |
| **Queue**: downloads (resumable) | Menu: Start or resume / Remove from queue | Run the whole queue | |
| **VMC**: saves inside card images on SD | Menu: Upload save / Restore save (asks) / Import whole image (asks) | Rescan images | Next card image |
| **Cards**: physical memory cards in slot A / B | Menu: Upload save / Restore from server (asks) / Send GameID (when on for the slot) | Rescan the slot | Slot A / slot B |
| **Server**: GameCube saves on the server | Menu: Restore to slot A / Restore to slot B (both ask) / Send GameID (when on) | Refresh | |
| **Settings** | Edit a text field, flip a toggle (SD device cycles and remounts), or run an action | | |

In menus, **Up / Down** pick, **A** runs, **B** cancels. Confirmations are
**A** yes, **B** no. Text fields in Settings open an on-screen editor:
**Up / Down** pick the letter under the cursor (the strip shows what comes
next), **Left / Right** move, **Z** inserts a space, **X** deletes, **A**
accepts, **B** cancels.

While a download runs, a progress card shows the bar, size done / total,
percentage, speed, elapsed and remaining time; while the server is still
converting an RVZ to ISO it shows how long it has been waiting. **B** stops
it (paused: the download resumes from the same offset with A later).

### Catalog cache

The catalog is kept on the SD card in `sd:/3dssync/cache/catalog_GC.tsv`
(a small tab-separated file: a version line, `GC <fingerprint> <count>`, one
line per game with only the fields the client uses, and an `END` line). On
start the client asks the server for its per-system fingerprints
(`/api/v1/roms/fingerprints`) and downloads the GameCube list again only when
the fingerprint changed; otherwise the list is read from the SD card. With the
server unreachable the cached list is shown, marked *offline (cached)*. A
server too old to publish fingerprints gets the full fetch every time, with
nothing cached.

**Settings > Refresh catalog** asks the server to rescan its ROM folder
(`/api/v1/roms/scan`; if that is refused, the refresh carries on), deletes
the cache and downloads the list again.

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
