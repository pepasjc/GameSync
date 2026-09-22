# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Project Overview

**GameSync** — sync save files between consoles, handhelds and emulators through a
self-hosted local server. One Python FastAPI server plus these clients:

| Client | Language / SDK | Output |
|---|---|---|
| `server/` | Python 3.11+, FastAPI, uv | — |
| `3ds/` | C, devkitARM + libctru | `3dssync.3dsx`, `.cia` |
| `ds/` | C, devkitARM + libnds/dswifi/libfat | `ndssync.nds` |
| `gc/` | C, devkitPPC + libogc/libfat/gxflux/libbba | `gcsync.dol` |
| `wiiu/` | C, devkitPPC + wut + libmocha | `wiiusync.rpx`, `.wuhb` |
| `ps2/` | C, PS2SDK (WSL) | `ps2sync.elf` |
| `ps3/` | C, PSL1GHT/ps3dev (WSL) | `ps3sync.pkg` |
| `psp/` | C, pspdev/PSPSDK (WSL) | `EBOOT.PBP` |
| `vita/` | C, VitaSDK (WSL) | `vitasync.vpk` |
| `xbox/` | C, nxdk + SDL2/SDL_ttf (WSL) | `default.xbe`, `.iso` |
| `android/` | Kotlin, Compose, Room, Retrofit | `gamesync.apk` |
| `desktop/` | Python, PyQt6 | — |
| `steamdeck/` | Python, PyQt6 + pygame | — |
| `mister/` | Python 3.9 (stdlib only), framebuffer UI | `gamesync.pyz` |

Shared Python logic lives in `shared/` and is imported by server, desktop and
Steam Deck alike. `tools/` holds scrapers and one-off utilities.

## Commands

### Build

`build_all.bat` is the authoritative build entry point and encodes the exact
toolchain environment for every target. Prefer it over hand-rolled commands.

```bat
build_all.bat                    :: all targets
build_all.bat ps2 ps3 psp        :: specific targets
```

Targets: `3ds`, `nds`, `gc`, `wiiu`, `android`, `ps2`, `ps3`, `psp`, `vita`,
`xbox`, `all`. A failing target does not abort the run; failures are summarized
at the end and the exit code is non-zero. Artifacts land in `build_output/`.

Individual builds, if you need them:

```bash
# devkitPro targets — MUST use devkitPro's MSYS2 login shell, not Git Bash
# (recursive make fails in Git Bash: $(MAKE) resolves to the wrong path)
/c/devkitpro/msys2/usr/bin/bash.exe --login -c 'cd /e/projects/3dssync/3ds && make'
/c/devkitpro/msys2/usr/bin/bash.exe --login -c 'cd /e/projects/3dssync/ds && make'   # make dsi for DSi build
/c/devkitpro/msys2/usr/bin/bash.exe --login -c 'cd /e/projects/3dssync/gc && make'
/c/devkitpro/msys2/usr/bin/bash.exe --login /e/projects/3dssync/wiiu/build.sh

# WSL targets — see build_all.bat for the full env var incantations
wsl bash /mnt/e/projects/3dssync/ps2/tools/build-wsl.sh
wsl bash /mnt/e/projects/3dssync/xbox/build.sh

# Android
cd android && ./gradlew.bat --no-daemon :app:assembleDebug
```

### Test

```bash
cd server && uv run pytest tests/ -q          # 304 tests
cd server && uv run pytest tests/test_sync.py -v
cd server && uv run pytest tests/test_api.py::TestUploadEndpoint::test_upload_success -v
cd steamdeck && python -m pytest tests/ -q
cd desktop  && python -m pytest tests/ -q
python -m pytest shared/tests/ -q
```

### Run

```bash
cd server && uv sync && uv run python run.py   # port 8000, auto-reload
cd desktop && python main.py
cd steamdeck && python main.py
```

### Version

Single source of truth: the root `VERSION` file (currently `0.5.3`). Every
Makefile reads it and passes `-DAPP_VERSION`. Never hardcode a version.

## Architecture

```
[3DS] [DS] [GC] [Wii U] [PS2] [PS3] [PSP] [Vita] [Xbox] [Android] [Desktop] [Steam Deck] [MiSTer]
   \     \     \     |      |      |      |      |        |          |           |         /
    ---------------------- HTTP (X-API-Key) ---------------------------------------------
                                     |
                         [ FastAPI server, port 8000 ]
                                     |
                  saves/<title_id>/current/    saves/<title_id>/history/<ts>/
                  saves/metadata.db (SQLite)   roms/  (ROM catalog)
```

### Storage

```
saves/<title_id>/current/       extracted save files
saves/<title_id>/history/<ts>/  previous versions (SYNC_MAX_HISTORY_VERSIONS, default 10)
saves/metadata.db               SQLite metadata — replaces per-title metadata.json
```

`metadata.json` files are legacy read-only backups (`.bak` after
`migrate_to_sqlite.py`); `storage.py` still falls back to reading them for titles
absent from the DB. All clients share one flat slot per title ID — `console_id`
in metadata is informational and does not affect the storage path.

### Identity — `title_id` and `sync_id`

`shared/sync_id.py` and `shared/systems.py` are the single source of truth for
"which identifier do we use", mirrored by every Python client and the server.
`SYNC_ID_RULES` in `shared/systems.py` picks one of four strategies per system:

| Strategy | Systems | Example |
|---|---|---|
| `title_id` | 3DS, Wii U | `0004000000055D00` (16-char uppercase hex) |
| `prefix_hex_serial` | NDS | `00048000` + hex of 4-byte gamecode |
| `serial` | PS1/PS2/PSP/Vita/Saturn/DC/GC | `SLUS01279`, `SAT_T-4507G`, `DC_T1249M` |
| `slug` | SNES, GBA, NES, … | `GBA_legend_of_zelda_minish_cap_usa` |

Slug rules live in `shared/rom_id/normalizer.py`. Server and clients must agree
here or the same save lands under two different keys — change this module with
care and run `shared/tests/test_sync_id.py`.

### Sync protocol — three-way hash

Metadata-first, conflict-free across multiple consoles. Client state is the hash
of the last successfully synced version, stored per title on the client.

1. Client hashes each local save, loads `last_synced_hash`, sends **one**
   `POST /api/v1/sync` with all title metadata.
2. Server returns a plan: `{upload, download, conflict, up_to_date, server_only}`
   plus `conflict_info` (server/client hash, size, console, `same_console`).
3. Client uploads only what's in `upload`, downloads `download` + `server_only`,
   then writes the new hash to its state file.

Decision logic (`server/app/routes/sync.py`):

| Condition | Result |
|---|---|
| `save_hash == server_hash` | `up_to_date` |
| no save on server | `upload` |
| `last_synced_hash == server_hash` | `upload` (only client changed) |
| `last_synced_hash == save_hash` | `download` (only server changed) |
| all three differ | `conflict` |
| no `last_synced_hash` and hashes differ | `conflict` (safe fallback) |

### Transports

Clients pick whichever fits their platform; all land in the same storage.

| Transport | Endpoint | Used by |
|---|---|---|
| **3DSS bundle** v1 uncompressed / v2 zlib | `GET`/`POST /saves/{title_id}` | 3DS, Wii U, PSP, Vita, PS3, Xbox |
| **Raw** — server bundles internally | `GET`/`POST /saves/{title_id}/raw` | DS |
| **PS1 card** | `/saves/{tid}/ps1-card`, `/ps1-save` | PS2, desktop |
| **PS2 card / P2FD folder** | `/saves/{tid}/ps2-card`, `/ps2-files` | PS2, desktop |
| **GC card / GCI** | `/saves/{tid}/gc-card` | GC, Android (Dolphin) |
| **VMC import** | `POST /saves/{ps1,ps2,gc}-vmc/import` | desktop, PS2 |

Wii U ROMs are folder-shaped: an encrypted WUP/NUS set (`title.tmd` + `.app`)
or a decrypted loadiine tree (`code`/`content`/`meta`). Both scan as bundles, and
a title id embedded in the folder name (`Game [0005000010145C00]`) becomes the
catalog `title_id` so a ROM and its save share one key. The raw bundle is what
both targets want — hardware installs it through MCP, and Cemu 2.x decrypts it
itself from the bundled `title.tik` — so **no server-side decrypter is needed**.
`?extract=loadiine` / `?extract=wua` are optional extras backed by an
operator-configured tool (`SYNC_ROM_WIIU_*_COMMAND`, e.g. CDecrypt), and are
advertised in `extract_formats` only when that command is set; otherwise clients
see nothing and take the raw bundle.

Bundle format (`server/app/services/bundle.py`): magic `3DSS`, version, title_id
(u64 BE), timestamp (u32 LE), file count, total size, file table, file data.
v2 zlib-compresses the payload and stores `uncompressed_size` last — this is what
lets 1–2 MB saves fit the 3DS's 448 KB network buffer. Clients write v2; the
server accepts both.

Saves are interchangeable across transports: a save uploaded from a 3DS bundle
downloads fine as raw bytes on a DS.

### Server layout

```
server/app/
  config.py            pydantic-settings, all env vars SYNC_-prefixed
  main.py              lifespan startup (loads game names, ROM scan)
  middleware/auth.py   X-API-Key on everything except GET /status
  models/save.py       SaveBundle, SaveMetadata, SyncPlan, ConflictInfo
  routes/              saves sync titles roms catalog normalize status update web
  services/            bundle storage db game_names rom_scanner rom_db
                       ps1mc ps2mc gc_cards ps1_cards ps2_cards mcr2vmp_tool
                       ctr_rom dat_normalizer saturn_archives serialstation share_token
  templates/           web UI
```

`routes/roms.py` (~2950 lines) and `routes/saves.py` (~1230) are the big ones.

Config is all env vars with the `SYNC_` prefix: `SYNC_API_KEY`, `SYNC_SAVE_DIR`,
`SYNC_ROM_DIR`, `SYNC_TMP_DIR`, `SYNC_HOST`, `SYNC_PORT`,
`SYNC_MAX_HISTORY_VERSIONS`, `SYNC_ADMIN_USERS`, and more in `config.py`.

### Auth

`X-API-Key` header on all endpoints except `GET /api/v1/status`. Middleware
returns a `JSONResponse` directly — **BaseHTTPMiddleware cannot raise
HTTPException**. Extra roles via `SYNC_ADMIN_USERS`, `SYNC_TRUST_PROXY_AUTH`,
`SYNC_LAN_ADMIN`.

### Game names

`server/app/services/game_names.py` loads libretro DAT files from
`server/data/dats/` plus legacy `.txt` databases at startup. Lookup is by 4-char
game code (3DS `CTR-P-BRBE` → `BRBE`), full serial, 16-hex title id, or
normalized name slug.

Wii U is DAT-driven: `Nintendo - Wii U.dat` carries `title_id "…"` lines so a
save is named from its id alone. Custom VC injects (ids not ending in `00`)
aren't in the DAT — those fall back to client-parsed `meta.xml`
(`shared/wiiu_meta.py`). Clients share resolved names via `?game_code=&game_name=`
on upload and `POST /titles/update_names`. The server prefers its own DAT name,
then `game_code`, then the client's `game_name`, and never overwrites a name that
already resolved.

### ROM catalog

Beyond saves, the server indexes and serves ROMs from `SYNC_ROM_DIR` using
EmuDeck-style folder names. `rom_scanner.py` + `rom_db.py` (SQLite) back
`GET /roms`, `/roms/{rom_key}` (download, range-capable), `/manifest`,
`/wbfs-manifest` and `/roms/scan`. CD systems convert on the fly via external
tools (`chdman`, `wit`, `dolphin-tool`, `maxcso`) invoked as subprocesses — these
are **not** bundled. `share_token.py` issues HMAC-signed links so a ROM or save
can be fetched without exposing the API key.

**MSU packs** (SNES MSU-1, Mega Drive MSU-MD / MD+) are enhanced-audio hacks
that must land on a device as a *folder* — the ROM plus the audio it streams
from beside itself. `shared/msu.py` is the single source of truth (Kotlin
port: `android/.../sync/MsuPack.kt`, keep in step). Detection is by
**contents, not layout**: under `snes/` or `genesis/` (any depth) a per-game
subfolder, or a `.zip` ≥16 MB / with `msu` in its name, becomes one bundle
entry with `bundle_kind` = `msu1` (`.msu` + `.sfc`), `msu-md` (`.cue` with
`BINARY` audio + `.md`) or `mdplus` (`.cue` with `WAVE` tracks + `.md`). The
two Mega Drive kinds carry differently patched ROMs and are never
interchangeable. A zipped pack is served **as the zip on disk** (`path` is a
file, Range works, `/file/{rel}` reads members); the manifest lists members
with the single wrapping folder stripped and every client hoists it on
extract (`msu.plan_extraction`). Identity: a pack must share the save slot of
the ROM it patched, but its name keeps the MSU author's `[Hack by …]` (which
no plain ROM carries) while a translated baseline keeps *its* tags
(`[T-En …] [FastROM …] [n]`) — so neither "keep brackets" nor "strip
brackets" works. `msu.identity_candidates` yields name → name minus `[Hack
by …]` → cart member → cart minus hack tags; `rom_scanner._resolve_msu_identities`
runs after the whole scan and takes the first candidate that is a plain ROM
in the catalog, else the first DAT hit, else the hack-stripped name. Display
name keeps every tag; `rom_id` stays unique. `tools/msu_pick.py` picks one
pack per game from several source sets using exactly this code against a
baseline list (curated set last wins; hand-named `(US)`/`(BR)`/`(MSU-1)`
copies are second-class and get tidied), `tools/msu_copy.sh` rsyncs the plan
to the Pi. MiSTer layout lives in `shared/mister_install.msu_pack_layout`: MSU-1 →
`games/SNES/<Game>/`, MD+ → `games/Genesis/<Game>/` (MegaDrive core, loads
the `.md`), MSU-MD → `games/MegaCD/<Game>/` with the cart renamed `cart.rom`
(MegaCD core; the MegaDrive core does not play BIN/CUE audio). Emulator
clients (Android, Steam Deck, desktop EmuDeck/FXPak) extract into
`<roms>/<system>/<Game>/` with no rename. Pack junk (`.srm`, `.asm`, `.txt`…)
is dropped on the way out. The MiSTer client's L1/R1 system cycle gains a
`SNES: MSU packs` stop after any system that has packs (`catalog_systems`
in `mister/gamesync/app.py`), and pack rows carry an `MSU-1`/`MSU-MD`/`MD+`
prefix in their detail column.

## Client notes and hard-won gotchas

**3DS** — libctru `AM`/`FS`/`httpc`/`AC`. After writing save data you **must**
call `FSUSER_ControlArchive(archive, ARCHIVE_ACTION_COMMIT_SAVE_DATA, ...)` or
the write is silently lost. Cartridge titles show in cyan and are excluded from
"Sync All" to prevent accidents. Config at `sdmc:/3ds/3dssync/config.txt`, state
in `sdmc:/3ds/3dssync/state/`. For CIA builds do **not** use makerom's `-ver`;
the Makefile computes `RemasterVersion` = `(major<<10)|(minor<<4)|micro` and
passes `-major/-minor/-micro`.

**DS** — dswifi with manual SSID/WEP config (DS hardware is WEP-only), raw
sockets for HTTP/1.0, saves read from the flashcard via libfat. Config at
`fat:/3dssync/config.txt`, editable in-app.

**PS2** — two protocol landmines:

- `mcOpen` takes **IOP flags, not newlib POSIX flags**. newlib `O_RDONLY` is 0,
  which mcman reads as "no permissions", so every `mcRead` returns -5. Use
  `IOP_O_RDONLY=0x0001`, `IOP_O_WRONLY=0x0002`, `IOP_O_CREAT=0x0200`,
  `IOP_O_TRUNC=0x0400`.
- MemCard Pro GameID: gen1 speaks SIO command `0x21`, MMCE devices (MCP2,
  SD2PSX) speak `0x8B` — and **each command breaks the other device**. `AUTO`
  discriminates by card type from `mcGetInfo` (safe, no SIO probe): PS1-type →
  `0x21` only, PS2-type → `0x8B` only. The gen1 `0x21` transfer is **TX-only**
  (`rx_size=0`); waiting for a reply that never comes was the freeze.
- `sio2man`/`mcman`/`mcserv`/`padman` load from `rom0:` at runtime — the PS2SDK
  equivalents have a different RPC ABI and hang `mcInit()`.

**GC** — MemCard Pro GC GameID rides EXI (`0x8B`-prefixed), ported from libogc2.
GC EXI is master-clocked, so sending to a plain Nintendo card is harmless, unlike
the PS2's ACK-based SIO2.

**Wii U** — libmocha/FSA reaches vWii (`slccmpt01`) and Wii U saves. vWii
`nocopy/` is excluded by design (console-bound). Wii U saves are per-account: the
bundle carries `user/common/` plus `user/<persistentId>/` verbatim. Under Cemu
only the UI/network/config paths work; anything touching SLC/MLC needs real
hardware. Catalog downloads land on SD or a FAT32 USB drive (`rom_storage=sd|usb`
mounts `/dev/usb01` at `/vol/usb` — a *different* device from the WFS
`storage_usb01:` that holds saves); app data always stays on SD. Wii U titles
download as WUP folders under `<root>/install/<Name>/` and install through MCP
(`install_target=mlc|usb`) — MCP wants an FSA path (`/vol/external01/...`), never
a devoptab path, and its structs must be heap-allocated at 0x40 alignment.

**PSP / Vita ROM catalog** — both clients install PSP and PS1 games into
the PSP tree (`ms0:/` on PSP, `<pspemu_root>/` — default `ux0:pspemu` — on
the Vita for Adrenaline): PSP CHDs as `ISO/<name>.cso`, PS1 discs as
`PSP/GAME/<serial>/EBOOT.PBP`. The server's catalog `extract_format` hint for
PS1 is `cue` (the PS3 client's choice) with `eboot` only in the
`extract_formats` list the C clients don't parse — so the client must ask
for `?extract=eboot` itself, never pass the hint through. PS1 folder
bundles have no EBOOT route and are refused client-side. `vita/source/roms.c`
and `downloads.c` are ports of the PSP files; keep them parallel.

**PS3** — `PARAM.PFD` resigning and per-file save decryption via PolarSSL.
Per-game `secure_file_id` keys come from `ps3/data/games.conf` (Apollo Save Tool
format, redistributed in the PKG).

**Dreamcast** — keyed by the disc **serial** (the IP.BIN product number), not
by a name slug: `SYNC_ID_RULES["DC"]` is `{"strategy": "serial", "prefix":
"DC_"}`, so a save is `DC_T1249M`. That is what every Dreamcast save device
already files by, so a card save and a Flycast save share one slot and a
mis-named ROM can't split a game in two. `shared/rom_id/dreamcast.py` holds the
canonical form: punctuation stripped, and Sega's `MK` publisher prefix dropped
because the disc says `MK-51000` where the Redump DAT says `51000` — both must
fold onto `DC_51000`. The region suffix is *kept* (`MK-51064-50` -> `DC_5106450`
is the PAL disc, `DC_51064` the NTSC one). Writing back to a device reverses it:
`dc_device_folder_ids()` offers `MK51000` before `51000`, since IP.BIN is what
the card names its folder after. Server-side, `game_names.lookup_dc_serial()`
resolves a ROM name through `Sega - Dreamcast.dat`, and slug-form uploads from
older clients are upgraded at the API boundary by `canonicalize_slug_title_id`;
`server/migrate_dc_serial_ids.py` re-keys saves already stored under a slug.

Every client resolves the serial the same way, mirroring the Saturn plumbing:
`shared/rom_id/dreamcast.py` reads IP.BIN out of the disc (GDI/CDI/ISO/BIN —
never a CHD, which is compressed) and falls back to a DAT name lookup, exposed
as `resolve_dreamcast_title_id()`. Desktop and the Steam Deck scanner call it
directly (`desktop/dreamcast_ipbin.py` is only a shim over it); Android has a
Kotlin port in `emulators/DreamcastSerialDatabase.kt` (`DreamcastDisc`,
`DreamcastSerial`, `DreamcastSerialDatabase`) with the DAT bundled as an asset.
The canonical-serial rule is duplicated in Kotlin — change one and change the
other, or the same game lands in two slots; `DreamcastSerialTest` and
`shared/tests/test_dreamcast.py` assert the same cases on both sides.

Three Dreamcast-only desktop device types build on that. `GDEMU` and `openMenu` install ROMs into numbered
folders at the SD card root (`01` = menu disc, games start at `02`, each folder
gets a `name.txt`); GDEMU cannot read CHD, so a catalog CHD is always fetched as
a converted GDI set. The menu's game list (`LIST.INI` for GDMENU,
`OPENMENU.INI` for openMenu) lives **inside the folder-01 menu image** — openMenu
opens it off its own disc and never reads the card, so a newly installed game is
bootable but invisible until that file changes. Each install regenerates the
list (`desktop/gdemu_menu.py`), reading each folder's IP.BIN header
(`desktop/dreamcast_ipbin.py`) for name/disc/VGA/region/version/date/product and
reusing the previous list for folders whose `name.txt` is unchanged, then writes
it twice: staged at the card root, and patched into the menu image by
`desktop/openmenu_image.py`. That patch is in-place ISO9660 surgery — the menu's
data tracks are 2048-byte-sector `.iso` (no EDC/ECC), the list gets a whole
sector to itself, so only the file's bytes and the both-endian size fields of
its directory records change. A GD-ROM image carries **two** filesystems (low
density, and high density whose directory can point at content in a later
track), so there are normally two copies to keep in step; both are checked for
room before anything is written, and every touched region is backed up under
`desktop/.menu_image_backups/`. Installs also mirror GD MENU Card Manager's
folder layout: `disc.gdi` + `trackNN.*` (never quoted/spaced names) plus the
`name.txt`/`serial.txt`/`disc.txt`/`region.txt`/`version.txt`/`date.txt`/
`vga.txt`/`type.txt`/`folder.txt` caches. "Repair Installed Files" in the ROM
Installer tab applies all of this to games already on a card, and also
renumbers the game folders so `02`, `03`, … run alphabetically — folder number
*is* menu order, since the list is emitted in folder order. The renumber is
two-phase (movers park under `.gs_sort_<n>` first, because a rename needs a
free name) and recovers strays left by an interrupted run; folder `01` never
moves. GDEMU stores no saves. `openMenu` syncs its Serial VMU
backups from a *second* card (the serial SD adapter) at
`OPENMENU/SAVES/<GameID>/SLOT1.VMU`; `MemCard Pro DC` syncs
`Dreamcast/<GameID>/<GameID>-1.vmu`. Slot/channel 1 only, 128 KB raw VMU images
uploaded through `/raw`. `desktop/dreamcast.py` turns a card folder name into
`DC_<serial>` and, for an emulator profile that only knows a filename, maps the
name to its serial through the DAT (falling back to a name slug for a disc the
DAT has never seen). A card scan only ever walks a real `Dreamcast/` or
`OPENMENU/SAVES/` folder — never a bare drive root, since a reader's drive
letter can point anywhere. `MemoryCard1` (the shared card) and `openmenu` (the
menu's own VMU) are skipped.

**Android / Steam Deck** — emulator save locations live in per-emulator scanners
(`android/.../emulators/impl/`, `steamdeck/scanner/`), kept deliberately parallel
so a fix in one ports to the other.

## Licensing and attribution — REQUIRED on every change

GameSync is **GPL-3.0-or-later** (root `LICENSE`). This is not decorative: the
project links PyQt6 under its GPL option, bundles GPL-3.0 `mcr2vmp`, and links
LGPL-3.0 `libmocha`.

**Whenever you write or add code, keep attribution current in the same change.**
Not as a follow-up.

### Adding a dependency

1. **Check license compatibility first.** Only GPL-3.0-compatible licenses may be
   added: MIT, BSD (2/3-clause), Apache-2.0, zlib, ISC, public domain, MPL-2.0,
   LGPL, GPL-3.0. **Reject** GPL-incompatible ones — Academic Free License, CDDL,
   original 4-clause BSD, GPL-2.0-only, and anything proprietary or unlicensed.
   If the only viable library is incompatible, stop and raise it rather than
   adding it quietly.
2. Add it to the **Libraries and frameworks** section of `README.md` Credits.
3. If copyleft (GPL/LGPL/MPL), also add a subsection to `THIRD_PARTY_NOTICES.md`
   explaining how it is linked and why that is compliant, and put its license
   text in `licenses/`.

### Vendoring or copying code

Any code copied into this repo — even a single function — needs all four:

1. A header comment in the file naming the original author, project, URL and
   license.
2. The upstream `LICENSE` preserved alongside it (as `server/third_party/mcr2vmp/`
   does).
3. A row in the **Bundled and vendored code** table in `README.md`.
4. An entry in `THIRD_PARTY_NOTICES.md` if copyleft.

### Porting or adapting

Code written by reading someone else's implementation still needs credit even
though nothing was copied verbatim. Add a file header comment (`Based on analysis
of …`, `Protocol ported from …`) **and** a bullet under **Format documentation
and reverse-engineering references** in `README.md`. Existing examples:
`ps3/source/pfd.c`, `gc/source/saves.c`, `shared/saturn_format.py`.

### Adding data files

DATs, title databases, scraped lists and binaries are **not** covered by
GameSync's license. Add the source to the **Game databases** section of
`README.md` and to the **Redistributed data** table in `THIRD_PARTY_NOTICES.md`,
and record the scrape URL inside the tool that generated it (`tools/scrape_*.py`).

### Adding a new console client

Add its SDK to the **Toolchains and SDKs** table in `README.md`. If the SDK is
not clearly GPL-compatible, extend the **GPLv3 §7 console SDK linking exception**
in the README License section to name it. That exception already covers
devkitARM/PPC, libctru, libnds, dswifi, libfat, libogc, gxflux, wut, PSPSDK,
VitaSDK, PSL1GHT, PS2SDK and nxdk — PS2SDK is the reason it exists (Academic Free
License 2.0 is GPL-incompatible).

### Files that must stay in sync

| File | Holds |
|---|---|
| `README.md` → Credits | Every external author, project, database and tool |
| `THIRD_PARTY_NOTICES.md` | Copyleft deps, SDK exception, redistributed data |
| `LICENSE` | GPLv3 text — do not edit |
| `licenses/` | Third-party license texts: `LGPL-3.0.txt`, `Apache-2.0.txt`, `MIT-mmceman.txt` |

Where a redistributed file ships inside a client package, its notice must ship
with it. `ps3/Makefile` copies `games.conf.LICENSE` into the PKG next to Apollo's
`games.conf` — keep that pattern for any future bundled third-party data.

## General gotchas

- **BaseHTTPMiddleware cannot raise HTTPException** — return `JSONResponse`.
- FastAPI `on_event` is deprecated; use the `lifespan` async context manager.
- devkitPro builds need the **MSYS2 login shell**, not Git Bash.
- PowerShell mangles `$VAR` inside `wsl bash -lc "..."` even single-quoted — use a
  script file (`ps2/tools/build-wsl.sh` exists for this reason).
- Shell scripts run under WSL must be **LF, not CRLF**.
- 3DS title IDs are 16-char hex; the server validates `^[0-9A-Fa-f]{16}$` and
  normalizes to uppercase.
- PS1 saves are keyed by the **in-card code**, not the disc serial.
- `uv sync` in `server/` prunes anything not in the lockfile — don't rely on
  packages installed ad hoc into that venv.
- `CLAUDE.md` is the **only** agent-guidance file — there is deliberately no
  `AGENTS.md`, to avoid two copies drifting apart. It is untracked and not
  gitignored, so git will not recover it if overwritten.
