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
| `mmceman.irx` ([ps2-mmce/mmceman](https://github.com/ps2-mmce/mmceman)) | MIT — [`licenses/MIT-mmceman.txt`](licenses/MIT-mmceman.txt) | `ps2/` |
| PSPSDK debug font (**M. R. Brown**, **J. Forshaw**, **J. Kelley**) | BSD | `vita/` |
| Vegur typeface (**Sora Sagano**) | Public domain | `xbox/assets/font.ttf` |
| Apktool (**Connor Tumbleson**) | Apache-2.0 — [`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt) | `tools/apktool.jar` |
| zlib (**Gailly**, **Adler**) | zlib | several clients |
| PolarSSL / mbed TLS | Apache-2.0 | `ps3/` via PSL1GHT |
| FastAPI, Pydantic, Uvicorn, httpx, Pillow | MIT / BSD | `server/` |
| requests, paramiko, py7zr, pygame | Apache-2.0 / LGPL | desktop, Steam Deck |
| AndroidX, Compose, Room, Retrofit, OkHttp, Kotlin | Apache-2.0 | `android/` |

---

## Console SDKs — GPLv3 §7 linking exception

The console toolchains are permissively licensed, with one wrinkle: **PS2SDK is
under the Academic Free License 2.0**, which the FSF classifies as
GPL-incompatible despite being permissive. The others (`libctru`, `libnds`,
`dswifi`, `libfat`, `libogc`, `gxflux`, `wut`, PSPSDK, VitaSDK, PSL1GHT, nxdk)
are zlib/BSD/MIT-style and compose with the GPL without difficulty.

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

---

If you believe a component is mis-licensed or under-credited here, please open an
issue.
