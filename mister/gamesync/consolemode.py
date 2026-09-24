"""Open GameSync (and other Scripts) from Console Mode's own menu.

Console Mode (Retro-Remake's MiSTer frontend) builds its main menu from
``ConsoleMode/themeconfig/section_groups/*.ini``, and those sections only
launch games: picking a ``.sh`` in one makes its UI exit as for a game
(exit code 10), the host finds no core for it, and the user is left on the
bare MiSTer menu. Scripts are only run through Dev Tools -> Load Script,
which exits the UI with code 12 after writing the script's path to
``/tmp/consolemode_script_path``; the host then runs it on tty2 and brings
the UI back when it ends. That handoff is what this module reproduces.

A resident watcher, started at boot from ``linux/user-startup.sh`` (the same
hook MisterZine uses for its menu entry), polls ``/tmp/ACTIVEGAME``, which
Console Mode writes with the path of every launch. When it names our
launcher, the watcher keeps the exit-code file at 12 while the UI shuts
down, so the host takes the Load Script path instead of the game path. Any
script in /media/fat/Scripts is handled the same way, which is what lets the
menu group offer every script and not just this one. If
that race is lost, it reloads Console Mode's menu core and hands off from
the freshly started UI, which is slower but always works.

Only the standard library, and nothing imported after start-up: the watcher
outlives reinstalls of the zipapp it was loaded from.
"""

from __future__ import annotations

import errno
import os
import signal
import time

CM_DIR = "/media/fat/ConsoleMode"
CM_FRONTEND = CM_DIR + "/ConsoleMode_arm"
CM_MENU_CORE = CM_DIR + "/menu_ConsoleMode.rbf"
SECTION_DIR = CM_DIR + "/themeconfig/section_groups"
#: The ini's file name is the group's label in Console Mode's top menu.
SECTION_INI = SECTION_DIR + "/Scripts.ini"

ACTIVE_GAME = "/tmp/ACTIVEGAME"
SCRIPT_PATH_FILE = "/tmp/consolemode_script_path"
EXIT_CODE_FILE = "/tmp/consolemode_exit_code"
KEEP_SHELL = "12"

SCRIPTS_DIR = "/media/fat/Scripts"
LAUNCHER = SCRIPTS_DIR + "/GameSync.sh"
PYZ = "/media/fat/Scripts/.gamesync/gamesync.pyz"
STARTUP = "/media/fat/linux/user-startup.sh"
PID_FILE = "/tmp/gamesync_cm_launcher.pid"
LOG = "/media/fat/Scripts/.config/gamesync/cm_launcher.log"

WATCH_FLAG = "--console-mode-watch"
BEGIN = "# GameSync Console Mode launcher BEGIN"
END = "# GameSync Console Mode launcher END"
STARTUP_BLOCK = (
    BEGIN + "\n"
    "[ -e %s ] && [ -e %s ] && python3 %s %s >/dev/null 2>&1 &\n"
    % (PYZ, CM_FRONTEND, PYZ, WATCH_FLAG)
    + END + "\n"
)

#: One system only: Console Mode skips the system screen for a group with a
#: single entry, so this is group -> script list -> launch. A second entry
#: brings that screen back and costs every script an extra click.
SECTION_BODY = (
    "[CONSOLES]\n"
    "consoleList = SCRIPTS\n"
    "\n"
    "[SCRIPTS]\n"
    "execs = none\n"
    "romExts = .sh\n"
    "romDirs = %s/\n" % SCRIPTS_DIR
)

#: Earlier Scripts.ini bodies of ours, replaced on setup. Anything else in
#: that file is the user's and is left alone.
OLD_SECTION_BODIES = (
    "[CONSOLES]\n"
    "consoleList = GAMESYNC,ALL SCRIPTS\n"
    "\n"
    "[GAMESYNC]\n"
    "execs = none\n"
    "romExts = .sh\n"
    "romDirs = %s\n"
    "\n"
    "[ALL SCRIPTS]\n"
    "execs = none\n"
    "romExts = .sh\n"
    "romDirs = %s/\n" % (LAUNCHER, SCRIPTS_DIR),
)

#: Console Mode names a group's tiles after the ini, lower-cased.
TILES = (("console1.png", "scripts1.png"),
         ("console-dark.png", "scripts-dark.png"))

#: The first version's group, named GameSync. Removed on setup, but only
#: when it is exactly what we wrote, never a user's own file of that name.
LEGACY_SECTION_INI = SECTION_DIR + "/GameSync.ini"
LEGACY_SECTION_BODY = (
    "[CONSOLES]\n"
    "consoleList = GAMESYNC\n"
    "\n"
    "[GAMESYNC]\n"
    "execs = none\n"
    "romExts = .sh\n"
    "romDirs = %s\n" % LAUNCHER
)
LEGACY_TILES = ("gamesync1.png", "gamesync-dark.png")


def installed() -> bool:
    return os.path.exists(CM_FRONTEND)


# ---------------------------------------------------------------- processes


def _pids(match) -> list:
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            if match(int(entry)):
                pids.append(int(entry))
        except OSError:
            continue
    return pids


def _comm(pid: int) -> str:
    with open("/proc/%d/comm" % pid) as handle:
        return handle.read().strip()


def _cmdline(pid: int) -> bytes:
    with open("/proc/%d/cmdline" % pid, "rb") as handle:
        return handle.read()


def frontend_pids() -> list:
    # comm is cut to 15 characters, which "ConsoleMode_arm" exactly fills.
    return _pids(lambda pid: _comm(pid) == "ConsoleMode_arm")


def script_running(script: str) -> bool:
    name = os.path.basename(script).encode()
    return bool(_pids(lambda pid: name in _cmdline(pid)
                      and pid != os.getpid()))


def watcher_pids() -> list:
    flag = WATCH_FLAG.encode()
    return _pids(lambda pid: flag in _cmdline(pid).split(b"\0")
                 and pid != os.getpid())


# ------------------------------------------------------------------ handoff


def _write(path: str, text: str) -> None:
    with open(path, "w") as handle:
        handle.write(text)


def _read(path: str) -> str | None:
    try:
        with open(path) as handle:
            return handle.read().strip()
    except OSError:
        return None


def request_script(script: str) -> None:
    """What Load Script leaves behind for the host to find."""
    _write(SCRIPT_PATH_FILE, script)
    _write(EXIT_CODE_FILE, KEEP_SHELL + "\n")


def send_main_cmd(line: str, attempts: int = 40) -> bool:
    """One line to Main's FIFO, which vanishes briefly on every core load."""
    for _ in range(attempts):
        try:
            fd = os.open("/dev/MiSTer_cmd", os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno not in (errno.ENOENT, errno.ENXIO):
                return False
            time.sleep(0.25)
            continue
        try:
            os.write(fd, (line + "\n").encode())
            return True
        finally:
            os.close(fd)
    return False


def _wait(predicate, seconds: float, step: float = 0.05) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


def is_script(path) -> bool:
    """A launch Console Mode can't run itself: a .sh from the Scripts folder."""
    return (bool(path) and path.endswith(".sh")
            and os.path.dirname(path) == SCRIPTS_DIR and os.path.isfile(path))


def hand_off(script: str, log) -> None:
    """Turn the UI's game-launch exit into a Load Script exit."""
    # Fast path: the UI writes ACTIVEGAME a little before it exits, and the
    # host only settles on the exit code once the UI's wrapper has returned.
    # Holding the file at 12 across that window makes it a script launch.
    request_script(script)
    deadline = time.monotonic() + 4.0
    while frontend_pids() and time.monotonic() < deadline:
        if _read(EXIT_CODE_FILE) != KEEP_SHELL:
            request_script(script)
        time.sleep(0.005)
    # The host reads the file once more after the wrapper exits, and deletes
    # it when it has. Only correct what is there: recreating a consumed file
    # would leave a stale 12 for the next ordinary exit.
    tail = time.monotonic() + 0.4
    while time.monotonic() < tail:
        code = _read(EXIT_CODE_FILE)
        if code is not None and code != KEEP_SHELL:
            request_script(script)
        time.sleep(0.005)

    if _wait(lambda: script_running(script), 3.0):
        log("handoff: host took the script path")
        return

    # Slow path: the host went the game way and is sitting on its own menu.
    # Bring Console Mode back, then leave it the way Load Script does.
    log("handoff: host treated it as a game; restarting Console Mode")
    if not send_main_cmd("load_core " + CM_MENU_CORE):
        log("handoff: could not reach /dev/MiSTer_cmd")
        return
    if not _wait(frontend_pids, 30.0, 0.1):
        log("handoff: Console Mode did not come back")
        return
    time.sleep(1.5)  # let the UI finish starting before it is asked to leave
    request_script(script)
    for pid in frontend_pids():
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    if _wait(lambda: script_running(script), 5.0):
        log("handoff: script started after restart")
    else:
        log("handoff: script did not start")


# -------------------------------------------------------------------- watch


def _mtime(path: str):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def watch() -> int:
    if watcher_pids():
        return 0
    try:
        _write(PID_FILE, str(os.getpid()))
    except OSError:
        pass

    def log(message):
        try:
            with open(LOG, "a") as handle:
                handle.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                          message))
        except OSError:
            pass

    log("watch: started, pid %d" % os.getpid())
    # Whatever ACTIVEGAME says now happened before we were watching.
    seen = _mtime(ACTIVE_GAME)
    while True:
        time.sleep(0.02)
        stamp = _mtime(ACTIVE_GAME)
        if stamp is None or stamp == seen:
            continue
        seen = stamp
        script = _read(ACTIVE_GAME)
        if not is_script(script):
            continue
        log("watch: %s picked in Console Mode" % os.path.basename(script))
        try:
            hand_off(script, log)
        except Exception as exc:  # noqa: BLE001 - the watcher must survive
            log("handoff failed: %r" % exc)


# -------------------------------------------------------------------- setup


def _startup_text() -> str:
    try:
        with open(STARTUP) as handle:
            return handle.read()
    except OSError:
        return "#!/bin/sh\n"


def _without_block(text: str) -> str:
    out, skipping = [], False
    for line in text.splitlines(True):
        if line.strip() == BEGIN:
            skipping = True
        elif line.strip() == END:
            skipping = False
        elif not skipping:
            out.append(line)
    return "".join(out)


def setup() -> int:
    """Add the menu entry and the boot hook, and start the watcher now."""
    if not installed():
        print("Console Mode is not installed; nothing to do.")
        return 0
    if _read(LEGACY_SECTION_INI) == LEGACY_SECTION_BODY.strip():
        for path in [LEGACY_SECTION_INI] + [os.path.join(SECTION_DIR, name)
                                            for name in LEGACY_TILES]:
            try:
                os.remove(path)
            except OSError:
                pass
    current = _read(SECTION_INI)
    if current is None or current in [b.strip() for b in OLD_SECTION_BODIES]:
        _write(SECTION_INI, SECTION_BODY)
    print("  menu entry           %s" % SECTION_INI)
    for source, target in TILES:
        source, target = (os.path.join(SECTION_DIR, source),
                          os.path.join(SECTION_DIR, target))
        if os.path.exists(source) and not os.path.exists(target):
            with open(source, "rb") as src, open(target, "wb") as dst:
                dst.write(src.read())

    text = _without_block(_startup_text())
    if not text.endswith("\n"):
        text += "\n"
    _write(STARTUP, text + "\n" + STARTUP_BLOCK)
    os.chmod(STARTUP, 0o755)
    print("  boot hook            %s" % STARTUP)

    stop()
    pid = os.fork()
    if pid == 0:
        os.setsid()
        null = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(null, fd)
        os.execvp("python3", ["python3", PYZ, WATCH_FLAG])
    print("  launcher             running (pid %d)" % pid)
    print("Clear Console Mode's cache (or reboot) to see GameSync in its menu.")
    return 0


def stop() -> None:
    for pid in watcher_pids():
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def remove() -> int:
    stop()
    paths = [SECTION_INI] + [os.path.join(SECTION_DIR, t) for _s, t in TILES]
    if _read(LEGACY_SECTION_INI) == LEGACY_SECTION_BODY.strip():
        paths += [LEGACY_SECTION_INI] + [os.path.join(SECTION_DIR, name)
                                         for name in LEGACY_TILES]
    for path in paths:
        try:
            os.remove(path)
            print("  removed              %s" % path)
        except OSError:
            pass
    text = _startup_text()
    stripped = _without_block(text)
    if stripped != text:
        _write(STARTUP, stripped.rstrip("\n") + "\n")
        print("  boot hook removed    %s" % STARTUP)
    return 0
