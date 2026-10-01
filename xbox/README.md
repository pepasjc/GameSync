# GameSync - original Xbox client

nxdk + SDL2/SDL_ttf client: save sync for `E:\UDATA`, the server's Xbox game
catalog (installs to `F:\Games`), installed-game management and settings.
Build with `bash xbox/build.sh` from WSL (see the root `CLAUDE.md`); output is
`xbox/bin/default.xbe` and `xbox/SaveSync.iso`.

## Controls

Same scheme as every GameSync client. Holding a D-pad direction (or the left
stick) repeats it.

| Button | Everywhere |
|---|---|
| D-pad Up / Down | Move one row |
| D-pad Left / Right | Page up / page down |
| Left / right trigger | Previous / next tab (Saves, Catalog, Installed, Settings; wraps) |
| A | Confirm / primary action of the row |
| B | Cancel: closes dialogs, stops a running game download |
| START | Exit to the dashboard (asks first) |

| Tab | A | X | Y |
|---|---|---|---|
| Saves | Smart sync the save (on a conflict: opens details) | Sync all | Details: local vs server comparison, upload, download, compare all again |
| Catalog | Download and install the game | - | Game details |
| Installed | Uninstall (asks first) | Rescan `F:\Games` | Game details |
| Settings | Change the field (cycles network mode / game format) or run the action | Save `config.txt` | - |

Saves are compared with the server automatically at startup, after Sync all
and after clearing the hash cache, so there is no separate compare button.
BACK is unused: the catalog only lists Xbox games, so there are no sub-tabs.
WHITE and BLACK are unbound.

Settings actions: **Refresh catalog** (asks the server to rescan its ROM
folder, drops the cached catalog and downloads it again), **Clear save hash
cache**, **Reload config.txt**.

## Catalog cache

The catalog is kept in `E:\UDATA\TDSV0000\catalog_cache.txt` (next to
`config.txt`) together with the server's fingerprint for the XBOX system
(`GET /api/v1/roms/fingerprints`), the same strategy as the MiSTer client. On
opening the Catalog tab the client fetches only the fingerprints and reuses
the cached rows when the fingerprint has not moved; otherwise it pages through
`/api/v1/roms?system=XBOX` and rewrites the cache. If the server cannot be
reached the cached copy is shown with a "cached / offline" notice; a server
without the fingerprints route gets the old uncached full fetch. The list is
heap-allocated (no fixed 512-game cap). Host tests for the cache format and
JSON parsing: `sh xbox/tests/run_host_tests.sh` (WSL / any gcc).
