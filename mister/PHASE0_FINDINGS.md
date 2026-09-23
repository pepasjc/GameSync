# MiSTer on-device client — Phase 0 findings

Measured on real hardware (MiSTer DE10-Nano, `root@192.168.1.41`) with
`mister/tools/spike_input.py`. Raw output kept in `mister/tools/spike_report.txt`.
Everything below replaces assumption with observation; the client design in
`docs/mister-client.md` follows from it.

## Platform

| Item | Value |
|---|---|
| OS / arch | Buildroot, Linux 5.15.1-MiSTer, `armv7l`, dual-core Altera SOCFPGA |
| Python | 3.9.6, starts in 0.18 s |
| RAM | 492 MB total, ~447 MB available |
| SD | exfat, `rw,noatime,sync`, 59 GB (51 GB free) |
| USB | `/dev/sda2` exfat at `/media/usb0`, `rw,nodev,**noexec**,sync` |
| Framebuffer | `/dev/fb0`, 1920x1080x32 |

### Python stdlib

Present: `curses`, `curses.panel`, `ssl`, `zlib`, `lzma`, `bz2`, `struct`,
`select`, `fcntl`, `termios`, `ctypes`, `zipfile`, `tarfile`, `json`,
`hashlib`, `urllib.request`, `http.client`, `socket`, `threading`,
`concurrent.futures`, `dataclasses`, `subprocess`, `signal`, `logging`,
`argparse`, `queue`, `re`.

**`sqlite3` is MISSING.** The download queue must persist to JSON, not a
database. Everything else the client needs is available, so the client stays
dependency-free.

Binaries available: `curl`, `wget`, `unzip`, `sha256sum`, `tput`, `infocmp`,
`stty`, `reset`, `fbset`. Not available: `7z`, `pgrep` (busybox userland — do
not use `pgrep` in scripts).

`shared/` is safe to vendor as-is: all 12 modules parse under 3.9 and 11 of
them already carry `from __future__ import annotations`, so no PEP 604 union
evaluates at runtime.

## How MiSTer runs a script

Launched from OSD → Scripts, a script runs as:

```
/bin/bash /tmp/script -f root      # parent
  └── <our process>                # stdin/stdout/stderr all on /dev/tty2
```

- The active VT (`/sys/class/tty/tty0/active`) is switched to **`tty2`**, and
  stays there while the script runs.
- **`TERM` is unset.** `curses.setupterm()` fails with "could not find terminfo
  database" until it is set. `/usr/share/terminfo/l/linux` exists, so the
  launcher (or the client itself) must default `TERM=linux`.
- The shebang is honoured — a file named `*.sh` with `#!/usr/bin/env python3`
  runs under Python (stock `favorites.sh` does exactly this).
- Only `*.sh` files show up in the Scripts menu, so the launcher must be
  `GameSync.sh` with the payload beside it in a dot-directory.

With `TERM=linux` set, curses reports a **240x67** terminal, `has_colors`,
8 colors / 64 pairs, `use_default_colors()` OK, `can_change_color`. Plenty of
room; still design for an 80x24 floor.

### Driving the console remotely

Because the script console is a real VT, the whole app can be run over SSH
against the live screen — no menu navigation per iteration:

```sh
python3 /media/fat/Scripts/GameSync.sh < /dev/tty2 > /dev/tty2 2>&1
```

This is the Phase 2+ development loop.

## Input — the important part

### Both paths work

| Path | Verdict |
|---|---|
| `curses.getch()` on stdin | **works** |
| Raw evdev `/dev/input/event*` | **works** |
| joydev `/dev/input/js*` | **works** |

MiSTer creates a uinput keyboard called **"MiSTer virtual input"** and
translates pad presses onto it, and that lands on the console tty. Observed
translation (from the raw capture of that node, which is authoritative — the
per-prompt attribution in section 6 of the report is skewed because the tester
pressed at their own pace):

| Pad | Key code | Key |
|---|---|---|
| A / Cross | 28 | ENTER |
| B / Circle | 1 | ESC |
| X / Square | 57 | SPACE |
| Y / Triangle | 15 | TAB |
| L1 | 104 | PAGE UP |
| D-pad U / D / L / R | 103 / 108 / 105 / 106 | arrows |
| L2, R2, SELECT, START | — | **not translated** |

So getch alone cannot reach four of the controls we want. Raw input is
required for a full mapping — but getch must still be supported, both for the
IR keyboard and because it costs nothing.

### Grab status depends on what MiSTer is doing

This is the finding that overturned the first conclusion:

- While MiSTer was **running a core**, `EVIOCGRAB` on the DualSense and the IR
  keyboard was refused with `EBUSY` — MiSTer held them exclusively, so a
  grabbed evdev node would have been silent for us.
- While a **script is running**, nothing is grabbed: every node reported
  "grab+ungrab OK", and evdev delivered all 14 buttons, both hats and every
  axis.

Since the client only ever runs as a script, evdev is available. The fallback
chain still matters for robustness (someone may launch it another way):

**evdev (by name) → joydev → getch.** joydev is a separate handler and is not
affected by an evdev grab; during the grabbed run, `js1` still opened and
delivered its 22-event init burst.

### Node numbers are not stable

Across three runs on the same box the DualSense moved from `event4`/`js1` to
`event3`/`js0` simply because it reconnected, and an extra `event1`/`js0` pair
appeared while a script ran. **Always enumerate by `EVIOCGNAME` and match on
capability (`BTN_SOUTH` present), never by a fixed index.**

### Sensor nodes must be excluded by name

"DualSense Wireless Controller Motion Sensors" produced **10,911 events in 3
seconds** (`event5`), and its joydev twin `js2` another 8,754. The touchpad
node is similarly chatty. Skip any node whose name matches
`motion sensor` / `touchpad` / `accelerometer` / `gyro`.

This is not theoretical: the first spike build accumulated ~200 k events and
then went quadratic in its dedupe pass, spinning at 100 % CPU and never writing
its report.

### Analogue jitter

The DualSense right stick idles between 128 and 129 (evdev `ABS_RX`, 8-bit
range, centre 128), emitting up to 578 events in a single 3.5 s prompt with
nobody touching it. joydev scales the same jitter to 258..516 of ±32767.
A centre deadzone is mandatory, not a nicety.

### Observed codes (DualSense, evdev)

- Buttons: `BTN_SOUTH` A, `BTN_EAST` B, `BTN_WEST` X, `BTN_NORTH` Y,
  `BTN_TL` L1, `BTN_TR` R1, `BTN_TL2` L2, `BTN_TR2` R2, `BTN_SELECT`,
  `BTN_START`, `BTN_MODE`, `BTN_THUMBL`, `BTN_THUMBR`, `BTN_Z`.
- D-pad: `ABS_HAT0X` / `ABS_HAT0Y`, values -1 / 0 / 1.
- Sticks: `ABS_X`, `ABS_Y`, `ABS_RX`, `ABS_RY` — 0..255, centre 128.
- Triggers: `ABS_Z` (L2), `ABS_RZ` (R2) — 0..255, analogue.

joydev equivalents: buttons 0..13 in the same order, axes 0/1 left stick,
2 L2, 3/4 right stick, 5 R2, 6/7 d-pad, all scaled to ±32767.

## Confirmed rules from the live filesystem

- Save folders present: `saves/{GBA,MegaCD,MegaDrive,PSX,SNES,Saturn}`.
- Sizes match the desktop client's expectations exactly: PSX `.sav` = 131072
  (raw 128 KB memory card), Saturn = 65536 (byte-expanded 64 KB), MegaCD =
  8192 (raw BRAM), cartridge cores = raw SRAM.
- `saves/PSX/PSX.sav` exists — MiSTer's unnamed default card. It carries no
  game name in the filename, so it exercises the in-card-serial re-key path
  and is a good regression fixture.
- `/media/usb0/games` holds `{GBA,MegaCD,MegaDrive,PSX,SNES,Saturn}` and
  shadows `/media/fat/games`, which holds different content — the USB-hijack
  rule is live on this box.
- `usb0/games/PSX` contains `boot.rom`, `boot1.rom`, `boot2.rom`;
  `usb0/games/Saturn` contains `boot.rom`; `usb0/games/SNES` contains
  `boot1.rom`. Confirms the `cp -n` BIOS-seeding rule is load-bearing.
- CD games already sit in per-game folders (`usb0/games/PSX/Breath of Fire IV
  (USA)/`), matching the installer's one-folder-per-game rule.
- No `/media/fat/3dssync.cfg` and no state file yet.

## Display — a real GUI, not a TUI

Measured with `mister/tools/spike_fb.py` and `mister/tools/spike_text.py`.

### PyQt6 and SDL2 are both out

No X11 or Wayland server exists on the image, there is no pip, and Qt for
Buildroot armv7 would be a bigger project than the client. `libSDL2-2.0.so.0`
(2.0.14) *is* installed and loads fine from ctypes, but it was built with only
the **`dummy`** video driver, and even that fails to initialise. `SDL2_ttf`,
`SDL2_image` and `SDL2_gfx` are absent.

Retro Remake's Console Mode gets its GUI by statically linking **SDL 1.2.15**
(which still has an fbcon driver) plus SDL_ttf, SDL_gfx, FreeType and libpng,
with Akrobat and PromptFont baked into the binary — see its `00-NOTICES.txt`.
That is a C build; the technique, not the library, is what transfers.

### /dev/fb0 directly

The framebuffer is writable, mmap-able, and fast. `KDSETMODE`/`KD_GRAPHICS` on
`/dev/tty2` stops the console drawing over us, and restoring `KD_TEXT` plus the
saved pixels puts everything back.

**The mode changes at runtime** — 1920x1080x32 was reported at one point and
1280x720x32 later on the same box, with `line_length` 5120. Read
`FBIOGET_VSCREENINFO` / `FBIOGET_FSCREENINFO` at startup and lay out from that;
never hardcode a resolution. Channel layout observed: R offset 16, G 8, B 0,
no alpha — i.e. BGRA bytes, `0x00RRGGBB` little-endian.

| Operation (1280x720x32) | Time | Rate |
|---|---|---|
| Full-screen clear, one memcpy | 5.7 ms | 175 fps |
| Full-screen clear, 720 row copies | 4.6 ms | 215 fps |
| 23 list rows (460 row copies) | 3.4 ms | 297 fps |
| Compose offscreen + one memcpy | 16.3 ms | 61 fps |
| **1920 glyphs, per-glyph blit** | **215 ms** | 4.6 fps |
| **Alpha blend 8000 px, Python loop** | **59 ms** | — |

Rectangle fills are effectively free. Per-glyph blitting and per-pixel
blending are not, and a naive renderer would be unusable.

### The technique that makes text cheap

Keep antialiased glyph coverage as an 8-bit mask. Colour it with three
`bytes.translate` lookups, one per channel, then interleave into BGRA with
**strided bytearray slice assignment** (`out[0::4] = blue`). Both operations
run in C, so no Python-level pixel loop exists anywhere.

| Operation | Time |
|---|---|
| Colour a 600x26 text run (40 chars) | **1.05 ms** |
| Same, naive per-pixel loop | 195.6 ms (**186x slower**) |
| Build the LUT pair | 2.45 ms (once per colour pair) |
| Full screen, 21 rows, cached strips | 8.1 ms (124 fps) |
| Full screen, cold cache | 32.6 ms (31 fps) |
| **Move the selection (2 dirty rows)** | **0.72 ms (1383 fps)** |

A menu redraws on input, not continuously, so the numbers that matter are the
cold-cache cost when entering a screen (~33 ms, one frame) and the scroll cost
(sub-millisecond). Both are comfortable.

### The image's own C libraries are callable from Python

`/media/fat/MiSTer` — the main binary — links `libfreetype.so.6`,
`libpng16.so.16`, `libImlib2.so.1`, `libz` and `libbz2`. These are base-image
libraries (`/lib`, dated Apr 2025), not something Console Mode installed: if
they disappeared, MiSTer itself would not start. `ctypes` drives both of the
ones that matter, verified by `mister/tools/spike_ctypes_libs.py`:

| Capability | Result |
|---|---|
| FreeType 2.10.4 init + face load | works |
| Rasterise antialiased glyph (256 grays, 8-bit coverage) | works |
| 96 glyphs at 24 px | 27.9 ms total, **0.29 ms each** |
| Imlib2 create / scale 640→320 | 15.1 ms |
| Imlib2 alpha-blend 640x480 | **4.57 ms** (pure Python: ~2.3 s) |

Consequences:

- **No build-time atlas pipeline is needed.** A full ASCII atlas rasterises in
  ~28 ms at startup, so the client can use any TTF at any size, and re-rasterise
  when the framebuffer mode changes.
- **Alpha blending, scaling and PNG/JPEG decode are available at C speed**, so
  overlays, dialogs, fades and box art are all on the table — the three things
  pure Python could not do.
- This is exactly the capability set Console Mode gets from statically linked
  SDL 1.2 + SDL_ttf + FreeType + libpng. Same libraries, reached a different way.

Both are optional accelerators: feature-detect at startup and fall back to the
pure-Python renderer (prebaked atlas, no alpha) if a future image drops them.

The only fonts on the device are Console Mode's own (`Akrobat-Bold`,
`Akrobat-SemiBold`, `promptfont`). Those are its assets, not ours — the client
ships its own OFL-licensed font.

Design rules that follow:

1. Rasterise the glyph atlas at startup with FreeType via ctypes, from a TTF
   shipped in the zipapp. Fall back to a prebaked 8-bit atlas if the library is
   missing.
2. Cache rendered text strips in an LRU keyed by `(text, size, fg, bg)`.
   Scrolling then only renders rows entering the viewport.
3. Colour glyph coverage with the `translate` + strided-slice path; use Imlib2
   only where it wins (alpha overlays, scaling, image decode). Never
   alpha-blend per pixel in Python.
4. Dirty-rect rendering only. A full-screen repaint is for screen changes.
5. A second render target that writes PNGs instead of the framebuffer keeps the
   UI testable on the dev machine, where there is no `/dev/fb0`.

A font must be added under a GPL-compatible licence (SIL OFL 1.1 fonts such as
Inter or Noto Sans qualify) and recorded in `README.md` Credits and
`THIRD_PARTY_NOTICES.md`, per the attribution rules in `CLAUDE.md`.

## Consequences for the design

1. Download queue persists to JSON (no sqlite3); `MAX_CONCURRENT = 1` given
   492 MB RAM and `sync`-mounted exfat.
2. App installs to `/media/fat/Scripts/GameSync.sh` + `/media/fat/Scripts/
   .gamesync/gamesync.pyz`. Never to USB — it is mounted `noexec`.
3. Input layer: enumerate by name, exclude sensor nodes, deadzone the sticks,
   fall back evdev → joydev → getch, and always accept keyboard keys.
4. Set `TERM=linux` before touching curses.
5. Never call `pgrep` (busybox); never assume a fixed `event`/`js` index.
6. Dev loop is SSH + `< /dev/tty2 > /dev/tty2`, with `mister/tools/deploy.py`
   for the push. From Git Bash that needs `MSYS_NO_PATHCONV=1`, or MSYS
   rewrites `/media/...` into a Windows path.
