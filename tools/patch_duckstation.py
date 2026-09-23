#!/usr/bin/env python3
"""Patch the DuckStation Android APK so its memory-card files are group-readable.

Why
---
DuckStation Android writes its memory cards (and everything else) under
``Android/data/com.github.stenzek.duckstation/files/`` with the default umask
``0077`` -> mode **600** (owner-only). On Android 11+ no other app — not even
with MANAGE_EXTERNAL_STORAGE, Shizuku (shell uid), or the SAF
ExternalStorageProvider — can read another app's owner-only 600 files without
root. NetherSX2 works only because it writes **660** (group ``ext_data_rw``),
which the SAF provider *can* read.

This script injects a single call — ``android.system.Os.umask(0007)`` — at the
top of DuckStation's ``MainActivity.onCreate`` and ``EmulationActivity.onCreate``
(umask is process-wide and inherited by the native code that writes the cards),
so every file it creates afterwards is **660**. The SaveSync app's SAF mirror
can then read them via a one-time per-app folder grant.

Trade-offs
----------
* A re-signed APK has a different signature, so it cannot update the installed
  build in place: the original must be **uninstalled first, which wipes the
  existing 600 saves**. Only saves created by the patched build (660) are
  syncable. There is no non-root way to back up the old 600 saves.
* The patch must be re-applied to every new DuckStation release.

Requirements
------------
* ``adb`` on PATH, device connected with USB debugging.
* A JDK (provides ``keytool``); Java on PATH.
* ``apktool`` jar (pass --apktool or place apktool.jar next to this script).
* Android SDK build-tools (provides ``apksigner`` + ``zipalign``); auto-detected
  from ANDROID_HOME / the usual location, or pass --build-tools.

Usage
-----
    python patch_duckstation.py                 # pull from device, patch, sign, install
    python patch_duckstation.py --no-install    # produce signed APKs, don't install
    python patch_duckstation.py --apk base.apk --split split.apk   # patch local APKs

See patch_duckstation_README.md for the full walkthrough.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PACKAGE = "com.github.stenzek.duckstation"

# Activities whose onCreate we hook. The launcher (MainActivity) is the process
# entry point so its umask persists for the whole process; EmulationActivity is
# patched too in case a game is launched directly via a shortcut/intent.
TARGET_ACTIVITIES = ["MainActivity", "EmulationActivity"]

PATCH_MARKER = "DsChmod"  # presence of the helper-call marks a file as patched

# umask 0007 helps plain writes, BUT DuckStation writes memory cards via
# mkstemp(), which forces mode 0600 regardless of umask. So the real fix is a
# chmod: DuckStation OWNS its files, so it can widen them to 0660 itself. The
# DsChmod helper below does that for every file in the memcards dir; we call it
# on game pause/resume (after the card is flushed) and on launch (to widen
# pre-existing cards). umask(0007) is kept too — it fixes the non-mkstemp files.
UMASK_SMALI = (
    "\n    # SaveSync patch: umask 0007 (helps non-mkstemp writes)\n"
    "    const/16 v0, 0x7\n\n"
    "    invoke-static {v0}, Landroid/system/Os;->umask(I)I\n"
)

# No-arg static call; needs no registers, so it can be injected anywhere.
CHMOD_CALL = (
    "\n    # SaveSync patch: widen memcards to 0660 so other apps can read them\n"
    "    invoke-static {}, Lcom/savesync/dspatch/DsChmod;->fix()V\n"
)

HELPER_PKG = "com/savesync/dspatch"
# 0x1b0 == octal 0660. Chmods every file in DuckStation's memcards dir; the app
# owns them so chmod succeeds. Per-file try/catch so one failure can't abort.
HELPER_SMALI = """.class public final Lcom/savesync/dspatch/DsChmod;
.super Ljava/lang/Object;

.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method

.method public static fix()V
    .locals 5

    new-instance v0, Ljava/io/File;

    invoke-static {}, Landroid/os/Environment;->getExternalStorageDirectory()Ljava/io/File;

    move-result-object v1

    const-string v2, "Android/data/com.github.stenzek.duckstation/files/memcards"

    invoke-direct {v0, v1, v2}, Ljava/io/File;-><init>(Ljava/io/File;Ljava/lang/String;)V

    invoke-virtual {v0}, Ljava/io/File;->listFiles()[Ljava/io/File;

    move-result-object v0

    if-eqz v0, :done

    const/4 v1, 0x0

    array-length v2, v0

    :loop
    if-ge v1, v2, :done

    aget-object v3, v0, v1

    :try_start
    invoke-virtual {v3}, Ljava/io/File;->getAbsolutePath()Ljava/lang/String;

    move-result-object v3

    const/16 v4, 0x1b0

    invoke-static {v3, v4}, Landroid/system/Os;->chmod(Ljava/lang/String;I)V
    :try_end
    .catch Ljava/lang/Throwable; {:try_start .. :try_end} :catch

    goto :next

    :catch
    move-exception v3

    :next
    add-int/lit8 v1, v1, 0x1

    goto :loop

    :done
    return-void
.end method
"""

KEYSTORE_PASS = "duckpatch"
KEY_ALIAS = "ds"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("  $", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, check=True, **kw)


def find_tool(name: str) -> str | None:
    return shutil.which(name)


def find_keytool() -> str:
    kt = shutil.which("keytool")
    if kt:
        return kt
    # Fall back to common JDK locations on Windows.
    for base in [r"C:\Program Files\Java", r"C:\Program Files\Eclipse Adoptium"]:
        p = Path(base)
        if p.is_dir():
            hits = list(p.glob("*/bin/keytool*"))
            if hits:
                return str(hits[0])
    sys.exit("keytool not found. Install a JDK or put keytool on PATH.")


def find_build_tools(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not sdk:
        default = Path.home() / "AppData" / "Local" / "Android" / "Sdk"
        if default.is_dir():
            sdk = str(default)
    if not sdk:
        sys.exit("Android SDK not found. Set ANDROID_HOME or pass --build-tools.")
    bt_root = Path(sdk) / "build-tools"
    versions = sorted([d for d in bt_root.iterdir() if d.is_dir()], key=lambda d: d.name)
    if not versions:
        sys.exit(f"No build-tools under {bt_root}. Install one via the SDK manager.")
    return versions[-1]


def find_apktool(explicit: str | None) -> str:
    if explicit:
        return explicit
    here = Path(__file__).parent / "apktool.jar"
    if here.is_file():
        return str(here)
    sys.exit(
        "apktool.jar not found. Download it from "
        "https://github.com/iBotPeaches/Apktool/releases and pass --apktool, "
        "or place apktool.jar next to this script."
    )


def adb_pull_apks(workdir: Path) -> tuple[Path, list[Path]]:
    out = subprocess.run(
        ["adb", "shell", "pm", "path", PACKAGE], capture_output=True, text=True, check=True
    ).stdout
    paths = [ln.split("package:", 1)[1].strip() for ln in out.splitlines() if "package:" in ln]
    if not paths:
        sys.exit(f"{PACKAGE} not installed on the device.")
    base: Path | None = None
    splits: list[Path] = []
    for remote in paths:
        name = remote.rsplit("/", 1)[-1]
        local = workdir / name
        run(["adb", "pull", remote, str(local)])
        if name == "base.apk":
            base = local
        else:
            splits.append(local)
    if base is None:
        sys.exit("Could not find base.apk among the installed splits.")
    return base, splits


def patch_smali_dir(decoded: Path) -> int:
    """Write the chmod helper and inject calls to it. Idempotent.

    * adds smali/com/savesync/dspatch/DsChmod.smali
    * MainActivity/EmulationActivity onCreate(Bundle): umask + DsChmod.fix()
      right after `.locals` (also widens existing cards on launch).
    * EmulationActivity onPause()/onResume(): DsChmod.fix() right after the
      `invoke-super` call (after the card is flushed). Matched by method name so
      it survives DuckStation's superclass obfuscation across versions.
    """
    # 1. Write the helper class into the primary smali tree.
    smali_root = decoded / "smali"
    if not smali_root.is_dir():
        # apktool may emit smali_classesN only; fall back to the first one.
        candidates = sorted(decoded.glob("smali*"))
        if not candidates:
            sys.exit("No smali/ dir in decoded APK — cannot inject helper.")
        smali_root = candidates[0]
    helper_dir = smali_root / HELPER_PKG
    helper_dir.mkdir(parents=True, exist_ok=True)
    (helper_dir / "DsChmod.smali").write_text(HELPER_SMALI, encoding="utf-8")
    print(f"  + helper {helper_dir / 'DsChmod.smali'}")

    patched = 0
    oncreate_re = re.compile(
        r"(\.method [^\n]*onCreate\(Landroid/os/Bundle;\)V\n)(\s*\.locals (\d+)\n)"
    )

    def inject_after_super(text: str, method: str) -> tuple[str, bool]:
        # invoke-super {p0}, L<obfuscated>;->onPause()V  (method name is stable)
        rx = re.compile(r"(invoke-super \{p0\}, L\S+;->" + method + r"\(\)V\n)")
        m = rx.search(text)
        if not m:
            return text, False
        return text[: m.end()] + CHMOD_CALL + text[m.end():], True

    for activity in TARGET_ACTIVITIES:
        for smali in decoded.rglob(f"{activity}.smali"):
            text = smali.read_text(encoding="utf-8")
            if PATCH_MARKER in text:
                print(f"  = {smali.name} already patched")
                continue
            changed = False

            # onCreate: umask + fix() after .locals
            m = oncreate_re.search(text)
            if m:
                locals_n = int(m.group(3))
                locals_line = m.group(2)
                if locals_n < 1:
                    locals_line = locals_line.replace(".locals 0", ".locals 1")
                text = (
                    text[: m.start()] + m.group(1) + locals_line
                    + UMASK_SMALI + CHMOD_CALL + text[m.end():]
                )
                changed = True

            # Lifecycle exits: fix() after invoke-super. onPause/onUserLeaveHint
            # fire when switching apps / pressing Home; onStop when fully
            # backgrounded; onDestroy on exit; onResume on return. Absent
            # methods are skipped. chmod is idempotent so extra calls are free.
            for method in ("onPause", "onResume", "onStop", "onDestroy", "onUserLeaveHint"):
                text, hit = inject_after_super(text, method)
                changed = changed or hit

            if changed:
                smali.write_text(text, encoding="utf-8")
                print(f"  + patched {smali.name}")
                patched += 1
            else:
                print(f"  ! no hook points found in {smali.name}")
    return patched


def main() -> None:
    ap = argparse.ArgumentParser(description="Patch DuckStation APK to write 660 saves.")
    ap.add_argument("--apk", help="local base.apk (skip pulling from device)")
    ap.add_argument("--split", action="append", default=[], help="local split apk(s); repeatable")
    ap.add_argument("--apktool", help="path to apktool.jar")
    ap.add_argument("--build-tools", help="path to an Android SDK build-tools dir")
    ap.add_argument("--workdir", default=str(Path(__file__).parent / "ds_patch_work"))
    ap.add_argument("--no-install", action="store_true", help="produce signed APKs but don't install")
    args = ap.parse_args()

    if not find_tool("adb") and not args.apk:
        sys.exit("adb not on PATH (needed to pull/install). Pass --apk to patch a local APK.")
    if not find_tool("java"):
        sys.exit("java not on PATH.")

    apktool = find_apktool(args.apktool)
    bt = find_build_tools(args.build_tools)
    keytool = find_keytool()
    apksigner = bt / ("apksigner.bat" if os.name == "nt" else "apksigner")
    zipalign = bt / ("zipalign.exe" if os.name == "nt" else "zipalign")
    for t in (apksigner, zipalign):
        if not t.exists():
            sys.exit(f"{t} not found in build-tools {bt}.")

    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)
    print(f"[1/6] workspace: {work}")

    # ---- obtain APKs ----
    if args.apk:
        base = work / "base.apk"
        shutil.copy(args.apk, base)
        splits = []
        for s in args.split:
            d = work / Path(s).name
            shutil.copy(s, d)
            splits.append(d)
    else:
        print("[2/6] pulling APKs from device")
        base, splits = adb_pull_apks(work)

    # ---- decode + patch ----
    print("[3/6] decoding + patching base.apk")
    decoded = work / "base_dec"
    if decoded.exists():
        shutil.rmtree(decoded)
    run(["java", "-jar", apktool, "d", "-f", "-o", str(decoded), str(base)])
    n = patch_smali_dir(decoded)
    if n == 0:
        print("  (nothing newly patched — already patched or methods not found)")

    print("[4/6] rebuilding base.apk")
    base_built = work / "base_patched.apk"
    run(["java", "-jar", apktool, "b", str(decoded), "-o", str(base_built)])

    # ---- sign everything with one fresh key ----
    print("[5/6] aligning + signing")
    keystore = work / "ds.keystore"
    if not keystore.exists():
        run([
            keytool, "-genkeypair", "-keystore", str(keystore), "-alias", KEY_ALIAS,
            "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
            "-storepass", KEYSTORE_PASS, "-keypass", KEYSTORE_PASS,
            "-dname", "CN=DSPatch, O=SaveSync, C=US",
        ])

    signed: list[Path] = []

    def align_and_sign(src: Path, out_name: str) -> Path:
        out = work / out_name
        run([str(zipalign), "-p", "-f", "4", str(src), str(out)])
        run([
            str(apksigner), "sign", "--ks", str(keystore),
            "--ks-pass", f"pass:{KEYSTORE_PASS}", "--key-pass", f"pass:{KEYSTORE_PASS}",
            str(out),
        ])
        return out

    signed.append(align_and_sign(base_built, "base_signed.apk"))
    for s in splits:
        signed.append(align_and_sign(s, f"{s.stem}_signed.apk"))

    print("  signed:", ", ".join(p.name for p in signed))

    # ---- install ----
    if args.no_install:
        print("[6/6] --no-install set; signed APKs are in", work)
        print("Install later with:")
        print(f"  adb uninstall {PACKAGE}")
        print("  adb install-multiple " + " ".join(str(p) for p in signed))
        return

    print("[6/6] installing (this WIPES existing DuckStation saves)")
    subprocess.run(["adb", "uninstall", PACKAGE])  # ok if not installed
    run(["adb", "install-multiple", "-r", *[str(p) for p in signed]])
    print("\nDone. Launch DuckStation, boot a game so it writes a memory card,")
    print("then scan in SaveSync — the new card is mode 660 and syncs via SAF.")


if __name__ == "__main__":
    main()
