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

## Configuration

Open the app and go to **Settings** to enter your server URL and API key:

```
Server URL:  http://192.168.1.100:8000
API Key:     your-secret-key
```

Settings are stored on-device using DataStore and persist across launches.
