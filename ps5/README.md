# GameSync — PS5 client

A homebrew app for a jailbroken PS5. It syncs **emulator saves** with a GameSync
server and installs **retro games from the server's ROM catalog** into the PS5
emulators. PS5 game saves are not handled yet.

| Emulator | Title | What syncs | Server endpoint |
|---|---|---|---|
| [PS5_RetroArch](https://github.com/mihawk-99/PS5_RetroArch) | `PPSA99169` | the `.srm` of every ROM in `content/<system>/` or a playlist (USB games too), in `savefiles/<core>/`; Saturn: Beetle Saturn's 32 KB backup RAM (the Mednafen image the server keeps for `SAT_` ids) as its *Save Method* core option says - `<game>.srm` for Libretro (default), `<game>.bkr` for Mednafen, read from `config/Beetle Saturn/<game>.opt`, `<content folder>.opt` or `Beetle Saturn.opt` the way RetroArch does; a card left in the other format is moved over, and with *Shared Internal Memory* on nothing is synced; Dreamcast: Flycast's per-game VMU `<game>.A1.bin` (128 KB raw, like a MemCard Pro DC's) - GameSync sets *Per-Game VMUs* to `VMU A1` in `config/Flycast/Flycast.opt` (Flycast otherwise keeps every game on one `system/dc/vmu_save_A1.bin`); a game whose own `.opt` turns it off isn't synced | `/saves/{id}/raw` |
| PS5_RetroArch's PPSSPP core (PSP) | `PPSA99169` | every slot folder in `PSP/SAVEDATA/` on PPSSPP's memory stick - the core's save folder, `savefiles/PPSSPP/` - as its folder name (`ULUS10551DATA00`), the PSP client's id. A slot only on the memory stick older builds used (`system/PPSSPP/`) is copied over first. PSone Classics are left out | `/saves/{id}` bundle |
| [SwanStationPS5](https://github.com/darkxex/SwanStationPS5) | `PPSA98510` | per-game PS1 cards `saves/<SERIAL>_1.mcd` (or `<game>_1.mcd`, see below) | `/saves/{serial}/ps1-card` |
| [PS5SX2](https://github.com/Swordpdf/PS5SX2) | `PPSA99203` | a card per game, `memcards/<game file>.ps2` (or every game on a shared `Mcd001.ps2`) | `/saves/{serial}/ps2-files` |
| [Porpoise](https://github.com/elripalda/Porpoise-Dolphin-Emulator-for-PS5) (Dolphin) | `PPSA99764` | GameCube `.gci` saves in `saves/User/GC/<region>/Card A/` as `GC_<CODE>`; Wii saves `saves/User/Wii/title/00010000/<hex>/data/` as `WII_<CODE>` | `/saves/{id}/gc-card?format=gci`; `/saves/{id}` bundle |
| [PS5CEMU-HAR](https://github.com/premohq/PS5CEMU-HAR) (Cemu + Azahar + melonDS) | `PPSA99360` | Wii U `mlc01/usr/save/00050000/<lo>/user/` as `00050000<LO>`; 3DS `azahar/sdmc/Nintendo 3DS/0…/0…/title/00040000/<lo>/data/00000001/` as `00040000<LO>` — the Wii U and 3DS clients' slots; DS (4.x) `melonds/saves/<game file>.sav` for each game in its DS games folder, as `00048000` + the hex of the ROM header's gamecode (the DS client's id) | `/saves/{id}` bundle; DS `/saves/{id}/raw` |
| [XPSemu](https://github.com/ZiZc3/XPSemu) (Xbox) | `PPSA97358` | `E:\UDATA\<title id>` read out of its emulated hard disk (`xemu.toml`'s `hdd_path`, default `/data/xemu/xbox_hdd.qcow2`) as the Xbox client's 3DSS bundle. A download writes the folder back into the image (`src/xbox_hdd.c`), after copying it to `xbox_hdd.qcow2.bak` once per run - **quit XPSemu first** | — |
| [ProsperoEden](https://github.com/blackbearreloaded/ProsperoEden) (Switch) | `PPSA99008` | the active profile's saves `user/nand/user/save/0000000000000000/<USER>/<TITLEID>/` as `NSW_<TITLEID>` | `/saves/{id}` bundle |

Saves share slots with every other GameSync client: a RetroArch `.srm` lands in
the same slot as the Steam Deck's or Android's, a SwanStation card in the same
slot as DuckStation's, a PS5SX2 folder in the same slot as a real PS2 card or
a MemCard Pro, a Porpoise `.gci` in the same slot as Dolphin on Android/Steam
Deck or a GameCube/Nintendont card, a Wii save in the same slot as the Wii U
client's vWii saves. Switch saves are new to GameSync: the PS5 client is the
first to sync them, as `NSW_<16-hex title id>` (a bare `0100…` id would be
taken for a 3DS title by the server).

### SwanStation card names

SwanStationPS5's *Memory Card 1 Type* decides the card's file name, and
GameSync reads it the way the app does: the active profile from
`/data/SwanStationPS5/profile.txt` (others live in `profiles/<name>/`, each with its own
`saves/`), the setting `swanstation_MemoryCards_Card1Type` from that
profile's `SwanStationPS5.ini` (`psxs5.ini` in older builds), and a per-game
override from `game-settings/<serial>.ini`. Settings shows what it found.

| Mode | Card file | What GameSync does |
|---|---|---|
| Libretro (default) | `<SERIAL>_1.mcd`, or `<game file>_1.mcd` when the app can't read a serial (any `.chd`) | writes `serial.txt` beside each PS1 game (serial from the catalog) so the card is named after the serial |
| Separate Card Per Game (Game Code) | `<SERIAL>_1.mcd` (the core reads CHDs itself) | syncs it as is |
| Separate Card Per Game (Game Title) | `<game title>_1.mcd` | syncs it (id found by name); a new card is named after the game's file |
| Shared / None | no per-game card | warns, nothing to sync |

Before every scan GameSync also puts each installed PS1 game's card under
the name the current mode reads (the catalog gives each game file's serial):

- a card under the other name is **renamed** when the right name is free;
- it is **set aside as `.bak`** when it's a copy - the same bytes as the card
  in use, or as the server's save (a download made under the wrong name);
- when the two differ from each other *and* from the server, both are left
  as they are and the banner says so (`gamesync.log` names the games): only
  you can tell which one holds the progress you want.

A save state carries the memory card inside it, and loading one puts that
card back in place of the file. So when GameSync downloads a card it also
sets aside that game's quick-resume state (`states/<game id>.resume`, plus
its `.thumb`) as `.bak`; otherwise SwanStation's *Resume* would bring back
the old card, and the game's next save would write it over the synced one.
Numbered state slots are left alone, so loading one of those after a sync
still brings back the card it was made with.

### Translations, hacks and Saturn discs

A RetroArch game's save id comes, in this order, from: the id cached in
`ids.txt`; for Saturn `.cue`/`.bin`/`.iso`, the product code in the disc's
own IP.BIN (`src/saturn.c`, a port of `shared/rom_id/saturn.py`), which a
translation keeps from the original; the catalog row with the same file name
(a `.chd` can't be read, but the catalog knows the serial of what it
installed); and last the server's `/catalog/resolve`. Square-bracket tags
aren't part of an id (`shared/rom_id/normalizer.py`), so `Grandia (Japan)
[T-En v1.1]`, its v0.9.3 and the untranslated disc all use `SAT_T-4507G`, and
`Chrono Trigger (Japan) [T-En …]` uses the original's `SNES_chrono_trigger_japan`.
`ids.txt` records the rules it was made under (`_rules`); older caches are
resolved again.

### Saves without a game here

The Saves tab lists every server save for a system the PS5 emulators play,
not only those with a local copy. A RetroArch save is named after its ROM, so
for one without a ROM here GameSync takes the file name from the catalog row
with the same id (the file it would install); with no such row it can't be
downloaded yet. *Sync all* only fetches saves for games that are installed.
A PSP save slot has no ROM name, so it counts as installed when a game file
in `content/psp/` (or an installed catalog row) has the name the server gave
the save ("Ys Seven (USA)").

For a save whose game the catalog has but the PS5 doesn't (matched by title
id, else by system and name - a PS2 CHD's catalog id can be a name while its
save goes by serial), *Details* offers **Install the game and sync the save**
and **Install the game only**, as the MiSTer client does; Cross on such a
save opens the same choice. The game joins the Downloads queue; once it has arrived GameSync rescans and
downloads the save into the place the emulator reads for that game. A save
that turns out to conflict by then is left for you to pick in *Details*.

### RetroArch save locations

The port puts a save where its own WebUI expects it (`src/webui_ps5.cpp`):
`savefile_directory` (default `savefiles/`), then the ROM's folder name when
`sort_savefiles_by_content_enable` is on, then the core's name (`Snes9x`,
`mGBA`, from `info/<core>.info`) unless `sort_savefiles_enable = "false"` — on
this port sorting by core is the default. `savefiles_in_content_dir` puts it
beside the ROM. A save already sitting one folder below `savefiles/` is found
whatever the settings, so switching cores doesn't hide it.

## Requirements on the console

- Something that can grant the title `/data` access. The app tries, in order:
  1. a HEN's jailbreak request — **etaHEN** (with *Network and Legacy CMD
     server* on in its toolbox), **LegacyJB**, or **OnionHEN**/**PS5SX2
     Helper** (the latter only for title ids in `/data/whitelist.txt` — add
     `PPSA90543`);
  2. the bundled **Lapy** helper (`lapy.elf`), sent to the ELF loader on
     port 9021 — works with just kstuff + elfldr running. Upstream validated it
     on 12.02; other firmware is untested.

  Without either the app still runs but can't list the emulator folders;
  Settings shows what each route answered.
- [ShadowMountPlus](https://github.com/drakmor/ShadowMountPlus) (or another
  loader that mounts `/data/homebrew/<TITLE>/` titles on the home screen).
- The emulators you want to use. Installed anywhere ShadowMountPlus looks is fine
  (by hand in `/data/homebrew`, or by [ProsperoStore](https://github.com/blackbearreloaded/ProsperoStore)
  in `/data/etaHEN/games`): GameSync finds them, see *Emulator folders*.

## Install

1. Build (below) or take `ps5sync.zip` from `build_output/`.
2. Extract it to `/data/homebrew/` over FTP, so the app is at
   `/data/homebrew/PPSA90543/`, and `chmod -R 777` that folder.
3. Reboot or let ShadowMountPlus pick it up; *GameSync* appears on the home screen.

## Setup

Open **Settings** (R1 twice), set **Server URL** (`http://<server IP>:8000` —
an IP address, the PS5 build doesn't resolve host names) and **API key** with
the on-screen keyboard, then **Test connection**.

Everything is stored in `/data/gamesync/`, so it can also be edited over FTP:

```ini
server_url=http://192.168.1.10:8000
api_key=your-key
console_id=ps5_1a2b3c4d          # generated on first start
retroarch_dir=auto
swanstation_dir=auto
ps5sx2_dir=auto
porpoise_dir=auto
eden_dir=auto
cemu_dir=auto
xemu_dir=auto
ps2_cards=per_game               # PS5SX2: per_game, or shared (every game on ps2_card)
ps2_card=Mcd001.ps2              # the shared card
ps1_target=swanstation           # PS1 catalog installs: swanstation or retroarch
```

### Emulator folders

With `auto` (the default) GameSync finds each emulator on the console at start
and on *Settings > Rescan saves and games*. It looks for the title folder
(`PPSA99169`, `PPSA99764`...) in `/data/etaHEN/games` (where ProsperoStore
installs), `/data/homebrew`, and the same two folders on USB (`/mnt/usbN`) and
extended storage (`/mnt/extN`), including one wrapper folder deeper (a release
zip extracted as-is). RetroArch keeps its games, saves and settings in its
title folder, so that's the folder used. SwanStation (`/data/SwanStationPS5`, or `/data/PSXS5` for builds before the rename), PS5SX2
(`/data/PCSX2`), Porpoise (`/data/porpoise`, or `<title>/porpoise` when it runs
sandboxed) and ProsperoEden (`/data/prosperoeden`) keep their data at fixed
paths. A path instead of `auto` overrides the search. Settings lists each
folder and marks the ones not found.

Other files there: `state.txt` (hash of each save's last synced version),
`ids.txt` (save ids the server assigned to RetroArch ROMs), `installed.txt`
(games installed from the catalog), `gamesync.log`.

## Use

The layout and controls are the ones every GameSync client shares (see
`steamdeck/README.md`): five tabs, a chip row to pick the system, a detail panel
beside the list, a status banner and button hints along the bottom.

| Button | Everywhere |
|---|---|
| L1 / R1 | previous / next tab: Saves, Catalog, Installed, Downloads, Settings (wraps) |
| L2 / R2 | previous / next system chip (All, PS1, PS2, SNES, ...); one choice for every tab (a tab without that system keeps its own), remembered between runs in `ui.txt` |
| D-pad / left stick Up/Down | move a row (hold repeats, faster after a second) |
| D-pad Left/Right | page (hold repeats, faster after a moment; after 1.5 s it jumps from initial letter to initial letter) |
| Cross | the row's main action |
| Circle | cancel only: closes dialogs, clears a search, pauses a download |
| Square | the tab's secondary action |
| Triangle | details (Saves) or search (Catalog, Installed) |
| Create | Saves status filter |
| Options | exit (asks first) |

| Tab | Cross | Square | Triangle |
|---|---|---|---|
| Saves | sync: upload or download as the status says; conflicts open details, and so does a server save whose game isn't installed | Sync all | details, with force upload / download, and for a game that isn't here *Install the game and sync the save* / *Install the game only* |
| Catalog | queue the download (stays on the Catalog) | RetroAchievements only / all | search |
| Installed | delete (asks first) | rescan | search |
| Downloads | pause / resume / retry | clear finished | remove |
| Settings | change | | |

Save statuses match the other clients: Synced, Upload, Download, Local Only,
Server Only, Conflict. The Saves list shows what this console has - a save, or
the game - first, then saves only on the server. Create steps the filter through All, Needs Action,
Upload, Download, Conflict, Synced, Local Only and Server Only.

Sync decisions use GameSync's three-way rule (this console's hash, the server's,
and the hash of the last version this console synced). A save changed on both
sides — or one that was never synced here and differs — is a **conflict**:
Triangle picks a side; *Sync all* leaves it alone.

*Sync all* downloads saves for games this console has (RetroArch ROMs, and
PS1/PS2 games installed from the catalog). Other PS1/PS2 saves on the server
are listed as *Server Only* and downloaded one at a time with Cross — a PS2 save
goes onto a shared 8 MB card, so they aren't added unasked.

Catalog rows carry the server's RetroAchievements data: a gold **RA** badge
when RA recognises that very dump (exact hash, or a file name RA registered), a
dim **RA?** when it only matched by name. The catalog is cached per system in
`/data/gamesync/catalog/` against the server's `/roms/fingerprints`, like the
MiSTer and Steam Deck clients: only systems that changed are refetched, and the
cached copy is shown when the server is unreachable. *Settings > Refresh
catalog* asks the server to rescan and refetches everything.

Downloads run in the background on their own thread; the tab label shows how
many are active. Pausing keeps the `.part` file, and resuming continues it with
an HTTP Range request.

### PS2 cards

By default every PS2 game gets **its own card**, named after the game file the
way SwanStation's *Game Title* cards are: `memcards/<game file>.ps2`. PS5SX2
reads a game's own settings from `settings/<game file>.ini` when it starts the
game, so GameSync writes the card there itself
(`MemoryCards/Slot1_Filename=<game file>.ps2`, and `Slot1_Enable=true` in
case the slot was switched off); nothing to set up in the emulator. PS5SX2
uses `memcards/` only when that folder exists, otherwise the cards in
`/data/PCSX2` itself; GameSync follows the same rule and makes `memcards/`
when there's no card in either place. For each installed game, on every scan and right after a catalog
install, GameSync:

- makes the card (blank, formatted, 8 MB with ECC) when it isn't there;
- copies the game's save over from a shared card (`Mcd001.ps2`, `Mcd002.ps2`
  or `ps2_card`) when its own card doesn't have one yet; the shared card is
  left as it was;
- leaves a card you picked yourself for the game (any card other than the
  shared ones) as it is, and syncs that one.

PS5SX2 takes every game's compatibility fixes from PCSX2's game database,
`/data/PCSX2/resources/GameIndex.yaml` (Star Ocean 3, for one, crashes at
boot without its `VuAddSubHack`). PS5SX2's own installer puts it there; an
install of just the app (ProsperoStore) doesn't. So on every scan, when
`/data/PCSX2` exists and that file doesn't, GameSync downloads it from the
server (`GET /api/v1/emulators/ps5sx2/game-index`, the latest PS5SX2
release's copy, which the server fetches from GitHub and caches) and checks
its hash before writing it. A copy already there is never replaced.

A server save finds its game by serial through PS5SX2's own record of each
image's serial (`chd-serials.txt`), since the catalog often has only a name
for a PS2 CHD; so start PS5SX2 once after installing a game. A save downloaded
for a game that isn't installed yet goes to the card named after the catalog's file, and that game's settings are pointed at it. A game's
own card wins over a copy of its save on a shared card. *Settings > PS5SX2
memory cards* switches to one shared card for every game (`ps2_cards=shared`).

To change one game the client parses the
card, swaps that game's folder and writes a freshly built card (same format,
`.ps2` with ECC or `.mc2` without); the previous image is kept as
`<card>.bak`. Card code: `src/ps2mc.c`, a port of the server's
`server/app/services/ps2mc.py` (cross-checked by `tests/test_ps2mc.c`). If one
game has several folders (`BASLUS-20312`, `BASLUS-20312SAVE2`), only the first
is synced — the server keeps one folder per serial.

### Catalog installs

| System | Goes to | Formats |
|---|---|---|
| PS1 | `/data/SwanStationPS5/games/<game>/<file>` (or RetroArch with `ps1_target=retroarch`) | `.chd`, `.pbp`, `.iso` |
| PS2 | `/data/PCSX2/games/<file>` | `.iso`, `.chd`, `.cso`, `.zso` |
| GC / Wii | `<porpoise_dir>/games/<file>` (Porpoise) | `.rvz`, `.iso`, `.wbfs`, `.gcm`, `.ciso`, `.gcz`, `.wia`, `.wad`; from a zipped game only the game file comes down (`/roms/{id}/file/<member>`, resumable when the zip stores it) |
| Wii U | `<cemu_dir>/games/<file>` (PS5CEMU) | `.wua`, `.wud`, `.wux`, `.rpx`, `.wuhb`; a WUP/NUS or loadiine folder goes to `games/<game>/` file by file from `/roms/{id}/manifest` + `/file/{name}` (each resumes; `.gamesync-complete` marks a whole set). PS5CEMU runs such an NUS set as it is (Cemu has the key for `title.tik`), but only lists it after a scan: *Settings > Look for games now* on its Wii U side |
| NDS | PS5CEMU's DS games folder (`ps5cemu.json` `nds.gamesFolder`, default `<cemu_dir>/melonds/games`) | `.nds`, `.srl`, `.dsi`; a `.zip`'s game file comes out of it as `<zip name>.nds` (melonDS doesn't open zips) |
| 3DS | `<cemu_dir>/azahar/games/<file>` (PS5CEMU) | `.3ds`, `.cci`, `.cxi`, `.3dsx`…; a zipped or encrypted dump comes as the decrypted `.cci` the server makes (`?extract=decrypted_cci`, its `SYNC_ROM_3DS_DECRYPTED_CCI_COMMAND`); a zip with no `.3ds`/`.cci` inside (a `.cia` or homebrew) can't be converted |
| Xbox | `<xemu_dir>/games/<file>` (XPSemu) | `.iso`/`.xiso` (XISO); a game folder (`.cci` + `default.xbe`) comes as the XISO the server makes (`?extract=iso`, its `SYNC_ROM_XBOX_ISO_COMMAND`); the server caches it, so the download resumes. (`src/xcci.c` decodes a CCI here byte-identically to XGDTool, but the app's write throttling made that crawl; it waits for the helper.) |
| GameCube, Wii | `<porpoise_dir>/games/<file>` (Porpoise) | `.iso`, `.rvz`, `.gcm`, `.ciso`, `.gcz`, `.wbfs`, `.wia` |
| Switch | `<eden_dir>/roms/<file>` (ProsperoEden) | `.nsp`, `.xci` (the server doesn't list Switch ROMs yet) |
| everything RetroArch plays | `<retroarch_dir>/content/<system>/<file>` | as served |

Downloads resume (`.part` + HTTP Range). Multi-file games (zipped `.cue`/`.bin`
sets, MSU packs) aren't installed by this version.

### Multi-disc games

The catalog lists a multi-disc game once ("3 discs"), grouped the way
`shared/mister_install.group_discs` does it for the MiSTer: rows of a CD system
(and PS2, GameCube) whose names differ only by a `(Disc N)` / `[Disk N of M]`
tag. Every other tag is part of the name, so a `[T-En]` translation, a revision
or a single-disc game's patched copy stays its own entry and only that version
installs. Cross queues every disc, in order, into one folder named without the
disc tag:

| Emulator | Layout | Loads it by |
|---|---|---|
| SwanStation | `games/<game>/<discs>` + `<game>.m3u` | the `.m3u`; the card is disc 1's serial (`serial.txt`, written with disc 1) |
| RetroArch | `content/<system>/<game>/.discs/<discs>` + `<game>.m3u` | the `.m3u` (the dot folder keeps the discs off its list); the save is `<game>.srm`, the id disc 1's |
| PS5SX2 | `games/<game>/<discs>` | any disc; it swaps between `(Disc N)` siblings, and every disc's `settings/<file>.ini` points at one card, `<game>.ps2` |
| Porpoise | `games/<game>/<discs>` | each disc on its own (it can't change discs); GameCube saves are per game id anyway |

The `.m3u` is written once the last disc is in (`catalog_group_finish`).

### Large downloads: the helper payload

The PS5 throttles an app's writes: about 1.3 GB at ~210 MB/s, then ~2 MB/s
until it has been idle a while (pausing a download let it refill). A disk
test showed a payload process writing to the same `/data` unaffected,
~215 MB/s for 4 GB. So the app carries a helper payload
(`helper/main.c`, built with PS5 Payload SDK v0.44 by `build.sh` and compiled
into the eboot as `helper_elf.c` - reading it back from `/app0/assets` failed
on a console), and hands it every catalog download:

- the app sends it to the ELF loader on port 9021 the first time it's needed
  (as it does the Lapy helper); it listens on `127.0.0.1:9135` only and quits
  after an hour idle, its log in `/data/gamesync/helper.log`;
- one connection per download: the request on one line, progress lines
  back, a result line (`src/helper_client.c` has the protocol); closing the
  connection - a pause - stops it at once, and a new job for a file still
  being written waits for the old one;
- the download itself is the app's own code (`src/fetch.c`: 128 MB Range
  segments, the real size from `Content-Range`), compiled into both;
- without a helper (no loader, the PC build) the app downloads itself.

`<data>/bench.request` (made over FTP) makes the app run its disk test at
start-up (`src/diskbench.c`), results in `gamesync.log`.

## Build

From WSL (Ubuntu 24.04) with `clang-18 lld-18 llvm-18 make ninja-build
python3 git curl wget unzip pkg-config`:

```bash
bash ps5/build.sh          # -> ps5/dist/ps5sync.zip and ps5/dist/PPSA90543/
build_all.bat ps5          # same, from Windows; copies the zip to build_output/
```

`build.sh` fetches [ps5-native-app-boilerplate](https://github.com/BlackBearReloaded/ps5-native-app-boilerplate)
at a pinned commit into `~/.cache/gamesync-ps5` (override with `PS5_WORK`); the
boilerplate in turn fetches the pinned PS5 Payload SDK and the PacBrew SDL2
sysroot. The version comes from the root `VERSION` file.

## Tests

```bash
bash ps5/tests/run_host_tests.sh    # needs cc and a Python that imports server/app
```

`src/platform_host.c` builds the whole UI on a PC without a window: buttons come
from a script and frames are written as PPM screenshots (see the file header),
which is how the UI can be exercised against a local server.

## Layout

| File | Purpose |
|---|---|
| `src/main.c`, `src/ui.c` | start-up, the three tabs, dialogs, keyboard |
| `src/saves.c` | finds each emulator's saves |
| `src/saturn.c` | a Saturn disc's product code from its IP.BIN |
| `src/xcci.c` | Xbox CCI images to XISO (LZ4 sectors) |
| `src/fetch.c` | a server path into a file, in Range segments (app and helper) |
| `src/helper_client.c` | hands downloads to the helper payload, or does them |
| `src/diskbench.c` | the on-request disk write test |
| `helper/main.c` | the helper payload (built with the PS5 Payload SDK v0.44) |
| `src/retroarch.c` | RetroArch games (content folders, playlists) and their save paths |
| `src/bundle.c` | save folders and the 3DSS bundle format (Wii, Switch) |
| `src/sync.c` | server comparison, three-way decisions, transfers |
| `src/catalog.c` | ROM catalog, per-system cache, search, install plans |
| `src/downloads.c` | background download queue (one worker thread) |
| `src/installed.c` | games installed in the emulators |
| `src/ps2mc.c` | PS2 card parse/build, ECC, P2FD |
| `src/naming.c` | systems, folders, SwanStation card names |
| `src/http.c`, `src/json.c`, `src/config.c`, `src/util.c` | plumbing |
| `src/gfx.c`, `src/font_data.c` | CPU canvas and text (Vegur atlas from `tools/gen_font.py`) |
| `src/platform_ps5.c`, `src/ps5_*.c` | VideoOut, SDL2 controllers, `/data` unlock, heap (adapted from SwanStationPS5) |
| `src/elevation*.cpp`, `src/protocol.hpp` | Lapy helper client (from ps5-native-app-boilerplate) |
| `src/platform_host.c` | host preview build |
