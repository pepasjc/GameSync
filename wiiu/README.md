# wiiusync — Save Sync Wii U client

Aroma homebrew (`.rpx` + `.wuhb`) that syncs three save families with the Save
Sync server, and installs GameCube / Wii games onto the SD card.

| Family | Where the saves live | How they sync |
|---|---|---|
| **GameCube** (Nintendont) | virtual memory-card images on SD (`sd:/saves/*.raw`) | per-save GCI through `/saves/GC_<code>/gc-card` — byte-identical to the GameCube client, Dolphin and the Android app |
| **vWii** | SLC NAND `/title/00010000/<tidlo>/data/` | whole-tree 3DSS v5 bundle + three-way-hash `/sync` |
| **Wii U** | MLC `/usr/save/00050000/<tidlo>/user/` | same as vWii |

ROM downloads cover **GC** (Nintendont layout), **Wii** (split WBFS for
USB Loader GX / WiiFlow) and **Wii U** (WUP folders, installed to NAND or the
console's USB drive through MCP from the Installed view).

The UI is drawn on both the TV (1280x720) and the GamePad (854x480, the same
frame scaled to two thirds) in GameSync's shared dark theme: a header with the
current view, the console IP and SD / NAND status; a tab strip of the eight
views; a list on the left with a detail card for the selection on the right; a
status banner; and a footer of button hints. Questions open as dialog cards,
long jobs (downloads, installs, sync runs) as progress cards with a bar,
percentage, speed and time left.

## Building

### 1. Toolchain

```bash
C:/devkitpro/msys2/usr/bin/bash.exe --login -c "pacman -Sy --noconfirm wiiu-dev ppc-zlib"
```

`wiiu-dev` pulls in `wut` + `wut-tools` (`elf2rpl`, `wuhbtool`); `ppc-zlib`
provides the zlib the 3DSS bundle compressor links against.

### 2. libmocha (not in pacman — built from source)

```bash
cd external
git clone https://github.com/wiiu-env/libmocha.git
cd libmocha && git checkout 50fefdf8307a875c63bdbdcf6c973779d4ddac92
C:/devkitpro/msys2/usr/bin/bash.exe --login /e/projects/3dssync/external/build_libmocha.sh
```

Pinned commit: **`50fefdf8307a875c63bdbdcf6c973779d4ddac92`** (libmocha 1.0.0).
`make install` drops the library into `$DEVKITPRO/wut/usr`, so verify:

```
C:/devkitpro/wut/usr/include/mocha/mocha.h
C:/devkitpro/wut/usr/lib/libmocha.a
```

libmocha is C++, so the Makefile links with `$(CXX)` even though every source
file here is C — otherwise `operator new` / `std::__throw_system_error` are
undefined at link time.

### 3. Build

```bash
C:/devkitpro/msys2/usr/bin/bash.exe --login /e/projects/3dssync/wiiu/build.sh
# or, from the repo root:
build_all.bat wiiu
```

Outputs `wiiusync.rpx` and `wiiusync.wuhb`.

## Installing

- **Homebrew Launcher / Aroma:** copy `wiiusync.rpx` to
  `sd:/wiiu/apps/wiiusync/wiiusync.rpx`.
- **Wii U Menu (Aroma):** copy `wiiusync.wuhb` to `sd:/wiiu/apps/`.

libmocha needs a CFW that exposes the Mocha API — Aroma (Tiramisu-era or
newer). Without it the app still runs; the vWii / Wii U views report
`mocha: off` and only the GC + ROM features work.

## SD layout

```
sd:/3dssync/config.txt              settings (created on first run)
sd:/3dssync/consoleid.txt           per-console id sent to the server
sd:/3dssync/downloads.dat           resumable download queue
sd:/3dssync/catalog/<SYSTEM>.bin    cached server catalog (GC / WII / WIIU)
sd:/3dssync/state/<title_id>.txt    last-synced hash (three-way sync)
sd:/3dssync/hashcache/<id>.txt      local save-hash cache
sd:/3dssync/backup/<title_id>/      pre-restore backup of a NAND save
sd:/3dssync/gci/<title_id>.gci      GCIs pulled from the Server view
sd:/saves/*.raw                     Nintendont virtual memory cards
sd:/games/<GAMEID6>/game.iso        Nintendont GameCube games
sd:/wbfs/<Name> [ID6]/<ID6>.wbfs    Wii games (+ .wbf1 ... split parts)
```

## Config keys (`sd:/3dssync/config.txt`)

| Key | Default | Meaning |
|---|---|---|
| `server_url` | `http://192.168.1.201:8000` | hostname or IP — the client resolves DNS |
| `api_key` | `anything` | `X-API-Key` header |
| `nintendont_saves_dir` | `/saves` | folder scanned for GC card images |
| `games_dir` | `/games` | Nintendont GameCube install folder |
| `wbfs_dir` | `/wbfs` | USB-loader Wii install folder |
| `sync_vwii` | `true` | enable the vWii save view |
| `sync_wiiu` | `true` | enable the Wii U save view |

There are no network keys: the Wii U uses its own system network settings.

Setting **both** `sync_vwii` and `sync_wiiu` to `false` skips libmocha
entirely — use that on emulators, or if boot stops at the "Opening NAND" step.

## If it hangs on boot

Boot is a checklist of eight steps, each drawn *before* the work it names, so
the highlighted step is the stage that stalled:

| Stuck at | Likely cause |
|---|---|
| nothing on screen | OSScreen/MEM1 never came up — the `.rpx` is running outside Aroma/HBL |
| 1 Mounting SD | no SD inserted or an unreadable card |
| 2 Reading config | corrupt `3dssync/config.txt` — delete it to regenerate |
| 3 Bringing up the network | console has no network profile |
| 4 Opening NAND | no CFW / no Mocha — set `sync_vwii=false` and `sync_wiiu=false` |
| 5-7 scans | very large `saves/` or NAND tree |
| 8 Checking server | `server_url` wrong — the probe gives up after ~15 s |

Nothing at boot fetches the catalog or the save list; those load from their
own views on demand, so an unreachable server can never wedge startup. Any
HTTP wait can be aborted with `B`.

## Screens and controls

The same scheme as every other GameSync client. Only the GamePad is read
(the client has never polled Pro / Wii Remote controllers).

| Button | Everywhere |
|---|---|
| `L` / `R` | previous / next view, wrapping (the tab strip under the header shows where you are) |
| `Up` / `Down` | move one row (hold to repeat) |
| `Left` / `Right` | page up / page down (hold to repeat) |
| `-` | switch the sub-tab inside a view (catalog system, memory card) |
| `A` | confirm / the focused row's main action |
| `B` | cancel: closes a dialog, pauses a running download, aborts any server wait |
| `+` | exit GameSync (asks first); `HOME` also exits |

| View | Shows | `A` | `X` | `Y` | `-` |
|---|---|---|---|---|---|
| **Catalog** | server games for the GC / Wii / Wii U sub-tab, queue state per row; detail: file, install format and target folder, related update / DLC | download now (asks first; loads the catalog when empty) | add to queue | details | GC → Wii → Wii U |
| **Installed** | games on SD / USB tagged GC / Wii / Wii U; detail: path, title id | install (Wii U, with its update / DLC, base game first) | delete | details | — |
| **Downloads** | one row per title with status pill and progress bar; detail: bytes, files, speed | start / resume the title | run the whole queue | remove from the queue | — |
| **GC Cards** | saves inside the open Nintendont card; detail: title id, blocks, whether the server has it | card for the save: `A` upload · `Y` restore the server copy · `B` cancel | import the whole card | details | next card image |
| **GC Server** | every GC save on the server, marked when it is on the open card | restore into the open card (loads the list when empty) | pull every save as `.gci` | details | — |
| **vWii** / **Wii U** | console saves plus server-only titles with a sync status (synced / upload / download / conflict / server only); detail: status card and the plan totals | sync card: `A` smart sync · `X` force upload · `Y` force download · `B` cancel | sync all (run the whole plan) | details | — |
| **Settings** | server, API key, download and install targets, folders, sync toggles, maintenance; detail: what the setting does | edit / toggle / run / save | — | — | — |

Settings also holds the maintenance actions that used to sit on per-view
buttons:

- **Refresh catalog** — asks the server to rescan its ROM folder
  (`GET /api/v1/roms/scan`; a refusal is reported and the refresh carries
  on), wipes the catalog cache on SD and downloads every system again.
- **Rescan saves and games** — rescans installed games, GC card images and
  vWii / Wii U saves, reloads the server GC save list and recomputes the
  sync plan.

Text settings open an editor card: `Up` / `Down` change the character under
the cursor (the card shows the next and previous ones), `Left` / `Right` move,
`Y` inserts, `X` deletes, `A` keeps, `B` cancels.

## Catalog cache

The catalog is kept on SD per system (`sd:/3dssync/catalog/GC.bin`,
`WII.bin`, `WIIU.bin`) next to the fingerprint the server published for it
(`GET /api/v1/roms/fingerprints`) — the same strategy as the MiSTer client.
The fingerprints are fetched once per session; opening a system then reads
its rows from SD when the fingerprint still matches and downloads it again
only when it changed. If the server cannot be reached, the SD copy is shown
and the list header says `offline copy`; a server without the fingerprints
route gets a plain fetch with nothing cached. The files are a compact
big-endian binary (magic `GSWC`, a format version that is bumped whenever a
row field changes, then only the fields the client uses — see
`include/catcache.h`).

## Server prerequisite for Wii downloads

Split-WBFS conversion runs **on the server** and needs
[wit (Wiimms ISO Tools)](https://wit.wiimm.de/) on `PATH`, plus DolphinTool
when the source is an RVZ. Without `wit` the client's queue attempt returns
HTTP 503 with an install hint.

Conversion is cached under the server's `SYNC_TMP_DIR`, but a dual-layer Wii
disc needs roughly **17 GB** of scratch space (RVZ → ISO → split parts) and can
take several minutes — hence the 30-minute conversion timeout and the "server
converting" wait screen in the client.

## Known limitations / caveats

- **Wii U saves are per-account.** The bundle carries `user/common/` plus the
  `user/<persistentId>/` folders verbatim. Two consoles with different account
  persistent IDs will exchange folders the other side does not read. v1 syncs
  the tree as-is; remapping is a follow-up.
- **vWii `nocopy/` is excluded** by design — that data is console-bound.
- **Every NAND restore backs up first** to `sd:/3dssync/backup/<title_id>/`.
  Writes are confined to the save directory itself; nothing is deleted without
  a confirmation prompt.
- **Cemu covers the UI, network and config only.** libmocha is unavailable
  there, so the vWii / Wii U views, and anything touching SLC/MLC, need real
  hardware.
- GC multi-disc auto-pairing (`disc2.iso`) is not implemented yet.

## License

GPL-3.0-or-later, like the rest of GameSync — see [LICENSE](../LICENSE).

This client links **[libmocha](https://github.com/wiiu-env/libmocha)** (Maschell
and the wiiu-env contributors), which is **LGPL-3.0-or-later**. It is statically
linked into the distributed `wiiusync.rpx` / `wiiusync.wuhb` and used unmodified;
LGPL-3.0 code may be conveyed as part of a GPL-3.0 work. License text:
[`licenses/LGPL-3.0.txt`](../licenses/LGPL-3.0.txt).

The UI rasterises text with **[stb_truetype](https://github.com/nothings/stb)**
(Sean Barrett, public domain or MIT), vendored unmodified in
[`third_party/stb/`](third_party/stb/) with its license. The font itself is the
console's own shared system font, read from memory at runtime — none is
bundled.

Building also links devkitPPC and `wut`, covered by the GPLv3 §7 console-SDK
linking exception described in the root [README](../README.md#license). Details:
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
