"""The MiSTer GameSync UI shell.

This is the Phase 2 vertical slice: real framebuffer rendering, real controller
input and real data read off the device, so the look and the feel can be judged
on hardware before the sync and install logic is wired in behind it.

Rows are drawn individually, so moving the selection repaints two rows rather
than the screen - measured at 0.72 ms against 8 ms for a full repaint.
"""

from __future__ import annotations

import os
import posixpath
import pkgutil
import time

from . import api as gsapi
from . import catalogcache as gscatalog
from . import config as gsconfig
from . import downloads as gsdownloads
from . import input as gsinput
from . import sync as gssync
from shared import mister_install as gsinstall
from . import theme
from .fb import Framebuffer
from .text import Font, TextRenderer

TABS = ["Saves", "Catalog", "Installed", "Downloads", "Settings"]

SAVES_ROOT = "/media/fat/saves"

# The vendored shared/ modules are the single source of truth for folder names
# and extensions, exactly as the desktop client uses them.
from shared.mister import (  # noqa: E402
    MISTER_FOLDER_TO_SYSTEM,
    MISTER_GAMES_ROOTS,
    MISTER_SYSTEM_FOLDER_CANDIDATES,
)
from shared.mister_scan import (  # noqa: E402
    installed_game_save_paths,
    list_installed_games,
)
from shared.systems import SAVE_EXTENSIONS  # noqa: E402

GAMES_ROOTS = [MISTER_GAMES_ROOTS["usb"], MISTER_GAMES_ROOTS["sd"]]
SAVE_SUFFIXES = tuple(SAVE_EXTENSIONS)


#: The catalog's system filter gets one extra stop after a system that has
#: MSU packs - "SNES: MSU packs" - showing only those.  A pseudo system:
#: same L1/R1 cycle, no new button.
MSU_FILTER_SUFFIX = ": MSU packs"
MSU_KIND_LABELS = {"msu1": "MSU-1", "msu-md": "MSU-MD", "mdplus": "MD+"}


def msu_filter_label(system: str) -> str:
    return system + MSU_FILTER_SUFFIX


def msu_filter_base(label: str):
    """The system behind a pseudo filter label, or None for a real system."""
    if label.endswith(MSU_FILTER_SUFFIX):
        return label[: -len(MSU_FILTER_SUFFIX)]
    return None


def _group_pack_kind(group) -> str:
    rows = getattr(group, "rows", None)
    if not rows:
        return ""
    first = rows[0]
    if not isinstance(first, dict) or not first.get("is_bundle"):
        return ""
    return str(first.get("bundle_kind") or "").lower()


def row_pack_kind(row) -> str:
    """``msu1`` / ``msu-md`` / ``mdplus`` for a catalog row that is a pack."""
    return _group_pack_kind(getattr(row, "ref", None))


def catalog_systems(rows) -> list:
    """The filter stops for the catalog tab: every system, and after each
    one that has MSU packs, the packs-only view of it."""
    systems = sorted({row.system for row in rows if row.system})
    packed = {row.system for row in rows if row_pack_kind(row)}
    out = ["All"]
    for system in systems:
        out.append(system)
        if system in packed:
            out.append(msu_filter_label(system))
    return out


#: Marquee pacing. A step per interval rather than pixels-per-second:
#: the MiSTer's frame cost varies with the video mode, and a fixed step
#: keeps the crawl even instead of lurching when a frame runs long.
MARQUEE_STEP = 2
MARQUEE_INTERVAL = 0.04
MARQUEE_START_HOLD = 1.2
MARQUEE_END_HOLD = 1.5
RA_BADGE_LABEL = "RA"


class Row:
    __slots__ = ("system", "name", "detail", "status", "ref", "ra")

    def __init__(self, system, name, detail, status, ref=None, ra=False):
        self.system = system
        self.name = name
        self.detail = detail
        self.status = status
        #: True when RetroAchievements has a published set for this exact
        #: ROM. Only a live set earns anything, so a hash RA merely knows
        #: does not get the badge.
        self.ra = bool(ra)
        #: The object behind the row, when the name alone cannot find it: a
        #: CD card and an ISO card for the same game share a display name.
        self.ref = ref


class InstalledGame:
    """A game already on the device, with what delete and move need."""

    __slots__ = ("system", "name", "path", "where", "folder", "is_pack")

    def __init__(self, system, name, path, where, folder, is_pack=False):
        self.system = system
        self.name = name
        self.path = path
        self.where = where          # "SD" or "USB"
        self.folder = folder        # the core folder, e.g. "PSX"
        self.is_pack = is_pack      # an MSU pack, not the plain ROM

    @property
    def is_folder(self) -> bool:
        return os.path.isdir(self.path)

    @property
    def target(self) -> str:
        """The storage this game is not on."""
        return "sd" if self.where == "USB" else "usb"


class App:
    #: Catalog search text; empty means no filter (see set_search).
    search = ""

    def __init__(self, take_over_console: bool = True):
        self.fb = Framebuffer(take_over_console=take_over_console).open()
        self.input = None
        # Once the console is in graphics mode, any failure here would leave
        # the screen blank, so everything after open() unwinds explicitly.
        try:
            # The config is read before anything is measured or drawn: it
            # carries the overscan inset, and that changes the size of every
            # box the layout is about to be computed from.
            self.config = gsconfig.load_config()
            # A 240p display that has never been calibrated gets the CRT
            # default; what a monitor shows is the whole picture.
            self.overscan = self.config.overscan_for(
                self.fb.phys_height <= theme.LOWRES_MAX_HEIGHT)
            self.fb.set_overscan(*self.overscan)

            # A 15 kHz mode reports a framebuffer whose pixels are not square
            # (640x240 for a 4:3 picture), so glyph width and horizontal
            # padding both have to be corrected by the pixel aspect.
            aspect = self.fb.pixel_aspect
            self.metrics = theme.Metrics(self.fb.width, self.fb.height, aspect)
            self.lowres = self.metrics.lowres
            if self.lowres:
                theme.apply_crt_palette()
            self.renderer = TextRenderer()

            font_bytes = pkgutil.get_data("gamesync", "assets/font.ttf")
            if not font_bytes:
                raise RuntimeError("bundled font missing")

            def make_font(size, bold=False):
                return Font(font_bytes, size, bold=bold, pixel_aspect=aspect,
                            mono=self.lowres)

            self.font_title = make_font(self.metrics.font_title, bold=True)
            self.font_row = make_font(self.metrics.font_row)
            self.font_small = make_font(self.metrics.font_small)
            self.font_chip = make_font(self.metrics.font_chip, bold=True)

            self.input = gsinput.InputReader(buttons=self.config.buttons)
            self.client = (
                gsapi.Client(self.config.base_url, self.config.api_key)
                if self.config.is_configured else None
            )
            self.engine = gssync.SyncEngine(self.config, client=self.client)
            self.queue = gsdownloads.DownloadQueue(
                client=self.client,
                provider=self.engine.provider,
                rom_target=self.config.rom_target,
            )
            # Downloads run on their own thread so the UI stays usable
            # while games install; see service_downloads().
            self.worker = gsdownloads.DownloadWorker(self.queue)
            self._progress_painted = 0.0
            #: Downloads that landed since the rows were last rebuilt, and
            #: when the rebuild last ran - see service_downloads().
            self._landed = []
            self._landed_painted = 0.0
            self.catalog = []
            self.catalog_groups = []
            self.catalog_cache = gscatalog.CatalogCache()
            # The engine resolves a slug-system save to the server's key by
            # exact file name; hand it the rows we already hold so that
            # never costs a fetch.
            self.engine.catalog_rows = self.catalog_cache.rows
            self.group_by_title = {}
            #: (system, normalized name) for every catalogue game with a
            #: published RA set, so the Saves and Installed tabs can badge
            #: without walking the catalogue again.
            self.ra_games = set()
            self._ra_badge_w = None
            self.installed_ids = set()
            self.installed_entries = []
            self.server_status = ""

            self.tab = 0
            self.selected = 0
            self.scroll = 0
            #: Marquee state for the selected row - see tick_marquee().
            self._marquee_offset = 0
            self._marquee_span = 0
            self._marquee_next = 0.0
            #: What the marquee is currently crawling. Keyed on the row
            #: rather than reset from each navigation path, so a filter
            #: change or a rebuilt list restarts it too.
            self._marquee_key = None
            #: (selected, scroll) per tab, restored when you come back to it.
            self.tab_positions: dict[int, tuple[int, int]] = {}
            self.system_filter = 0
            self.running = True
            #: Set by run(); any modal loop checks it so --timeout bounds them
            #: too and an unattended run cannot sit waiting for a keypress.
            self.deadline = None
            self.last_frame_ms = 0.0
            self.last_action = ""
            #: When a held toast expires; the main loop repaints then. Set by
            #: toast(hold=...), cleared by a keypress. A programmatic repaint
            #: puts the strip back so it survives a refresh that follows it.
            self.toast_until = None
            self._toast_shown = ("", None)
            #: Messages raised mid-sequence, shown as one held toast at the
            #: next full repaint - see notice().
            self._notices = []
            #: Layout the hints were last drawn for; a press from a different
            #: kind of pad, or a hot-plug, changes it and forces a repaint.
            self.shown_layout = self.input.layout

            self.all_rows: dict[int, list[Row]] = {}
            # Systems are tracked per tab: a MiSTer runs far more cores than
            # the server has ROMs for, and cycling through systems with nothing
            # in them is just noise. The selection is held by name so it can be
            # carried across tabs that both have it.
            self.tab_systems: dict[int, list[str]] = {}
            self.system_filter_name = "All"
            #: Catalog search text; empty means no filter. Every word must
            #: appear in the name, in any order.
            self.search = ""
            #: Where the catalogue cursor was last time - restored once the
            #: catalogue is in, and written back on exit.
            self.ui_state = gsconfig.load_ui_state()
            self._catalog_last = ("All", "")
            self._catalog_restore_pending = True
            self._data_version = 0
            self._rows_key = None
            self._rows_cache = []
            self.load_data()
        except Exception:
            self.close()
            raise

    # ------------------------------------------------------------------ data

    def load_data(self, rescan: bool = True) -> None:
        """Rebuild every tab's rows.

        ``rescan=False`` reuses the last walk of the games folders. That walk
        is the expensive part - a stat per entry across every core folder on
        an exfat card mounted ``sync`` - and most refreshes do not need it:
        queueing a download, clearing the queue, or re-labelling hints for a
        new pad changes nothing on disk.
        """
        saves = []
        for entry in self.engine.entries:
            detail = _save_detail(entry)
            if detail == _human_size(entry.size) and self.catalog_groups:
                # Only once the catalogue is known: a save for a game that
                # is not on this MiSTer can be installed straight from here.
                group = self.game_for_save(entry)
                if group is not None and not self.game_installed(group):
                    detail = "game not installed"
            saves.append(Row(entry.system, entry.display, detail,
                             entry.status, ref=entry,
                             ra=self.has_achievements(entry.system,
                                                      entry.display)))

        if rescan:
            self.scan_installed()
        installed = [Row(item.system, item.name, item.where, "installed",
                         ra=self.has_achievements(item.system, item.name))
                     for item in self.installed_entries]
        # Keyed with the pack flag: the name key folds "(MSU1)" away so a
        # pack and its plain ROM share a save, but they are not the same
        # install - ActRaiser on the card must not tick ActRaiser (MSU1).
        installed_ids = {
            (item.system, _normalize(item.name), bool(item.is_pack))
            for item in self.installed_entries
        }
        self.installed_ids = installed_ids
        catalog = []
        ra_games = set()
        for group in self.catalog_groups:
            state = ("installed" if self.game_installed(group)
                     else "not installed")
            detail = _human_size(group.size)
            if group.disc_count > 1:
                detail = "%d discs  %s" % (group.disc_count, detail)
            kind = str(group.rows[0].get("bundle_kind") or "").lower() \
                if group.rows and group.rows[0].get("is_bundle") else ""
            if kind:
                detail = "%s  %s" % (MSU_KIND_LABELS.get(kind, kind), detail)
            has_ra = _group_has_achievements(group)
            if has_ra:
                ra_games.add((group.system, _normalize(group.name)))
            catalog.append(Row(group.system, group.name, detail, state,
                               ref=group, ra=has_ra))
        self.ra_games = ra_games

        queued = [
            Row(item.system, item.name,
                _progress_text(item), item.status, ref=item)
            for item in self.queue.items
        ]

        self.all_rows = {
            0: saves,
            1: catalog,
            2: installed,
            3: queued,
            4: [
                Row("", "Server URL",
                    self.config.server_url or "not configured", ""),
                Row("", "API key",
                    "set" if self.config.api_key else "not set", ""),
                Row("", "ROM target", self.config.rom_target, ""),
                Row("", "Adjust screen",
                    "%g%% / %g%% margin%s"
                    % (self.overscan[0], self.overscan[1],
                       "" if self.config.overscan_is_set else " (auto)"), ""),
                Row("", "Remap buttons",
                    "%d bound" % len(self.config.buttons)
                    if self.config.buttons else "defaults", ""),
                Row("", "Refresh catalog",
                    "rescan the server's ROMs and refetch", ""),
                Row("", "Config file", self.config.path, ""),
                Row("", "Server status",
                    self.server_status or "not checked", ""),
                Row("", "Controller", self.input.layout_description(), ""),
                Row("", "Input devices",
                    ", ".join(self.input.device_names()) or "none", ""),
            ],
        }
        self.tab_systems = {
            tab: (catalog_systems(rows) if tab == 1
                  else ["All"] + sorted({row.system for row in rows if row.system}))
            for tab, rows in self.all_rows.items()
        }
        self._resync_system_filter()
        self._data_version += 1
        self._rows_key = None

    def scan_installed(self) -> None:
        """Walk every games folder on SD and USB for what is installed."""
        # Kept as objects alongside the rows so delete and move know the real
        # path, whether it is a folder, and which storage it currently sits on.
        self.installed_entries = []
        for root in GAMES_ROOTS:
            for folder in sorted(_listdir(root)):
                path = os.path.join(root, folder)
                if not os.path.isdir(path):
                    continue
                system = MISTER_FOLDER_TO_SYSTEM.get(folder, folder.upper())
                where = "USB" if root.startswith("/media/usb") else "SD"
                # A games folder also holds the core's BIOS, blank disks and
                # support folders; listing those made every system MiSTer
                # ships a folder for look like it had a game in it.
                for rom in list_installed_games(
                        self.engine.provider, path, folder, system):
                    self.installed_entries.append(
                        InstalledGame(system, rom.name, rom.path, where,
                                      folder, is_pack=rom.is_pack))

    @property
    def systems(self) -> list[str]:
        """Systems that actually have rows in the tab being viewed."""
        return self.tab_systems.get(self.tab, ["All"])

    @property
    def current_system(self) -> str:
        """The filter as shown: "All" when the index is stale for this tab.

        Every tab has its own system list, so an index carried over from a
        longer list can point past the end of a shorter one for a frame.
        """
        systems = self.systems
        if 0 <= self.system_filter < len(systems):
            return systems[self.system_filter]
        return "All"

    def _resync_system_filter(self) -> None:
        """Keep the chosen system when the new tab has it, else show all.

        The *name* is never dropped: a tab without that system shows "All"
        for as long as you are on it, and the choice comes back on the next
        tab - or the next refresh - that has it. Forgetting it here meant
        picking PS1 on Catalog, glancing at Downloads, and coming back to
        every system again.
        """
        systems = self.systems
        if self.system_filter_name in systems:
            self.system_filter = systems.index(self.system_filter_name)
        else:
            self.system_filter = 0

    def rows(self) -> list[Row]:
        """The visible rows for the current tab and filter.

        Cached because draw_list asks for it once per drawn row, and filtering
        a catalogue of tens of thousands of entries that many times per repaint
        is the difference between instant and unusable.
        """
        key = (self.tab, self.system_filter, self._data_version,
               self.search if self.tab == 1 else "")
        if self._rows_key == key:
            return self._rows_cache
        rows = self.all_rows.get(self.tab, [])
        wanted = self.current_system
        packs_of = msu_filter_base(wanted)
        if packs_of is not None:
            rows = [row for row in rows
                    if row.system == packs_of and row_pack_kind(row)]
        elif wanted != "All":
            rows = [row for row in rows if row.system == wanted]
        if self.tab == 1 and self.search:
            rows = _search_rows(rows, self.search)
        self._rows_key = key
        self._rows_cache = rows
        return rows

    # --------------------------------------------------------------- drawing

    def text(self, font: Font, x: int, y: int, message: str, fg, bg,
             max_width: int | None = None) -> int:
        if max_width is not None:
            message = font.ellipsize(message, max_width)
        width, height, pixels = self.renderer.render(font, message, fg, bg)
        if width:
            self.fb.blit(x, y, width, height, pixels)
        return width

    def draw_all(self) -> None:
        started = time.time()
        self.fb.clear(theme.BACKGROUND)
        self.draw_header()
        self.draw_tabs()
        self.draw_list()
        # Timed before the footer so the footer can report this frame's cost
        # rather than the previous one's.
        self.last_frame_ms = (time.time() - started) * 1000
        self.draw_footer()
        if self.tab == 1:
            rows = self.rows()
            if rows and self.selected < len(rows):
                self._catalog_last = (self.current_system,
                                      rows[self.selected].name)
        if self._notices:
            notices, self._notices = self._notices, []
            self.toast("  |  ".join(n[0] for n in notices),
                       next((n[1] for n in notices if n[1] is not None),
                            None),
                       hold=max(n[2] for n in notices))
        elif self.toast_until is not None:
            if time.time() < self.toast_until:
                message, colour = self._toast_shown
                self.toast(message, colour)
            else:
                self.toast_until = None

    def draw_header(self) -> None:
        metrics = self.metrics
        self.fb.fill_rect(0, 0, metrics.width, metrics.header_h, theme.HEADER)
        self.fb.fill_rect(0, metrics.header_h - 2, metrics.width, 2,
                          theme.DIVIDER)

        baseline = (metrics.header_h - self.font_title.line_height) // 2
        used = self.text(self.font_title, metrics.pad, baseline, "GameSync",
                         theme.TEXT_STRONG, theme.HEADER)
        self.text(self.font_small, metrics.pad + used + 10,
                  baseline + self.font_title.line_height
                  - self.font_small.line_height - 2,
                  "MiSTer", theme.ACCENT, theme.HEADER)

        label = "%s  -  %d items" % (self.current_system, len(self.rows()))
        if self.tab == 1 and self.search:
            label = '%s  "%s"  -  %d items' % (self.current_system,
                                                self.search, len(self.rows()))
        width = self.font_small.measure(label)
        self.text(self.font_small, metrics.width - metrics.pad - width,
                  (metrics.header_h - self.font_small.line_height) // 2,
                  label, theme.TEXT_DIM, theme.HEADER)

    def draw_tabs(self) -> None:
        metrics = self.metrics
        top = metrics.header_h
        self.fb.fill_rect(0, top, metrics.width, metrics.tab_h, theme.TAB_BAR)

        x = metrics.pad
        gap = int(metrics.pad * 1.4)
        for index, name in enumerate(TABS):
            active = index == self.tab
            if name == "Downloads":
                name = self._downloads_tab_label()
            width = self.font_row.measure(name)
            colour = theme.TEXT_STRONG if active else theme.TEXT_DIM
            self.text(self.font_row, x,
                      top + (metrics.tab_h - self.font_row.line_height) // 2 - 2,
                      name, colour, theme.TAB_BAR)
            if active:
                self.fb.fill_rect(x, top + metrics.tab_h - 4, width, 3,
                                  theme.ACCENT)
            x += width + gap
        self.fb.fill_rect(0, top + metrics.tab_h - 1, metrics.width, 1,
                          theme.DIVIDER)

    def _downloads_tab_label(self) -> str:
        """"Downloads 43%" while one runs, "Downloads (2)" while some wait."""
        current = self.worker.current
        if self.worker.running and current is not None:
            if current.size:
                return "Downloads %d%%" % int(current.progress * 100)
            return "Downloads ..."
        pending = len(self.queue.pending())
        if pending:
            return "Downloads (%d)" % pending
        return "Downloads"

    def draw_list(self) -> None:
        metrics = self.metrics
        rows = self.rows()
        self.fb.fill_rect(0, metrics.list_top, metrics.width,
                          metrics.list_bottom - metrics.list_top,
                          theme.BACKGROUND)

        if not rows:
            message = self._empty_message()
            width = self.font_row.measure(message)
            self.text(self.font_row, (metrics.width - width) // 2,
                      metrics.list_top + 40, message, theme.TEXT_DIM,
                      theme.BACKGROUND)
            return

        for offset in range(metrics.visible_rows):
            index = self.scroll + offset
            if index >= len(rows):
                break
            self.draw_row(index, offset)
        self.draw_scrollbar(len(rows))

    def _empty_message(self) -> str:
        """Say why a list is empty; "nothing here" is not a diagnosis."""
        scope = self.current_system
        if scope == "All":
            scope = ""
        if self.tab == 1:
            if not self.client:
                return "No server configured - see the Settings tab"
            if not self.catalog:
                return ("Catalog not loaded - press %s to fetch it"
                        % self.input.label(gsinput.ALT))
            if self.search:
                return ('Nothing matches "%s" - %s clears the search'
                        % (self.search, self.input.label(gsinput.SEARCH)))
            return ("No %s ROMs on the server" % scope) if scope else                 "The server has no ROMs this MiSTer can run"
        if self.tab == 0:
            return ("No %s saves on this MiSTer" % scope) if scope else                 "No saves found in /media/fat/saves"
        if self.tab == 2:
            return ("No %s games installed" % scope) if scope else                 "No games installed on the SD card or USB"
        if self.tab == 3:
            return "Nothing queued - pick a ROM on the Catalog tab"
        return "Nothing here yet"

    def draw_row(self, index: int, offset: int) -> None:
        metrics = self.metrics
        rows = self.rows()
        if index >= len(rows):
            return
        row = rows[index]
        y = metrics.list_top + offset * metrics.row_h
        selected = index == self.selected
        background = (theme.ROW_SELECTED if selected
                      else (theme.ROW if index % 2 == 0 else theme.ROW_ALT))

        self.fb.fill_rect(0, y, metrics.width, metrics.row_h, background)
        if selected:
            self.fb.fill_rect(0, y, 4, metrics.row_h, theme.ACCENT)

        x = metrics.pad
        if row.system and metrics.chip_w:
            chip_y = y + (metrics.row_h - metrics.chip_h) // 2
            self.fb.fill_rect(x, chip_y, metrics.chip_w, metrics.chip_h,
                              theme.system_color(row.system))
            label = self.font_chip.ellipsize(row.system, metrics.chip_w - 6)
            label_w = self.font_chip.measure(label)
            self.text(self.font_chip,
                      x + (metrics.chip_w - label_w) // 2,
                      chip_y + (metrics.chip_h
                                - self.font_chip.line_height) // 2,
                      label, theme.CHIP_TEXT, theme.system_color(row.system))
            x += metrics.chip_w + int(metrics.pad * 0.6)

        status_colour = theme.STATUS_COLORS.get(row.status, theme.TEXT_DIM)
        # At 240p there is not enough width for three columns, so the detail
        # gives way - but only where a status is already competing for the
        # right-hand side. On the Settings tab the detail *is* the value, and
        # dropping it would leave a list of labels with nothing beside them.
        detail = "" if (metrics.compact and row.status) else row.detail
        status_w = self.font_small.measure(row.status) if row.status else 0
        detail_w = self.font_small.measure(detail) if detail else 0
        right_edge = metrics.width - metrics.pad - metrics.scroll_w

        # The RA badge sits between the name and the detail, so it survives
        # at 240p where the detail column is the thing that gives way.
        badge_w = self._ra_badge_width() if row.ra else 0

        # The name always keeps at least half the row. A Settings value like a
        # full config path or a controller name is longer than the label it
        # belongs to, and without this it pushes that label out entirely.
        available = right_edge - x - int(metrics.pad * 2) - badge_w
        if detail_w:
            detail_w = min(detail_w, max(0, available - status_w) // 2)
        name_width = available - status_w - detail_w
        name_y = y + (metrics.row_h - self.font_row.line_height) // 2
        if selected:
            self._draw_name_marquee(x, name_y, row.name, name_width, background)
        else:
            self.text(self.font_row, x, name_y, row.name, theme.TEXT,
                      background, max_width=name_width)

        if badge_w:
            self._draw_ra_badge(x + name_width + int(metrics.pad * 0.4),
                                y, badge_w, background)

        if row.status:
            self.text(self.font_small, right_edge - status_w,
                      y + (metrics.row_h - self.font_small.line_height) // 2,
                      row.status, status_colour, background)
        if detail:
            self.text(self.font_small,
                      right_edge - status_w - detail_w - int(metrics.pad * 0.8),
                      y + (metrics.row_h - self.font_small.line_height) // 2,
                      detail, theme.TEXT_DIM, background, max_width=detail_w)

    # ------------------------------------------------------ RA badge

    def _ra_badge_width(self) -> int:
        if self._ra_badge_w is None:
            self._ra_badge_w = self.font_chip.measure(RA_BADGE_LABEL) + 8
        return self._ra_badge_w

    def _draw_ra_badge(self, x: int, y: int, width: int, background) -> None:
        metrics = self.metrics
        height = max(self.font_chip.line_height + 2, metrics.chip_h or 0)
        height = min(height, metrics.row_h - 2)
        top = y + (metrics.row_h - height) // 2
        self.fb.fill_rect(x, top, width, height, theme.RA_BADGE)
        label_w = self.font_chip.measure(RA_BADGE_LABEL)
        self.text(self.font_chip, x + (width - label_w) // 2,
                  top + (height - self.font_chip.line_height) // 2,
                  RA_BADGE_LABEL, theme.RA_BADGE_TEXT, theme.RA_BADGE)

    # -------------------------------------------------------- marquee

    def _draw_name_marquee(self, x: int, y: int, name: str,
                           name_width: int, background) -> None:
        """Draw the selected row's name, sliding it when it does not fit.

        On a CRT half a long filename is off the end of the column, so the
        selected row scrolls its own name instead of ellipsizing it: the
        cursor is the only place a full name is ever needed, and scrolling
        every row at once would be unreadable.
        """
        key = (self.tab, self.selected, name)
        if key != self._marquee_key:
            self._marquee_key = key
            self.reset_marquee()

        full_w = self.font_row.measure(name)
        overflow = full_w - name_width
        if overflow <= 0 or name_width <= 0:
            self._marquee_span = 0
            self.text(self.font_row, x, y, name, theme.TEXT, background,
                      max_width=name_width)
            return

        self._marquee_span = overflow
        offset = min(self._marquee_offset, overflow)
        width, height, pixels = self.renderer.render(
            self.font_row, name, theme.TEXT, background)
        if width:
            self.fb.blit(x, y, width, height, pixels,
                         crop_x=offset, crop_w=name_width)

    def reset_marquee(self) -> None:
        """Back to the first character, held there before the crawl starts."""
        self._marquee_offset = 0
        self._marquee_span = 0
        self._marquee_next = time.time() + MARQUEE_START_HOLD

    def tick_marquee(self) -> bool:
        """Advance the selected row's marquee. True when it repainted.

        Driven from the main loop rather than a timer: the loop already
        wakes every 30ms for input, and a thread here would be repainting
        the framebuffer from under whatever else is drawing.
        """
        if self._marquee_span <= 0:
            return False
        now = time.time()
        if now < self._marquee_next:
            return False
        if self._marquee_offset >= self._marquee_span:
            # Held at the end long enough to read it; snap back to the start.
            self._marquee_offset = 0
            self._marquee_next = now + MARQUEE_START_HOLD
        else:
            self._marquee_offset = min(self._marquee_span,
                                       self._marquee_offset + MARQUEE_STEP)
            self._marquee_next = now + (MARQUEE_END_HOLD
                                        if self._marquee_offset >= self._marquee_span
                                        else MARQUEE_INTERVAL)
        rows = self.rows()
        if not rows or self.selected >= len(rows):
            return False
        self.draw_row(self.selected, self.selected - self.scroll)
        return True

    def has_achievements(self, system: str, name: str) -> bool:
        """Does the catalogue know a game of this name with an RA set?"""
        if not self.ra_games:
            return False
        return (system, _normalize(name)) in self.ra_games

    def draw_scrollbar(self, total: int) -> None:
        metrics = self.metrics
        if total <= metrics.visible_rows:
            return
        track_x = metrics.width - metrics.scroll_w - 2
        track_h = metrics.list_bottom - metrics.list_top
        self.fb.fill_rect(track_x, metrics.list_top, metrics.scroll_w,
                          track_h, theme.SCROLL_TRACK)
        thumb_h = max(24, int(track_h * metrics.visible_rows / total))
        span = max(1, total - metrics.visible_rows)
        thumb_y = metrics.list_top + int((track_h - thumb_h) * self.scroll / span)
        self.fb.fill_rect(track_x, thumb_y, metrics.scroll_w, thumb_h,
                          theme.SCROLL_THUMB)

    def draw_footer(self) -> None:
        metrics = self.metrics
        top = metrics.height - metrics.footer_h
        self.fb.fill_rect(0, top, metrics.width, metrics.footer_h, theme.FOOTER)
        self.fb.fill_rect(0, top, metrics.width, 1, theme.DIVIDER)

        y = top + (metrics.footer_h - self.font_small.line_height) // 2
        # Hints name actions, not buttons: the label comes from whatever is
        # actually bound, so a cabinet with six buttons and no L2 is told the
        # truth rather than being told to press something it does not have.
        tabs = "%s/%s" % (self.input.label(gsinput.PREV_TAB),
                          self.input.label(gsinput.NEXT_TAB))
        systems = "%s/%s" % (self.input.label(gsinput.PREV_SYSTEM),
                             self.input.label(gsinput.NEXT_SYSTEM))
        primary = self.input.label(gsinput.PRIMARY)
        back = self.input.label(gsinput.BACK)
        sync = self.input.label(gsinput.SYNC)
        alt = self.input.label(gsinput.ALT)

        if self.tab == 1:
            hints = [(primary, "Install"), (back, "Exit"),
                     (alt, "Search"), (systems, "System"), (tabs, "Tab")]
            if self.search:
                hints.insert(4, (self.input.label(gsinput.SEARCH), "Clear"))
        elif self.tab == 2:
            hints = [(primary, "Move SD/USB"), (back, "Exit"), (sync, "Delete"),
                     (alt, "Refresh"), (systems, "System"), (tabs, "Tab")]
        elif self.tab == 3:
            rows = self.rows()
            failed = (rows and self.selected < len(rows)
                      and rows[self.selected].status == gsdownloads.FAILED)
            hints = [(primary, "Retry" if failed else "Start"),
                     (back, "Exit"),
                     (sync, "Start queue"), (alt, "Clear done"),
                     (systems, "System"), (tabs, "Tab")]
        elif self.tab == 4:
            hints = [(primary, "Change"), (back, "Exit"), (tabs, "Tab")]
        else:
            hints = [(primary, "Sync / install"), (back, "Exit"),
                     (sync, "Delete save"),
                     (alt, "Rescan / sync all"), (systems, "System"),
                     (tabs, "Tab")]

        if metrics.compact:
            # 240p has room for about four hints; keep the ones that are not
            # guessable, and always keep Tab because nothing else reveals that
            # the other screens exist.
            hints = hints[:3]
            if not any(key == tabs for key, _label in hints):
                hints.append((tabs, "Tab"))
        x = metrics.pad
        for key, label in hints:
            width = self.text(self.font_small, x, y, key, theme.ACCENT,
                              theme.FOOTER)
            x += width + 6
            width = self.text(self.font_small, x, y, label, theme.TEXT_DIM,
                              theme.FOOTER)
            x += width + int(metrics.pad * 0.9)

        # Live input feedback: proves on-screen that the pad is being read.
        # Dropped at 240p, where the width it costs is worth more than it is.
        if not metrics.compact:
            stamp = "%s   %.1f ms" % (self.last_action or "-",
                                      self.last_frame_ms)
            width = self.font_small.measure(stamp)
            self.text(self.font_small, metrics.width - metrics.pad - width, y,
                      stamp, theme.TEXT_DIM, theme.FOOTER)

    # ------------------------------------------------------------------ loop

    def move(self, delta: int) -> None:
        rows = self.rows()
        if not rows:
            return
        previous = self.selected
        previous_scroll = self.scroll
        self.selected = max(0, min(len(rows) - 1, self.selected + delta))
        if self.selected < self.scroll:
            self.scroll = self.selected
        elif self.selected >= self.scroll + self.metrics.visible_rows:
            self.scroll = self.selected - self.metrics.visible_rows + 1

        if self.scroll != previous_scroll:
            started = time.time()
            self.draw_list()
            self.last_frame_ms = (time.time() - started) * 1000
            self.draw_footer()
            return
        if self.selected == previous:
            return
        # Only the two affected rows are dirty.
        started = time.time()
        self.draw_row(previous, previous - self.scroll)
        self.draw_row(self.selected, self.selected - self.scroll)
        self.last_frame_ms = (time.time() - started) * 1000
        self.draw_footer()

    def jump_letter(self, direction: int) -> None:
        """Move to the first row whose name starts with a different letter.

        Runs off the end rather than wrapping, so a held stick comes to rest
        at the last (or first) row instead of cycling.
        """
        rows = self.rows()
        if not rows:
            return
        current = _first_letter(rows[self.selected].name)
        step = 1 if direction > 0 else -1
        target = None
        index = self.selected + step
        while 0 <= index < len(rows):
            letter = _first_letter(rows[index].name)
            if letter and letter != current:
                target = index
                break
            index += step
        if target is None:
            target = len(rows) - 1 if step > 0 else 0
        if step < 0 and target > 0:
            # Going back lands on the *first* row of the previous letter,
            # not its last, so each step reads as "now at M", "now at L".
            letter = _first_letter(rows[target].name)
            while target > 0 and _first_letter(rows[target - 1].name) == letter:
                target -= 1
        self.move(target - self.selected)

    def set_tab(self, delta: int) -> None:
        # Each tab keeps its own cursor: glancing at Downloads and coming
        # back should land on the game you were looking at, not row one.
        self.tab_positions[self.tab] = (self.selected, self.scroll)
        self.tab = (self.tab + delta) % len(TABS)
        self.selected, self.scroll = self.tab_positions.get(self.tab, (0, 0))
        self._resync_system_filter()
        self._rows_key = None
        self._clamp_cursor()
        self.draw_all()
        # The catalogue can be thousands of rows, so it is fetched the first
        # time it is actually looked at rather than on startup.
        if self.tab == 1 and not self.catalog and self.client is not None:
            self.load_catalog()

    def _clamp_cursor(self) -> None:
        """Keep a remembered cursor inside the rows the tab has now: the list
        may have shrunk (a download cleared, a game deleted) since it was
        last looked at."""
        count = len(self.rows())
        if count == 0:
            self.selected = self.scroll = 0
            return
        self.selected = max(0, min(count - 1, self.selected))
        visible = self.metrics.visible_rows
        self.scroll = max(0, min(self.scroll, max(0, count - visible)))
        if self.selected < self.scroll:
            self.scroll = self.selected
        elif self.selected >= self.scroll + visible:
            self.scroll = self.selected - visible + 1

    def set_system(self, delta: int) -> None:
        systems = self.systems
        if len(systems) < 2:
            return
        self.system_filter = (self.system_filter + delta) % len(systems)
        self.system_filter_name = systems[self.system_filter]
        self.selected = 0
        self.scroll = 0
        self._rows_key = None
        self.draw_all()

    def handle(self, action: str) -> None:
        self.last_action = action
        # A press dismisses whatever toast was being held.
        self.toast_until = None
        if action == gsinput.DOWN:
            self.move(1)
        elif action == gsinput.UP:
            self.move(-1)
        elif action in (gsinput.RIGHT, gsinput.LEFT):
            direction = 1 if action == gsinput.RIGHT else -1
            # A tap pages. Held long enough, the same direction sweeps by
            # initial instead - the Steam Deck client's ramp - so a 3,000-row
            # catalogue is crossed in a few seconds rather than a few hundred
            # page flips.
            if self.input.held_for() >= gsinput.LETTER_AFTER:
                self.jump_letter(direction)
            else:
                self.move(direction * self.metrics.visible_rows)
        elif action == gsinput.NEXT_TAB:
            self.set_tab(1)
        elif action == gsinput.PREV_TAB:
            self.set_tab(-1)
        elif action == gsinput.NEXT_SYSTEM:
            self.set_system(1)
        elif action == gsinput.PREV_SYSTEM:
            self.set_system(-1)
        elif action == gsinput.SYNC:
            if self.tab in (1, 3):
                self.do_run_queue()
            elif self.tab == 2:
                self.do_delete_installed()
            elif self.tab == 0:
                # Same button deletes on Installed; keep the two tabs alike.
                self.do_delete_save()
            else:
                self.do_sync()
        elif action == gsinput.PRIMARY:
            if self.tab == 1:
                self.do_install_selected()
            elif self.tab == 2:
                self.do_move_installed()
            elif self.tab == 3:
                self.do_download_action()
            elif self.tab == 4:
                self.do_settings_action()
            else:
                self.do_sync_selected()
        elif action == gsinput.ALT:
            if self.tab == 2:
                self.load_data()
                self.draw_all()
            elif self.tab == 1:
                if self.catalog:
                    self.do_search()
                else:
                    self.load_catalog()
            elif self.tab == 3:
                self.queue.clear_finished()
                self.load_data(rescan=False)
                self.draw_all()
            else:
                # Sync-all lost its own button to Delete, so it lives here
                # beside Rescan - both start with the same scan anyway.
                choice = self.choose("Saves", ["Rescan", "Sync all"],
                                     title="Saves")
                if choice == 0:
                    self.do_rescan()
                elif choice == 1:
                    self.do_sync()
                else:
                    self.draw_all()
        elif action == gsinput.SEARCH:
            if self.tab == 1:
                if self.search:
                    self.set_search("")
                elif self.catalog:
                    self.do_search()
        elif action == gsinput.SETTINGS:
            self.set_tab(TABS.index("Settings") - self.tab)
        elif action in (gsinput.BACK, gsinput.QUIT):
            self.confirm_exit()

    def confirm_exit(self) -> None:
        """Ask before leaving: Back is also the button that closes dialogs.

        A stray extra press after cancelling something used to drop the user
        straight back to the MiSTer menu, and relaunching means sitting
        through the scan again.
        """
        detail = ["Returns to the MiSTer menu."]
        if self.worker.running:
            detail.append("A download is in progress - it resumes "
                          "next time GameSync starts.")
        if self.confirm("Exit GameSync?", detail, danger=False,
                        title="Exit"):
            self.running = False
            return
        self.draw_all()

    def run(self, timeout: float | None = None, start_tab: int = 0,
            show_conflict: bool = False, calibrate: bool = False,
            demo_confirm: bool = False, demo_choose: bool = False,
            demo_search: bool = False) -> None:
        self.tab = max(0, min(start_tab, len(TABS) - 1))
        # Paint first, then scan: hashing every save and asking the server
        # takes a moment, and a blank screen while it happens looks broken.
        # Set before anything modal runs: --timeout has to bound the
        # calibration and remap loops too, or an automated run leaves the
        # cabinet sitting in graphics mode waiting for a press that is never
        # coming.
        self.deadline = None if timeout is None else time.time() + timeout
        self.draw_all()
        if calibrate:
            # Before the scan, so a cabinet that cannot show the Settings tab
            # can still be fixed without waiting for a catalogue fetch.
            self.adjust_screen()
        if demo_confirm:
            self.confirm(
                "Sync all saves now?",
                ["3 upload, 41 download",
                 "Downloads overwrite saves on this MiSTer.",
                 "2 conflicts will be skipped."],
                title="Sync all")
            self.draw_all()
        if demo_choose:
            self.choose("Final Fantasy IX (USA) is not installed",
                        ["Install the game and sync the save",
                         "Install the game only",
                         "Sync the save only"],
                        detail=["PS1  1.2 GB  4 discs"], title="Install")
            self.draw_all()
        if demo_search:
            self.prompt_text("Search the catalog", "FINAL FAN")
            self.draw_all()
        self.initial_load()
        # Whatever was left queued last time carries on; a download cut off
        # by closing the app resumes from its .part file.
        if self.client is not None:
            self.worker.start()
        if self.tab == 1 and not self.catalog and self.client is not None:
            self.load_catalog()
        if show_conflict:
            first = next((e for e in self.engine.entries
                          if e.status == gssync.CONFLICT), None)
            if first is not None:
                self.show_conflict(first)
        deadline = self.deadline

        # Safety net: with no readable input device there is no way to quit,
        # which on a console with no keyboard means a forced reboot.
        no_input_deadline = None
        if not self.input.device_names():
            self.draw_banner("No controller or keyboard found - exiting in 20s")
            no_input_deadline = time.time() + 20

        while self.running:
            for action in self.input.poll(0.03):
                self.handle(action)
                if not self.running:
                    break
            if deadline is not None and time.time() > deadline:
                break
            if self.toast_until is not None and time.time() > self.toast_until:
                self.draw_all()
            self.service_downloads()
            self.tick_marquee()
            if self.running and self.input.layout != self.shown_layout:
                # Someone picked up a different pad: every hint on screen
                # names buttons that pad does not have.
                self.shown_layout = self.input.layout
                self.load_data(rescan=False)
                self.draw_all()
            if no_input_deadline is not None:
                if self.input.device_names():
                    no_input_deadline = None
                    self.draw_all()
                elif time.time() > no_input_deadline:
                    break
        self.worker.stop()
        self.save_ui_state()

    def service_downloads(self) -> None:
        """Reflect the background downloads on screen.

        Called from the main loop between input polls, so everything here
        has to be cheap or the pad goes unread: a stalled loop is what made
        a held direction "latch". No games-folder rescan - the file just
        written is known exactly, so it is added to the installed list by
        hand - and no row rebuild for progress: the Downloads rows are
        updated in place and repainted. Landed games are batched, so a run
        of small ROMs finishing back to back costs one rebuild, not one
        each.
        """
        now = time.time()
        for item in self.worker.finished():
            if item.status == gsdownloads.DONE:
                self._note_installed(item)
            self._landed.append(item)

        if self._landed and (not self.worker.running
                             or now - self._landed_painted >= 1.5):
            landed, self._landed = self._landed, []
            self._landed_painted = now
            self.load_data(rescan=False)
            self.draw_all()
            failed = [item for item in landed
                      if item.status == gsdownloads.FAILED]
            if failed:
                self.toast("%s failed: %s" % (failed[0].name, failed[0].error),
                           theme.DANGER, hold=3.0)
            else:
                self.toast("Installed %s" % ", ".join(
                    item.name for item in landed), hold=2.0)
            self._progress_painted = now
            return

        if not self.worker.running or now - self._progress_painted < 0.5:
            return
        self._progress_painted = now
        if self.tab == 3:
            for row in self.all_rows.get(3, []):
                item = row.ref
                if item is not None:
                    row.detail = _progress_text(item)
                    row.status = item.status
            self.draw_list()
        self.draw_tabs()

    def _note_installed(self, item) -> None:
        """Add a download that just landed to the installed list.

        What the scan would find, without the scan: a CD system's game is
        its folder (the core names the save after it), anything else the
        file's stem.
        """
        system_dir = self._system_dir_of(item)
        folder = os.path.basename(system_dir)
        where = "USB" if item.target.startswith("/media/usb") else "SD"
        pack = bool(getattr(item, "bundle_kind", ""))
        if gsinstall.is_cd_system(item.system) or pack:
            path = item.directory
            name = os.path.basename(item.directory.rstrip("/"))
        else:
            path = item.target
            name = os.path.splitext(os.path.basename(item.target))[0]
        if any(entry.path == path for entry in self.installed_entries):
            return
        self.installed_entries.append(
            InstalledGame(item.system, name, path, where, folder, is_pack=pack))

    def _system_dir_of(self, item) -> str:
        """``games/<Core>`` for a queued item, above any per-game folder."""
        root = gsinstall.games_root(
            "usb" if item.target.startswith("/media/usb") else "sd").rstrip("/")
        directory = item.directory.rstrip("/")
        if directory.startswith(root + "/"):
            return posixpath.join(root, directory[len(root) + 1:].split("/")[0])
        return directory

    def save_ui_state(self) -> None:
        system, name = self._catalog_last
        if not name:
            return
        self.ui_state["catalog_system"] = system
        self.ui_state["catalog_row"] = name
        gsconfig.save_ui_state(self.ui_state)

    def restore_catalog_position(self) -> None:
        """Put the catalogue cursor back where it was last run, once."""
        if not self._catalog_restore_pending:
            return
        self._catalog_restore_pending = False
        system = str(self.ui_state.get("catalog_system") or "")
        name = str(self.ui_state.get("catalog_row") or "")
        if not name:
            return
        if system in self.tab_systems.get(1, []):
            self.system_filter_name = system
            self._resync_system_filter()
            self._rows_key = None
        tab, self.tab = self.tab, 1
        self._rows_key = None
        try:
            rows = self.rows()
            index = next((i for i, row in enumerate(rows)
                          if row.name == name), None)
        finally:
            self.tab = tab
            self._rows_key = None
        if index is None:
            return
        visible = self.metrics.visible_rows
        position = (index, max(0, min(index - visible // 2,
                                      len(rows) - visible)))
        if self.tab == 1:
            self.selected, self.scroll = position
        else:
            self.tab_positions[1] = position

    # ------------------------------------------------------------------ sync

    def toast(self, message: str, colour=None, hold: float = 0.0) -> None:
        """A one-line status strip over the list, drawn immediately.

        The UI is single-threaded on purpose: a MiSTer has two cores and the
        work here is network-bound, so a worker thread would buy nothing but a
        race against the framebuffer.

        ``hold`` keeps the strip up for that many seconds *without* blocking:
        the main loop repaints once it expires, and the next press repaints
        anyway. Use it instead of ``time.sleep`` after an action that is done
        - sleeping there just makes the app feel slow.
        """
        if hold > 0:
            self.toast_until = time.time() + hold
            self._toast_shown = (message, colour)
        metrics = self.metrics
        height = self.font_row.line_height + int(metrics.pad * 0.7)
        top = metrics.list_top
        background = colour or theme.HEADER
        self.fb.fill_rect(0, top, metrics.width, height, background)
        self.text(self.font_row, metrics.pad,
                  top + (height - self.font_row.line_height) // 2,
                  message, theme.TEXT_STRONG, background,
                  max_width=metrics.width - metrics.pad * 2)

    def notice(self, message: str, colour=None, hold: float = 2.5) -> None:
        """Queue a message for the next full repaint, as a held toast.

        For a warning raised in the middle of a sequence - a scan that failed
        before the server is contacted - where the next progress toast would
        paint over it at once. Sleeping there to make it readable froze the
        screen; this shows it once the work is done instead. Several notices
        share one strip.
        """
        self._notices.append((message, colour, hold))

    def require_client(self) -> bool:
        if self.client is not None:
            return True
        self.draw_all()
        self.toast("No server configured - edit %s" % self.config.path,
                   theme.DANGER, hold=2.5)
        return False

    def initial_load(self) -> None:
        """Scan on startup, and check the server if one is configured."""
        self.toast("Scanning saves...")
        try:
            self.engine.scan(progress=lambda text: self.toast(text))
        except Exception as exc:
            self.notice("Scan failed: %s" % exc, theme.DANGER)

        if self.client is not None:
            try:
                self.toast("Contacting %s..." % self.config.server_url)
                self.client.status()
                self.server_status = "connected"
                self.engine.fetch_plan(progress=lambda text: self.toast(text))
            except Exception as exc:
                self.server_status = str(exc)[:60]
                self.notice("Server unreachable: %s" % exc, theme.DANGER)
        else:
            self.server_status = "no server configured"

        self.load_data()
        self.draw_all()
        if self.client is not None and self.server_status == "connected":
            # Cheap now that it is cached by difference, and it is what lets
            # the Saves tab say which games are not installed.
            self.load_catalog(quiet=True)

    def do_rescan(self) -> None:
        if self.tab != 0:
            return
        # An explicit rescan is also how you say "the server changed".
        self.engine.refresh_server_data()
        self.toast("Scanning saves...")
        try:
            self.engine.scan(progress=lambda text: self.toast(text))
            if self.client is not None:
                self.engine.fetch_plan(progress=lambda text: self.toast(text))
        except Exception as exc:
            self.notice("Scan failed: %s" % exc, theme.DANGER)
        self.selected = 0
        self.scroll = 0
        self.load_data()
        self.draw_all()

    def do_sync(self) -> None:
        """X: sync everything the plan asked for."""
        if self.tab != 0 or not self.require_client():
            return
        self.toast("Scanning saves...")
        try:
            self.engine.scan(progress=lambda text: self.toast(text))
            self.engine.fetch_plan(progress=lambda text: self.toast(text))

            # Confirm after the plan is known, not before: "sync everything"
            # means nothing until you can see it is 3 uploads and 41
            # downloads, and 41 downloads overwrite 41 saves on this machine.
            uploads = sum(1 for e in self.engine.entries
                          if e.status == gssync.UPLOAD)
            downloads = sum(1 for e in self.engine.entries
                            if e.status in (gssync.DOWNLOAD,
                                            gssync.SERVER_ONLY))
            pending = sum(1 for e in self.engine.entries
                          if e.status == gssync.CONFLICT)
            if not uploads and not downloads:
                self.load_data()
                self.draw_all()
                self.toast("Everything is already up to date", hold=1.6)
                return

            detail = ["%d upload, %d download" % (uploads, downloads)]
            if downloads:
                detail.append("Downloads overwrite saves on this MiSTer.")
            if pending:
                detail.append("%d conflict%s will be skipped."
                              % (pending, "" if pending == 1 else "s"))
            if not self.confirm("Sync all saves now?", detail,
                                danger=bool(downloads), title="Sync all"):
                self.draw_all()
                return

            changed, failed = self.engine.sync_all(
                progress=lambda text: self.toast(text))
        except Exception as exc:
            self.load_data()
            self.draw_all()
            self.toast("Sync failed: %s" % exc, theme.DANGER, hold=3.0)
            return

        conflicts = sum(1 for e in self.engine.entries
                        if e.status == gssync.CONFLICT)
        summary = "Synced %d save%s" % (changed, "" if changed == 1 else "s")
        if failed:
            summary += ", %d failed" % failed
        if conflicts:
            summary += ", %d conflict%s left" % (
                conflicts, "" if conflicts == 1 else "s")
        self.load_data()
        self.draw_all()
        self.toast(summary, theme.DANGER if failed else theme.HEADER,
                   hold=2.0)

    # --------------------------------------------------------------- catalog

    #: Only the fields the UI and the installer use: a full catalogue can run
    #: to tens of thousands of rows and the device has 492 MB.
    CATALOG_FIELDS = ("rom_id", "system", "name", "filename", "size",
                      "disc_index", "disc_total", "primary_rom_id",
                      "title_id", "is_bundle", "bundle_kind",
                      "ra_achievements")

    def load_catalog(self, quiet: bool = False, force: bool = False) -> None:
        """The server's ROM list for the systems a MiSTer can run.

        Kept on disk between runs and refreshed by difference: the server
        publishes a fingerprint per system, and only systems whose
        fingerprint moved are fetched again. A server too old to publish
        fingerprints, or one that cannot be reached, falls back to a whole
        fetch or to the last copy respectively.

        ``force`` is the explicit "the library changed" gesture: the server
        is asked to rescan its ROM folder first (its catalogue lives in
        memory and otherwise waits for the periodic scan), then the on-disk
        copy is thrown away and every system fetched again.
        """
        if not self.require_client():
            return
        if not quiet:
            self.toast("Loading catalog...")
        runnable = sorted(MISTER_SYSTEM_FOLDER_CANDIDATES)
        cache = self.catalog_cache

        if force:
            self.toast("Asking the server to rescan its ROMs...")
            try:
                count = self.client.rescan_roms()
            except Exception as exc:
                self.notice("Server rescan failed: %s" % exc, theme.WARN)
            else:
                if count is None:
                    self.notice("Server did not allow a rescan - "
                                "refetching the catalog anyway", theme.WARN)
            cache.clear()
            cache.save()

        def report(loaded, total, system=""):
            self.toast("Loading catalog... %s%d/%d"
                       % ((system + " ") if system else "", loaded, total))

        try:
            server = self.client.rom_fingerprints()
        except Exception as exc:
            if len(cache):
                # Offline: the last copy is better than an empty tab.
                self.notice("Server unreachable - using the cached catalog",
                            theme.WARN)
                self._install_catalog(cache.all_rows(), quiet=quiet)
                return
            self.draw_all()
            self.toast("Catalog failed: %s" % exc, theme.DANGER, hold=2.5)
            return

        try:
            if server is None:
                # Pre-fingerprint server: fetch everything, cache nothing.
                roms = self.client.list_roms(progress=report,
                                             fields=self.CATALOG_FIELDS)
            else:
                fresh, stale = cache.plan(server, runnable)
                for system in stale:
                    rows = self.client.list_roms(
                        system,
                        progress=lambda n, t, s=system: report(n, t, s),
                        fields=self.CATALOG_FIELDS)
                    rows = [row for row in rows
                            if str(row.get("system") or "").upper() == system]
                    cache.put(system, str(server[system].get("fingerprint")
                                          or ""), rows)
                cache.save()
                roms = cache.all_rows()
                if stale and not quiet:
                    self.notice("Catalog: refreshed %d system%s, %d unchanged"
                                % (len(stale), "" if len(stale) == 1 else "s",
                                   len(fresh)))
        except Exception as exc:
            self.draw_all()
            self.toast("Catalog failed: %s" % exc, theme.DANGER, hold=2.5)
            return

        self._install_catalog(roms, quiet=quiet)

    def _install_catalog(self, roms, quiet: bool = False) -> None:
        """Turn catalogue rows into what the tabs show."""
        # Only offer what this device could actually run.
        runnable = set(MISTER_SYSTEM_FOLDER_CANDIDATES)
        self.catalog = sorted(
            (rom for rom in roms
             if str(rom.get("system") or "").upper() in runnable),
            key=lambda rom: (str(rom.get("system") or ""),
                             str(rom.get("name") or "").lower()),
        )
        self.catalog_groups = gsinstall.group_discs(self.catalog)
        self.engine.forget_catalog()
        # Save -> game: a save is keyed by title id, and so is every ROM row.
        self.group_by_title = {}
        for group in self.catalog_groups:
            for rom in group.rows:
                title_id = str(rom.get("title_id") or "").upper()
                if title_id:
                    self.group_by_title.setdefault(title_id, group)
        multi = sum(1 for g in self.catalog_groups if g.disc_count > 1)
        if not quiet:
            self.notice("%d games available%s"
                        % (len(self.catalog_groups),
                           " (%d multi-disc)" % multi if multi else ""),
                        hold=1.5)
            self.selected = 0
            self.scroll = 0
        self.load_data()
        self.restore_catalog_position()
        self.draw_all()

    def game_for_save(self, entry):
        """The catalogue entry for a save, or None if the server has no ROM.

        By title id first - a PS1 card and its disc share a serial, a slug
        system's save and ROM share a slug. Falls back to the name for the
        odd row the catalogue never gave a title id.
        """
        group = self.group_by_title.get(str(entry.title_id or "").upper())
        if group is not None:
            return group
        wanted = _normalize(entry.name)
        for candidate in self.catalog_groups:
            if candidate.system == entry.system \
                    and _normalize(candidate.name) == wanted:
                return candidate
        return None

    def game_installed(self, group) -> bool:
        kind = _group_pack_kind(group)
        system = group.system
        if kind:
            # A pack lives in the core folder its kind wants (an MSU-MD
            # title is a MegaCD install), so look there.
            layout = gsinstall.msu_pack_layout(kind, system)
            if layout is not None:
                system = layout[0]
        return (system, _normalize(group.name), bool(kind)) in self.installed_ids

    def queue_group(self, group):
        """Queue every disc of a game and start downloading.

        Returns the queued items, or an empty list when refused. Repaints
        with the Downloads tab updated and leaves the outcome as a held
        toast; the transfer itself runs in the background, so this must
        feel instant: no games-folder rescan, no sleep, no question.
        """
        # Every disc, or the core gets half a game and the wrong memory card.
        queued = [item for item in self.queue.enqueue_all(group.rows)
                  if item is not None]
        failed = [item for item in queued
                  if item.status == gsdownloads.FAILED]
        if queued and not failed:
            self.worker.start()
        self.load_data(rescan=False)
        self.draw_all()
        if not queued:
            self.toast("Could not queue %s" % group.name, theme.DANGER,
                       hold=2.0)
            return []
        if failed:
            self.toast(failed[0].error, theme.DANGER, hold=2.0)
            return []
        self.toast("Installing %s (%d disc%s) -> %s"
                   % (group.name, group.disc_count,
                      "" if group.disc_count == 1 else "s",
                      queued[0].directory), hold=1.4)
        return queued

    def do_install_selected(self) -> None:
        """A on the Catalog tab: queue the highlighted ROM."""
        if not self.require_client():
            return
        rows = self.rows()
        if not rows or self.selected >= len(rows):
            return
        row = rows[self.selected]
        group = next((g for g in self.catalog_groups
                      if g.system == row.system and g.name == row.name), None)
        if group is None:
            return
        self.queue_group(group)

    def offer_install_for_save(self, entry) -> bool:
        """A save whose game is not on this MiSTer: install it from here.

        The other clients do this from their save list too. Returns True
        when the sync of the save should still go ahead afterwards, and
        "now" when the user already chose "install and sync" - one decision
        covers the download and the save, so nothing else asks.
        """
        if self.client is None:
            return True
        if not self.catalog_groups:
            # Fetched on demand: the catalogue is not loaded until someone
            # looks at it, and this is someone looking.
            self.load_catalog()
            if not self.catalog_groups:
                return True
        group = self.game_for_save(entry)
        if group is None or self.game_installed(group):
            return True

        choice = self.choose(
            "%s is not installed" % group.name,
            ["Install the game and sync the save",
             "Install the game only",
             "Sync the save only"],
            detail=["%s  %s%s" % (group.system, _human_size(group.size),
                                   ("  %d discs" % group.disc_count)
                                   if group.disc_count > 1 else "")],
            title="Install")
        if choice is None:
            self.draw_all()
            return False
        if choice == 2:
            return True
        items = self.queue_group(group)
        if not items:
            return False
        if choice == 1:
            return False
        # The save needs the game on disk first, so this one waits for it.
        self.wait_for_downloads(items)
        # Only if it actually landed: a failed download leaves nothing to
        # write the save into.
        return "now" if self.game_installed(group) else False

    def do_download_action(self) -> None:
        """A on Downloads: retry the selected failure, else run the queue."""
        rows = self.rows()
        item = rows[self.selected].ref if rows and self.selected < len(rows) \
            else None
        if item is not None and item.status == gsdownloads.FAILED:
            if not self.queue.retry(item):
                self.toast("Still no MiSTer folder for %s" % item.system,
                           theme.DANGER, hold=2.0)
                return
            self.load_data(rescan=False)
            self.draw_all()
        self.do_run_queue()

    def do_run_queue(self) -> None:
        """X on Downloads: start (or resume) the queue in the background."""
        if not self.require_client():
            return
        if self.worker.running:
            self.toast("Already downloading", hold=1.0)
            return
        if not self.worker.start():
            self.toast("Nothing queued", hold=1.0)
            return
        self.draw_all()
        self.toast("Downloading in the background", hold=1.2)

    def wait_for_downloads(self, items) -> None:
        """Block until every one of ``items`` has left the queue.

        For the one flow that needs the file before it can go on - a save
        being installed together with its game. Progress shows in the toast
        strip; the worker does the transfer as usual.
        """
        if not self.worker.running:
            self.worker.start()
        last = 0.0
        while any(item.status in (gsdownloads.QUEUED, gsdownloads.DOWNLOADING)
                  for item in items):
            if self.deadline is not None and time.time() > self.deadline:
                break
            if not self.worker.running:
                break
            # Presses are swallowed rather than acted on: the list under the
            # toast is not what the user is looking at.
            self.input.poll(0.1)
            now = time.time()
            current = self.worker.current
            if current is not None and now - last >= 0.25:
                last = now
                self.toast("%s  %s" % (current.name, _progress_text(current)))
        # The finished events are consumed here, not by the main loop, so
        # the rescan happens before the caller looks for the game.
        self.worker.finished()
        self.load_data()
        self.draw_all()
        failed = [item for item in items
                  if item.status == gsdownloads.FAILED]
        if failed:
            self.toast("%s failed: %s" % (failed[0].name, failed[0].error),
                       theme.DANGER, hold=3.0)

    # -------------------------------------------------------------- settings

    def do_settings_action(self) -> None:
        """A on a Settings row: toggle what can be toggled, in place."""  # noqa: D401
        rows = self.rows()
        if not rows or self.selected >= len(rows):
            return
        row = rows[self.selected]

        if row.name == "Adjust screen":
            self.adjust_screen()
            return
        if row.name == "Remap buttons":
            self.remap_buttons()
            return
        if row.name == "Refresh catalog":
            if not self.require_client():
                return
            # "I changed the library": rescan server-side and refetch
            # everything, not just by fingerprint.
            self.load_catalog(force=True)
            return

        if row.name == "ROM target":
            order = list(gsconfig.ROM_TARGETS)
            current = self.config.rom_target
            index = order.index(current) if current in order else 0
            self.config.rom_target = order[(index + 1) % len(order)]
            try:
                gsconfig.save_config(self.config)
            except OSError as exc:
                self.draw_all()
                self.toast("Could not save config: %s" % exc, theme.DANGER,
                           hold=2.0)
                return
            # The queue installs into whichever storage is configured, so it
            # has to learn about this immediately.
            self.queue.rom_target = self.config.rom_target
            self.load_data(rescan=False)
            self.draw_all()
            self.toast("ROM target is now %s (%s)"
                       % (self.config.rom_target.upper(),
                          gsinstall.games_root(self.config.rom_target)),
                       hold=1.2)
            return

        self.draw_all()
        self.toast("%s is edited in %s" % (row.name, self.config.path),
                   hold=1.6)

    # ----------------------------------------------------------- calibration

    def _relayout(self) -> None:
        """Recompute the layout after the viewport changed size.

        Fonts are only re-rasterised when a size actually moved: at 240p the
        sizes are fixed constants, so nudging the overscan costs nothing but
        arithmetic. On a monitor they scale with height and do have to be
        rebuilt, and that is the slow path worth avoiding per keypress.
        """
        aspect = self.fb.pixel_aspect
        old = self.metrics
        self.metrics = theme.Metrics(self.fb.width, self.fb.height, aspect)
        sizes = ("font_title", "font_row", "font_small", "font_chip")
        if any(getattr(old, name) != getattr(self.metrics, name)
               for name in sizes):
            font_bytes = pkgutil.get_data("gamesync", "assets/font.ttf")

            def make_font(size, bold=False):
                return Font(font_bytes, size, bold=bold, pixel_aspect=aspect,
                            mono=self.lowres)

            self.font_title = make_font(self.metrics.font_title, bold=True)
            self.font_row = make_font(self.metrics.font_row)
            self.font_small = make_font(self.metrics.font_small)
            self.font_chip = make_font(self.metrics.font_chip, bold=True)
        self._rows_key = None

    def adjust_screen(self) -> None:
        """Console-style screen size calibration.

        A tube shows less than the signal carries, and how much less is a
        property of that particular set - there is nothing to read it from.
        So draw a frame at the edge of the safe area and let the picture
        itself be the instrument: shrink until all four corners are visible.
        """
        original = tuple(self.overscan)
        x_pct, y_pct = original
        step = 0.5

        while True:
            self.fb.set_overscan(x_pct, y_pct)
            self._relayout()
            self.draw_calibration(x_pct, y_pct)

            changed = False
            for action in self.input.poll(0.05):
                if action == gsinput.LEFT:
                    x_pct, changed = max(0.0, x_pct - step), True
                elif action == gsinput.RIGHT:
                    x_pct, changed = min(gsconfig.MAX_OVERSCAN,
                                         x_pct + step), True
                elif action == gsinput.UP:
                    y_pct, changed = max(0.0, y_pct - step), True
                elif action == gsinput.DOWN:
                    y_pct, changed = min(gsconfig.MAX_OVERSCAN,
                                         y_pct + step), True
                elif action == gsinput.PRIMARY:
                    self.config.overscan_x, self.config.overscan_y = x_pct, y_pct
                    self.overscan = (x_pct, y_pct)
                    try:
                        gsconfig.save_config(self.config)
                    except OSError as exc:
                        self.notice("Could not save: %s" % exc, theme.DANGER,
                                    hold=2.0)
                    self.load_data()
                    self.draw_all()
                    return
                elif action in (gsinput.BACK, gsinput.QUIT):
                    x_pct, y_pct = original
                    self.fb.set_overscan(x_pct, y_pct)
                    self._relayout()
                    self.load_data()
                    self.draw_all()
                    return
            if not changed:
                continue

    def draw_calibration(self, x_pct: float, y_pct: float) -> None:
        metrics = self.metrics
        self.fb.clear(theme.BACKGROUND)

        # A frame on the exact edge of the safe area, plus heavier corner
        # brackets: a thin line can sit just off the glass without it being
        # obvious, but a missing corner is unmistakable.
        edge = theme.ACCENT
        thickness = 2 if metrics.lowres else 3
        arm_x = max(12, metrics.width // 8)
        arm_y = max(8, metrics.height // 8)
        self.fb.fill_rect(0, 0, metrics.width, 1, edge)
        self.fb.fill_rect(0, metrics.height - 1, metrics.width, 1, edge)
        self.fb.fill_rect(0, 0, 1, metrics.height, edge)
        self.fb.fill_rect(metrics.width - 1, 0, 1, metrics.height, edge)
        for corner_x in (0, metrics.width - arm_x):
            for corner_y in (0, metrics.height - thickness):
                self.fb.fill_rect(corner_x, corner_y, arm_x, thickness, edge)
        for corner_x in (0, metrics.width - thickness):
            for corner_y in (0, metrics.height - arm_y):
                self.fb.fill_rect(corner_x, corner_y, thickness, arm_y, edge)

        lines = [
            ("Adjust screen", self.font_title, theme.TEXT_STRONG),
            ("Shrink until all four corners are on screen",
             self.font_row, theme.TEXT),
            ("Stick moves the edges   %s saves   %s cancels"
             % (self.input.label(gsinput.PRIMARY),
                self.input.label(gsinput.BACK)),
             self.font_small, theme.TEXT_DIM),
            ("margin  %g%% horizontal   %g%% vertical" % (x_pct, y_pct),
             self.font_small, theme.ACCENT),
        ]
        total = sum(font.line_height + 6 for _text, font, _rgb in lines)
        y = max(0, (metrics.height - total) // 2)
        for text, font, colour in lines:
            width = font.measure(text)
            self.text(font, max(0, (metrics.width - width) // 2), y, text,
                      colour, theme.BACKGROUND)
            y += font.line_height + 6

    # --------------------------------------------------------------- remap

    def remap_buttons(self) -> None:
        """Ask for one press per action and store the raw evdev codes.

        Necessary rather than nice-to-have: hid-generic hands an arcade
        encoder the whole BTN_GAMEPAD range in order, including BTN_C and
        BTN_Z, so which physical button produces which code differs per stick
        and cannot be assumed. Asking is the only way to know.
        """
        mapping = {}
        pending = list(gsinput.BINDABLE)
        index = 0
        per_action_timeout = 12.0

        while index < len(pending):
            action, label = pending[index]
            deadline = time.time() + per_action_timeout
            self.input.capture_next()
            captured = None

            while time.time() < deadline and captured is None:
                self.draw_remap(label, index, len(pending),
                                deadline - time.time(), mapping)
                # Keyboard actions still come through while the pad is being
                # captured, which is what makes escaping possible on a machine
                # whose only pad is the one being remapped.
                for keyboard_action in self.input.poll(0.05):
                    if keyboard_action in (gsinput.BACK, gsinput.QUIT):
                        self.load_data()
                        self.draw_all()
                        return
                captured = self.input.captured()

            if captured is not None:
                mapping[action] = captured
            index += 1

        if mapping:
            self.config.buttons = mapping
            self.input.set_buttons(mapping)
            try:
                gsconfig.save_config(self.config)
                self.notice("Saved %d bindings" % len(mapping), theme.OK,
                            hold=1.6)
            except OSError as exc:
                self.notice("Could not save: %s" % exc, theme.DANGER,
                            hold=1.6)
        self.load_data()
        self.draw_all()

    def draw_remap(self, label: str, index: int, total: int,
                   remaining: float, mapping: dict) -> None:
        metrics = self.metrics
        self.fb.clear(theme.BACKGROUND)

        lines = [
            ("Remap buttons  (%d of %d)" % (index + 1, total),
             self.font_small, theme.TEXT_DIM),
            (label, self.font_title, theme.TEXT_STRONG),
            ("Press the button you want for this",
             self.font_row, theme.TEXT),
            ("Wait %ds to leave it unbound" % max(0, int(remaining)),
             self.font_small, theme.TEXT_DIM),
        ]
        height = sum(font.line_height + 6 for _t, font, _c in lines)
        y = max(0, (metrics.height - height) // 2)
        for text, font, colour in lines:
            width = font.measure(text)
            self.text(font, max(0, (metrics.width - width) // 2), y, text,
                      colour, theme.BACKGROUND)
            y += font.line_height + 6

        # Progress bar doubles as proof the loop is alive while waiting.
        bar_w = metrics.width // 2
        bar_x = (metrics.width - bar_w) // 2
        bar_y = min(metrics.height - 4, y + 4)
        self.fb.fill_rect(bar_x, bar_y, bar_w, 3, theme.DIVIDER)
        done = int(bar_w * (index + 1) / float(total))
        self.fb.fill_rect(bar_x, bar_y, done, 3, theme.ACCENT)

    # ------------------------------------------------------------- installed

    def selected_install(self):
        rows = self.rows()
        if not rows or self.selected >= len(rows):
            return None
        row = rows[self.selected]
        return next((item for item in self.installed_entries
                     if item.name == row.name and item.system == row.system
                     and item.where == row.detail), None)

    def show_conflict(self, entry) -> None:
        """Both sides of a conflict, and the choice, on the device.

        A conflict means the save changed here *and* on the server since they
        last agreed, so there is no correct automatic answer - but there is
        enough information to make the call, and sending the user to another
        machine to make it was never a real answer.
        """
        metrics = self.metrics
        server = entry.server or {}
        server_hash = str(server.get("server_hash")
                          or server.get("save_hash") or "")
        server_size = int(server.get("server_size")
                          or server.get("save_size") or 0)
        source = (server.get("console") or server.get("last_sync_source")
                  or server.get("console_id") or "unknown")

        lines = [
            ("This MiSTer", _timestamp(entry.mtime), _human_size(entry.size),
             entry.hash),
            ("Server", _server_time(server), _human_size(server_size),
             server_hash),
        ]

        # A PlayStation card is shared between games: taking the server's copy
        # can delete other games' progress. Say so before offering the choice.
        own_lost, others_lost = self.engine.ps1_saves_at_risk(entry)

        width = int(metrics.width * 0.78)
        # Sized to its content rather than a guessed number of rows.
        height = (metrics.pad
                  + self.font_row.line_height + 4
                  + self.font_small.line_height + 8
                  + (self.font_small.line_height + 4) * len(lines)
                  + 4 + self.font_small.line_height + 10
                  + self.font_small.line_height
                  + ((self.font_small.line_height + 2)
                     if (own_lost or others_lost) else 0)
                  + ((self.font_small.line_height + 2)
                     if entry.message else 0)
                  + metrics.pad)
        left = (metrics.width - width) // 2
        top = (metrics.height - height) // 2

        self.fb.fill_rect(left - 2, top - 2, width + 4, height + 4,
                          theme.DANGER)
        self.fb.fill_rect(left, top, width, height, theme.HEADER)

        pad = metrics.pad
        y = top + pad // 2
        self.text(self.font_row, left + pad, y,
                  "Conflict: %s" % entry.display, theme.TEXT_STRONG, theme.HEADER,
                  max_width=width - pad * 2)
        y += self.font_row.line_height + 4
        self.text(self.font_small, left + pad, y,
                  "%s   %s" % (entry.system, entry.title_id),
                  theme.TEXT_DIM, theme.HEADER, max_width=width - pad * 2)
        y += self.font_small.line_height + 8

        for label, when, size, digest in lines:
            self.text(self.font_small, left + pad, y, label,
                      theme.ACCENT, theme.HEADER)
            self.text(self.font_small, left + pad + int(width * 0.20), y,
                      when or "unknown", theme.TEXT, theme.HEADER)
            self.text(self.font_small, left + pad + int(width * 0.50), y,
                      size, theme.TEXT, theme.HEADER)
            self.text(self.font_small, left + pad + int(width * 0.64), y,
                      (digest[:16] + "...") if digest else "none",
                      theme.TEXT_DIM, theme.HEADER)
            y += self.font_small.line_height + 4

        y += 4
        self.text(self.font_small, left + pad, y,
                  "Last synced by: %s" % source, theme.TEXT_DIM, theme.HEADER,
                  max_width=width - pad * 2)
        if own_lost:
            y += self.font_small.line_height + 2
            self.text(self.font_small, left + pad, y,
                      "The server's card has NO save for this game - taking it "
                      "would delete yours.",
                      theme.DANGER, theme.HEADER, max_width=width - pad * 2)
        elif others_lost:
            y += self.font_small.line_height + 2
            self.text(self.font_small, left + pad, y,
                      "This card also holds %d save(s) for other games, which "
                      "the server does not have." % len(others_lost),
                      theme.WARN, theme.HEADER, max_width=width - pad * 2)
        if entry.message:
            y += self.font_small.line_height + 2
            self.text(self.font_small, left + pad, y,
                      entry.message[0].upper() + entry.message[1:],
                      theme.TEXT_DIM, theme.HEADER, max_width=width - pad * 2)

        y += self.font_small.line_height + 10
        self.text(self.font_small, left + pad, y,
                  "%s = keep mine (upload)    %s = take the server's (download)"
                  "    %s = decide later"
                  % (self.input.label(gsinput.SYNC),
                     self.input.label(gsinput.PRIMARY),
                     self.input.label(gsinput.BACK)),
                  theme.TEXT_STRONG, theme.HEADER, max_width=width - pad * 2)

        while True:
            for action in self.input.poll(0.05):
                if action == gsinput.SYNC:
                    self._resolve_conflict(entry, upload=True)
                    return
                if action == gsinput.PRIMARY:
                    # Only losing *this* game's save needs a second question;
                    # other games sharing the card do not block the sync.
                    if own_lost and not self.confirm(
                            "Take the server's card anyway?",
                            ["The server's card has no save for %s."
                             % entry.display[:40],
                             "Your progress in this game will be deleted."],
                            title="Data loss"):
                        self.draw_all()
                        return
                    self._resolve_conflict(entry, upload=False,
                                           allow_data_loss=own_lost)
                    return
                if action in (gsinput.BACK, gsinput.QUIT):
                    self.draw_all()
                    return

    def _resolve_conflict(self, entry, upload: bool,
                          allow_data_loss: bool = False) -> None:
        """Apply the user's choice. The losing side is kept in save history."""
        entry.status = gssync.UPLOAD if upload else gssync.DOWNLOAD
        self.toast("%s %s..." % ("Uploading" if upload else "Downloading",
                                 entry.display))
        try:
            self.engine.sync_entry(entry, progress=lambda t: self.toast(t),
                                   allow_data_loss=allow_data_loss)
            self.notice("%s resolved" % entry.display, hold=1.2)
        except Exception as exc:
            entry.status = gssync.ERROR
            entry.message = str(exc)
            self.notice("Failed: %s" % exc, theme.DANGER)
        self.load_data()
        self.draw_all()

    # --------------------------------------------------------------- search

    def do_search(self) -> None:
        """Filter the catalogue by name, typed on the pad or a keyboard."""
        text = self.prompt_text("Search the catalog", self.search)
        if text is None:
            self.draw_all()
            return
        self.set_search(text)

    def set_search(self, text: str) -> None:
        self.search = " ".join(text.split())
        self.selected = self.scroll = 0
        self._rows_key = None
        self.draw_all()
        if self.search:
            count = len(self.rows())
            self.toast('%d match%s for "%s"'
                       % (count, "" if count == 1 else "es", self.search),
                       hold=1.2)

    #: On-screen keyboard: three rows of characters and one of wide keys.
    KEYBOARD = ["ABCDEFGHIJKLM", "NOPQRSTUVWXYZ", "0123456789-.'"]
    KEYBOARD_WIDE = ["Space", "Delete", "Clear", "OK"]

    def prompt_text(self, title: str, initial: str = ""):
        """A modal text field with an on-screen keyboard. None on cancel.

        A pad moves over the keys and confirms one at a time; a real keyboard
        types straight in. The button that opened the prompt confirms it, so
        a search is "press, type, press again".
        """
        metrics = self.metrics
        pad = metrics.pad
        accent = theme.ACCENT
        text = initial
        grid = [list(row) for row in self.KEYBOARD] + [self.KEYBOARD_WIDE]
        row = col = 0
        labels = self.input.label
        prompt = "%s Delete   %s OK   %s Cancel" % (
            labels(gsinput.SYNC), labels(gsinput.ALT), labels(gsinput.BACK))
        if not metrics.compact:
            # Obvious enough to drop at 240p, where "Cross Type" is what
            # pushes "Circle Cancel" off the edge.
            prompt = "%s Type   " % labels(gsinput.PRIMARY) + prompt

        width = int(metrics.width * 0.84)
        cell_h = self.font_row.line_height + 6
        field_h = self.font_row.line_height + 8
        height = pad // 2 + self.font_small.line_height + 6
        height += field_h + 6 + cell_h * len(grid) + 8
        height += self.font_small.line_height + pad // 2
        left = max(0, (metrics.width - width) // 2)
        top = max(0, (metrics.height - height) // 2)
        inner = width - pad

        def draw():
            self.fb.fill_rect(left - 2, top - 2, width + 4, height + 4, accent)
            self.fb.fill_rect(left, top, width, height, theme.HEADER)
            y = top + pad // 2
            self.text(self.font_small, left + pad, y, title.upper(), accent,
                      theme.HEADER, max_width=width - pad * 2)
            y += self.font_small.line_height + 6
            # The field, with a cursor so an empty one still reads as one.
            self.fb.fill_rect(left + pad // 2, y, inner, field_h,
                              theme.BACKGROUND)
            self.text(self.font_row, left + pad, y + 4, text + "_",
                      theme.TEXT_STRONG, theme.BACKGROUND,
                      max_width=inner - pad)
            y += field_h + 6
            for r, keys in enumerate(grid):
                cell_w = inner // len(keys)
                for c, key in enumerate(keys):
                    x = left + pad // 2 + c * cell_w
                    active = (r, c) == (row, col)
                    background = theme.ROW_SELECTED if active else theme.ROW
                    self.fb.fill_rect(x + 1, y + 1, cell_w - 2, cell_h - 2,
                                      background)
                    key_w = self.font_row.measure(key)
                    self.text(self.font_row, x + max(2, (cell_w - key_w) // 2),
                              y + 3, key,
                              theme.TEXT_STRONG if active else theme.TEXT,
                              background, max_width=cell_w - 4)
                y += cell_h
            y += 8
            prompt_w = self.font_small.measure(prompt)
            self.text(self.font_small, left + (width - prompt_w) // 2, y,
                      prompt, accent, theme.HEADER, max_width=width - pad)

        def press():
            nonlocal text
            key = grid[row][col]
            if key == "OK":
                return True
            if key == "Space":
                text += " "
            elif key == "Delete":
                text = text[:-1]
            elif key == "Clear":
                text = ""
            else:
                text += key
            return False

        self.input.text_keys = True
        try:
            draw()
            while True:
                if self.deadline is not None and time.time() > self.deadline:
                    return None
                for action in self.input.poll(0.05):
                    if action.startswith(gsinput.CHAR_PREFIX):
                        text += action[len(gsinput.CHAR_PREFIX):]
                    elif action == gsinput.TEXT_DELETE or \
                            action == gsinput.SYNC:
                        text = text[:-1]
                    elif action in (gsinput.TEXT_OK, gsinput.ALT,
                                    gsinput.SETTINGS):
                        return text
                    elif action == gsinput.PRIMARY:
                        if press():
                            return text
                    elif action in (gsinput.BACK, gsinput.QUIT):
                        return None
                    elif action == gsinput.LEFT:
                        col = (col - 1) % len(grid[row])
                    elif action == gsinput.RIGHT:
                        col = (col + 1) % len(grid[row])
                    elif action in (gsinput.UP, gsinput.DOWN):
                        step = 1 if action == gsinput.DOWN else -1
                        new_row = (row + step) % len(grid)
                        # Keep the same horizontal position across rows of
                        # different widths, so the wide keys sit under the
                        # letters above them.
                        col = col * len(grid[new_row]) // len(grid[row])
                        row = new_row
                    else:
                        continue
                    draw()
        finally:
            self.input.text_keys = False

    def confirm(self, question: str, detail=None, danger: bool = True,
                title: str = "") -> bool:
        """Block for a yes/no on anything that overwrites or removes data.

        A modal box rather than the one-line toast this used to be: on a 240p
        cabinet a thin strip at the top of the list is easy to miss entirely,
        and the operations behind it - overwriting a save, deleting a game -
        are not undoable.

        Answers No on anything other than an explicit yes, including the
        --timeout deadline expiring, so an unattended run cannot destroy
        anything by falling through.
        """
        detail = list(detail or [])
        metrics = self.metrics
        accent = theme.DANGER if danger else theme.ACCENT
        yes, no = (self.input.label(gsinput.PRIMARY),
                   self.input.label(gsinput.BACK))
        prompt = "%s  Yes      %s  No" % (yes, no)

        width = int(metrics.width * 0.84)
        pad = metrics.pad
        body = [(question, self.font_row, theme.TEXT_STRONG)]
        body += [(line, self.font_small, theme.TEXT_DIM) for line in detail]
        height = pad
        if title:
            height += self.font_small.line_height + 6
        height += sum(font.line_height + 4 for _t, font, _c in body)
        height += 8 + self.font_row.line_height + pad

        left = max(0, (metrics.width - width) // 2)
        top = max(0, (metrics.height - height) // 2)

        self.fb.fill_rect(left - 2, top - 2, width + 4, height + 4, accent)
        self.fb.fill_rect(left, top, width, height, theme.HEADER)

        y = top + pad // 2
        if title:
            self.text(self.font_small, left + pad, y, title.upper(), accent,
                      theme.HEADER, max_width=width - pad * 2)
            y += self.font_small.line_height + 6
        for text, font, colour in body:
            self.text(font, left + pad, y, text, colour, theme.HEADER,
                      max_width=width - pad * 2)
            y += font.line_height + 4

        y += 8
        prompt_w = self.font_row.measure(prompt)
        self.text(self.font_row, left + (width - prompt_w) // 2, y, prompt,
                  accent, theme.HEADER)

        while True:
            if self.deadline is not None and time.time() > self.deadline:
                return False
            for action in self.input.poll(0.05):
                if action == gsinput.PRIMARY:
                    return True
                if action in (gsinput.BACK, gsinput.QUIT):
                    return False

    def choose(self, question: str, options, detail=None, title: str = ""):
        """A modal pick-one list. Returns the chosen index, or None.

        Same box as confirm(), with the options as rows instead of a Yes/No
        line. Up/down move, confirm picks, back cancels; the --timeout
        deadline cancels too, so an unattended run cannot pick anything.
        """
        options = list(options)
        detail = list(detail or [])
        metrics = self.metrics
        accent = theme.ACCENT
        pick, back = (self.input.label(gsinput.PRIMARY),
                      self.input.label(gsinput.BACK))
        prompt = "%s  Select      %s  Cancel" % (pick, back)
        selected = 0

        width = int(metrics.width * 0.84)
        pad = metrics.pad
        body = [(question, self.font_row, theme.TEXT_STRONG)]
        body += [(line, self.font_small, theme.TEXT_DIM) for line in detail]
        option_h = self.font_row.line_height + 6
        height = pad
        if title:
            height += self.font_small.line_height + 6
        height += sum(font.line_height + 4 for _t, font, _c in body)
        height += 6 + option_h * len(options)
        height += 8 + self.font_row.line_height + pad
        left = max(0, (metrics.width - width) // 2)
        top = max(0, (metrics.height - height) // 2)

        def draw():
            self.fb.fill_rect(left - 2, top - 2, width + 4, height + 4, accent)
            self.fb.fill_rect(left, top, width, height, theme.HEADER)
            y = top + pad // 2
            if title:
                self.text(self.font_small, left + pad, y, title.upper(),
                          accent, theme.HEADER, max_width=width - pad * 2)
                y += self.font_small.line_height + 6
            for text, font, colour in body:
                self.text(font, left + pad, y, text, colour, theme.HEADER,
                          max_width=width - pad * 2)
                y += font.line_height + 4
            y += 6
            for index, option in enumerate(options):
                active = index == selected
                background = theme.ROW_SELECTED if active else theme.HEADER
                self.fb.fill_rect(left + pad // 2, y, width - pad, option_h,
                                  background)
                if active:
                    self.fb.fill_rect(left + pad // 2, y, 3, option_h, accent)
                self.text(self.font_row, left + pad, y + 3, option,
                          theme.TEXT_STRONG if active else theme.TEXT,
                          background, max_width=width - pad * 2)
                y += option_h
            y += 8
            prompt_w = self.font_row.measure(prompt)
            self.text(self.font_row, left + (width - prompt_w) // 2, y,
                      prompt, accent, theme.HEADER)

        draw()
        while True:
            if self.deadline is not None and time.time() > self.deadline:
                return None
            for action in self.input.poll(0.05):
                if action == gsinput.DOWN:
                    selected = (selected + 1) % len(options)
                    draw()
                elif action == gsinput.UP:
                    selected = (selected - 1) % len(options)
                    draw()
                elif action == gsinput.PRIMARY:
                    return selected
                elif action in (gsinput.BACK, gsinput.QUIT):
                    return None

    def do_delete_installed(self) -> None:
        item = self.selected_install()
        if item is None:
            return
        what = "folder" if item.is_folder else "file"
        detail = ["%s %s on %s" % (item.system, what, item.where),
                  item.path,
                  "This cannot be undone."]
        # The core named its save after this game. Offer to take the save
        # too: a leftover card for a variant you no longer keep is what makes
        # the remaining copy fight it over one server slot.
        saves = installed_game_save_paths(self.engine.provider, item.system,
                                          item.name)
        if saves:
            detail.append("Save file: %s" % ", ".join(
                os.path.basename(path) for path in saves))
            choice = self.choose(
                "Delete %s?" % item.name,
                ["Delete game only", "Delete game and its save"],
                detail, title="Delete game")
            if choice is None:
                self.draw_all()
                return
            delete_saves = choice == 1
        else:
            if not self.confirm("Delete %s?" % item.name, detail,
                                title="Delete game"):
                self.draw_all()
                return
            delete_saves = False

        self.toast("Deleting %s..." % item.name)
        try:
            if item.is_folder:
                _remove_tree(item.path)
            else:
                os.remove(item.path)
            if delete_saves:
                for path in saves:
                    os.remove(path)
        except OSError as exc:
            self.notice("Delete failed: %s" % exc, theme.DANGER)
        else:
            self.notice("Deleted %s%s"
                        % (item.name, " and its save" if delete_saves else ""),
                        hold=1.0)
        self.selected = max(0, self.selected - 1)
        self.load_data()
        self.draw_all()

    def do_move_installed(self) -> None:
        """Move a game between the SD card and USB."""
        item = self.selected_install()
        if item is None:
            return

        destination_dir = gsinstall.system_games_dir(
            self.engine.provider, item.system, item.target)
        if not destination_dir:
            self.draw_all()
            self.toast("No MiSTer folder for %s" % item.system, theme.DANGER,
                       hold=2.0)
            return
        destination = os.path.join(destination_dir,
                                   os.path.basename(item.path))
        if os.path.exists(destination):
            self.draw_all()
            self.toast("Already present on %s" % item.target.upper(),
                       theme.DANGER, hold=2.0)
            return
        detail = ["From %s to %s" % (item.where, item.target.upper()),
                  destination]
        if not os.path.isdir(destination_dir):
            # Worth saying up front: a new USB core folder makes the core stop
            # looking at the SD card, which is why the BIOS gets copied too.
            detail.append("Creates the %s folder on %s and seeds its BIOS."
                          % (item.folder, item.target.upper()))
        if not self.confirm("Move %s?" % item.name, detail, danger=False,
                            title="Move game"):
            self.draw_all()
            return

        self.toast("Moving %s to %s..." % (item.name, item.target.upper()))
        try:
            # Creating a USB core folder makes the core ignore the SD card, so
            # its BIOS has to come along or a CD core stops booting.
            created = not os.path.isdir(destination_dir)
            os.makedirs(destination_dir, exist_ok=True)
            if created:
                self._seed_bios(item, destination_dir)
            _move_path(item.path, destination)
        except OSError as exc:
            self.notice("Move failed: %s" % exc, theme.DANGER)
        else:
            self.notice("Moved to %s" % destination, hold=1.4)
        self.load_data()
        self.draw_all()

    def _seed_bios(self, item, destination_dir):
        for source in gsinstall.bios_seed_sources(
                self.engine.provider, item.system, item.target):
            target = os.path.join(destination_dir,
                                  os.path.basename(source))
            if os.path.exists(target):
                continue
            try:
                with open(source, "rb") as src, open(target, "wb") as dst:
                    dst.write(src.read())
            except OSError:
                pass

    def do_delete_save(self) -> None:
        """X on Saves: remove the highlighted save file from this MiSTer.

        The way out when leftover cards from earlier variants of a game
        (``Snatcher (Japan).sav``, ``... [T-En v0.4].sav``) keep claiming
        the server slot, so the copy for the variant actually installed
        can never be downloaded. Nothing on the server is touched.
        """
        if self.tab != 0:
            return
        rows = self.rows()
        if not rows or self.selected >= len(rows):
            return
        entry = rows[self.selected].ref
        if entry is None or not entry.path or not os.path.exists(entry.path):
            self.draw_all()
            self.toast("Nothing on this MiSTer to delete for %s"
                       % (entry.display if entry else "this row"), hold=1.5)
            return

        detail = [entry.path, "This cannot be undone."]
        # Say plainly when the server does not hold what is about to go.
        if entry.status in (gssync.UPLOAD, gssync.CONFLICT):
            detail.insert(1, "This save has progress the server does not.")
        elif entry.status == gssync.LOCAL:
            detail.insert(1, "Not compared with the server yet.")
        elif entry.status == gssync.SYNCED:
            detail.insert(1, "The server holds an identical copy.")
        elif entry.status == gssync.DOWNLOAD:
            detail.insert(1, "The server holds a newer copy.")
        if not self.confirm("Delete the save for %s?" % entry.display,
                            detail, title="Delete save"):
            self.draw_all()
            return

        try:
            os.remove(entry.path)
        except OSError as exc:
            self.draw_all()
            self.toast("Delete failed: %s" % exc, theme.DANGER, hold=2.5)
            return
        if entry in self.engine.entries:
            self.engine.entries.remove(entry)
        self.notice("Deleted %s" % os.path.basename(entry.path), hold=1.0)
        # The slot may now be server-only, or belong to a sibling card:
        # let the scan and the plan say so rather than guessing. Stay
        # where the cursor was - the next leftover is usually right there.
        keep = (self.selected, self.scroll)
        self.do_rescan()
        self.selected, self.scroll = keep
        self._clamp_cursor()
        self.draw_all()

    def do_sync_selected(self) -> None:
        """A: sync just the highlighted save."""
        if self.tab != 0 or not self.require_client():
            return
        rows = self.rows()
        if not rows or self.selected >= len(rows):
            return
        row = rows[self.selected]
        entry = row.ref
        if entry is None or entry not in self.engine.entries:
            return
        approval = self.offer_install_for_save(entry)
        if not approval:
            return
        if entry.status == gssync.CONFLICT:
            self.show_conflict(entry)
            return
        if entry.status not in (gssync.UPLOAD, gssync.DOWNLOAD,
                                gssync.SERVER_ONLY):
            self.draw_all()
            self.toast("%s is already up to date" % entry.display, hold=1.2)
            return

        # Name which side loses its copy. "Sync" is ambiguous in exactly the
        # situation where being wrong costs you a save file.
        if entry.status == gssync.DOWNLOAD:
            question = "Download %s from the server?" % entry.display
            detail = ["The save on this MiSTer will be overwritten.",
                      "Local %s, server %s"
                      % (_human_size(entry.size),
                         _human_size(int((entry.server or {}).get(
                             "server_size")
                             or (entry.server or {}).get("save_size") or 0)))]
            danger = True
        elif entry.status == gssync.UPLOAD:
            question = "Upload %s to the server?" % entry.display
            detail = ["The server's copy will be replaced."]
            danger = True
        else:
            question = "Download %s?" % entry.display
            # The row says nothing is here, but the file it would be written
            # to may exist under another title id (a card the scan keyed by
            # a different serial, or a save made since the last scan).
            target = self.engine.download_target(entry)
            if target and os.path.exists(target):
                detail = ["A file already exists at the destination:",
                          os.path.basename(target),
                          "It will be overwritten."]
                danger = True
            else:
                detail = ["Not on this MiSTer yet - nothing is overwritten."]
                danger = False
        if entry.message:
            # Where the two copies disagree, when the engine compared them.
            detail.append(entry.message[0].upper() + entry.message[1:])
        # "Install the game and sync the save" was the decision; only a
        # destination that would lose something is worth a second question.
        if (approval != "now" or danger) and not self.confirm(
                question, detail, danger=danger, title="Sync"):
            self.draw_all()
            return

        try:
            self.engine.sync_entry(entry, progress=lambda t: self.toast(t))
            message = "%s: %s" % (entry.display, entry.message or "done")
        except Exception as exc:
            entry.status = gssync.ERROR
            message = "%s failed: %s" % (entry.display, exc)
        self.load_data()
        self.draw_all()
        self.toast(message, hold=1.5)

    def draw_banner(self, message: str) -> None:
        metrics = self.metrics
        height = self.font_row.line_height + int(metrics.pad * 0.8)
        top = metrics.list_top
        self.fb.fill_rect(0, top, metrics.width, height, theme.DANGER)
        width = self.font_row.measure(message)
        self.text(self.font_row, (metrics.width - width) // 2,
                  top + (height - self.font_row.line_height) // 2,
                  message, theme.TEXT_STRONG, theme.DANGER)

    def close(self) -> None:
        if self.input is not None:
            self.input.close()
            self.input = None
        self.fb.close()


def _timestamp(value) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(value)))
    except (TypeError, ValueError):
        return ""


def _server_time(server: dict) -> str:
    """The server's timestamp, however this endpoint happened to spell it."""
    for key in ("server_timestamp", "last_sync", "timestamp"):
        raw = server.get(key)
        if not raw:
            continue
        text = str(raw)
        if text.isdigit():
            return _timestamp(int(text))
        # ISO-8601 from the server; second precision is plenty here.
        return text.replace("T", " ")[:16]
    for key in ("client_timestamp", "server_time"):
        if server.get(key):
            return _timestamp(server[key])
    return ""


def _remove_tree(path: str) -> None:
    """Delete a directory and everything under it."""
    for base, dirs, files in os.walk(path, topdown=False):
        for name in files:
            os.remove(os.path.join(base, name))
        for name in dirs:
            os.rmdir(os.path.join(base, name))
    os.rmdir(path)


def _move_path(source: str, destination: str) -> None:
    """Move a file or folder, across filesystems if need be.

    The SD card and USB are separate mounts, so os.rename cannot cross between
    them; fall back to a streaming copy rather than loading a multi-gigabyte
    CHD into 492 MB of RAM.
    """
    try:
        os.rename(source, destination)
        return
    except OSError:
        pass

    if os.path.isdir(source):
        for base, dirs, files in os.walk(source):
            relative = os.path.relpath(base, source)
            target_dir = (destination if relative == "."
                          else os.path.join(destination, relative))
            os.makedirs(target_dir, exist_ok=True)
            for name in files:
                _copy_file(os.path.join(base, name),
                           os.path.join(target_dir, name))
        _remove_tree(source)
    else:
        _copy_file(source, destination)
        os.remove(source)


def _copy_file(source: str, destination: str) -> None:
    with open(source, "rb") as src, open(destination, "wb") as dst:
        while True:
            chunk = src.read(256 * 1024)
            if not chunk:
                break
            dst.write(chunk)
        dst.flush()
        try:
            os.fsync(dst.fileno())
        except OSError:
            pass


def _first_letter(name: str) -> str:
    """Uppercase first alphabetic character of *name*, "" if none."""
    for char in str(name or ""):
        if char.isalpha():
            return char.upper()
    return ""


def _search_rows(rows, search: str):
    """Rows whose name has every word of ``search``, in any order.

    A catalogue row's file names count too: a translation patch is listed
    under the original title ("Famicom Tantei Club …") but the file - the
    name you know it by - says "Famicom Detective Club".
    """
    words = search.lower().split()
    if not words:
        return rows
    return [row for row in rows
            if all(word in _search_text(row) for word in words)]


def _search_text(row) -> str:
    text = row.name.lower()
    group = row.ref
    if group is not None and hasattr(group, "rows"):
        text += " " + " ".join(
            str(rom.get("filename") or "").lower() for rom in group.rows)
    return text


def _listdir(path: str) -> list[str]:
    try:
        return os.listdir(path)
    except OSError:
        return []


_NORMALIZE_CACHE: dict = {}


def _group_has_achievements(group) -> bool:
    """True when any ROM in this catalogue group has a published RA set.

    A multi-disc game is one group; RA registers the discs separately, so
    the badge belongs to the group as soon as one of them earns something.
    ``ra_achievements`` is -1 when the server had no API key and could not
    read the count, which is not a promise of anything and does not badge.
    """
    for row in getattr(group, "rows", None) or ():
        try:
            if int(row.get("ra_achievements") or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _normalize(name: str) -> str:
    """Loose key for "is this catalogue row already installed?".

    Memoised: a full catalogue is tens of thousands of rows and this runs a
    regex chain over every one of them on each refresh.
    """
    key = str(name or "")
    cached = _NORMALIZE_CACHE.get(key)
    if cached is None:
        from shared.rom_id import normalize_rom_name

        cached = normalize_rom_name(key)
        if len(_NORMALIZE_CACHE) > 40000:
            _NORMALIZE_CACHE.clear()
        _NORMALIZE_CACHE[key] = cached
    return cached


def _save_detail(entry) -> str:
    """The detail column for a save row: what differs, else how big it is.

    For a save the server disagrees with, *where* it differs is worth more
    than its size - it is what tells a PlayStation card the core rewrote
    ("block 0 write-test frame") apart from one the game saved to.
    """
    if entry.message and entry.status in (gssync.UPLOAD, gssync.DOWNLOAD,
                                          gssync.CONFLICT, gssync.ERROR):
        message = entry.message
        for prefix in ("differs from server: ",):
            if message.startswith(prefix):
                message = message[len(prefix):]
        return message
    return _human_size(entry.size)


def _progress_text(item) -> str:
    if item.status == gsdownloads.DONE:
        return _human_size(item.size)
    if item.status == gsdownloads.FAILED:
        return (item.error or "failed")[:40]
    if item.size:
        return "%d%%  %s" % (int(item.progress * 100), _human_size(item.size))
    return _human_size(item.received)


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return "%d B" % size
            return "%.0f %s" % (size, unit)
        size /= 1024.0
    return "%d B" % size
