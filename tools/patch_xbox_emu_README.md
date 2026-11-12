# Patching hakuX / X1 BOX for GameSync

GameSync's Android client syncs Xbox saves from **hakuX** (`com.rfandango.haku_x`)
and **X1 BOX** (`com.izzy2lost.x1box`). As shipped, neither leaves its saves
where another app can read them:

- **hakuX** unpacks the Xbox hard disk into its private app storage
  (`files/native/hdd`). Without root, no other app can reach it.
- **X1 BOX** keeps xemu's disk image at
  `Android/data/com.izzy2lost.x1box/files/x1box/hdd.img`, but writes it
  owner-only (mode 600).

`patch_xbox_emu.py` rebuilds the APK with a small helper class
(`xbox_emu_patch/XboxEmuFix.java`) that the app calls itself:

| App | Hook | What the helper does |
|---|---|---|
| hakuX | `HakuXApplication.onCreate` (every process) | `umask 007`; links `files/native` to `Android/data/com.rfandango.haku_x/files/native`; takes over save files GameSync downloaded into `E:\UDATA` |
| hakuX | `LauncherActivity.onResume` | the same take-over, for a sync that ran while hakuX was open |
| X1 BOX | library, launcher, setup, settings and both cores' `onCreate`/`onResume`/`onPause`/`onStop` | `umask 007`; chmod 660/770 over `files/x1box` |

GameSync's user is in the `ext_data_rw` group that owns `Android/data`, so
group-readable (660) files there are within its reach. The DuckStation patch
(`patch_duckstation.py`) relies on the same thing.

## Before you patch: back up the saves

A re-signed APK can't replace the installed one, so the first patch has to
**uninstall the emulator, and that deletes its data**:

- **hakuX**: Hard Disk → *Export saves* (a zip). After patching, run the setup
  again, then import that zip (Hard Disk → Import).
- **X1 BOX**: Settings → export the hard disk image. After patching, pick it
  again in the setup.

Later runs sign with the same key (`xbox_patch_work/xbox.keystore`, kept out
of git), so a newly patched release installs over the old one and the data
stays. hakuX's in-app updater can't install a stock release over the patched
one. Run this script on each new release instead.

## Requirements

`adb` (device connected, USB debugging on), a JDK (`java`, `javac`,
`keytool`), `apktool.jar` next to the script (shared with the DuckStation
patch), Android SDK build-tools (`d8`, `zipalign`, `apksigner`) and an SDK
platform (`android.jar`).

## Usage

```bash
python tools/patch_xbox_emu.py --app hakux               # pull the installed APK, patch, install
python tools/patch_xbox_emu.py --app hakux --download    # patch hakuX's latest GitHub release
python tools/patch_xbox_emu.py --app x1box               # pull the installed X1 BOX
python tools/patch_xbox_emu.py --app x1box --apk app-release.apk --no-install
```

The installer first tries an in-place update. If the installed build has a
different signature, the script asks before it uninstalls (`--yes` skips the
question).

After installing, open the emulator once and finish its setup. Then go to
GameSync → Settings → **Xbox Emulator**. The hint under the field shows which
emulator is in use and anything that still blocks it.

## Notes

- X1 BOX must be closed while GameSync downloads a save, because the save is
  written into `hdd.img`. GameSync copies the image to `hdd.img.bak` before its
  first write in each run.
- With hakuX, `files/native` (the disk folders, savestates and `mods.txt`)
  now lives on shared storage. Nothing executable is kept there, and GPU
  drivers stay in private storage.
- When the hakuX core overwrites a file, it moves the old copy to
  `<hdd>/.overwrite`. That folder is now on shared storage with the disk, so
  the rename still stays on one filesystem.
