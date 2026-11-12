# Third-Party Notices

GameSync is licensed under the **GNU GPL v3 or later** — see [LICENSE](LICENSE).
This file records the third-party code it bundles or links, and how each fits
that license. The full attribution list, including format documentation and
game databases, is in the Credits section of [README.md](README.md).

Because GameSync is GPLv3, every copyleft dependency below composes cleanly.
None of them requires a structural workaround.

---

## Copyleft dependencies

### `mcr2vmp` — GPL-3.0

**Location:** `server/third_party/mcr2vmp/`
**Upstream:** [chrisbrasington/psp_psx_save_sync](https://github.com/chrisbrasington/psp_psx_save_sync),
packaging [vita-mcr2vmp](https://github.com/dots-tb/vita-mcr2vmp) by **@dots_tb**
(with **@AnalogMan151** and **@teakhanirons** of CBPS).
**License text:** `server/third_party/mcr2vmp/LICENSE`

Converts raw PS1 `.mcd`/`.mcr` memory-card images into the signed `SCEVMC*.VMP`
format PSP and Vita require.

Same license as this project, so no separation is required. It is nonetheless
built and run as a standalone executable rather than linked:
`server/app/services/mcr2vmp_tool.py` compiles the bundled `.c` sources on first
use and invokes the binary as a subprocess, exchanging data through a temporary
file. That is an implementation choice, not a licensing one.

The complete corresponding source ships in-tree, unmodified, with its `LICENSE`.
The compiled binary is `.gitignore`d and is not included in release artifacts.

Note that `aes.c` (tiny-AES-c, **kokke**) and `sha1.c` (**Steve Reid**) inside
that directory are public domain in their own right.

### `libmocha` — LGPL-3.0

**Upstream:** [wiiu-env/libmocha](https://github.com/wiiu-env/libmocha) by
**Maschell** and the wiiu-env contributors. Requires
[MochaPayload](https://github.com/wiiu-env/MochaPayload) via
[EnvironmentLoader](https://github.com/wiiu-env/EnvironmentLoader).
**License text:** [`licenses/LGPL-3.0.txt`](licenses/LGPL-3.0.txt)

The Wii U client links libmocha (`-lmocha`, see `wiiu/Makefile`) to reach the FSA
interface, which is what allows reading and writing vWii (`slccmpt01`) and Wii U
save data. It is statically linked into the distributed `wiiusync.rpx` /
`wiiusync.wuhb`, and is used unmodified.

LGPLv3 permits conveying a combined work under the GPL — LGPLv3 §2 and GPLv3
§13 make LGPL-3.0 code usable in a GPL-3.0 project directly. The relink
provisions of LGPLv3 §4 are satisfied regardless, since the complete source of
the Wii U client is in [`wiiu/`](wiiu/) and `wiiu/README.md` documents the
toolchain and the libmocha build.

### `SDL_gfx` and `SDL_ttf` — LGPL-2.1

**Upstream:** [SDL_gfx](https://www.ferzkopp.net/wordpress/2016/01/02/sdl_gfx-sdlgfx/)
2.0.22 by **Andreas Schiffler**; [SDL_ttf](https://github.com/libsdl-org/SDL_ttf)
2.0.10 by **Sam Lantinga**. Both as built by the ps3dev `ps3libraries` portlibs.
**License text:** [`licenses/LGPL-2.1.txt`](licenses/LGPL-2.1.txt)

The PS3 client draws its interface with SDL_gfx (rounded panels, circles,
lines, and the built-in 8x8 font as a fallback) and rasterises text with
SDL_ttf on top of FreeType (`-lSDL_ttf -lSDL_gfx`, see `ps3/Makefile`). Both are
statically linked into `ps3sync.pkg`'s `EBOOT.BIN`, unmodified.

LGPL-2.1 §3 lets the licensee apply the terms of the GNU GPL (version 2 or any
later version) to the library instead, so combining it with a GPL-3.0 program
is permitted. The relink provisions of §6 are met regardless: the complete
source of the PS3 client is in [`ps3/`](ps3/) and `ps3/README.md` documents the
toolchain, so the program can be rebuilt against a modified library.

### GNU libiconv — LGPL-2.1-or-later (PS5 client)

**Upstream:** [GNU libiconv](https://www.gnu.org/software/libiconv/) by **Bruno
Haible** and contributors, as built by the
[PacBrew ports for PS5](https://github.com/ps5-payload-dev/pacbrew-repo) v0.40.2.
**License text:** [`licenses/LGPL-2.1.txt`](licenses/LGPL-2.1.txt)

The PS5 client links PacBrew's SDL2 for controller input, and SDL2 pulls in
libiconv; both are statically linked into `eboot.bin`, unmodified. As with
SDL_gfx above, LGPL-2.1 §3 allows the GPL to be applied instead, so the
combination with this GPL-3.0 program is permitted, and the full client source
plus the build script (`ps5/build.sh`) let anyone relink against a modified
library.

### SwanStationPS5 platform code — GPL-3.0-or-later (PS5 client)

**Location:** `ps5/src/ps5_video.c`, `ps5_unlock.c`, `ps5_heap.c`,
`ps5_dlmalloc.c`, `ps5_shims.c`, `platform_ps5.c` (each file's header names the
original)
**Upstream:** [darkxex/SwanStationPS5](https://github.com/darkxex/SwanStationPS5)
(PSXS5), GPL-3.0-or-later — the same license as GameSync, see [LICENSE](LICENSE).

Adapted, not copied wholesale: the VideoOut presenter, the request that asks
a running HEN to let the title list `/data`, the direct-memory dlmalloc heap
and a few symbol stand-ins. The build also uses
[ps5-native-app-boilerplate](https://github.com/BlackBearReloaded/ps5-native-app-boilerplate)
by **BlackBearReloaded** (GPL-3.0-or-later), fetched at a pinned commit by
`ps5/build.sh`: its generated `libc.prx` runtime shim ships in the PS5 title
folder, and its build tooling produces `eboot.bin`.

### Lapy helper — MIT, with GPL-3.0 and LGPL-2.1 build inputs (PS5 client)

**Location:** `lapy.elf`, `lapy-manifest.json` and `licenses/Lapy-MIT.txt` in
the PS5 title folder (not in this repository).
**Upstream:** [PS5-Lapy-JB-Daemon](https://github.com/ArkSama/PS5-Lapy-JB-Daemon)
by **ArkSama / Team PHU**, exact-title helper from
[mpereiraesaa's fork](https://github.com/mpereiraesaa/PS5-Lapy-JB-Daemon), built
from source at the commit pinned by ps5-native-app-boilerplate.

When no HEN answers GameSync's jailbreak request, the client sends this helper
to the console's ELF loader so the title can list `/data`. It is a separate
program, not linked into `eboot.bin`. Lapy is MIT; its build links the `ps5log`
header (GPL-3.0-or-later) and uses a protocol header published under
LGPL-2.1-or-later — all compatible with this project's GPL-3.0-or-later. The
client side (`ps5/src/elevation.cpp`) is BlackBearReloaded's GPL-3.0-or-later
code.

### Apollo Save Tool `games.conf` — GPL-3.0-or-later

**Location:** `ps3/data/games.conf` (shipped inside `ps3sync.pkg`)
**Upstream:** [bucanero/apollo-ps3](https://github.com/bucanero/apollo-ps3)
**Copyright:** Apollo Save Tool (PS3) — Copyright © 2020-2025 **Damian Parrino**
**Notice shipped with it:** `ps3/data/games.conf.LICENSE`

The per-game key database (1818 titles) supplying the `secure_file_id` and
`disc_hash_key` values the PS3 client needs to decrypt and resign saves. It is
redistributed **verbatim and unmodified**, and its header credits **SHAkA** and
the other contributors who dumped keys for individual games.

Same license as this project, so redistribution is straightforward. Two things
must hold and are enforced by `ps3/Makefile`:

* `games.conf.LICENSE` is copied into the PKG alongside `games.conf`, so the
  notice travels with the binary distribution.
* The complete corresponding source — including this data file — is available
  from the GameSync repository.

### PyQt6 — GPL-3.0

**Upstream:** [Riverbank Computing](https://www.riverbankcomputing.com/software/pyqt/)

The desktop (`desktop/`) and Steam Deck (`steamdeck/`) clients depend on PyQt6,
which Riverbank dual-licenses under GPL v3 or a commercial license. GameSync uses
it under the **GPL v3 option**, which is why those clients — and therefore the
project as a whole — are GPLv3 rather than permissively licensed.

Anyone redistributing a modified GameSync desktop client must do so under the
GPL, or obtain a commercial PyQt license from Riverbank.

---

## Permissive dependencies

These impose no copyleft obligation and are listed for attribution only. Full
credits, including the format references and game databases GameSync relies on,
are in [README.md](README.md).

| Component | License | Used by |
|---|---|---|
| SHA-256 reference implementation (**Brad Conte**) | Public domain | all C clients |
| MD5 (**L. Peter Deutsch**, Aladdin Enterprises), as vendored in [rcheevos](https://github.com/RetroAchievements/rcheevos) | zlib-style, notice kept in `ds/source/md5.c` | `ds/` |
| `mmceman.irx` ([ps2-mmce/mmceman](https://github.com/ps2-mmce/mmceman)) | MIT — [`licenses/MIT-mmceman.txt`](licenses/MIT-mmceman.txt) | `ps2/` |
| PSPSDK debug font (**M. R. Brown**, **J. Forshaw**, **J. Kelley**) | BSD | `vita/` |
| Vegur typeface (**Sora Sagano**) | Public domain | `xbox/assets/font.ttf`, `psp/source/font_data.c` |
| Vegur typeface (**Sora Sagano**) | Public domain | `xbox/assets/font.ttf`, `ps2/source/font_data.c` (baked glyph atlas) |
| Vegur typeface (**Sora Sagano**) | Public domain | `ps5/src/font_data.c` (baked glyph atlas); the GameSync wordmark in every client's artwork (`tools/brand_art.py`) |
| font8x8 (**Daniel Hepper**, after **Marcel Sondaar** and the IBM PC VGA glyphs) | Public domain | `mister/gamesync/bitfont.py` |
| Apktool (**Connor Tumbleson**) | Apache-2.0 — [`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt) | `tools/apktool.jar` |
| zlib (**Gailly**, **Adler**) | zlib | several clients |
| PolarSSL / mbed TLS | Apache-2.0 | `ps3/` via PSL1GHT |
| SDL, PSL1GHT port (**Sam Lantinga**) | zlib | `ps3/` |
| SDL2, PacBrew PS5 port (**Sam Lantinga**) | zlib | `ps5/` |
| libsamplerate (**Erik de Castro Lopo**), PacBrew PS5 port | BSD-2-Clause | `ps5/` (pulled in by SDL2) |
| dlmalloc 2.8.6 (**Doug Lea**) | Public domain (CC0) | `ps5/` (fetched at build time) |
| PS5 Payload SDK (**John Törnblom**) | GPL-3.0 | `ps5/` toolchain and system-library stubs |
| FreeType 2.4.3 (The FreeType Project) | FreeType License — [`licenses/FreeType-FTL.txt`](licenses/FreeType-FTL.txt). Portions of this software are copyright © 2010 The FreeType Project (www.freetype.org). All rights reserved. | `ps3/` |
| FastAPI, Pydantic, Uvicorn, httpx, Pillow, zstandard | MIT / BSD | `server/` |
| requests, paramiko, py7zr, pygame | Apache-2.0 / LGPL | desktop, Steam Deck |
| AndroidX, Compose, Room, Retrofit, OkHttp, Kotlin | Apache-2.0 | `android/` |

---

## Console SDKs — GPLv3 §7 linking exception

The console toolchains are permissively licensed, with one wrinkle: **PS2SDK (and
gsKit, which the PS2 client uses to draw its UI) is under the Academic Free
License 2.0**, which the FSF classifies as
GPL-incompatible despite being permissive. The others (`libctru`, `libnds`,
`dswifi`, `libfat`, `libogc`, `gxflux`, `wut`, PSPSDK, VitaSDK, PSL1GHT, nxdk)
are zlib/BSD/MIT-style, and the PS5 Payload SDK is GPL-3.0; all compose with the GPL without difficulty.

To remove any doubt across every console target at once, the LICENSE section of
[README.md](README.md) grants an **additional permission under GPLv3 §7**
allowing GameSync to be linked and conveyed with those SDKs. This does not
weaken the GPL over GameSync's own sources.

---

## Redistributed data

The following are third-party data and binaries included for convenience. They
are **not** covered by GameSync's license and retain their original terms:

| Path | Origin |
|---|---|
| `server/data/dats/**` | libretro-database, libretro-dats (Rob Loach) — derived from No-Intro, Redump, GameTDB, Pleasuredome |
| `server/data/dats/EN-Dats/**` | The [T-En] Collection — fan-translation DATs |
| `android/app/src/main/assets/Sega - Saturn (libretro).dat`, `Sega - Dreamcast (libretro).dat` | Copies of the libretro DATs above, bundled in the APK so the Android client can resolve Saturn / Dreamcast disc serials offline |
| `server/data/3dsdb.txt`, `3dstdb.txt` | 3dsdb.com, ds-scene.net |
| `server/data/ps3db.txt` | PS3 title lists |
| `server/data/xbox_titleids.json`, `xbox_titleid_map.txt` | dbox.tools |
| Wii U title ids in `server/data/dats/Nintendo - Wii U.dat` | wiiubrew Title database |
| `GameIndex.yaml` (not in the repository) | PCSX2's game database (GPL-3.0) as shipped by [PS5SX2](https://github.com/Swordpdf/PS5SX2); downloaded at run time by `server/app/routes/emulators.py` and passed unchanged to the PS5 client, which writes it to `/data/PCSX2/resources/` |

---

If you believe a component is mis-licensed or under-credited here, please open an
issue.
