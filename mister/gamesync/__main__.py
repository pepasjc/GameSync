"""Entry point for the MiSTer GameSync client.

MiSTer launches Scripts without TERM set and with stdin/stdout on /dev/tty2.
The framebuffer is taken over for drawing and always handed back, including on
a signal, so a crash never leaves the console in graphics mode.
"""

import os
import signal
import sys
import traceback

#: Logs sit with the config, following the MiSTer script convention.
LOG_PATH = "/media/fat/Scripts/.config/gamesync/gamesync.log"


class QuietConsole:
    """Keep the script VT from echoing while the framebuffer is ours.

    MiSTer translates pad presses into keyboard events as well, and the
    console we were started on (tty2) keeps echoing them - every D-pad
    press lands as an escape sequence on a screen we have painted over.
    They all show up the moment the framebuffer is handed back, as a wall
    of ``^[[A^[[B`` above the prompt. So while the app runs the tty has
    echo and line editing off, and on the way out whatever queued up is
    flushed and the screen cleared, so the console comes back blank.
    """

    def __init__(self, fd=0):
        self.fd = fd
        self._saved = None

    def __enter__(self):
        try:
            import termios

            if not os.isatty(self.fd):
                return self
            self._saved = termios.tcgetattr(self.fd)
            attrs = termios.tcgetattr(self.fd)
            attrs[3] &= ~(termios.ECHO | termios.ICANON)
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        except Exception:
            self._saved = None
        return self

    def __exit__(self, *_exc):
        if self._saved is None:
            return
        try:
            import termios

            termios.tcflush(self.fd, termios.TCIOFLUSH)
            termios.tcsetattr(self.fd, termios.TCSANOW, self._saved)
            os.write(self.fd, b"\033[2J\033[H")
        except Exception:
            pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    os.environ.setdefault("TERM", "linux")

    # Console Mode menu entry. Dispatched before the UI is imported: the
    # watcher stays resident and must not hold anything it doesn't need.
    if "--console-mode-watch" in argv:
        from . import consolemode

        return consolemode.watch()
    if "--console-mode-setup" in argv:
        from . import consolemode

        return consolemode.setup()
    if "--precompile" in argv:
        # Run by the installer on the device, right after the upload.
        from . import precompile

        archive = getattr(__loader__, "archive", "") or sys.argv[0]
        print("  bytecode             %d modules compiled"
              % precompile.compile_archive(archive))
        return 0
    if "--console-mode-remove" in argv:
        from . import consolemode

        return consolemode.remove()

    from .app import App

    app = None

    def bail(signum, _frame):
        if app is not None:
            app.close()
        sys.exit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(sig, bail)
        except (ValueError, OSError):
            pass

    if "--selftest" in argv:
        return selftest()

    timeout = None
    start_tab = 0
    show_conflict = False
    calibrate = False
    demo_confirm = False
    demo_choose = False
    demo_search = False
    for index, arg in enumerate(argv):
        if arg == "--timeout" and index + 1 < len(argv):
            timeout = float(argv[index + 1])
        # Lets a screenshot or a smoke test land on a given tab without
        # anyone having to press a button.
        elif arg == "--tab" and index + 1 < len(argv):
            start_tab = int(argv[index + 1])
        elif arg == "--show-conflict":
            # Opens the conflict dialog for the first conflicting save, so its
            # layout can be checked without staging a conflict by hand.
            show_conflict = True
        elif arg == "--demo-confirm":
            # Opens a sample confirmation so its layout can be checked on a
            # real display without performing the operation behind it.
            demo_confirm = True
        elif arg == "--demo-choose":
            demo_choose = True
        elif arg == "--demo-search":
            demo_search = True
        elif arg == "--calibrate":
            # Straight into screen adjustment. On a cabinet whose overscan is
            # bad enough to hide the Settings tab, this is the way in.
            calibrate = True

    try:
        with QuietConsole():
            app = App()
            _log_display(app.fb)
            app.run(timeout=timeout, start_tab=start_tab,
                    show_conflict=show_conflict, calibrate=calibrate,
                    demo_confirm=demo_confirm, demo_choose=demo_choose,
                    demo_search=demo_search)
            app.close()
    except Exception:
        report = traceback.format_exc()
        try:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            with open(LOG_PATH, "a") as handle:
                handle.write(report + "\n")
        except OSError:
            pass
        if app is not None:
            app.close()
        sys.stderr.write(report)
        return 1
    finally:
        if app is not None:
            app.close()
    return 0


def _console_mode() -> bool:
    """Whether Retro-Remake's Console Mode host is the one running us."""
    if os.path.exists("/tmp/consolemode_script_path"):
        return True
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                # comm is cut to 15 characters: "MiSTer_ConsoleM".
                with open("/proc/%s/comm" % pid) as handle:
                    if handle.read().strip() == "MiSTer_ConsoleMode"[:15]:
                        return True
            except OSError:
                continue
    except OSError:
        pass
    return False


def _log_display(fb):
    """Record the geometry each launch inherited. Never fatal."""
    try:
        import time

        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as handle:
            handle.write("%s start %s console_mode=%d\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), fb.describe(),
                int(_console_mode())))
    except Exception:
        pass


def selftest():
    """Check everything the client needs, without taking over the screen.

    Run by the installer so a successful install is verified rather than
    assumed. Prints one line per check and returns non-zero if any fail.
    """
    from . import __version__

    failures = []

    def check(label, function):
        try:
            print("  %-22s %s" % (label, function()))
        except Exception as exc:  # noqa: BLE001
            print("  %-22s FAIL: %s" % (label, exc))
            failures.append(label)

    print("GameSync %s selftest" % __version__)
    print("  %-22s %s" % ("python",
                          sys.version.split()[0] + " on " + sys.platform))

    def shared_rules():
        from shared.rom_id import make_title_id
        from shared.mister_saves import needs_payload_read

        slug = make_title_id("GBA", "Zelda - Minish Cap (USA).gba")
        if slug != "GBA_zelda_minish_cap_usa":
            raise RuntimeError("unexpected slug %r" % slug)
        if not needs_payload_read("PS1"):
            raise RuntimeError("PS1 rule missing")
        return "ok (%s)" % slug

    def saturn():
        from shared.saturn_format import normalize_saturn_save  # noqa: F401

        return "ok"

    def framebuffer():
        from .fb import Framebuffer

        surface = Framebuffer(take_over_console=False).open()
        try:
            return "%dx%d @ %d bpp" % (surface.width, surface.height,
                                       surface.bytes_per_pixel * 8)
        finally:
            surface.close()

    def font():
        import pkgutil

        from .text import Font

        data = pkgutil.get_data("gamesync", "assets/font.ttf")
        rendered = Font(data, 18)
        return "%d glyphs rasterised" % len(rendered.glyphs)

    def controllers():
        from .input import InputReader

        reader = InputReader()
        try:
            names = reader.device_names()
            return ", ".join(names) if names else "none detected"
        finally:
            reader.close()

    check("shared rules", shared_rules)
    check("saturn format", saturn)
    check("framebuffer", framebuffer)
    check("font / freetype", font)
    check("input devices", controllers)

    if failures:
        print("FAILED: %s" % ", ".join(failures))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
