"""Controller and keyboard input for the MiSTer GameSync client.

Reads /dev/input/event* directly. Phase 0 measurements drive every rule here:

* Node numbers move. The DualSense was event4/js1 on one run and event3/js0 on
  the next, purely because it reconnected. Devices are always matched by
  EVIOCGNAME and capability, never by index, and are re-scanned periodically.
* Sensor nodes flood. "DualSense Wireless Controller Motion Sensors" emitted
  10,911 events in three seconds. They are excluded by name.
* Sticks jitter. The right stick idles between 128 and 129 and produced 578
  events in one 3.5 s window untouched, so axes use a deadzone derived from
  the driver's own reported range.
* MiSTer mirrors the pad onto a uinput keyboard called "MiSTer virtual input",
  which also reaches us. Without care every press fires twice; see
  _select_sources for how that is resolved.
"""

from __future__ import annotations

import array
import errno
try:
    import fcntl
except ImportError:  # pragma: no cover
    # Linux-only. Guarded so the UI logic can be imported and unit-tested on a
    # development machine; nothing here works without a real MiSTer anyway.
    fcntl = None
import glob
import os
import select
import struct
import time

EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03

# Buttons
BTN_SOUTH, BTN_EAST, BTN_C, BTN_NORTH, BTN_WEST = 0x130, 0x131, 0x132, 0x133, 0x134
BTN_Z = 0x135
BTN_TL, BTN_TR, BTN_TL2, BTN_TR2 = 0x136, 0x137, 0x138, 0x139
BTN_SELECT, BTN_START, BTN_MODE = 0x13a, 0x13b, 0x13c
BTN_THUMBL, BTN_THUMBR = 0x13d, 0x13e

# Axes
ABS_X, ABS_Y, ABS_HAT0X, ABS_HAT0Y = 0x00, 0x01, 0x10, 0x11

# Keyboard scancodes
KEY_ESC, KEY_TAB, KEY_ENTER, KEY_SPACE = 1, 15, 28, 57
KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT = 103, 108, 105, 106
KEY_PAGEUP, KEY_PAGEDOWN, KEY_HOME, KEY_END = 104, 109, 102, 107
KEY_KPENTER, KEY_BACKSPACE, KEY_Q = 96, 14, 16

#: Keyboard keys that type a character while a text prompt is open. Linux
#: keycodes are laid out by keyboard row, so each row is one contiguous run.
TEXT_KEYS = {}
for _row, _first in (("1234567890-", 2), ("QWERTYUIOP", 16),
                     ("ASDFGHJKL", 30), ("ZXCVBNM", 44)):
    for _offset, _char in enumerate(_row):
        TEXT_KEYS[_first + _offset] = _char
TEXT_KEYS[KEY_SPACE] = " "
TEXT_KEYS[52] = "."          # KEY_DOT
TEXT_KEYS[40] = "'"          # KEY_APOSTROPHE
del _row, _first, _offset, _char

NOISY_NAME_PARTS = ("motion sensor", "touchpad", "accelerometer", "gyro")
VIRTUAL_NAME = "mister virtual input"

# Actions
UP, DOWN, LEFT, RIGHT = "up", "down", "left", "right"
PRIMARY, BACK, SYNC, ALT = "primary", "back", "sync", "alt"
PREV_SYSTEM, NEXT_SYSTEM = "prev_system", "next_system"
PREV_TAB, NEXT_TAB = "prev_tab", "next_tab"
SEARCH, SETTINGS, QUIT = "search", "settings", "quit"
#: Emitted only while a text prompt is open (``InputReader.text_keys``):
#: a typed character is ``CHAR_PREFIX + char``.
TEXT_DELETE, TEXT_OK, CHAR_PREFIX = "text_delete", "text_ok", "char:"

#: Every action that can be bound, in the order a remap wizard should ask for
#: them. QUIT is deliberately absent: BACK already leaves, and a cabinet with
#: a mis-bound quit button is a cabinet you cannot use.
BINDABLE = (
    (PRIMARY, "Confirm"),
    (BACK, "Back / exit"),
    (SYNC, "Sync all"),
    (ALT, "Rescan"),
    (PREV_TAB, "Previous tab"),
    (NEXT_TAB, "Next tab"),
    (PREV_SYSTEM, "Previous system"),
    (NEXT_SYSTEM, "Next system"),
    (SETTINGS, "Settings / menu"),
    (SEARCH, "Filter"),
)

BUTTON_ACTIONS = {
    BTN_SOUTH: PRIMARY,
    BTN_EAST: BACK,
    BTN_WEST: SYNC,
    BTN_NORTH: ALT,
    BTN_TL: PREV_SYSTEM,
    BTN_TR: NEXT_SYSTEM,
    BTN_TL2: PREV_TAB,
    BTN_TR2: NEXT_TAB,
    BTN_SELECT: SEARCH,
    BTN_START: SETTINGS,
}

#: hid-generic hands a plain HID gamepad the whole BTN_GAMEPAD range in order,
#: including BTN_C and BTN_Z, which most console pads skip. A GP2040-CE stick
#: - what an arcade cabinet is likely to be running - therefore lands two of
#: its six face buttons on codes the gamepad map above does not mention, and
#: those buttons do nothing at all. Bind the full contiguous range so that
#: cannot happen, and let the remap wizard correct the assignment.
ARCADE_BUTTON_ACTIONS = {
    BTN_SOUTH: PRIMARY,       # GP2040 B1
    BTN_EAST: BACK,           # GP2040 B2
    BTN_C: SYNC,              # GP2040 B3
    BTN_NORTH: ALT,           # GP2040 B4
    BTN_WEST: PREV_TAB,       # GP2040 L1
    BTN_Z: NEXT_TAB,          # GP2040 R1
    BTN_TL: PREV_SYSTEM,      # GP2040 L2
    BTN_TR: NEXT_SYSTEM,      # GP2040 R2
    BTN_TL2: SEARCH,          # GP2040 S1 - the coin switch on most cabinets
    BTN_TR2: SETTINGS,        # GP2040 S2 - start
    BTN_SELECT: SEARCH,
    BTN_START: SETTINGS,
}

#: What to call each code in the on-screen hints. A cabinet's buttons are not
#: labelled "BTN_SOUTH", and a footer that says L2/R2 on a panel with no L2 is
#: worse than no footer.
BUTTON_LABELS = {
    BTN_SOUTH: "A", BTN_EAST: "B", BTN_C: "C", BTN_NORTH: "X",
    BTN_WEST: "Y", BTN_Z: "Z", BTN_TL: "L1", BTN_TR: "R1",
    BTN_TL2: "L2", BTN_TR2: "R2", BTN_SELECT: "Select",
    BTN_START: "Start", BTN_MODE: "Mode",
    BTN_THUMBL: "L3", BTN_THUMBR: "R3",
}

# Controller families. The kernel maps every pad onto the same BTN_* codes by
# *position* (BTN_SOUTH is always the bottom face button), so the bindings do
# not change between them - only what is printed on the plastic does. A
# DualShock owner told to "press A" has nothing to press, and a Switch owner
# told to press A presses the wrong button, because Nintendo's A is on the
# east. The layout is detected from the device and only affects labels.
LAYOUT_GENERIC, LAYOUT_XBOX = "generic", "xbox"
LAYOUT_PLAYSTATION, LAYOUT_NINTENDO = "playstation", "nintendo"

LAYOUT_NAMES = {
    LAYOUT_GENERIC: "Generic gamepad",
    LAYOUT_XBOX: "Xbox",
    LAYOUT_PLAYSTATION: "PlayStation",
    LAYOUT_NINTENDO: "Nintendo",
}

#: Per-layout overrides of BUTTON_LABELS. Anything not listed falls back to
#: the generic name, so an odd code on a known pad still gets *some* label.
LAYOUT_LABELS = {
    LAYOUT_GENERIC: {},
    LAYOUT_XBOX: {
        BTN_TL: "LB", BTN_TR: "RB", BTN_TL2: "LT", BTN_TR2: "RT",
        BTN_SELECT: "View", BTN_START: "Menu", BTN_MODE: "Xbox",
        BTN_THUMBL: "LS", BTN_THUMBR: "RS",
    },
    # hid-playstation / hid-sony: SOUTH=Cross EAST=Circle NORTH=Triangle
    # WEST=Square, SELECT=Share/Create, START=Options.
    LAYOUT_PLAYSTATION: {
        BTN_SOUTH: "Cross", BTN_EAST: "Circle", BTN_NORTH: "Triangle",
        BTN_WEST: "Square", BTN_SELECT: "Share", BTN_START: "Options",
        BTN_MODE: "PS",
    },
    # hid-nintendo: SOUTH=B EAST=A NORTH=X WEST=Y (positional, so the letters
    # are swapped relative to Xbox), TL/TR=L/R, TL2/TR2=ZL/ZR, Z=Capture.
    LAYOUT_NINTENDO: {
        BTN_SOUTH: "B", BTN_EAST: "A", BTN_NORTH: "X", BTN_WEST: "Y",
        BTN_TL: "L", BTN_TR: "R", BTN_TL2: "ZL", BTN_TR2: "ZR",
        BTN_SELECT: "-", BTN_START: "+", BTN_MODE: "Home", BTN_Z: "Capture",
    },
}

# USB vendor ids, the most reliable signal: a DualShock 4 over Bluetooth is
# named just "Wireless Controller", and an 8BitDo pad is named after whichever
# console it is impersonating.
VENDOR_SONY, VENDOR_NINTENDO, VENDOR_MICROSOFT = 0x054C, 0x057E, 0x045E
VENDOR_LAYOUTS = {
    VENDOR_SONY: LAYOUT_PLAYSTATION,
    VENDOR_NINTENDO: LAYOUT_NINTENDO,
    VENDOR_MICROSOFT: LAYOUT_XBOX,
}

# Name fragments, checked in order, for pads whose vendor id says nothing
# (clones report whatever they like). Most specific first.
NAME_LAYOUTS = (
    ("dualsense", LAYOUT_PLAYSTATION),
    ("dualshock", LAYOUT_PLAYSTATION),
    ("playstation", LAYOUT_PLAYSTATION),
    ("sony", LAYOUT_PLAYSTATION),
    ("joy-con", LAYOUT_NINTENDO),
    ("pro controller", LAYOUT_NINTENDO),
    ("nintendo", LAYOUT_NINTENDO),
    ("switch", LAYOUT_NINTENDO),
    ("xbox", LAYOUT_XBOX),
    ("x-box", LAYOUT_XBOX),
    ("xinput", LAYOUT_XBOX),
    ("x-input", LAYOUT_XBOX),
    ("microsoft", LAYOUT_XBOX),
    # Last, because it is weak: the bare "Wireless Controller" is how hid-sony
    # names a DualShock 4 over Bluetooth, but "Xbox Wireless Controller"
    # contains it too and must be caught above.
    ("wireless controller", LAYOUT_PLAYSTATION),
)


def detect_layout(name: str, vendor: int = 0) -> str:
    """Which family of labels a pad wants, from its evdev name and vendor id."""
    layout = VENDOR_LAYOUTS.get(vendor)
    if layout:
        return layout
    lowered = (name or "").lower()
    for fragment, layout in NAME_LAYOUTS:
        if fragment in lowered:
            return layout
    return LAYOUT_GENERIC


def button_label(code: int, layout: str = LAYOUT_GENERIC) -> str:
    labels = LAYOUT_LABELS.get(layout, {})
    return labels.get(code) or BUTTON_LABELS.get(code) or "0x%x" % code

KEY_ACTIONS = {
    KEY_UP: UP, KEY_DOWN: DOWN, KEY_LEFT: LEFT, KEY_RIGHT: RIGHT,
    KEY_ENTER: PRIMARY, KEY_KPENTER: PRIMARY, KEY_SPACE: SYNC,
    KEY_ESC: BACK, KEY_BACKSPACE: BACK, KEY_TAB: ALT,
    KEY_PAGEUP: PREV_SYSTEM, KEY_PAGEDOWN: NEXT_SYSTEM,
    KEY_HOME: PREV_TAB, KEY_END: NEXT_TAB,
    KEY_Q: QUIT,
}

DIRECTION_ACTIONS = {(0, -1): UP, (0, 1): DOWN, (-1, 0): LEFT, (1, 0): RIGHT}

# Hold-to-accelerate ramp, mirroring the Steam Deck client's feel.
#
# Vertical (one row per step) ramps from REPEAT_INTERVAL to FAST_INTERVAL.
# Horizontal (one page per step) is paced more slowly, because after
# LETTER_AFTER of holding the app stops paging and jumps from initial to
# initial instead - a sweep from A to Z through a 3,000-row catalogue - and
# 20 letters a second would be uncontrollable.
REPEAT_DELAY = 0.25
REPEAT_INTERVAL = 0.10
FAST_AFTER = 1.2
FAST_INTERVAL = 0.05
PAGE_INTERVAL = 0.10
# Page for a good while before switching to initials, and step slowly once
# there: at 4 letters a second the one you want is gone before you let go.
LETTER_AFTER = 3.0
LETTER_INTERVAL = 0.6

#: Any identical action arriving inside this window is a mirrored duplicate.
DEDUPE_WINDOW = 0.06

RESCAN_INTERVAL = 2.0

_IOC_READ, _IOC_WRITE = 2, 1


def _ioc(direction, typ, nr, size):
    return (direction << 30) | (size << 16) | (ord(typ) << 8) | nr


def _eviocgname(fd, length=256):
    buf = array.array("B", [0] * length)
    fcntl.ioctl(fd, _ioc(_IOC_READ, "E", 0x06, length), buf)
    return bytes(buf).split(b"\x00")[0].decode("utf-8", "replace")


def _eviocgbit(fd, ev_type, length=96):
    buf = array.array("B", [0] * length)
    fcntl.ioctl(fd, _ioc(_IOC_READ, "E", 0x20 + ev_type, length), buf)
    codes = set()
    for index, byte in enumerate(buf):
        if not byte:
            continue
        for bit in range(8):
            if byte & (1 << bit):
                codes.add(index * 8 + bit)
    return codes


def _eviocgid(fd):
    """struct input_id: bustype, vendor, product, version (four u16)."""
    buf = array.array("B", [0] * 8)
    fcntl.ioctl(fd, _ioc(_IOC_READ, "E", 0x02, 8), buf)
    _bustype, vendor, product, _version = struct.unpack("4H", bytes(buf))
    return vendor, product


def _eviocgabs(fd, axis):
    """struct input_absinfo: value, min, max, fuzz, flat, resolution."""
    buf = array.array("B", [0] * 24)
    fcntl.ioctl(fd, _ioc(_IOC_READ, "E", 0x40 + axis, 24), buf)
    value, minimum, maximum, fuzz, flat, resolution = struct.unpack(
        "6i", bytes(buf))
    return {"min": minimum, "max": maximum, "flat": flat, "fuzz": fuzz}


class Device:
    __slots__ = ("path", "name", "fd", "is_pad", "is_keyboard", "axes",
                 "layout", "arcade", "code_map")

    def __init__(self, path, name, fd, is_pad, is_keyboard, axes,
                 layout=LAYOUT_GENERIC, arcade=False, code_map=None):
        self.path = path
        self.name = name
        self.fd = fd
        self.is_pad = is_pad
        self.is_keyboard = is_keyboard
        self.axes = axes
        self.layout = layout
        #: Uses the contiguous-range defaults (ARCADE_BUTTON_ACTIONS).
        self.arcade = arcade
        #: Raw code -> real code, for a pad the kernel enumerated generically.
        self.code_map = code_map


def is_arcade_pad(keys: set, layout: str) -> bool:
    """Does this pad enumerate the way hid-generic enumerates a stick encoder?

    A pad with a dedicated kernel driver (Sony, Nintendo, Microsoft) reports
    the console layout and skips BTN_C/BTN_Z. hid-generic hands anything else
    the whole BTN_GAMEPAD range in order, so a GP2040 stick - or a nameless
    USB pad - lands buttons on BTN_C and BTN_Z. That is the signal; the
    display mode is not. A 240p CRT with a DualShock plugged in is a living
    room, not a cabinet, and was getting the cabinet map.
    """
    return layout == LAYOUT_GENERIC and BTN_C in keys and BTN_Z in keys


#: Sony pads that only ``hid-playstation`` knows how to name.
DUALSENSE_PRODUCTS = {0x0ce6, 0x0df2}
SONY_VENDOR = 0x054c

#: What a DualSense's buttons come out as when the kernel has no
#: ``hid-playstation`` (the September 2026 MiSTer image: 6.18 with only
#: ``hid-sony``, which stops at the DualShock 4). ``hid-generic`` then hands
#: the report's buttons out in order from BTN_SOUTH - Square first, because
#: that is button 1 on the wire - so Cross landed on BTN_EAST and read as
#: Back. Keyed by what arrives, valued by what the button really is.
GENERIC_DUALSENSE_CODES = {
    BTN_SOUTH: BTN_WEST,       # 1  Square
    BTN_EAST: BTN_SOUTH,       # 2  Cross
    BTN_C: BTN_EAST,           # 3  Circle
    BTN_NORTH: BTN_NORTH,      # 4  Triangle
    BTN_WEST: BTN_TL,          # 5  L1
    BTN_Z: BTN_TR,             # 6  R1
    BTN_TL: BTN_TL2,           # 7  L2
    BTN_TR: BTN_TR2,           # 8  R2
    BTN_TL2: BTN_SELECT,       # 9  Create
    BTN_TR2: BTN_START,        # 10 Options
    BTN_SELECT: BTN_THUMBL,    # 11 L3
    BTN_START: BTN_THUMBR,     # 12 R3
    BTN_MODE: BTN_MODE,        # 13 PS
    BTN_THUMBL: None,          # 14 touchpad click
}


def generic_code_map(vendor: int, product: int, keys: set) -> dict | None:
    """A code translation for a pad the kernel enumerated generically.

    A DualSense bound to hid-generic is recognisable by its id plus BTN_C in
    its key set - hid-playstation never reports BTN_C. With that driver
    present the codes are already right and nothing is translated.
    """
    if vendor == SONY_VENDOR and product in DUALSENSE_PRODUCTS             and BTN_C in keys:
        return GENERIC_DUALSENSE_CODES
    return None


class InputReader:
    """Turns raw evdev traffic into a stream of high-level actions."""

    #: While True a keyboard types characters (see TEXT_KEYS) instead of
    #: driving the list. Set by the app around a text prompt.
    text_keys = False

    def __init__(self, buttons: dict | None = None, arcade: bool = False):
        """``buttons`` is ``{action: code}`` from the config, and wins.

        Defaults for anything left unbound come per device: the console-pad
        map, or the contiguous-range map for a pad that enumerates like a
        stick encoder (``is_arcade_pad``). ``arcade`` forces the latter for
        every pad, for tests and for a cabinet whose encoder hides it.
        """
        self._force_arcade = arcade
        self._devices: list[Device] = []
        self._by_fd: dict[int, Device] = {}
        self._last_scan = 0.0
        self._pending: list[str] = []
        self._capture: list[int] | None = None
        #: Path of the pad that last produced a press. With two different pads
        #: plugged in the hints follow whichever one is actually in hand.
        self._active_pad: str | None = None

        self.set_buttons(buttons, arcade)

        self._direction = (0, 0)
        self._axis_direction = (0, 0)
        self._hat_direction = (0, 0)
        self._key_direction = (0, 0)
        self._held_since = 0.0
        self._next_repeat = 0.0
        self._last_action: dict[str, float] = {}

        self.rescan()

    # ----------------------------------------------------------- button map

    def set_buttons(self, buttons: dict | None = None,
                    arcade: bool = False) -> None:
        self._force_arcade = arcade

        def merged(base):
            mapping = dict(base)
            for action, code in (buttons or {}).items():
                # A configured binding replaces whatever else claimed that
                # action, otherwise remapping Confirm onto B would leave B
                # doing both.
                for existing in [c for c, a in mapping.items()
                                 if a == action]:
                    del mapping[existing]
                mapping[code] = action
            return mapping

        self._buttons_pad = merged(BUTTON_ACTIONS)
        self._buttons_arcade = merged(ARCADE_BUTTON_ACTIONS)

    def _buttons_for(self, device: Device | None) -> dict:
        if self._force_arcade or (device is not None and device.arcade):
            return self._buttons_arcade
        return self._buttons_pad

    @property
    def _buttons(self) -> dict:
        """The map for the pad in hand - what the hints describe."""
        return self._buttons_for(self.active_pad())

    def label(self, action: str) -> str:
        """The on-screen name of whichever button currently does ``action``.

        Named the way the active controller prints it: Cross on a DualShock,
        B on a Switch pad, A on everything else, for the same BTN_SOUTH.
        """
        layout = self.layout
        for code, bound in self._buttons.items():
            if bound == action:
                return button_label(code, layout)
        return "-"

    def active_pad(self) -> Device | None:
        """The pad the hints describe: last one pressed, else the first."""
        pads = [device for device in self._devices if device.is_pad]
        if not pads:
            return None
        for device in pads:
            if device.path == self._active_pad:
                return device
        # Nothing pressed yet: guess the pad in hand. A recognised console
        # pad over a nameless one - an always-plugged PC Engine pad on
        # event1 was captioning the footer A/B/C for a DualSense user until
        # the first press.
        for device in pads:
            if device.layout != LAYOUT_GENERIC and not device.arcade:
                return device
        return pads[0]

    @property
    def layout(self) -> str:
        pad = self.active_pad()
        return pad.layout if pad is not None else LAYOUT_GENERIC

    def layout_description(self) -> str:
        """For the Settings tab: "PlayStation - DualSense Wireless Controller"."""
        pad = self.active_pad()
        if pad is None:
            return "no controller"
        family = LAYOUT_NAMES.get(pad.layout, pad.layout)
        if pad.arcade or self._force_arcade:
            family += " (arcade map)"
        return "%s - %s" % (family, pad.name)

    def capture_next(self) -> None:
        """Start recording raw button codes instead of acting on them."""
        self._capture = []

    def captured(self) -> int | None:
        """The code pressed since capture_next, or None if nothing yet."""
        if self._capture:
            code = self._capture[0]
            self._capture = None
            return code
        return None

    # ------------------------------------------------------------ device set

    def rescan(self) -> None:
        self._last_scan = time.time()
        if fcntl is None:
            return
        seen = {device.path for device in self._devices}
        found = {}

        for path in sorted(glob.glob("/dev/input/event*")):
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            try:
                name = _eviocgname(fd)
            except OSError:
                os.close(fd)
                continue
            if any(part in name.lower() for part in NOISY_NAME_PARTS):
                os.close(fd)
                continue
            try:
                keys = _eviocgbit(fd, EV_KEY, 96)
            except OSError:
                keys = set()

            is_pad = BTN_SOUTH in keys
            is_keyboard = KEY_ENTER in keys and KEY_UP in keys
            if not is_pad and not is_keyboard:
                os.close(fd)
                continue

            axes = {}
            layout = LAYOUT_GENERIC
            arcade = False
            code_map = None
            if is_pad:
                for axis in (ABS_X, ABS_Y):
                    try:
                        axes[axis] = _eviocgabs(fd, axis)
                    except OSError:
                        pass
                try:
                    vendor, product = _eviocgid(fd)
                except OSError:
                    vendor = product = 0
                layout = detect_layout(name, vendor)
                code_map = generic_code_map(vendor, product, keys)
                arcade = code_map is None and is_arcade_pad(keys, layout)
            found[path] = Device(path, name, fd, is_pad, is_keyboard, axes,
                                 layout, arcade, code_map)

        keep = self._select_sources(found)

        for device in self._devices:
            if device.path not in keep:
                try:
                    os.close(device.fd)
                except OSError:
                    pass
        for path, device in found.items():
            if path not in keep:
                try:
                    os.close(device.fd)
                except OSError:
                    pass

        # Reuse already-open descriptors so held state is not disturbed.
        merged = []
        existing = {device.path: device for device in self._devices}
        for path in keep:
            if path in existing and path in found:
                try:
                    os.close(found[path].fd)
                except OSError:
                    pass
                merged.append(existing[path])
            elif path in found:
                merged.append(found[path])
        self._devices = merged
        self._by_fd = {device.fd: device for device in self._devices}
        _ = seen

    @staticmethod
    def _select_sources(found: dict) -> set:
        """Choose which nodes to listen to, avoiding mirrored duplicates.

        MiSTer grabs real pads and re-emits translated keystrokes on its own
        uinput keyboard. Listening to both means every press arrives twice, so
        when a real pad is present the virtual node is dropped. The dedupe
        window in _emit is the safety net for anything this misses.
        """
        pads = [path for path, device in found.items() if device.is_pad]
        keep = set(pads)
        for path, device in found.items():
            if device.is_pad:
                continue
            if pads and device.name.lower() == VIRTUAL_NAME:
                continue
            keep.add(path)
        return keep

    def device_names(self) -> list[str]:
        return ["%s (%s)" % (device.name, device.path)
                for device in self._devices]

    def close(self) -> None:
        for device in self._devices:
            try:
                os.close(device.fd)
            except OSError:
                pass
        self._devices = []
        self._by_fd = {}

    # ---------------------------------------------------------------- events

    def poll(self, timeout: float = 0.05) -> list[str]:
        now = time.time()
        if now - self._last_scan > RESCAN_INTERVAL:
            self.rescan()

        if self._devices:
            try:
                ready, _, _ = select.select(list(self._by_fd), [], [], timeout)
            except (OSError, ValueError):
                self.rescan()
                ready = []
            for fd in ready:
                device = self._by_fd.get(fd)
                if device is not None:
                    self._read_device(device)
        else:
            time.sleep(timeout)

        self._pump_repeat()
        actions, self._pending = self._pending, []
        return actions

    def _read_device(self, device: Device) -> None:
        try:
            data = os.read(device.fd, EVENT_SIZE * 128)
        except OSError as exc:
            if exc.errno in (errno.ENODEV, errno.EBADF, errno.EIO):
                self.rescan()
            return
        for offset in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
            _, _, ev_type, code, value = struct.unpack(
                EVENT_FORMAT, data[offset:offset + EVENT_SIZE])
            if ev_type == EV_KEY:
                self._on_key(device, code, value)
            elif ev_type == EV_ABS:
                self._on_abs(device, code, value)

    def _on_key(self, device: Device, code: int, value: int) -> None:
        if value == 0 and not device.is_pad:
            # An arrow key coming up ends its hold, exactly as a stick
            # returning to centre does; without this a keyboard scrolled
            # forever after one tap.
            if KEY_ACTIONS.get(code) in (UP, DOWN, LEFT, RIGHT):
                self._key_direction = (0, 0)
                self._update_direction()
            return
        if value != 1:          # ignore kernel autorepeat
            return
        if device.is_pad:
            self._active_pad = device.path
            if device.code_map is not None:
                code = device.code_map.get(code, code)
                if code is None:
                    return
        if self._capture is not None and device.is_pad:
            self._capture.append(code)
            return
        if self.text_keys and not device.is_pad:
            # A real keyboard types straight into the prompt; the pad's
            # bindings still apply so the on-screen keys work beside it.
            char = TEXT_KEYS.get(code)
            if char is not None:
                self._emit(CHAR_PREFIX + char)
                return
            if code == KEY_BACKSPACE:
                self._emit(TEXT_DELETE)
                return
            if code in (KEY_ENTER, KEY_KPENTER):
                self._emit(TEXT_OK)
                return
        action = (self._buttons_for(device).get(code)
                  if device.is_pad else None)
        if action is None:
            action = KEY_ACTIONS.get(code)
        if action is None:
            return
        if action in (UP, DOWN, LEFT, RIGHT):
            # Keyboard arrows drive the same ramp as the stick would.
            self._key_direction = {UP: (0, -1), DOWN: (0, 1),
                                   LEFT: (-1, 0), RIGHT: (1, 0)}[action]
            self._update_direction()
            return
        self._emit(action)

    def held_for(self) -> float:
        """How long the current direction has been held, or 0 when idle."""
        if self._direction == (0, 0):
            return 0.0
        return max(0.0, time.time() - self._held_since)

    def _on_abs(self, device: Device, code: int, value: int) -> None:
        if code == ABS_HAT0X:
            self._hat_direction = (_sign(value), self._hat_direction[1])
        elif code == ABS_HAT0Y:
            self._hat_direction = (self._hat_direction[0], _sign(value))
        elif code in (ABS_X, ABS_Y):
            info = device.axes.get(code)
            if not info:
                return
            centre = (info["min"] + info["max"]) / 2.0
            span = max(1.0, (info["max"] - info["min"]) / 2.0)
            # A generous deadzone: the observed idle jitter is tiny, but the
            # flat value the driver reports can be zero.
            threshold = max(0.45, (info["flat"] / span) if info["flat"] else 0)
            offset = (value - centre) / span
            direction = 0
            if offset > threshold:
                direction = 1
            elif offset < -threshold:
                direction = -1
            if code == ABS_X:
                self._axis_direction = (direction, self._axis_direction[1])
            else:
                self._axis_direction = (self._axis_direction[0], direction)
        else:
            return
        self._update_direction()

    def _update_direction(self) -> None:
        """Fold hat, stick and arrow keys into one held direction."""
        combined = (self._hat_direction[0] or self._axis_direction[0]
                    or self._key_direction[0],
                    self._hat_direction[1] or self._axis_direction[1]
                    or self._key_direction[1])
        if combined != self._direction:
            self._direction = combined
            if combined != (0, 0):
                action = DIRECTION_ACTIONS.get(_dominant(combined))
                if action:
                    self._emit(action)
                now = time.time()
                self._held_since = now
                self._next_repeat = now + REPEAT_DELAY

    def _pump_repeat(self) -> None:
        if self._direction == (0, 0):
            return
        dominant = _dominant(self._direction)
        action = DIRECTION_ACTIONS.get(dominant)
        if action is None:
            return
        now = time.time()
        if now < self._next_repeat:
            return
        held = now - self._held_since
        if dominant[0]:
            # Horizontal: pages, then initials once held past LETTER_AFTER.
            interval = (LETTER_INTERVAL if held > LETTER_AFTER
                        else PAGE_INTERVAL)
        else:
            interval = FAST_INTERVAL if held > FAST_AFTER else REPEAT_INTERVAL
        self._next_repeat = now + interval
        self._pending.append(action)   # repeats bypass the dedupe window

    def _emit(self, action: str) -> None:
        now = time.time()
        previous = self._last_action.get(action, 0.0)
        if now - previous < DEDUPE_WINDOW:
            return
        self._last_action[action] = now
        self._pending.append(action)


def _sign(value: int) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _dominant(direction: tuple[int, int]) -> tuple[int, int]:
    """Vertical wins when both axes are engaged, so diagonals stay sane."""
    dx, dy = direction
    if dy:
        return (0, dy)
    return (dx, 0)
