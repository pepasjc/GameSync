# GameSync

Sync save files between consoles, handhelds, and emulators through a self-hosted local server.

## Repository Structure

| Folder | Description |
|---|---|
| `server/` | FastAPI server — stores saves and history |
| `3ds/` | Nintendo 3DS homebrew client |
| `ds/` | Nintendo DS / DSi homebrew client |
| `wiiu/` | Wii U homebrew client (Aroma) — GameCube/Nintendont, vWii and Wii U saves; GC/Wii/Wii U game catalog to SD or FAT32 USB |
| `psp/` | PSP homebrew client |
| `vita/` | PS Vita homebrew client |
| `ps2/` | PlayStation 2 homebrew client — ⚠️ work in progress, not functional yet |
| `ps3/` | PS3 homebrew client |
| `android/` | Android app |
| `steamdeck/` | Steam Deck / Linux desktop client |
| `desktop/` | Windows/macOS desktop client (PyQt6) |

Each client folder has its own README with build instructions and setup details.

## Server Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
cd server
uv sync
uv run python run.py
```

Configuration is done via environment variables:

| Variable | Default | Description |
|---|---|---|
| `SYNC_API_KEY` | `dev-key-change-me` | API key required by all clients |
| `SYNC_SAVE_DIR` | `./saves` | Directory where saves are stored |
| `SYNC_HOST` | `0.0.0.0` | Bind address |
| `SYNC_PORT` | `8000` | Server port |
| `SYNC_MAX_HISTORY_VERSIONS` | `5` | Number of previous save versions to keep |

```bash
# Linux / macOS
SYNC_API_KEY=your-secret-key uv run python run.py

# Windows
set SYNC_API_KEY=your-secret-key
uv run python run.py
```

## How It Works

1. Start the server on any PC on your local network.
2. Configure each client with the server URL and API key.
3. Clients upload saves to the server and download them on other devices.
4. A three-way hash protocol detects which side changed since the last sync, avoiding conflicts across multiple consoles.
5. The server keeps a configurable history of previous save versions.

## Documentation

- [docs/sync-profiles.md](docs/sync-profiles.md)
- [docs/technical-overview.md](docs/technical-overview.md)
- [docs/ps1-sync.md](docs/ps1-sync.md)
- [docs/ps3-sync.md](docs/ps3-sync.md)

## Credits

GameSync stands on a large amount of prior work from the homebrew, emulation and
preservation communities. Thanks to everyone below.

### Toolchains and SDKs

| Project | Used by | Authors |
|---|---|---|
| [devkitPro](https://devkitpro.org/) — devkitARM / devkitPPC, `libctru`, `libnds`, `libogc`, `wut`, portlibs | `3ds/`, `ds/`, `gc/`, `wiiu/` | WinterMute and the devkitPro contributors |
| [libctru](https://github.com/devkitPro/libctru) | `3ds/` | Smealum, fincs, devkitPro contributors |
| [libnds](https://github.com/devkitPro/libnds) | `ds/` | Dave Murphy (WinterMute), Dovoto and contributors |
| [dswifi](https://github.com/devkitPro/libnds) | `ds/` | Stephen Stair (sgstair) |
| [libfat](https://github.com/devkitPro/libfat) | `ds/`, `gc/` | Michael Chisholm (chishm), Dave Murphy |
| [libogc](https://github.com/devkitPro/libogc) | `gc/` | Michael Wiedenbauer (shagkur), Dave Murphy |
| [gxflux](https://github.com/Extrems/) | `gc/` | Extrems |
| [wut](https://github.com/devkitPro/wut) | `wiiu/` | James Benton (exjam) and contributors |
| [libmocha](https://github.com/wiiu-env/libmocha) + [MochaPayload](https://github.com/wiiu-env/MochaPayload) / [EnvironmentLoader](https://github.com/wiiu-env/EnvironmentLoader) | `wiiu/` | Maschell and the wiiu-env contributors (LGPL-3.0) |
| [pspdev / pspsdk](https://github.com/pspdev/pspdev) | `psp/` | The pspdev contributors |
| [VitaSDK](https://vitasdk.org/) | `vita/` | The VitaSDK contributors |
| [PSL1GHT / ps3dev](https://github.com/ps3dev/ps3dev) | `ps3/` | The ps3dev contributors |
| [PS2SDK / ps2dev](https://github.com/ps2dev/ps2sdk) | `ps2/` | ps2dev.org, Marcus R. Brown and contributors |
| [nxdk](https://github.com/XboxDev/nxdk) | `xbox/` | The XboxDev contributors |

### Bundled and vendored code

| Component | Where | Credit |
|---|---|---|
| `mcr2vmp` — PS1 `.mcd`/`.mcr` ↔ `SCEVMC*.VMP` converter | `server/third_party/mcr2vmp/` | [vita-mcr2vmp](https://github.com/dots-tb/vita-mcr2vmp) by **@dots_tb**, with help from the CBPS community (**@AnalogMan151**, **@teakhanirons**), packaged via [chrisbrasington/psp_psx_save_sync](https://github.com/chrisbrasington/psp_psx_save_sync). **GPLv3** — see `server/third_party/mcr2vmp/LICENSE`. |
| [tiny-AES-c](https://github.com/kokke/tiny-AES-c) | `server/third_party/mcr2vmp/aes.c` | **kokke** and contributors (public domain) |
| SHA-1 | `server/third_party/mcr2vmp/sha1.c` | **Steve Reid** (public domain) |
| SHA-256 (FIPS 180-4) | `3ds/`, `ds/`, `gc/`, `ps2/`, `psp/`, `vita/`, `wiiu/`, `xbox/` `sha256.c` | Public-domain reference implementation by **Brad Conte** ([B-Con/crypto-algorithms](https://github.com/B-Con/crypto-algorithms)) |
| `mmceman.irx` — MMCE (MemCard PRO2 / SD2PSX) IOP driver | `ps2/irx/` | [ps2-mmce/mmceman](https://github.com/ps2-mmce/mmceman) — "MMCE Authors" (MIT). Rebuilt locally with an added gen1 MemCard Pro `0x21` GameID command. |
| Debug screen font | `vita/source/debugScreenFont.c` | PSPSDK debug font — **Marcus R. Brown**, **James Forshaw**, **John Kelley** (BSD) |
| `zlib` | `3ds/`, `psp/`, `ps3/`, `wiiu/` | **Jean-loup Gailly** and **Mark Adler** |
| PolarSSL / mbedTLS (AES-128-CBC, HMAC-SHA1) | `ps3/` (via PSL1GHT) | The PolarSSL / mbed TLS authors |

### Format documentation and reverse-engineering references

No code was copied from these; they documented the binary formats this project
reads and writes.

- **PS1 memory card filesystem** (`server/app/services/ps1mc.py`) — [PSXSPX / no$psx docs](https://problemkaputt.de/psx-spx.htm) by **Martin Korth**.
- **PS2 memory card filesystem** (`server/app/services/ps2mc.py`) — [ps2savetools.com](http://www.ps2savetools.com/) documentation and [mymc](http://www.csclub.uwaterloo.ca:11068/mymc/) by **Ross Ridge**; also [mymc+](https://git.sr.ht/~thestr4ng3r/mymcplus) by **Florian Märkl** and [mymc++](https://pypi.org/project/mymcplusplus/) used as a cross-check reference.
- **GameCube memory card filesystem** (`server/app/services/gc_cards.py`, `gc/source/vmcfs.c`) — layout as documented by the **Dolphin** project and mymc.
- **PS3 `PARAM.PFD` resigning / save decryption** (`ps3/source/pfd.c`, `ps3/source/decrypt.c`) — based on analysis of [Apollo Save Tool](https://github.com/bucanero/apollo-ps3) by **Damián Parrino (bucanero)** and **flatz**'s `pfd_sfo_tools`. `ps3/data/games.conf` is Apollo's per-game key database (Copyright © 2020-2025 Damian Parrino, GPL-3.0-or-later), redistributed verbatim (1818 titles) and shipped inside `ps3sync.pkg` with its notice — thanks also to **SHAkA** and the other key finders credited in that file's header.
- **MemCard Pro GC GameID over EXI** (`gc/source/saves.c`) — protocol ported from `mmce.c` in **libogc2** by **Extrems**, cross-checked against **Swiss**.
- **gen1 MemCard Pro `0x21` GameID command** (`ps2/`) — [jdfr228/PS1-Disc-Based-Game-ID](https://github.com/jdfr228).
- **Saroo `SS_SAVE.BIN` Saturn saves** (`shared/saturn_format.py`) — [SAROO](https://github.com/tpunix/SAROO) by **tpunix** and [save-file-converter](https://github.com/euan-forrester/save-file-converter) by **Euan Forrester**.
- **3DS NCSD/NCCH header layout** (`server/app/services/ctr_rom.py`) — [ninfs / pyctr](https://github.com/ihaveamac/ninfs) and `3dsconv` by **ihaveamac**.
- **MSU-1 / MSU-MD / MD+ pack layouts** (`shared/msu.py`, `shared/mister_install.py`) — file naming and per-core placement as documented by the [MiSTer MegaCD core docs](https://mister-devel.github.io/MkDocs_MiSTer/cores/highlights/megacd/) (`cart.rom` for MSU-MD), the [Zeldix MD+ emulator guide](https://www.zeldix.net/t2175-md-32x-emulators-mister-fpga-genesis-plus-gx-picodrive-mame) (WAVE-track MD+ in the MegaDrive core and Genesis Plus GX), and **ArcadeTV**'s [MSU-MD patches wiki](https://arcadetv.github.io/msu-md-patches/); MSU-1 itself is **byuu / Near**'s design. No code copied.
- **Wii U WUP/NUS installable layout** (`server/app/services/rom_scanner.py`) — `title.tmd`/`title.tik`/`.app`/`.h3` structure and the TMD content flags (`0x0001` encrypted, `0x0002` hashed) as documented by [wiiubrew](https://wiiubrew.org/wiki/Title_metadata).
- **Wii U MCP title installation** (`wiiu/source/install.c`) — the `MCP_InstallSetTargetDevice` → `MCP_InstallTitleAsync` → `MCP_InstallGetProgress` sequence, and the 0x40-byte heap alignment IOS requires for those structs, based on analysis of [WUP Installer GX2](https://github.com/Willyanto/wup-installer-gx2) by **Dimok**, **Maschell** and contributors (GPL-2.0-or-later). No code copied.
- **FAT32 USB mounting on Wii U** (`wiiu/source/natives.c`) — the `/dev/usb01` → `/vol/usb` FSA mount that Tiramisu/Aroma homebrew uses to reach a non-WFS drive, via [libmocha](https://github.com/wiiu-env/libmocha) by **Maschell**.
- **Wii U download pipeline and install** (`wiiu/source/iowrite.c`, socket tuning in `wiiu/source/http.c`, `title.cert` template and exit sequence in `wiiu/source/install.c` / `appstate.c`) — based on analysis of [NUSspli](https://github.com/V10lator/NUSspli) by **V10lator** and **Pokes303** (GPL-3.0-or-later). No code copied.
- **GDEMU / openMenu SD card layout** (`desktop/rom_installer.py`, `desktop/gdemu_menu.py`) — the numbered game folders (`01` = menu disc), `name.txt` labels, GDI-only image support and the `LIST.INI` / `OPENMENU.INI` game-list format (`NN.name`/`.disc`/`.vga`/`.region`/`.version`/`.date`/`.product`), as generated by [GDMENU Card Manager](https://github.com/sonik-br/GDMENUCardManager) by **sonik-br**. GameSync regenerates that list on every install, stages it at the card root and patches it into the menu image in place.
- **Dreamcast IP.BIN disc header** (`shared/rom_id/dreamcast.py`, ported to Kotlin in `android/.../DreamcastSerialDatabase.kt`) — field offsets as decoded by [Aaru](https://github.com/aaru-dps/Aaru)'s `Aaru.Decoders.Sega.Dreamcast` (MIT) by **Natalia Portillo**, read via GDMENU Card Manager's vendored copy. Offsets only, no code copied.
- **openMenu game list loading** (`desktop/gdemu_menu.py`, `desktop/openmenu_image.py`) — that `OPENMENU.INI` is read off the booted menu disc, not the SD card, per [openMenu](https://github.com/mrneo240/openmenu)'s `backend/gd_list.c` by **Hayden Kowalchuk (mrneo240)**; the `.folder` / `.type` item keys come from the [openMenu Virtual Folder Bundle](https://github.com/DerekPascarella/openMenu-Virtual-Folder-Bundle).
- **ISO9660 directory records** (`desktop/openmenu_image.py`) — extent/size both-endian fields and file identifiers per ECMA-119 §9.1, used to rewrite the game list inside a menu image without rebuilding it.
- **openMenu Serial VMU layout** (`desktop/sync_engine.py`, `desktop/dreamcast.py`) — the `OPENMENU/SAVES/<GameID>/SLOT1.VMU` + `TITLE.TXT` per-game virtual VMU structure, as documented by the [openMenu Virtual Folder Bundle](https://github.com/DerekPascarella/openMenu-Virtual-Folder-Bundle) by **Derek Pascarella**, building on **openMenu** by **mrneo240**.
- **MemCard PRO DC virtual VMU layout** (`desktop/sync_engine.py`, `desktop/dreamcast.py`, `shared/rom_id/dreamcast.py`) — the `Dreamcast/<GameID>/<GameID>-<channel>.vmu` 128 KB card layout and GameID naming (IP.BIN product number, separators stripped), as documented by the [8BitMods wiki](https://www.8bitmods.wiki/importing-saves).
- **RetroAchievements game hashes** (`shared/ra_hash.py`) — the per-system MD5 rules (iNES/FDS, SNES copier, PC Engine, A78 and LNX header stripping, N64 byte-order normalisation, DS header + ARM9/ARM7 + icon block, arcade romset-name hashing with FBNeo sub-system folders) ported from [rcheevos](https://github.com/RetroAchievements/rcheevos) `src/rhash/hash.c` and `hash_rom.c` by the **RetroAchievements** team (MIT). `shared/ra_api.py` looks those hashes up through RA's public `dorequest.php` `hashlibrary` / `gameslist` endpoints (the same ones RAIntegration uses), or through the [RetroAchievements Web API](https://api-docs.retroachievements.org/) `API_GetGameList` when a key is configured — that one also carries each game's achievement count. Used by the desktop RetroAchievements tab and by the server's catalog indexer (`server/app/services/ra_index.py`), which is what puts the RA badge on the MiSTer, Steam Deck and Android ROM lists. No code copied.
- **CHD (Compressed Hunks of Data) v5** (`shared/chd.py`) — header layout, the entropy-coded hunk map (RLE code-length table, canonical Huffman, bit-packed lengths and offsets) and the CD hunk structure (ECC bitmap, separately compressed sector and subcode streams), as implemented by [libchdr](https://github.com/rtissera/libchdr) by **Romain Tisserand** (BSD-3-Clause) and MAME's `chd.c`, which it derives from. Structure and field offsets only — an independent Python implementation, no code copied. Only the hunks a caller asks for are decompressed, which is what makes reading a boot executable out of a 700 MB image cost a tenth of a second.
- **RetroAchievements disc hashes** (`shared/ra_disc.py`) — the PlayStation rule (`SYSTEM.CNF` → `BOOT=` → the boot executable, hashed as its name followed by the `PS-X EXE` header and payload) and the ISO9660 walk beneath it, ported from [rcheevos](https://github.com/RetroAchievements/rcheevos) `src/rhash/hash_disc.c` by the **RetroAchievements** team (MIT). Rewritten in Python from reading that implementation; no code copied.
- **OSScreen geometry** (`wiiu/source/ui.c`) — the 16x24 font cell, framebuffer sizes and colour format as documented by [Cemu](https://github.com/cemu-project/Cemu)'s coreinit OSScreen implementation (MPL-2.0).

### Game databases

- [libretro-database](https://github.com/libretro/libretro-database) and [libretro-dats](https://github.com/RobLoach/libretro-dats) / [libretro-database-gametdb](https://github.com/RobLoach/libretro-database-gametdb) by **Rob Loach** — the DAT files in `server/data/dats/`.
- **[No-Intro](https://no-intro.org/)** and **[Redump](http://redump.org/)** — the upstream cartridge and disc datasets those DATs are built from.
- **[GameTDB](https://www.gametdb.com/)** — Wii / Wii U / GameCube title metadata.
- **[Pleasuredome](https://pleasuredome.miraheze.org/)** — the MAME DAT.
- **The [T-En] Collection** — fan-translation DATs in `server/data/dats/EN-Dats/`.
- **[wiiubrew](https://wiiubrew.org/wiki/Title_database)** — Wii U title-id database, imported by `tools/enrich_wiiu_dat_titleids.py`.
- **[3dsdb.com](https://3dsdb.com/)** and **[ds-scene.net](https://www.ds-scene.net/)** — 3DS / DS release lists (`server/data/3dsdb.txt`, `3dstdb.txt`).
- **[dbox.tools](https://dbox.tools/)** — original Xbox title-id database (`server/data/xbox_titleids.json`).
- **[Serial Station](https://serialstation.com/)** — PlayStation serial lookups (`server/app/services/serialstation.py`).

### External tools invoked or recommended

- **[chdman](https://www.mamedev.org/)** — CHD compression (MAMEdev, part of `mame-tools`).
- **[Wiimms ISO Tools (`wit`)](https://wit.wiimm.de/)** by **Dirk Clemens** — Wii/GameCube disc conversion.
- **[`dolphin-tool`](https://dolphin-emu.org/)** — the Dolphin team's disc converter.
- **[maxcso](https://github.com/unknownbrackets/maxcso)** by **Unknown W. Brackets** — CSO compression.
- **[pop-fe](https://github.com/sahlberg/pop-fe)** by **Ronnie Sahlberg** — PS1 → PSP/PS3 EBOOT packaging.
- **[Apktool](https://github.com/iBotPeaches/Apktool)** by **Connor Tumbleson (iBotPeaches)** — bundled as `tools/apktool.jar` for `tools/patch_duckstation.py` (Apache-2.0).
- **[Project_CTR / makerom](https://github.com/3DSGuy/Project_CTR)** by **jakcron (3DSGuy)** and **[bannertool](https://github.com/Steveice10/bannertool)** by **Steveice10** — 3DS CIA packaging.
- **[FBI](https://github.com/Steveice10/FBI)** by **Steveice10**, **[Homebrew Launcher](https://github.com/fincs/)** by **fincs** / **smealum**, **[VitaShell](https://github.com/TheOfficialFloW/VitaShell)** by **TheOfficialFloW** — installers used to run the clients.

### Libraries and frameworks

- **Server**: [FastAPI](https://fastapi.tiangolo.com/) and [Pydantic](https://docs.pydantic.dev/) (Sebastián Ramírez, Samuel Colvin and contributors), [Uvicorn](https://www.uvicorn.org/) (Encode), [httpx](https://www.python-httpx.org/), [Pillow](https://python-pillow.org/), [pytest](https://pytest.org/), [uv](https://docs.astral.sh/uv/) (Astral).
- **Desktop / Steam Deck**: [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) (Riverbank Computing) and Qt, [requests](https://requests.readthedocs.io/), [paramiko](https://www.paramiko.org/), [py7zr](https://github.com/miurahr/py7zr), [pygame](https://www.pygame.org/).
- **Android**: [AndroidX / Jetpack Compose](https://developer.android.com/jetpack/compose), Room, WorkManager and DataStore (Google), [Retrofit](https://square.github.io/retrofit/) and [OkHttp](https://square.github.io/okhttp/) (Square), [Kotlin](https://kotlinlang.org/) and kotlinx.coroutines (JetBrains), [Accompanist](https://github.com/google/accompanist) (Google).

### Assets

- `xbox/assets/font.ttf` — the **Vegur** typeface by **Sora Sagano** (public domain).

### Interoperability targets

GameSync reads and writes save data laid out by these projects and devices. They
are not bundled or modified — thanks to their authors for the formats and layouts
this project relies on:

RetroArch and the libretro cores, Dolphin, DuckStation, PCSX2, AetherSX2, PPSSPP,
Cemu, melonDS, DraStic, mGBA, Azahar/Citra, RPCS3, Mednafen, yabasanshiro, xemu,
EmuDeck, Adrenaline, POPStarter, Nintendont, Swiss, the Aroma environment,
MiSTer FPGA, Analogue Pocket, EverDrive, SAROO, Super SD System 3, GDEMU,
openMenu, Flycast, MemCard PRO / PRO2 / PRO DC and SD2PSX.

If your work is used here and is missing or mis-credited, please open an issue.

## License

Copyright © 2026 Luiz Perrella

GameSync is free software: you can redistribute it and/or modify it under the
terms of the **GNU General Public License version 3, or (at your option) any
later version**. See [LICENSE](LICENSE).

It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE.

### Additional permission (GPLv3 §7) — console SDK linking exception

If you modify this Program, or any covered work, by linking or combining it with
any of the following console SDKs and support libraries, or with modified
versions of them, the licensor grants you additional permission to convey the
resulting work:

> devkitARM, devkitPPC, `libctru`, `libnds`, `dswifi`, `libfat`, `libogc`,
> `gxflux`, `wut`, PSPSDK / pspdev, VitaSDK, PSL1GHT / ps3dev, PS2SDK / ps2dev,
> and nxdk — together with the vendor runtime libraries those SDKs link against.

This exists because PS2SDK is under the Academic Free License 2.0, which the FSF
considers GPL-incompatible despite being permissive. The exception removes any
doubt for every console target at once. If you modify the GameSync sources
themselves, the GPL still applies to those modifications.

### Third-party components

Bundled or linked third-party code is documented in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The three copyleft
dependencies are all compatible with this license:

- `server/third_party/mcr2vmp/` — GPL-3.0, same license as this project.
- `libmocha` — LGPL-3.0, linked into the Wii U client
  ([`licenses/LGPL-3.0.txt`](licenses/LGPL-3.0.txt)).
- **PyQt6** — used under Riverbank's GPL-3.0 option by the desktop and Steam
  Deck clients. This is the main reason GameSync is GPL rather than permissive.

### Scope

This license covers GameSync's own source code. It does **not** cover the
third-party data and binaries redistributed for convenience, which keep their
original terms:

| Path | Origin |
|---|---|
| `server/data/dats/**` | libretro-database / No-Intro / Redump / GameTDB / Pleasuredome |
| `server/data/*.txt`, `server/data/xbox_titleids.json` | 3dsdb.com, ds-scene.net, wiiubrew, dbox.tools |
| `server/third_party/**` | See `THIRD_PARTY_NOTICES.md` |
| `ps2/irx/mmceman.irx` | ps2-mmce/mmceman (MIT) |
| `tools/apktool.jar` | Apktool (Apache-2.0) |
| `xbox/assets/font.ttf` | Vegur (public domain) |
