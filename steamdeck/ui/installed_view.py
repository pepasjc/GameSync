"""Installed Games tab body.

Lists every ROM the scanner finds under ``<emulation>/roms`` /
``<rom_scan_dir>``, grouped by system, with a delete action so the user
can free up disk space without dropping to the shell.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QRect,
    QSize,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListView,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from scanner.installed_roms import InstalledRom
from shared.systems import DEFAULT_SYSTEM_COLOR, SYSTEM_COLOR

from . import theme


_RomRole = Qt.ItemDataRole.UserRole + 1


class InstalledModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._roms: list[InstalledRom] = []

    def set_roms(self, roms: list[InstalledRom]) -> None:
        self.beginResetModel()
        self._roms = list(roms)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._roms)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._roms)):
            return None
        rom = self._roms[index.row()]
        if role == _RomRole:
            return rom
        if role == Qt.ItemDataRole.DisplayRole:
            return rom.display_name
        return None

    def rom_at(self, row: int) -> Optional[InstalledRom]:
        if 0 <= row < len(self._roms):
            return self._roms[row]
        return None


class InstalledDelegate(QStyledItemDelegate):
    SYSTEM_BADGE_W = 52
    SYSTEM_BADGE_H = 22
    H_PAD = 14
    V_PAD = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self._name_font = QFont()
        self._name_font.setPointSize(theme.FONT_TITLE)
        self._name_font.setBold(True)

        self._sub_font = QFont()
        self._sub_font.setPointSize(theme.FONT_SUBTITLE)

        self._badge_font = QFont()
        self._badge_font.setPointSize(theme.FONT_BADGE)
        self._badge_font.setBold(True)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(option.rect.width(), theme.CARD_H)

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        rom: Optional[InstalledRom] = index.data(_RomRole)
        if rom is None:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect
        is_selected = option.state & option.state.State_Selected

        card_color = QColor(theme.BG_CARD_SEL if is_selected else theme.BG_CARD)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(card_color))
        card_rect = rect.adjusted(4, 3, -4, -3)
        painter.drawRoundedRect(card_rect, theme.CARD_RADIUS, theme.CARD_RADIUS)

        if is_selected:
            painter.setBrush(QBrush(QColor(theme.ACCENT)))
            painter.drawRoundedRect(
                QRect(card_rect.left(), card_rect.top(), 4, card_rect.height()),
                2,
                2,
            )

        system = (rom.system or "?").upper()
        sys_color = QColor(SYSTEM_COLOR.get(system, DEFAULT_SYSTEM_COLOR))
        badge_x = card_rect.left() + self.H_PAD
        badge_y = card_rect.top() + (card_rect.height() - self.SYSTEM_BADGE_H) // 2
        sys_badge = QRect(
            badge_x, badge_y, self.SYSTEM_BADGE_W, self.SYSTEM_BADGE_H
        )
        painter.setBrush(QBrush(sys_color))
        painter.drawRoundedRect(sys_badge, theme.BADGE_RADIUS, theme.BADGE_RADIUS)
        painter.setFont(self._badge_font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(sys_badge, Qt.AlignmentFlag.AlignCenter, system)

        size_text = _fmt_size(rom.size)
        fm_badge = QFontMetrics(self._badge_font)
        size_w = fm_badge.horizontalAdvance(size_text) + 16 if size_text else 0
        size_x = card_rect.right() - self.H_PAD - size_w if size_w else card_rect.right()
        if size_w:
            size_rect = QRect(
                size_x,
                card_rect.top() + (card_rect.height() - self.SYSTEM_BADGE_H) // 2,
                size_w,
                self.SYSTEM_BADGE_H,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(theme.TEXT_DIM)))
            painter.drawRoundedRect(size_rect, theme.BADGE_RADIUS, theme.BADGE_RADIUS)
            painter.setFont(self._badge_font)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(size_rect, Qt.AlignmentFlag.AlignCenter, size_text)

        text_x = badge_x + self.SYSTEM_BADGE_W + 12
        text_right = (size_x - 12) if size_w else (card_rect.right() - self.H_PAD)
        text_w = max(0, text_right - text_x)
        name_h = card_rect.height() // 2

        painter.setFont(self._name_font)
        painter.setPen(QColor(theme.TEXT_PRIMARY))
        fm_name = QFontMetrics(self._name_font)
        name_rect = QRect(text_x, card_rect.top() + self.V_PAD - 2, text_w, name_h)
        painter.drawText(
            name_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            fm_name.elidedText(rom.display_name, Qt.TextElideMode.ElideRight, text_w),
        )

        sub_parts = [rom.filename]
        if rom.companion_files:
            sub_parts.append(f"+{len(rom.companion_files)} file(s)")
        sub_text = "  ·  ".join(sub_parts)
        sub_rect = QRect(
            text_x, card_rect.top() + name_h, text_w, name_h - self.V_PAD + 2
        )
        painter.setFont(self._sub_font)
        painter.setPen(QColor(theme.TEXT_SECONDARY))
        fm_sub = QFontMetrics(self._sub_font)
        painter.drawText(
            sub_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            fm_sub.elidedText(sub_text, Qt.TextElideMode.ElideRight, text_w),
        )

        painter.restore()


class InstalledListView(QListView):
    rom_activated = pyqtSignal(object)  # InstalledRom

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = InstalledModel(self)
        self._delegate = InstalledDelegate(self)
        self.setModel(self._model)
        self.setItemDelegate(self._delegate)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.doubleClicked.connect(self._on_double_click)

    def set_roms(self, roms: list[InstalledRom]) -> None:
        prev_key = self._selected_key()
        self._model.set_roms(roms)
        if not roms:
            return
        target_row = 0
        if prev_key is not None:
            for row, rom in enumerate(roms):
                if (str(rom.path), rom.system) == prev_key:
                    target_row = row
                    break
        idx = self._model.index(target_row, 0)
        self.setCurrentIndex(idx)
        self.scrollTo(idx, QAbstractItemView.ScrollHint.EnsureVisible)

    def selected_rom(self) -> Optional[InstalledRom]:
        idx = self.currentIndex()
        return self._model.rom_at(idx.row()) if idx.isValid() else None

    def move_selection(
        self,
        delta: int,
        hint: QAbstractItemView.ScrollHint = QAbstractItemView.ScrollHint.EnsureVisible,
    ) -> None:
        cur = self.currentIndex()
        row = cur.row() if cur.isValid() else -1
        new_row = max(0, min(self._model.rowCount() - 1, row + delta))
        new_idx = self._model.index(new_row, 0)
        self.setCurrentIndex(new_idx)
        self.scrollTo(new_idx, hint)

    def _viewport_rows(self) -> int:
        row_h = max(theme.CARD_H, 1)
        return max(1, self.viewport().height() // row_h)

    def page_up(self) -> None:
        self.move_selection(-self._viewport_rows(),
                            QAbstractItemView.ScrollHint.PositionAtTop)

    def page_down(self) -> None:
        self.move_selection(self._viewport_rows(),
                            QAbstractItemView.ScrollHint.PositionAtBottom)

    def row_count(self) -> int:
        return self._model.rowCount()

    def _selected_key(self) -> Optional[tuple]:
        rom = self.selected_rom()
        return (str(rom.path), rom.system) if rom else None

    def _on_double_click(self, index: QModelIndex) -> None:
        rom = self._model.rom_at(index.row())
        if rom is not None:
            self.rom_activated.emit(rom)


class InstalledView(QWidget):
    """Installed Games tab body — list + delete request signal."""

    delete_requested = pyqtSignal(object)  # InstalledRom
    status_changed = pyqtSignal(str)
    systems_changed = pyqtSignal(list)

    ALL_SYSTEMS = "All Systems"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_roms: list[InstalledRom] = []
        self._search_text = ""
        self._system_filter = self.ALL_SYSTEMS
        self._loaded = False
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._empty_label = QLabel("Scanning installed ROMs…")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:13pt; padding:32px;"
        )

        self._list = InstalledListView(self)
        self._list.rom_activated.connect(self.delete_requested.emit)

        layout.addWidget(self._empty_label)
        layout.addWidget(self._list, 1)
        self._empty_label.hide()

    # ── Data plumbing ────────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def is_loading(self) -> bool:
        return self._loading

    def mark_loading(self, loading: bool) -> None:
        self._loading = loading
        if loading:
            self._empty_label.setText("Scanning installed ROMs…")
            self._empty_label.show()
            self._list.hide()

    def set_roms(self, roms: list[InstalledRom]) -> None:
        self._all_roms = list(roms)
        self._loaded = True
        self._loading = False
        systems = sorted({r.system for r in self._all_roms if r.system})
        self.systems_changed.emit(systems)
        self._apply_filters()

    def set_system_filter(self, system: str) -> None:
        if system == self._system_filter:
            return
        self._system_filter = system
        self._apply_filters()

    def system_filter(self) -> str:
        return self._system_filter

    def set_search_text(self, text: str) -> None:
        text = (text or "").strip()
        if text == self._search_text:
            return
        self._search_text = text
        self._apply_filters()

    def search_text(self) -> str:
        return self._search_text

    def cycle_system(self, delta: int, systems: list[str]) -> None:
        if not systems:
            return
        try:
            idx = systems.index(self._system_filter)
        except ValueError:
            idx = 0
        idx = (idx + delta) % len(systems)
        self.set_system_filter(systems[idx])

    # ── Selection / navigation delegates ────────────────────────

    def selected_rom(self) -> Optional[InstalledRom]:
        return self._list.selected_rom()

    def move_selection(self, delta: int) -> None:
        self._list.move_selection(delta)

    def page_up(self) -> None:
        self._list.page_up()

    def page_down(self) -> None:
        self._list.page_down()

    def visible_count(self) -> int:
        return self._list.row_count()

    # ── Internal ─────────────────────────────────────────────────

    def _apply_filters(self) -> None:
        system = (
            None if self._system_filter == self.ALL_SYSTEMS else self._system_filter
        )
        query = self._search_text.lower()
        filtered: list[InstalledRom] = []
        for rom in self._all_roms:
            if system and rom.system.upper() != system.upper():
                continue
            if query:
                haystack = (
                    f"{rom.display_name} {rom.filename} {rom.system}".lower()
                )
                if query not in haystack:
                    continue
            filtered.append(rom)
        self._list.set_roms(filtered)
        self._refresh_empty_state(count=len(filtered))
        self.status_changed.emit(self._status_text(len(filtered)))

    def _refresh_empty_state(self, count: Optional[int] = None) -> None:
        if self._loading and not self._all_roms:
            return
        if not self._loaded:
            return
        if count is None:
            count = self._list.row_count()
        if count == 0:
            if not self._all_roms:
                self._empty_label.setText(
                    "No installed ROMs found.\n"
                    "Download ROMs from the Catalog tab or point the "
                    "Emulation path / ROM scan dir at your library in Settings."
                )
            else:
                self._empty_label.setText("No ROMs match this search.")
            self._empty_label.show()
            self._list.hide()
        else:
            self._empty_label.hide()
            self._list.show()

    def _status_text(self, count: int) -> str:
        if not self._loaded:
            return ""
        total = len(self._all_roms)
        total_size = sum(r.size for r in self._all_roms)
        total_txt = _fmt_size(total_size)
        if count == total:
            return f"{total} ROMs · {total_txt}" if total_txt else f"{total} ROMs"
        return f"{count} / {total} ROMs"


def _fmt_size(num_bytes: int) -> str:
    if num_bytes <= 0:
        return ""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.0f} KB"
    if num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"
