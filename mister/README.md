# GameSync for MiSTer

An on-device client for MiSTer FPGA: sync saves and install games from the
GameSync server, driven entirely by a controller.

* **Saves** - scan, three-way hash comparison, upload, download, blank-card
  handling and per-system format conversion.
* **Catalog** - browse the server's ROMs and queue them for install.
* **Installed** - what is already on the SD card and USB.
* **Downloads** - a resumable queue that survives the app being closed.

It is a single self-contained Python zipapp with **no dependencies** — MiSTer
ships Python 3.9.6 but no pip, so everything it needs (including the vendored
`shared/` modules and the font) is inside the file. The UI is drawn straight to
`/dev/fb0`; there is no X server on a MiSTer.

## Install

From a machine that can reach the MiSTer over the network:

```bash
python mister/install.py 192.168.1.41
```

That builds the client, checks the target really is a MiSTer with a usable
Python, uploads it, and then runs the client's own selftest **on the device**, so
a reported success is a verified one:

```
GameSync 0.5.4 selftest
  python                 3.9.6 on linux
  shared rules           ok (GBA_zelda_minish_cap_usa)
  saturn format          ok
  framebuffer            1280x720 @ 32 bpp
  font / freetype        191 glyphs rasterised
  input devices          DualSense Wireless Controller (/dev/input/event3)
all checks passed
```

Then on the MiSTer: **OSD → Scripts → GameSync**.

Seed the configuration at the same time if you like:

```bash
python mister/install.py 192.168.1.41 \
    --server-url http://192.168.1.10:8000 --api-key SECRET --rom-target usb
```

MiSTer's stock credentials (`root` / `1`) are the default. Override with
`--user`, `--password`, or `--key` for an SSH key. `--uninstall` removes the
client and leaves your configuration alone. Requires `paramiko` on the machine
you run it from.

## Configuration

Three keys, in `/media/fat/Scripts/.config/gamesync/gamesync.cfg` — the
directory convention MiSTer scripts use, the same place the stock `downloader`
keeps its data. Installing always leaves a complete commented file there, so
every setting is visible even before you fill it in:

```ini
SERVER_URL=http://192.168.1.10:8000
API_KEY=your-servers-SYNC_API_KEY
ROM_TARGET=sd
```

| Key | Meaning |
|---|---|
| `SERVER_URL` | the GameSync server, e.g. `http://192.168.1.10:8000` |
| `API_KEY` | must match the server's `SYNC_API_KEY` |
| `ROM_TARGET` | `sd` or `usb` — where downloaded ROMs are installed |
| `OVERSCAN_X` / `OVERSCAN_Y` | percent of each edge kept clear, for CRTs |
| `BUTTONS` | `action=code` pairs overriding the button map |

`OVERSCAN_*` and `BUTTONS` are written by the client — Settings → **Adjust
screen** and **Remap buttons** — and are not meant to be typed by hand.

Edit it on the device, or set it from the installer with `--server-url` and
`--api-key`. Values may be quoted; blank lines and `#` comments are ignored.
The Settings tab shows the loaded values and which file they came from.

Nothing else is needed: save folders, games roots and system names all come from
`shared/mister.py`.

Alongside it live `state.json` (the last synced hash per title),
`hash_cache.json`, `server_cache.json`, `downloads.json` and `gamesync.log`. The pre-0.5.4 paths (`/media/fat/3dssync.cfg` and
`/media/fat/3dssync_state.json`) are migrated on install and still read if the
current ones are missing.

The state file is deliberately shared with the desktop client's SSH sync and
with `sync_saves.sh`, so the three can be used interchangeably without any of
them seeing phantom conflicts.

## Controls

| Input | Action |
|---|---|
| D-pad / stick up / down | Move one row (hold to accelerate) |
| D-pad / stick left / right | Page; hold 1.5 s to sweep by initial letter |
| A | Sync the highlighted save |
| B | Exit (asks first) |
| X | Sync everything the plan asks for |
| Y | Rescan |
| L1 / R1 | Previous / next system filter (only systems present in the tab) |
| L2 / R2 | Previous / next tab |
| SELECT | Clear the catalog search (or open it) |
| START | Settings |

On **Catalog**, A installs the highlighted ROM: it joins the queue and the
download starts at once, in the background, one file at a time - keep
browsing and queueing while it runs. The Downloads tab label shows the
progress (`Downloads 43%`) or how many are waiting; a finished game gets a
toast and shows up on Installed. Closing the app mid-download is safe: the
`.part` file is kept and the transfer resumes the next time GameSync starts.
Y opens a search: an on-screen keyboard driven by the pad (a USB keyboard types
straight in), Y again or OK applies it. Every word has to appear in the name,
in any order, and the search stacks with the system filter; the header shows
the active search and SELECT clears it. The system and row you were on are
remembered across runs, so the tab reopens where you left it. A forced refresh
of the catalog - "I changed the server's library" - lives on the **Settings**
tab. On **Downloads**, A retries a failed row (or starts a stopped queue),
X starts the queue and Y clears
finished rows. On **Installed**, A moves a game between the SD card and USB
and X deletes it; both ask first, and a move seeds the BIOS when it creates a
USB core folder. On **Settings**, A changes the highlighted setting - ROM
target cycles `sd` / `usb` and is written to the config immediately.

The system filter offers only systems that actually have rows in the tab you
are looking at, so a MiSTer's long list of cores does not turn into a long list
of empty filters. Your choice is remembered for the whole session: a tab that
has no rows for that system shows *All* while you are on it, and the filter
comes back on the next tab - or the next refresh - that has it.

### "upload" on a save you never touched

When a save the server already holds comes back as *upload*, *download* or
*conflict*, the client fetches the server's copy and compares the two. For
PlayStation cards, Saturn backup RAM and Mega Drive SRAM, differences the game
never reads (the PS1 write-test frame the core rewrites on boot, a Saturn
archive comment, Mega Drive bus filler) are settled as *synced* on the spot.
Anything else is reported **where it differs**, in the row's detail column and
in the sync dialog - `block 3 (BASLUS-01251FF7-S01)` means that game's save
changed; `block 0 write-test frame` alone means the core's bookkeeping did. If
a card keeps coming back as changed after a session without saving, that text
is what to report. (Measured: a PS1 game rewrites frame 63 with its own test
pattern on every boot, which is exactly the region the comparison ignores.)

A PlayStation card is keyed by the product code of a save *inside* it. Cards
are shared between games, so a card copied from another game - or downloaded
from the server into a blank one - can open with some other game's save. When
the card holds a save for the game the file is named after (by disc-serial
name or catalogue match), that save's code is the key; only a card with no
such save falls back to its first save, which is the variant-disc case.
`mister/tools/diff_saves.py`, run on the device, prints the byte-level
comparison for every save when something still looks wrong.

### Real discs and ISOs

The PSX core names a card after the ROM file, but when booting a real CD all
it has is the disc serial, so a game played both ways has two cards -
`Dino Crisis 2 (USA).sav` and `SLUS_012.79.sav`. Both key to the same server
slot and both stay on the Saves tab; the disc card borrows the ISO card's name
(or the server's) and is marked **`[CD]`**. Each card gets its own status, and
either can be synced.

After one card is synced, the other is brought to the same bytes
automatically when that is safe: it is blank, or it has not changed since the
slot was last synced, and it holds no save the new card lacks. Otherwise it is
shown as a *conflict* saying why (`also changed on the ISO card`, `card holds
N save(s) the other does not`) and the conflict dialog lets you choose which
side wins; uploading that side mirrors back the other way.

The table above is the gamepad default, in Xbox lettering. On-screen hints
name whichever button is actually bound, so remapping changes the footer too.

### Controller names

The kernel maps every pad onto the same *positional* codes - the bottom face
button is always `BTN_SOUTH` - so the bindings are the same on every
controller. What is printed on the plastic is not: that button is **A** on an
Xbox pad, **Cross** on a DualShock and **B** on a Switch Pro Controller, whose
**A** is on the right. The client detects the controller family from its USB
vendor id (Sony, Nintendo, Microsoft), falling back to the evdev name for
clones, and labels the footer, dialogs and remap prompts accordingly:

| Family | Face buttons (S / E / N / W) | Shoulders | Menu |
|---|---|---|---|
| Xbox | A / B / X / Y | LB RB / LT RT | View / Menu |
| PlayStation | Cross / Circle / Triangle / Square | L1 R1 / L2 R2 | Share / Options |
| Nintendo | B / A / X / Y | L R / ZL ZR | - / + |
| Generic | A / B / X / Y | L1 R1 / L2 R2 | Select / Start |

With two different pads connected the hints follow whichever one was pressed
last, and the screen repaints when that changes. The Settings tab shows the
detected family next to the device name; if it is wrong for your pad, the
bindings are still right - only the names are off - and *Remap buttons* is
unaffected.

### Confirmations

Anything that overwrites or removes data asks first, in a modal box that names
what is about to happen:

| Operation | Asks | Why |
|---|---|---|
| Sync all | after the plan is fetched | "sync everything" is meaningless until you can see it is 3 uploads and 41 downloads |
| Sync one | naming the direction | a download replaces the save on this MiSTer, an upload replaces the server's |
| Take server's card | when your save would be lost | a PlayStation card is shared between games |
| Delete game | with the full path | not undoable |
| Move game | with the destination | also warns when it will create a core folder and seed its BIOS |
| Run download queue | with the file count and size | only adds files, so this is about transfer size, not loss |
| Exit | always | B also closes every dialog, so a stray extra press used to drop you back to the MiSTer menu and cost a rescan |

Queueing a ROM, clearing finished downloads and changing `ROM_TARGET` do not
ask: nothing is lost and all three are trivially reversible.

The dialog answers **No** to anything that is not an explicit yes, including
`--timeout` expiring, so an unattended run cannot destroy anything by falling
through. Its Yes/No labels come from the live mapping, like the footer hints.

## CRT and arcade monitors

A MiSTer feeding a 15 kHz monitor reports a framebuffer like `640x240`, and two
things follow from that which do not apply to a monitor.

**Pixels are not square.** 640 pixels across a 4:3 picture that is 240
scanlines tall means each pixel is twice as tall as it is wide. Text rendered
square into that comes out half the width it should be. The client reads the
geometry, derives the pixel aspect, and rasterises glyphs with a wider `x_ppem`
than `y_ppem` to compensate. Below 288 scanlines it also switches to a separate
layout — larger type, fewer rows, no system chip, no size column — because
scaling the desktop layout down linearly produces 12-scanline rows holding a
13-pixel font. Antialiasing is dropped at that size too: the grey fringes are
wider than the stems they are smoothing.

**The tube does not show the whole picture.** How much it hides is a property
of that set, and there is nothing to read it from — MiSTer's framebuffer driver
reports `pixclock`, `vmode` and `sync` all as zero, so there is no timing to
derive anything from and no interlace flag worth trusting. The scanline count
is the only signal available. Settings → **Adjust screen** draws a frame at the
edge of the safe area with heavy corner brackets; shrink it with the stick
until all four corners are visible. The inset is stored as a percentage, not
pixels, because the mode changes underneath it — the same cabinet reports
`640x240` for one core and `640x480` for another.

If overscan is bad enough to hide the Settings tab itself, start the client
with `--calibrate` to go straight there.

## Arcade sticks

`hid-generic` hands a plain HID gamepad the whole `BTN_GAMEPAD` range in order,
including `BTN_C` and `BTN_Z` — codes most console pads skip. A GP2040-CE
encoder, which is what an arcade cabinet is likely to be running, therefore
lands face buttons on codes a console-pad map never mentions, and those buttons
do nothing at all.

The client binds the full contiguous range when it detects a low-resolution
display, so no button is dead out of the box. Which physical button ends up on
which code still differs per encoder, so Settings → **Remap buttons** asks for
one press per action and stores the raw codes. Leave an action unpressed for
twelve seconds to skip it.

A cabinet typically has six buttons plus start and a coin switch rather than a
pad's ten, which is why the footer hints are generated from the live mapping —
telling someone to press L2 on a panel with no L2 is worse than saying nothing.

Two devices can hold the same save and still differ byte-for-byte, because each
writes its own bookkeeping into regions the game never reads. Where that is the
only difference, the save is reported as in sync rather than as a conflict no
amount of syncing could settle — the core rewrites those bytes on the next boot
and the two sides differ again. Observed on real hardware:

| System | The difference | Verdict |
|---|---|---|
| PS1 | 3 bytes in block 0 frame 63, the card's write-test frame | directory and every save block identical |
| Saturn | 6 bytes in an archive's 10-byte comment field | same archive, same save data |
| Mega Drive | the filler at every even offset (SRAM sits on the odd byte of the bus) | every odd byte identical |

Real save data is still compared exactly: a single changed byte inside a save
block, a directory entry or the SRAM itself is a genuine difference.

### A save folder is not always named after the games folder

The TurboGrafx-16 core writes **both** HuCard and CD saves into
`saves/TGFX16`. There is no `saves/TGFX16-CD`, even though CD *games* live in
`games/TGFX16-CD`, so a CD save downloaded into a folder named after the games
folder lands somewhere the core never looks.

That also means the save folder no longer says which system a save belongs to.
The games folders are still separate, so the installed game decides: a save in
`saves/TGFX16` whose name matches a game in `games/TGFX16-CD` is a PC Engine CD
save and is keyed as one. Both clients follow the same rule
(`shared/mister.MISTER_SYSTEM_SAVE_FOLDERS` and
`shared/mister_scan.system_for_save`).

If the same save exists in both the current and a stale folder, only the one
the core actually reads is listed, so the two do not fight over one server slot.

### PlayStation cards hold more than one game

A PS1 memory card is shared: up to 15 saves belonging to *different games*. The
card is keyed by whichever save is first in its directory, so the server slot
for one game can hold saves for several others.

That makes downloading a card destructive, so the two are compared save by
save first. Losing **this game's** save - the one being synced - is refused
unless it is confirmed explicitly, and a bulk sync never confirms it. Saves for
*other* games that happen to share the card are reported but do not block:
cards cloned from a MemCard Pro routinely carry a decade of unrelated saves.

Uploading is never destructive this way - the server keeps the whole card, and
`POST /saves/{id}/ps1-card` regenerates the PSP `.VMP` view from it while
leaving the other slot and its metadata alone.

### Multi-disc games are one entry

A CD game's discs all install into the same folder, which is what lets the core
keep one memory card for the whole game. The Catalog folds them into a single
entry - "Final Fantasy IX (USA), 4 discs" - and queuing it downloads every
disc.

Grouping is by that install folder, not by the server's `primary_rom_id`: the
server gave a vanilla release, a fan translation of it and a bonus disc the
same id, and those install into three different folders. Across a 2933-row CD
catalogue this keeps every entry mapped to exactly one folder.

A save reads `local` until it has been compared with the server, and only then
becomes `synced`, `upload`, `download` or `conflict` - "synced" is never shown
for something that was merely scanned.

On PS1 and Saturn a save is keyed by the disc **serial**, not by its file name.
When the save carries no serial of its own — a memory card no game has written
to, or Saturn backup RAM, which has no disc id at all — the name is matched
against the server's ROMs and saves allowing for regional naming differences
(`Final Fantasy IX (USA)` against `Final Fantasy IX (USA, Canada) (Disc 1)`).
A region mismatch is never accepted: filing a USA save under the Europe serial
would corrupt the sync.

A conflict - a save that changed here *and* on the server since they last
agreed - opens a dialog showing both sides: timestamp, size, hash, and which
client last wrote the server's copy. X keeps yours, A takes the server's, B
decides later. Nothing is resolved automatically, but it is resolved *here*.

Saves that exist only on the server are listed and can be downloaded: the file
is written under the installed game's name, so the core finds it.

A USB or Bluetooth keyboard works too — arrows, Enter, Esc, Tab, Page Up/Down.
If no input device is found at all, the client says so and exits after 20
seconds rather than leaving you with no way out.

## Why a rescan is fast

A save is re-read only when its size or mtime changed; otherwise its hash, PS1
in-card serial and blank-card flag come from `hash_cache.json`.

That alone was not the win it looks like. Profiled on real hardware, reading
and hashing every save took **37 ms** while the whole scan took **6.9 s** - the
rest was fetching the catalogue and title lists needed to resolve names for
serial-keyed systems, and then re-running the slug rules over every one of
those names. So the built name matcher is cached too, in `server_cache.json`,
with a 10-minute expiry.

Slug-keyed systems (SNES, NES, GBA, …) do not pay that cost. Their save is
still checked against the catalogue - a translation patch is filed on the
server under the original title's slug, and a save named after the patched
file has to land in that slot - but only an *exact* file-name hit counts, so
the index is a plain dictionary built from the catalogue rows the client
already holds (`catalog_cache.json`), with no slug pass and no fetch.

| | |
|---|---|
| Cold scan (no caches) | ~9.8 s |
| Warm scan | **~50 ms** |

Only *name matching* is cached. What to upload or download always comes from a
live `POST /api/v1/sync`, so a stale cache can never cause a wrong transfer -
at worst a save added on another console minutes ago is not recognised yet, and
**Y** forces a full refresh.

## Development

```bash
# build only
python mister/tools/build_pyz.py

# build and push
python mister/tools/build_pyz.py --deploy --host 192.168.1.41 --password 1

# push arbitrary files / run commands / fetch results
python mister/tools/deploy.py --host 192.168.1.41 --password 1 \
    --put local.py /media/fat/Scripts/.gamesync/local.py \
    --run "python3 /media/fat/Scripts/.gamesync/gamesync.pyz --selftest"
```

MiSTer runs Scripts on a real VT (`/dev/tty2`), so the client can be driven over
SSH against the live screen instead of navigating the Scripts menu each
iteration:

```bash
python3 /media/fat/Scripts/.gamesync/gamesync.pyz < /dev/tty2 > /dev/tty2 2>&1
```

Two things will bite you:

* **From Git Bash, set `MSYS_NO_PATHCONV=1`**, or MSYS rewrites `/media/...`
  arguments into Windows paths.
* The build **fails the syntax check if any shipped file would not parse under
  Python 3.9**, which is the version on the device. That gate exists because the
  development machine runs a newer Python.

### Where the ROM install rules live

`shared/mister_install.py` decides where a downloaded ROM lands, and the desktop
installer follows the same rules:

* CD systems (PS1, Saturn, Mega CD, TG16-CD, Neo Geo CD, 3DO) install into a
  per-game subfolder with the disc tag stripped, so every disc of a game shares
  one folder - which is what makes the core keep a single memory card for the
  whole game.
* An existing legacy core folder (`Genesis`, `PCEngine`) is reused rather than a
  second modern one being created alongside it.
* A system with no known MiSTer folder is refused, not dumped into the games
  root.
* CHDs are installed byte-for-byte; MiSTer CD cores read them natively, so the
  server is never asked to convert.
* Installing to USB seeds `boot*.rom` from the SD folder first, because a core
  ignores the SD card entirely once the USB folder exists.

`mister/PHASE0_FINDINGS.md` records what was measured on real hardware — why the
UI is drawn the way it is, why input is read the way it is, and the traps
(no `sqlite3`, `/media/usb0` mounted `noexec`, unset `TERM`, input device numbers
that move between runs, sensor nodes that flood thousands of events per second).

## Layout

```
install.py            one-command network installer
gamesync/
  __main__.py         entry point, selftest, console restore on any exit
  app.py              UI shell and screens
  fb.py               framebuffer surface (mode read at runtime)
  text.py             FreeType atlas + cached coloured text runs
  input.py            controller and keyboard reader
  api.py              server client (urllib only)
  sync.py             save scan, plan and transfer
  downloads.py        resumable ROM download queue (JSON, no sqlite3)
  config.py           config and sync state
  theme.py            palette and layout metrics
  assets/font.ttf     Vegur (public domain)
tools/
  build_pyz.py        zipapp builder with the Python 3.9 gate
  deploy.py           push files / run commands over SSH
  spike_*.py          the Phase 0 hardware spikes
sync_saves.sh         legacy standalone sync script, superseded by the client
```
