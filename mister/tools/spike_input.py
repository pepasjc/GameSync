#!/usr/bin/env python3
"""GameSync MiSTer Phase 0 spike.

Answers, on real hardware, the questions the on-device client design depends on:

  1. Does the MiSTer Scripts console give us a usable curses terminal
     (TERM, size, colors, keypad)?
  2. Does MiSTer's pad -> keyboard translation ("MiSTer virtual input") reach
     a script's stdin, so curses getch() alone is enough for navigation?
  3. If not, which node still delivers input while MiSTer main is running?
     MiSTer takes an exclusive EVIOCGRAB on real pads, and a grabbed evdev
     node delivers events only to the grabber - but joydev (/dev/input/js*)
     is a separate handler and is not affected by an evdev grab.
  4. Which evdev / joydev codes this controller actually emits per button.

Usage:
    python3 spike_input.py --probe     enumeration only, safe over SSH
    python3 spike_input.py             full interactive capture (needs a tty)

MiSTer runs Scripts as `/bin/bash /tmp/script` with stdin/stdout on /dev/tty2,
so the interactive mode can also be driven over SSH with:

    python3 spike_input.py < /dev/tty2 > /dev/tty2 2>&1

Writes /media/fat/Scripts/.gamesync/spike_report.txt either way.
"""

import array
import fcntl
import glob
import os
import struct
import sys
import time
import traceback

REPORT_DIR = "/media/fat/Scripts/.gamesync"
REPORT_PATH = os.path.join(REPORT_DIR, "spike_report.txt")

EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

# struct js_event { __u32 time; __s16 value; __u8 type; __u8 number; }
JS_FORMAT = "IhBB"
JS_SIZE = struct.calcsize(JS_FORMAT)
JS_EVENT_BUTTON, JS_EVENT_AXIS, JS_EVENT_INIT = 0x01, 0x02, 0x80

EV_SYN, EV_KEY, EV_REL, EV_ABS = 0x00, 0x01, 0x02, 0x03

BTN_NAMES = {
    0x130: "BTN_SOUTH/A", 0x131: "BTN_EAST/B", 0x132: "BTN_C",
    0x133: "BTN_NORTH/Y", 0x134: "BTN_WEST/X", 0x135: "BTN_Z",
    0x136: "BTN_TL/L1", 0x137: "BTN_TR/R1", 0x138: "BTN_TL2/L2",
    0x139: "BTN_TR2/R2", 0x13a: "BTN_SELECT", 0x13b: "BTN_START",
    0x13c: "BTN_MODE", 0x13d: "BTN_THUMBL", 0x13e: "BTN_THUMBR",
}
ABS_NAMES = {
    0x00: "ABS_X", 0x01: "ABS_Y", 0x02: "ABS_Z", 0x03: "ABS_RX",
    0x04: "ABS_RY", 0x05: "ABS_RZ", 0x10: "ABS_HAT0X", 0x11: "ABS_HAT0Y",
}

BTN_SOUTH = 0x130

# Nodes that flood thousands of events per second and carry nothing we want.
# Matched case-insensitively against the evdev device name.
NOISY_NAME_PARTS = ("motion sensor", "touchpad", "accelerometer", "gyro")


# ---------------------------------------------------------------- ioctl glue

_IOC_NRBITS, _IOC_TYPEBITS, _IOC_SIZEBITS = 8, 8, 14
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS
_IOC_WRITE, _IOC_READ = 1, 2


def _ioc(direction, typ, nr, size):
    return ((direction << _IOC_DIRSHIFT) | (ord(typ) << _IOC_TYPESHIFT)
            | (nr << _IOC_NRSHIFT) | (size << _IOC_SIZESHIFT))


def eviocgname(fd, length=256):
    buf = array.array("B", [0] * length)
    fcntl.ioctl(fd, _ioc(_IOC_READ, "E", 0x06, length), buf)
    return bytes(buf).split(b"\x00")[0].decode("utf-8", "replace")


def eviocgbit(fd, ev_type, length=96):
    """Return the capability bitmask for ev_type as a set of codes."""
    buf = array.array("B", [0] * length)
    fcntl.ioctl(fd, _ioc(_IOC_READ, "E", 0x20 + ev_type, length), buf)
    codes = set()
    for byte_index, byte in enumerate(buf):
        for bit in range(8):
            if byte & (1 << bit):
                codes.add(byte_index * 8 + bit)
    return codes


EVIOCGRAB = _ioc(_IOC_WRITE, "E", 0x90, 4)


def try_grab(fd):
    """Return (can_grab, detail). Ungrabs again immediately on success."""
    try:
        fcntl.ioctl(fd, EVIOCGRAB, 1)
    except OSError as exc:
        return False, "grab refused: %s" % exc
    try:
        fcntl.ioctl(fd, EVIOCGRAB, 0)
    except OSError as exc:
        return True, "grabbed but ungrab failed: %s" % exc
    return True, "grab+ungrab OK (device was not exclusively held)"


# -------------------------------------------------------------- input source

class Source(object):
    """One readable input node we are watching.

    read_events() returns (channel, value) pairs where channel is a stable,
    low-cardinality string. Analogue axes must not put their value in the
    channel name or the per-prompt summary grows without bound - which is
    exactly how an earlier version of this spike went quadratic and hung.
    """

    def __init__(self, path, name, fd, kind):
        self.path = path
        self.name = name
        self.fd = fd
        self.kind = kind          # "evdev" or "joydev"
        self.label = "%s %s" % (path, name)

    def read_events(self):
        try:
            data = os.read(self.fd, 8192)
        except OSError:
            return []
        out = []
        if self.kind == "evdev":
            for offset in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                _, _, ev_type, code, value = struct.unpack(
                    EVENT_FORMAT, data[offset:offset + EVENT_SIZE])
                if ev_type == EV_SYN:
                    continue
                if ev_type == EV_KEY:
                    out.append(("KEY %s" % BTN_NAMES.get(
                        code, "code %d (0x%x)" % (code, code)), value))
                elif ev_type == EV_ABS:
                    out.append(("ABS %s" % ABS_NAMES.get(
                        code, "code %d (0x%x)" % (code, code)), value))
                elif ev_type == EV_REL:
                    out.append(("REL code %d" % code, value))
                else:
                    out.append(("type 0x%02x code %d" % (ev_type, code), value))
        else:
            for offset in range(0, len(data) - JS_SIZE + 1, JS_SIZE):
                _, value, ev_type, number = struct.unpack(
                    JS_FORMAT, data[offset:offset + JS_SIZE])
                initial = " (init)" if ev_type & JS_EVENT_INIT else ""
                base = ev_type & ~JS_EVENT_INIT
                if base == JS_EVENT_BUTTON:
                    out.append(("JS button %d%s" % (number, initial), value))
                elif base == JS_EVENT_AXIS:
                    out.append(("JS axis %d%s" % (number, initial), value))
                else:
                    out.append(("JS type 0x%02x n=%d" % (ev_type, number),
                                value))
        return out


class Channel(object):
    """Bounded accumulator for one (node, channel) pair within one prompt."""

    __slots__ = ("count", "low", "high", "values")

    def __init__(self):
        self.count = 0
        self.low = None
        self.high = None
        self.values = set()

    def add(self, value):
        self.count += 1
        if self.low is None or value < self.low:
            self.low = value
        if self.high is None or value > self.high:
            self.high = value
        if len(self.values) < 6:
            self.values.add(value)

    def describe(self):
        if self.low == self.high:
            return "= %d  (x%d)" % (self.low, self.count)
        sample = ", ".join(str(v) for v in sorted(self.values)[:6])
        return "range %d..%d  (x%d, e.g. %s)" % (self.low, self.high,
                                                 self.count, sample)


# ------------------------------------------------------------ report helpers

class Report(object):
    def __init__(self):
        self.lines = []

    def __call__(self, text=""):
        self.lines.append(text)

    def section(self, title):
        self("")
        self("=" * 70)
        self(title)
        self("=" * 70)

    def write(self):
        try:
            os.makedirs(REPORT_DIR, exist_ok=True)
            with open(REPORT_PATH, "w") as handle:
                handle.write("\n".join(self.lines) + "\n")
            return REPORT_PATH
        except Exception as exc:  # noqa: BLE001 - report must never crash
            return "FAILED to write report: %s" % exc


# ----------------------------------------------------------------- section 1

def probe_environment(report):
    report.section("1. ENVIRONMENT")
    report("python           : %s" % sys.version.replace("\n", " "))
    report("argv             : %r" % (sys.argv,))
    report("cwd              : %s" % os.getcwd())
    report("TERM             : %r" % os.environ.get("TERM"))
    for stream, label in ((0, "stdin"), (1, "stdout"), (2, "stderr")):
        try:
            report("%-17s: isatty=%s ttyname=%s"
                   % (label, os.isatty(stream),
                      os.ttyname(stream) if os.isatty(stream) else "-"))
        except Exception as exc:  # noqa: BLE001
            report("%-17s: error %s" % (label, exc))
    try:
        size = os.get_terminal_size()
        report("terminal size    : %dx%d" % (size.columns, size.lines))
    except Exception as exc:  # noqa: BLE001
        report("terminal size    : unavailable (%s)" % exc)
    try:
        with open("/sys/class/tty/tty0/active") as handle:
            report("active VT        : %s" % handle.read().strip())
    except Exception as exc:  # noqa: BLE001
        report("active VT        : unavailable (%s)" % exc)
    report("euid             : %d" % os.geteuid())


def probe_curses_static(report):
    report.section("2. CURSES (non-interactive checks)")
    try:
        import curses
    except Exception as exc:  # noqa: BLE001
        report("import curses FAILED: %s" % exc)
        return
    report("curses imported  : ok (version %s)"
           % getattr(curses, "version", b"?"))
    try:
        __import__("curses.panel")
        report("curses.panel     : yes")
    except Exception as exc:  # noqa: BLE001
        report("curses.panel     : no (%s)" % exc)
    try:
        curses.setupterm()
        report("setupterm        : ok")
        for cap in ("lines", "cols", "colors"):
            report("  tigetnum(%-6s): %s" % (cap, curses.tigetnum(cap)))
    except Exception as exc:  # noqa: BLE001
        report("setupterm FAILED : %s" % exc)


# ----------------------------------------------------------------- section 3

def is_noisy(name):
    lowered = name.lower()
    return any(part in lowered for part in NOISY_NAME_PARTS)


def probe_devices(report):
    """Enumerate input nodes. Returns the list of Sources worth watching."""
    report.section("3. INPUT DEVICES")
    sources = []
    evdev_names = {}

    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except Exception as exc:  # noqa: BLE001
            report("%-20s OPEN FAILED: %s" % (path, exc))
            continue
        try:
            name = eviocgname(fd)
        except Exception as exc:  # noqa: BLE001
            name = "<name failed: %s>" % exc
        evdev_names[path] = name
        try:
            keys = eviocgbit(fd, EV_KEY, 96)
        except Exception:  # noqa: BLE001
            keys = set()
        try:
            axes = eviocgbit(fd, EV_ABS, 8)
        except Exception:  # noqa: BLE001
            axes = set()

        _, grab_detail = try_grab(fd)
        noisy = is_noisy(name)

        report("")
        report("%s  %r" % (path, name))
        report("    gamepad(BTN_SOUTH) : %s" % (BTN_SOUTH in keys))
        report("    keyboard-ish keys  : %s"
               % any(code in keys for code in (28, 103, 108)))
        report("    EV_KEY codes       : %d  (buttons: %s)"
               % (len(keys),
                  ", ".join(BTN_NAMES[c] for c in sorted(keys)
                            if c in BTN_NAMES) or "none"))
        report("    EV_ABS axes        : %s"
               % (", ".join(ABS_NAMES.get(a, str(a))
                            for a in sorted(axes)) or "none"))
        report("    exclusive grab     : %s" % grab_detail)
        report("    watching           : %s"
               % ("no - noisy sensor node" if noisy else "yes"))

        if noisy:
            os.close(fd)
        else:
            sources.append(Source(path, name, fd, "evdev"))

    # joydev nodes: a grabbed evdev node is silent for us, joydev is not.
    for path in sorted(glob.glob("/dev/input/js*")):
        name = joydev_name(path)
        noisy = is_noisy(name)
        report("")
        report("%s  %r  (joydev - unaffected by an evdev grab)" % (path, name))
        report("    watching           : %s"
               % ("no - noisy sensor node" if noisy else "yes"))
        if noisy:
            continue
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except Exception as exc:  # noqa: BLE001
            report("    OPEN FAILED        : %s" % exc)
            continue
        sources.append(Source(path, name, fd, "joydev"))

    report("")
    report("NOTE: node numbers move when a controller reconnects. This run")
    report("      differs from earlier runs on the same box - the client must")
    report("      always enumerate by name, never by a fixed index.")
    report("watching %d nodes: %s"
           % (len(sources), ", ".join(s.path for s in sources)))
    return sources


def joydev_name(path):
    """JSIOCGNAME(128) - joydev's own name ioctl."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        return "<open failed: %s>" % exc
    try:
        buf = array.array("B", [0] * 128)
        fcntl.ioctl(fd, _ioc(_IOC_READ, "j", 0x13, 128), buf)
        return bytes(buf).split(b"\x00")[0].decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return "<name failed: %s>" % exc
    finally:
        os.close(fd)


def probe_passive_read(report, sources, seconds=3.0):
    """Confirm the nodes are readable and quiet while nobody touches them."""
    report.section("4. PASSIVE READ TEST (%.0fs, no input expected)" % seconds)
    if not sources:
        report("no input nodes found - skipped")
        return
    import select
    by_fd = {s.fd: s for s in sources}
    deadline = time.time() + seconds
    counts = {}
    while time.time() < deadline:
        ready, _, _ = select.select(list(by_fd), [], [], 0.2)
        for fd in ready:
            source = by_fd[fd]
            events = source.read_events()
            if events:
                counts[source.path] = counts.get(source.path, 0) + len(events)
    report("readable without error: yes")
    if counts:
        for path, count in sorted(counts.items()):
            note = " (joydev init burst on open is expected)" \
                if path.startswith("/dev/input/js") else ""
            report("  idle traffic %-22s %d events%s" % (path, count, note))
    else:
        report("no idle traffic on any node")


# ----------------------------------------------------------------- section 5

PROMPTS = [
    ("A / Cross", "primary action"),
    ("B / Circle", "back"),
    ("X / Square", "sync"),
    ("Y / Triangle", "rescan / search"),
    ("L1", "prev system"),
    ("R1", "next system"),
    ("L2", "prev tab"),
    ("R2", "next tab"),
    ("SELECT / Create", "search toggle"),
    ("START / Options", "settings"),
    ("D-PAD UP", "nav"),
    ("D-PAD DOWN", "nav"),
    ("D-PAD LEFT", "page / jump"),
    ("D-PAD RIGHT", "page / jump"),
    ("LEFT STICK: up, then down", "nav axis"),
]

PROMPT_SECONDS = 3.5
MAX_EVENTS_PER_PROMPT = 4000


def interactive_capture(report, sources):
    """Curses UI: capture getch() and every watched node side by side."""
    import curses
    import select

    # per prompt: {(node_label, channel): Channel}
    per_prompt = [dict() for _ in PROMPTS]
    getch_seen = [[] for _ in PROMPTS]
    recent = []

    def run(stdscr):
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.keypad(True)
        report.section("5. CURSES RUNTIME")
        report("COLS x LINES     : %d x %d" % (curses.COLS, curses.LINES))
        report("has_colors       : %s" % curses.has_colors())
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
                report("use_default_colors: ok")
            except Exception as exc:  # noqa: BLE001
                report("use_default_colors: %s" % exc)
            report("COLORS / PAIRS   : %d / %d"
                   % (curses.COLORS, curses.COLOR_PAIRS))
            report("can_change_color : %s" % curses.can_change_color())

        by_fd = {s.fd: s for s in sources}

        for index, (label, purpose) in enumerate(PROMPTS):
            channels = per_prompt[index]
            budget = MAX_EVENTS_PER_PROMPT
            end = time.time() + PROMPT_SECONDS
            while True:
                remaining = end - time.time()
                if remaining <= 0:
                    break
                _draw_prompt(stdscr, curses, index, label, purpose, remaining,
                             recent, getch_seen[index])

                key = stdscr.getch()
                while key != -1:
                    getch_seen[index].append((key, _key_name(curses, key)))
                    key = stdscr.getch()

                if not by_fd:
                    time.sleep(0.05)
                    continue
                ready, _, _ = select.select(list(by_fd), [], [], 0.05)
                for fd in ready:
                    source = by_fd[fd]
                    for channel, value in source.read_events():
                        if budget <= 0:
                            break
                        budget -= 1
                        key_pair = (source.label, channel)
                        entry = channels.get(key_pair)
                        if entry is None:
                            entry = channels[key_pair] = Channel()
                        entry.add(value)
                        recent.append("%s | %s = %d"
                                      % (source.path, channel, value))
                        if len(recent) > 40:
                            del recent[:20]

        stdscr.nodelay(False)
        stdscr.erase()
        _safe_addstr(stdscr, 1, 2, "Capture done. Writing report...")
        stdscr.refresh()
        time.sleep(1.5)

    curses.wrapper(run)
    _summarize_capture(report, per_prompt, getch_seen)


def _safe_addstr(stdscr, row, col, text, attr=0):
    height, width = stdscr.getmaxyx()
    if row >= height or col >= width:
        return
    try:
        stdscr.addstr(row, col, text[:max(0, width - col - 1)], attr)
    except Exception:  # noqa: BLE001 - never let drawing kill the spike
        pass


def _draw_prompt(stdscr, curses, index, label, purpose, remaining, recent,
                 getch_seen):
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    _safe_addstr(stdscr, 0, 2, "GameSync MiSTer input spike", curses.A_BOLD)
    _safe_addstr(stdscr, 1, 2, "terminal %dx%d" % (width, height))
    _safe_addstr(stdscr, 3, 2, "Press and release:  %s" % label,
                 curses.A_REVERSE)
    _safe_addstr(stdscr, 4, 2, "(%d/%d - %s)" % (index + 1, len(PROMPTS),
                                                 purpose))
    _safe_addstr(stdscr, 5, 2, "next in %.1fs" % remaining)

    _safe_addstr(stdscr, 7, 2, "raw input events (last 8):", curses.A_BOLD)
    for row, line in enumerate(recent[-8:]):
        _safe_addstr(stdscr, 8 + row, 4, line)

    row0 = 17
    _safe_addstr(stdscr, row0, 2, "curses getch() this prompt:", curses.A_BOLD)
    if getch_seen:
        for row, (code, name) in enumerate(getch_seen[-4:]):
            _safe_addstr(stdscr, row0 + 1 + row, 4,
                         "key %d = %s" % (code, name))
    else:
        _safe_addstr(stdscr, row0 + 1, 4, "(nothing yet)")

    _safe_addstr(stdscr, row0 + 6, 2,
                 "Nothing under getch() means MiSTer does not forward the pad "
                 "to stdin.")
    stdscr.refresh()


def _key_name(curses, key):
    try:
        return curses.keyname(key).decode("ascii", "replace")
    except Exception:  # noqa: BLE001
        return "?"


def _summarize_capture(report, per_prompt, getch_seen):
    any_getch = any(getch_seen)
    report.section("6. CAPTURE - curses getch()")
    if any_getch:
        report("getch() DID receive input:")
        for index, (label, _) in enumerate(PROMPTS):
            keys = getch_seen[index]
            report("  %-28s -> %s"
                   % (label, ", ".join("%d(%s)" % (k, n) for k, n in keys)
                      or "(nothing)"))
    else:
        report("getch() received NOTHING.")
        report("=> MiSTer does not translate the pad to console keystrokes;")
        report("   a raw input reader is mandatory for controller navigation.")

    report.section("7. CAPTURE - raw input per prompt")
    for index, (label, _) in enumerate(PROMPTS):
        channels = per_prompt[index]
        report("")
        report("%s:" % label)
        if not channels:
            report("    (no events)")
            continue
        ordered = sorted(channels.items(),
                         key=lambda kv: (-kv[1].count, kv[0][1]))
        for (node, channel), entry in ordered[:12]:
            report("    %-26s %-28s %s"
                   % (channel, entry.describe(), node))
        if len(ordered) > 12:
            report("    ... %d more channels" % (len(ordered) - 12))

    report.section("8. VERDICT")
    totals = {}
    for channels in per_prompt:
        for (node, _), entry in channels.items():
            totals[node] = totals.get(node, 0) + entry.count
    report("stdin/getch navigation viable : %s" % ("YES" if any_getch else "NO"))
    report("raw-node navigation viable    : %s" % ("YES" if totals else "NO"))
    report("")
    report("nodes that actually delivered input:")
    if totals:
        for node, count in sorted(totals.items(), key=lambda kv: -kv[1]):
            report("  %-56s %d events" % (node, count))
    else:
        report("  none")


# ----------------------------------------------------------------------- main

def main():
    report = Report()
    report("GameSync MiSTer Phase 0 spike")
    report("run at %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    probe_only = "--probe" in sys.argv

    # MiSTer launches scripts without TERM set, which makes setupterm fail even
    # though /usr/share/terminfo/l/linux exists. The real client must do this.
    original_term = os.environ.get("TERM")
    if not original_term:
        os.environ["TERM"] = "linux"
    report("TERM was %r, using %r" % (original_term, os.environ["TERM"]))

    sources = []
    try:
        probe_environment(report)
        probe_curses_static(report)
        sources = probe_devices(report)
        probe_passive_read(report, sources)

        if probe_only:
            report.section("INTERACTIVE CAPTURE")
            report("skipped (--probe).")
        else:
            interactive_capture(report, sources)
    except Exception:  # noqa: BLE001 - always produce a report
        report.section("UNHANDLED EXCEPTION")
        report(traceback.format_exc())
    finally:
        for source in sources:
            try:
                os.close(source.fd)
            except OSError:
                pass

    path = report.write()
    print("\n".join(report.lines))
    print("")
    print("report written to: %s" % path)
    if not probe_only:
        print("")
        print("Done. Exiting in 10s.")
        try:
            import select as _select
            _select.select([sys.stdin], [], [], 10.0)
        except Exception:  # noqa: BLE001
            time.sleep(10)


if __name__ == "__main__":
    main()
