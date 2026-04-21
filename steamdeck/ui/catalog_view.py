"""Server ROM catalog browser tab.

Runs alongside the existing save-sync list.  Presents the full
``GET /api/v1/roms`` catalog with a smart tokenised search (roman numerals,
region-tag stripping, fuzzy word order) and a per-system filter, and
downloads each selected ROM straight into the matching
``~/Emulation/roms/<system>/`` folder via ``resolve_rom_target_dir``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

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

from scanner.catalog_search import filter_catalog, unique_systems
from shared.systems import DEFAULT_SYSTEM_COLOR, SYSTEM_COLOR

from . import theme


_RomRole = Qt.ItemDataRole.UserRole + 1


class CatalogModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._roms: list[dict] = []

    def set_roms(self, roms: list[dict]) -> None:
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
            return rom.get("name") or rom.get("filename") or rom.get("title_id") or ""
        return None

    def rom_at(self, row: int) -> Optional[dict]:
        if 0 <= row < len(self._roms):
            return self._roms[row]
        return None


class CatalogDelegate(QStyledItemDelegate):
    """Mirrors ``GameDelegate`` visually so the two tabs feel consistent."""

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
        rom = index.data(_RomRole)
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

        system = (rom.get("system") or "?").upper()
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

        # Size badge on the right (only if the server advertised one).
        size_text = _fmt_size(int(rom.get("size") or 0))
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
            painter.setBrush(QBrush(QColor(theme.STATUS_DOWNLOAD)))
            painter.drawRoundedRect(size_rect, theme.BADGE_RADIUS, theme.BADGE_RADIUS)
            painter.setFont(self._badge_font)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(size_rect, Qt.AlignmentFlag.AlignCenter, size_text)

        text_x = badge_x + self.SYSTEM_BADGE_W + 12
        text_right = (size_x - 12) if size_w else (card_rect.right() - self.H_PAD)
        text_w = max(0, text_right - text_x)
        name_h = card_rect.height() // 2

        name = rom.get("name") or rom.get("filename") or rom.get("title_id") or ""
        painter.setFont(self._name_font)
        painter.setPen(QColor(theme.TEXT_PRIMARY))
        fm_name = QFontMetrics(self._name_font)
        name_rect = QRect(text_x, card_rect.top() + self.V_PAD - 2, text_w, name_h)
        painter.drawText(
            name_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            fm_name.elidedText(name, Qt.TextElideMode.ElideRight, text_w),
        )

        sub_parts: list[str] = []
        filename = rom.get("filename")
        if filename and filename != name:
            sub_parts.append(filename)
        title_id = rom.get("title_id")
        if title_id:
            sub_parts.append(title_id)
        sub_text = "  ·  ".join(sub_parts)
        sub_rect = QRect(
            text_x,
            card_rect.top() + name_h,
            text_w,
            name_h - self.V_PAD + 2,
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


class RomListView(QListView):
    rom_activated = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = CatalogModel(self)
        self._delegate = CatalogDelegate(self)
        self.setModel(self._model)
        self.setItemDelegate(self._delegate)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.doubleClicked.connect(self._on_double_click)

    def set_roms(self, roms: list[dict]) -> None:
        prev_key = self._selected_key()
        self._model.set_roms(roms)
        if not roms:
            return
        target_row = 0
        if prev_key is not None:
            for row, rom in enumerate(roms):
                if _rom_key(rom) == prev_key:
                    target_row = row
                    break
        idx = self._model.index(target_row, 0)
        self.setCurrentIndex(idx)
        self.scrollTo(idx, QAbstractItemView.ScrollHint.EnsureVisible)

    def selected_rom(self) -> Optional[dict]:
        idx = self.currentIndex()
        return self._model.rom_at(idx.row()) if idx.isValid() else None

    def move_selection(self, delta: int) -> None:
        cur = self.currentIndex()
        row = cur.row() if cur.isValid() else -1
        new_row = max(0, min(self._model.rowCount() - 1, row + delta))
        new_idx = self._model.index(new_row, 0)
        self.setCurrentIndex(new_idx)
        self.scrollTo(new_idx, QAbstractItemView.ScrollHint.EnsureVisible)

    def page_up(self) -> None:
        self.move_selection(-8)

    def page_down(self) -> None:
        self.move_selection(8)

    def row_count(self) -> int:
        return self._model.rowCount()

    def _selected_key(self) -> Optional[tuple]:
        rom = self.selected_rom()
        return _rom_key(rom) if rom else None

    def _on_double_click(self, index: QModelIndex) -> None:
        rom = self._model.rom_at(index.row())
        if rom is not None:
            self.rom_activated.emit(rom)


class CatalogView(QWidget):
    """The ROM-catalog tab body.  Owns the list + the full/filtered data."""

    download_requested = pyqtSignal(dict)
    status_changed = pyqtSignal(str)
    systems_changed = pyqtSignal(list)  # list[str] of system codes (excluding ALL)

    ALL_SYSTEMS = "All Systems"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_roms: list[dict] = []
        self._search_text = ""
        self._system_filter = self.ALL_SYSTEMS
        self._loaded = False
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._empty_label = QLabel("Loading catalog…")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:13pt; padding:32px;"
        )

        self._list = RomListView(self)
        self._list.rom_activated.connect(self.download_requested.emit)

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
            self._empty_label.setText("Loading catalog…")
            self._empty_label.show()
            self._list.hide()
        self._refresh_empty_state()

    def set_catalog(self, roms: list[dict]) -> None:
        self._all_roms = list(roms)
        self._loaded = True
        self._loading = False
        self.systems_changed.emit(unique_systems(self._all_roms))
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

    def selected_rom(self) -> Optional[dict]:
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
        system = None if self._system_filter == self.ALL_SYSTEMS else self._system_filter
        filtered = filter_catalog(self._all_roms, self._search_text, system)
        self._list.set_roms(filtered)
        self._refresh_empty_state(count=len(filtered))
        self.status_changed.emit(self._status_text(len(filtered)))

    def _refresh_empty_state(self, count: Optional[int] = None) -> None:
        if self._loading and not self._all_roms:
            return
        if not self._loaded:
            self._empty_label.setText("Loading catalog…")
            self._empty_label.show()
            self._list.hide()
            return
        if count is None:
            count = self._list.row_count()
        if count == 0:
            if not self._all_roms:
                self._empty_label.setText(
                    "The server's ROM catalog is empty.\n"
                    "Add ROMs via the server and press Y to refresh."
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
        if count == total:
            return f"{total} ROMs"
        return f"{count} / {total} ROMs"


# ── Helpers ──────────────────────────────────────────────────────


def _rom_key(rom: Optional[dict]) -> Optional[tuple]:
    if not rom:
        return None
    return (
        (rom.get("system") or "").upper(),
        rom.get("rom_id") or "",
        rom.get("filename") or "",
    )


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
