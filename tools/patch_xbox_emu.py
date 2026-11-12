#!/usr/bin/env python3
"""Patch the hakuX or X1 BOX Android APK so GameSync can sync its Xbox saves.

Why
---
Both Xbox emulators keep their saves where another app can't read them:

* **hakuX** (com.rfandango.haku_x) unpacks the Xbox hard disk into its
  *private* storage, ``files/native/hdd`` (E: = ``Partition1``, saves in
  ``Partition1/UDATA/<title id>``). No other app can reach it without root.
* **X1 BOX** (com.izzy2lost.x1box) keeps xemu's disk image in
  ``Android/data/com.izzy2lost.x1box/files/x1box/hdd.img`` but writes it
  owner-only (600), which GameSync can't open.

The patch adds one helper class (``tools/xbox_emu_patch/XboxEmuFix.java``,
compiled to an extra ``classesN.dex``) and calls it from the apps' lifecycle:

* hakuX, ``HakuXApplication.onCreate`` (every process, before the core opens
  the disk): umask 007, ``files/native`` becomes a link to
  ``Android/data/com.rfandango.haku_x/files/native`` so the disk folders live
  where GameSync reads them (as 660/770), and save files GameSync downloaded
  are taken over as hakuX's own. ``LauncherActivity.onResume`` repeats the
  take-over for a sync that ran while hakuX was open.
* X1 BOX, the library/launcher/core activities' onCreate/onResume/onPause/
  onStop: umask 007 and chmod 660/770 over ``files/x1box``.

Trade-offs (as with tools/patch_duckstation.py)
-----------------------------------------------
* A re-signed APK can't update the installed one in place: the original must
  be **uninstalled, which deletes the app's data** - hakuX's whole hard disk,
  X1 BOX's ``hdd.img``. Back up first: hakuX -> Hard Disk -> Export saves (a
  zip; import it again after the patch), X1 BOX -> Settings -> export the
  hard disk image (pick it again in the setup).
* Later patched builds are signed with the same key
  (``xbox_patch_work/xbox.keystore``) and install over each other without a
  wipe. hakuX's own in-app updater can't install the stock release over the
  patched one: patch each new release with this script instead.

Requirements
------------
adb (device with USB debugging), a JDK (javac, keytool), ``apktool.jar`` next
to this script (or --apktool), Android SDK build-tools (d8, zipalign,
apksigner) and a platform's android.jar.

Usage
-----
    python patch_xbox_emu.py --app hakux                # pull from device, patch, install
    python patch_xbox_emu.py --app hakux --download     # latest GitHub release instead
    python patch_xbox_emu.py --app x1box --no-install   # signed APKs only
    python patch_xbox_emu.py --app hakux --apk app-release.apk
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from patch_duckstation import find_apktool, find_build_tools, find_keytool, find_tool, run  # noqa: E402

HELPER_SRC = HERE / "xbox_emu_patch" / "XboxEmuFix.java"
HELPER_CLASS = "Lcom/savesync/xpatch/XboxEmuFix;"
PATCH_MARKER = "Lcom/savesync/xpatch/XboxEmuFix;->"
KEYSTORE_PASS = "xboxpatch"
KEY_ALIAS = "xbox"

# Per app: package, release download, and the hooks - (class smali path,
# method, helper call). A method the class doesn't override is added.
APPS = {
    "hakux": {
        "package": "com.rfandango.haku_x",
        "github": "rfandango/hakuX",
        "asset": r"hakuX-.*\.apk|app-release\.apk",
        "hooks": [
            ("com/rfandango/haku_x/HakuXApplication", "onCreate()V", "hakux"),
            ("com/rfandango/haku_x/LauncherActivity", "onResume()V", "hakuxAdopt"),
        ],
    },
    "x1box": {
        "package": "com.izzy2lost.x1box",
        "github": None,
        "asset": None,
        "hooks": [
            (cls, method, "x1box")
            for cls in (
                "com/izzy2lost/x1box/LauncherActivity",
                "com/izzy2lost/x1box/GameLibraryActivity",
                "com/izzy2lost/x1box/SetupWizardActivity",
                "com/izzy2lost/x1box/SettingsActivity",
                "com/izzy2lost/x1box/MainActivity",
                "com/izzy2lost/x1box/hakux/HakuxCoreActivity",
            )
            for method in ("onCreate(Landroid/os/Bundle;)V", "onResume()V", "onPause()V", "onStop()V")
        ],
    },
}


def find_android_jar(build_tools: Path) -> Path:
    platforms = build_tools.parent.parent / "platforms"
    jars = sorted(platforms.glob("android-*/android.jar"))
    if not jars:
        sys.exit(f"No android.jar under {platforms}. Install an SDK platform.")
    return jars[-1]


def compile_helper(work: Path, build_tools: Path) -> Path:
    """XboxEmuFix.java -> classes.dex (javac, then d8)."""
    android_jar = find_android_jar(build_tools)
    classes = work / "helper_classes"
    out = work / "helper_dex"
    for d in (classes, out):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    javac = shutil.which("javac") or sys.exit("javac not found (install a JDK).")
    run([javac, "-source", "8", "-target", "8", "-bootclasspath", str(android_jar),
         "-Xlint:-options", "-d", str(classes), str(HELPER_SRC)])
    d8 = build_tools / ("d8.bat" if os.name == "nt" else "d8")
    run([str(d8), "--min-api", "26", "--lib", str(android_jar), "--output", str(out),
         *[str(p) for p in classes.rglob("*.class")]])
    return out / "classes.dex"


def download_release(app: dict, work: Path) -> Path:
    repo = app["github"]
    if not repo:
        sys.exit("--download is only available for hakuX; pass --apk or pull from the device.")
    with urllib.request.urlopen(f"https://api.github.com/repos/{repo}/releases/latest") as r:
        release = json.load(r)
    asset = next((a for a in release["assets"] if re.fullmatch(app["asset"], a["name"])), None)
    if asset is None:
        sys.exit(f"{repo} {release['tag_name']} has no {app['asset']}.")
    dst = work / "base.apk"
    print(f"  downloading {repo} {release['tag_name']} {asset['name']}")
    urllib.request.urlretrieve(asset["browser_download_url"], dst)
    return dst


def adb_pull(package: str, work: Path) -> tuple[Path, list[Path]]:
    out = subprocess.run(["adb", "shell", "pm", "path", package],
                         capture_output=True, text=True, check=True).stdout
    paths = [ln.split("package:", 1)[1].strip() for ln in out.splitlines() if "package:" in ln]
    if not paths:
        sys.exit(f"{package} is not installed on the device. Pass --apk (or --download).")
    base, splits = None, []
    for remote in paths:
        local = work / remote.rsplit("/", 1)[-1]
        run(["adb", "pull", remote, str(local)])
        if local.name == "base.apk":
            base = local
        else:
            splits.append(local)
    if base is None:
        sys.exit("No base.apk among the installed splits.")
    return base, splits


def call_line(helper: str) -> str:
    return (f"\n    # GameSync patch (tools/patch_xbox_emu.py)\n"
            f"    invoke-static/range {{p0 .. p0}}, {HELPER_CLASS}->{helper}(Landroid/content/Context;)V\n")


def hook(smali: Path, method: str, helper: str) -> bool:
    """Calls helper(this) right after method's super call; adds the method if it isn't there."""
    text = smali.read_text(encoding="utf-8")
    name = method.split("(", 1)[0]
    sig = method[len(name):]
    if f"{PATCH_MARKER}{helper}" in text and re.search(
            rf"\.method [^\n]* {re.escape(name)}{re.escape(sig)}\n(?:(?!\.end method).)*{re.escape(PATCH_MARKER)}",
            text, re.S):
        print(f"  = {smali.stem}.{name} already patched")
        return False
    m = re.search(rf"\.method [^\n]* {re.escape(name)}{re.escape(sig)}\n(.*?)\.end method", text, re.S)
    if m:
        body_start = m.start(1)
        sup = re.compile(
            rf"invoke-super(?:/range)? \{{[^}}]*\}}, L[^;]+;->{re.escape(name)}{re.escape(sig)}\n")
        s = sup.search(text, body_start, m.end())
        if not s:
            print(f"  ! {smali.stem}.{name} has no super call; not hooked")
            return False
        text = text[: s.end()] + call_line(helper) + text[s.end():]
    else:
        sup_class = re.search(r"^\.super (L[^;]+;)", text, re.M).group(1)
        params = sig[1: sig.index(")")]
        regs = "{p0, p1}" if params else "{p0}"
        text += (f"\n.method public {name}{sig}\n    .locals 0\n\n"
                 f"    invoke-super {regs}, {sup_class}->{name}{sig}\n"
                 f"{call_line(helper)}\n    return-void\n.end method\n")
    smali.write_text(text, encoding="utf-8")
    print(f"  + {smali.stem}.{name} -> {helper}")
    return True


def patch_smali(decoded: Path, app: dict) -> int:
    patched = 0
    for cls, method, helper in app["hooks"]:
        hits = [p for d in decoded.glob("smali*") for p in [d / f"{cls}.smali"] if p.is_file()]
        if not hits:
            print(f"  ! {cls} not found (renamed in this release?); skipped")
            continue
        patched += hook(hits[0], method, helper)
    return patched


def add_dex(apk: Path, dex: Path) -> None:
    """The helper as the next classesN.dex (the runtime loads them in order)."""
    with zipfile.ZipFile(apk) as z:
        names = z.namelist()
    if any("com/savesync/xpatch" in n for n in names):
        return
    nums = [int(m.group(1) or 1) for n in names for m in [re.fullmatch(r"classes(\d*)\.dex", n)] if m]
    target = f"classes{max(nums) + 1}.dex"
    with zipfile.ZipFile(apk, "a", compression=zipfile.ZIP_DEFLATED) as z:
        z.write(dex, target)
    print(f"  + {target} (XboxEmuFix)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Patch hakuX / X1 BOX so GameSync can sync Xbox saves.")
    ap.add_argument("--app", required=True, choices=sorted(APPS))
    ap.add_argument("--apk", help="local base APK (skip pulling from the device)")
    ap.add_argument("--split", action="append", default=[], help="local split APK(s); repeatable")
    ap.add_argument("--download", action="store_true", help="hakuX: patch the latest GitHub release")
    ap.add_argument("--apktool", help="path to apktool.jar")
    ap.add_argument("--build-tools", help="path to an Android SDK build-tools dir")
    ap.add_argument("--workdir", default=str(HERE / "xbox_patch_work"))
    ap.add_argument("--no-install", action="store_true", help="produce signed APKs, don't install")
    ap.add_argument("--yes", action="store_true", help="don't ask before uninstalling")
    args = ap.parse_args()
    app = APPS[args.app]
    package = app["package"]

    for tool in ("java", "javac"):
        if not find_tool(tool):
            sys.exit(f"{tool} not on PATH (install a JDK).")
    if not args.no_install and not find_tool("adb"):
        sys.exit("adb not on PATH (needed to install). Use --no-install to only build.")
    apktool = find_apktool(args.apktool)
    bt = find_build_tools(args.build_tools)
    keytool = find_keytool()
    apksigner = bt / ("apksigner.bat" if os.name == "nt" else "apksigner")
    zipalign = bt / ("zipalign.exe" if os.name == "nt" else "zipalign")

    work = Path(args.workdir) / args.app
    work.mkdir(parents=True, exist_ok=True)
    print(f"[1/6] workspace: {work}")

    print("[2/6] getting the APK")
    splits: list[Path] = []
    if args.apk:
        base = work / "base.apk"
        shutil.copy(args.apk, base)
        for s in args.split:
            splits.append(Path(shutil.copy(s, work / Path(s).name)))
    elif args.download:
        base = download_release(app, work)
    else:
        base, splits = adb_pull(package, work)

    print("[3/6] decoding + patching")
    decoded = work / "decoded"
    shutil.rmtree(decoded, ignore_errors=True)
    # -r: resources stay as they are; only code changes.
    run(["java", "-jar", apktool, "d", "-r", "-f", "-o", str(decoded), str(base)])
    if patch_smali(decoded, app) == 0:
        print("  (no new hooks: already patched, or the classes changed)")

    print("[4/6] rebuilding")
    built = work / "base_patched.apk"
    run(["java", "-jar", apktool, "b", str(decoded), "-o", str(built)])
    add_dex(built, compile_helper(work, bt))

    print("[5/6] aligning + signing")
    keystore = Path(args.workdir) / "xbox.keystore"
    if not keystore.exists():
        run([keytool, "-genkeypair", "-keystore", str(keystore), "-alias", KEY_ALIAS,
             "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
             "-storepass", KEYSTORE_PASS, "-keypass", KEYSTORE_PASS,
             "-dname", "CN=XboxPatch, O=GameSync, C=US"])

    def align_and_sign(src: Path, name: str) -> Path:
        out = work / name
        run([str(zipalign), "-p", "-f", "4", str(src), str(out)])
        run([str(apksigner), "sign", "--ks", str(keystore), "--ks-pass", f"pass:{KEYSTORE_PASS}",
             "--key-pass", f"pass:{KEYSTORE_PASS}", str(out)])
        return out

    signed = [align_and_sign(built, "base_signed.apk")]
    signed += [align_and_sign(s, f"{s.stem}_signed.apk") for s in splits]
    print("  signed:", ", ".join(p.name for p in signed))

    install = ["adb", "install-multiple", "-r", *[str(p) for p in signed]] if len(signed) > 1 \
        else ["adb", "install", "-r", str(signed[0])]
    if args.no_install:
        print(f"[6/6] --no-install: APKs in {work}. Install with:")
        print(f"  adb uninstall {package}   # only if the installed one isn't patched")
        print("  " + " ".join(install))
        return

    print("[6/6] installing")
    # Same key as the installed build: an in-place update keeps the data.
    if subprocess.run(install).returncode == 0:
        print("\nUpdated in place. Open the emulator once, then scan in GameSync.")
        return
    print(f"\nThe installed {package} is signed with another key: it has to be uninstalled,")
    print("which DELETES ITS DATA (hakuX's hard disk / X1 BOX's hdd.img).")
    print("Back up first: hakuX -> Hard Disk -> Export saves; X1 BOX -> Settings -> export the hard disk.")
    if not args.yes and input("Uninstall and install the patched build? [y/N] ").strip().lower() != "y":
        sys.exit("Stopped; nothing was uninstalled.")
    subprocess.run(["adb", "uninstall", package])
    run(install)
    print("\nDone. Open the emulator, run its setup (and import the saves you exported),")
    print("then scan in GameSync.")


if __name__ == "__main__":
    main()
