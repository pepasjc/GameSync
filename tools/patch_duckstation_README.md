# DuckStation save-permission patch

DuckStation Android stores its memory cards under
`Android/data/com.github.stenzek.duckstation/files/memcards/` with mode **600**
(owner-only). On Android 11+ **no other app can read another app's 600 files
without root** — not with MANAGE_EXTERNAL_STORAGE, not via Shizuku (shell uid),
not via the SAF system provider. (Verified on hardware: `adb shell` and FV File
Manager both get `EACCES` / produce 0-byte copies.)

NetherSX2 works only because it writes **660** (group `ext_data_rw`), which the
SAF `ExternalStorageProvider` *can* read.

`patch_duckstation.py` makes DuckStation's saves group-readable. Note: a plain
`umask(0007)` is **not enough** — DuckStation writes memory cards via
`mkstemp()`, which forces mode `0600` regardless of umask. So the patch instead
injects a tiny helper (`DsChmod.fix()`) that `chmod`s every file in the memcards
dir to **0660**. DuckStation *owns* those files, so the chmod succeeds. The
helper is called from:

- `EmulationActivity.onPause()` / `onResume()` — right after a game flushes its
  card, so freshly-written cards are widened immediately.
- `MainActivity.onCreate()` — on launch, widening any pre-existing cards.

(A `umask(0007)` call is also injected, which fixes the non-`mkstemp` files like
`playtime.dat`.) Once a card is `660`, SaveSync — which is in the `ext_data_rw`
group — reads and syncs it **directly**, no SAF/Shizuku, exactly like NetherSX2.

## ⚠️ The first patch wipes current saves (once)

A re-signed APK can't update the installed build in place (different signature),
so the **first** install must **uninstall the original — which deletes whatever
600 saves exist at that moment**. There is no non-root way to back those up
(they're unreadable until patched — chicken/egg). After the patch is installed,
cards created by the patched build persist and are auto-widened to 660, so
they're no longer lost on subsequent syncs. A Google Play update reverts the app
(see "Re-applying" below) and will wipe again when you re-patch.

If preserving the *current* saves matters more than avoiding root, root the
device instead and skip this patch.

## Requirements

- `adb` on PATH, device connected (USB debugging on).
- A JDK on PATH (for `keytool`); Java 11+ works.
- `apktool.jar` — download from
  <https://github.com/iBotPeaches/Apktool/releases> and either put it next to
  `patch_duckstation.py` or pass `--apktool C:\path\apktool.jar`.
- Android SDK build-tools (`apksigner`, `zipalign`). Auto-detected from
  `ANDROID_HOME` / `%LOCALAPPDATA%\Android\Sdk`, or pass
  `--build-tools "<sdk>\build-tools\34.0.0"`.

## Usage

Pull from the connected device, patch, sign, and install in one go:

```
python tools/patch_duckstation.py
```

Build the signed APKs without installing (inspect / install manually):

```
python tools/patch_duckstation.py --no-install
```

Patch APKs you already have on disk (e.g. a freshly downloaded release):

```
python tools/patch_duckstation.py --apk base.apk --split split_config.arm64_v8a.apk
```

## After patching

1. Open the patched DuckStation, add a PS1 game, **boot it** so it creates/writes
   a memory card. The new card is mode 660.
2. In SaveSync, grant the DuckStation `memcards` folder once when prompted (SAF
   picker, "Use this folder"), then scan. The card appears and syncs.

## Re-applying on updates

A DuckStation update from Google Play replaces the patched build (and reverts to
600). Re-run the script against the new version:

- If the new APK is installed, just run `python tools/patch_duckstation.py`
  again (it re-pulls and re-patches; existing saves made by the patched build
  are wiped again, so sync first).
- The smali injection is idempotent and locates `onCreate` by signature, so it
  keeps working across versions unless DuckStation restructures those
  activities — if a target activity is missing, the script prints a warning.

## How it works (smali)

Injected at the top of each target `onCreate(Landroid/os/Bundle;)V`:

```smali
    # SaveSync umask patch: files become group-readable (mode 660) not 600
    const/16 v0, 0x7

    invoke-static {v0}, Landroid/system/Os;->umask(I)I
```

`0x7` == octal `0007`: new files `0666 & ~0007 = 0660`, new dirs
`0777 & ~0007 = 0770` — matching NetherSX2.
