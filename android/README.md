# GameSync — Android Client

Android app for syncing emulator save files with the GameSync server. Built with Kotlin and Jetpack Compose.

**Minimum Android version:** Android 10 (API 29)

## Requirements

- [Android Studio](https://developer.android.com/studio) (Hedgehog or newer recommended)
- Android SDK 34
- Java 8+

## Build

Open the `android/` folder in Android Studio and let Gradle sync, then:

```
Build → Build App Bundle(s) / APK(s) → Build APK(s)
```

Or from the command line:

```bash
cd android
./gradlew assembleDebug     # debug APK → app/build/outputs/apk/debug/
./gradlew assembleRelease   # release APK (requires signing config)
```

### Output

`app-debug.apk` — install via ADB or by sideloading:

```bash
adb install app/build/outputs/apk/debug/app-debug.apk
```

## Permissions

On Android 11+, the app requires **All Files Access** (`MANAGE_EXTERNAL_STORAGE`) to read save files from emulator directories. A permission prompt is shown on first launch.

## Supported Emulators

| Emulator | System(s) |
|---|---|
| RetroArch | Multi-system |
| Dolphin | GameCube, Wii |
| PPSSPP | PSP |
| DuckStation | PS1 |
| AetherSX2 / NetherSX2 | PS2 |
| melonDS | NDS |
| DraStic | NDS |
| mGBA | GBA |
| Cemu | Wii U (`mlc01/usr/save/00050000/`) |

## Screens

Five top-level tabs — **Saves**, **Catalog**, **Installed**, **Downloads**,
**Settings** — in the same dark GameSync palette and layout as the console
clients: a header with the tab strip and a server status dot (green = the
server answered, red = it did not), a sub-tab chip row (system filter, sync
status, RetroAchievements), list rows with a teal selection bar and status
pills, and a footer of controller button hints. The footer (and the L1 / R1 /
SELECT glyphs) show whenever a gamepad is attached or the screen is in
landscape; on a phone in portrait the app is driven by touch as before (tap a
tab, tap a row, the toolbar icons).

## Controls

The same scheme on every screen (shared by all GameSync console clients):

| Button | Everywhere |
|---|---|
| D-pad / left stick Up / Down | Move one row (hold repeats) |
| D-pad Left / Right | Page up / page down (hold to speed up, then jump by first letter) |
| L1 / R1 | Previous / next tab (Saves, Catalog, Installed, Downloads, Settings; wraps around) |
| SELECT | Next sub-tab: the system filter on Saves, Catalog and Installed |
| A | Confirm / the highlighted row's main action |
| B | Cancel / back: closes dialogs and the search (never starts anything, never leaves the app from a tab) |
| START | Exit (asks first: START again or A exits, B stays) |

The touch / system Back gesture behaves as before.

### Saves

| Button | Action |
|---|---|
| A | Smart sync the selected save (upload or download, whichever it needs); a conflict, or a Saturn save, opens its detail screen |
| X | Sync all saves in the current filter (asks first) |
| Y | Details: compare with the server, force upload / download, download the ROM, normalize the name |
| L2 / R2 | Step the sync-status filter (all / synced / local newer / conflict / ...) |
| SELECT | Next system |
| Touch | Tap a row for details; the search and web-library icons stay in the header |

On the detail screen D-pad moves between the actions, A runs one, B goes back.

### Catalog

| Button | Action |
|---|---|
| A | Download the selected game (asks first; a Wii U game queues its update and DLC too) |
| X | Only games with RetroAchievements / all games |
| Y | Search |
| B | Clear and close the search |
| SELECT | Next system |

### Installed

| Button | Action |
|---|---|
| A | The game's actions: A sync its saves, X delete it (asks again — it can't be undone), B cancel |
| X | Rescan the ROM folders |
| Y | Search |
| B | Clear and close the search |
| SELECT | Next system |

### Downloads

| Button | Action |
|---|---|
| A | Pause / resume / retry the selected download |
| B | Pause the running download |
| X | Clear finished downloads |
| Y | Cancel (running) or remove (finished) the selected download, after asking |

### Settings

D-pad moves between the entries, A activates the highlighted one.

| Entry | What it does |
|---|---|
| Rescan saves | Look for emulator saves again |
| Refresh catalog | Ask the server to rescan its ROM folder (`GET /api/v1/roms/scan`; a server that refuses with 403/404/405 is fine), throw the cached catalog away and download it again |
| Server URL / API key, Test Connection, Save | Connection settings |
| Emudeck, Auto Sync, Saturn / Sega CD / PS2 formats, ROM folders | As before |
| Emulator Configuration → | Per-emulator save folders (its own screen; B goes back) |

## Game catalog cache

The Catalog tab keeps the server's ROM catalog on the device in
`<app files dir>/catalog_cache.json` (one JSON file: a format version, then per
system the server's fingerprint and that system's rows), using the same
strategy as the MiSTer client (`shared/catalog_cache.py`):

- On open the app asks `GET /api/v1/roms/fingerprints` and downloads again
  only the systems whose fingerprint changed (`GET /api/v1/roms?system=…`,
  paged 2000 rows at a time); systems the server no longer lists are dropped.
  The file is written to a temp file and renamed, so an interrupted write
  never leaves half a catalog.
- If the server can't be reached, the cached catalog is shown with an
  *Offline — cached catalog* notice.
- A server too old to publish fingerprints (404/405) is fetched whole, live,
  and nothing is cached.
- Search and the RetroAchievements filter run locally over the rows.
- The save scan and the save detail screen reuse the same cache to match
  saves to ROMs instead of downloading the catalog again (until the cache is
  first filled by opening the Catalog tab, the save scan asks the server for
  just the ROMs that have saves, as before).
- *Settings > Refresh catalog* forces a full reload. Bump
  `CatalogCache.VERSION` when the meaning of a stored row changes so old
  caches are refetched.

The cache logic is plain Kotlin (`catalog/CatalogCache.kt`) with JVM unit
tests in `app/src/test/.../catalog/CatalogCacheTest.kt`.

## Configuration

Open the app and go to **Settings** to enter your server URL and API key:

```
Server URL:  http://192.168.1.100:8000
API Key:     your-secret-key
```

Settings are stored on-device using DataStore and persist across launches.
