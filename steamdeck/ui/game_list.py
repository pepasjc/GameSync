"""
Custom QListView + delegate for the game list.

Each row shows:
  [SYSTEM]  Game Name                          [STATUS]
            Emulator · Save size
"""

from PyQt6.QtWidgets import QListView, QAbstractItemView, QStyledItemDelegate, QStyleOptionViewItem
from PyQt6.QtCore import (
    Qt, QAbstractListModel, QModelIndex, QSize, QRect, QPoint, pyqtSignal
)
from PyQt6.QtGui import QPainter, QColor, QFont, QFontMetrics, QPen, QBrush

from scanner.models import GameEntry, SyncStatus, STATUS_LABEL, STATUS_COLOR, SYSTEM_COLOR, DEFAULT_SYSTEM_COLOR
from . import theme


# Roles
EntryRole = Qt.ItemDataRole.UserRole + 1


def _find_selection_row(entries: list[GameEntry], selected_title_id: str | None) -> int:
    """Return the row that should stay selected after a list refresh."""
    if not entries:
        return -1
    if selected_title_id is not None:
        for row, entry in enumerate(entries):
            if entry.title_id == selected_title_id:
                return row
    return 0


class GameListModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[GameEntry] = []

    def set_entries(self, entries: list[GameEntry]) -> None:
        self.beginResetModel()
        self._entries = entries
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._entries)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._entries)):
            return None
        entry = self._entries[index.row()]
        if role == EntryRole:
            return entry
        if role == Qt.ItemDataRole.DisplayRole:
            return entry.display_name
        return None

    def entry_at(self, row: int) -> GameEntry | None:
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None


class GameDelegate(QStyledItemDelegate):
    """Custom painter for each game row."""

    SYSTEM_BADGE_W = 52
    SYSTEM_BADGE_H = 22
    STATUS_BADGE_H = 22
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

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        entry: GameEntry | None = index.data(EntryRole)
        if entry is None:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect
        is_selected = option.state & option.state.State_Selected

        # ── Card background ───────────────────────────────────────
        card_color = QColor(theme.BG_CARD_SEL if is_selected else theme.BG_CARD)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(card_color))
        card_rect = rect.adjusted(4, 3, -4, -3)
        painter.drawRoundedRect(card_rect, theme.CARD_RADIUS, theme.CARD_RADIUS)

        if is_selected:
            # Accent left border
            painter.setBrush(QBrush(QColor(theme.ACCENT)))
            painter.drawRoundedRect(
                QRect(card_rect.left(), card_rect.top(), 4, card_rect.height()),
                2, 2,
            )

        # ── System badge ─────────────────────────────────────────
        sys_color = QColor(SYSTEM_COLOR.get(entry.system, DEFAULT_SYSTEM_COLOR))
        badge_x = card_rect.left() + self.H_PAD
        badge_y = card_rect.top() + (card_rect.height() - self.SYSTEM_BADGE_H) // 2
        sys_badge = QRect(badge_x, badge_y, self.SYSTEM_BADGE_W, self.SYSTEM_BADGE_H)
        painter.setBrush(QBrush(sys_color))
        painter.drawRoundedRect(sys_badge, theme.BADGE_RADIUS, theme.BADGE_RADIUS)

        painter.setFont(self._badge_font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(sys_badge, Qt.AlignmentFlag.AlignCenter, entry.system)

        # ── Status badge ─────────────────────────────────────────
        status_text = STATUS_LABEL.get(entry.status, "?")
        status_color = QColor(STATUS_COLOR.get(entry.status, theme.STATUS_UNKNOWN))
        fm_badge = QFontMetrics(self._badge_font)
        status_w = fm_badge.horizontalAdvance(status_text) + 16
        status_x = card_rect.right() - self.H_PAD - status_w
        status_y = card_rect.top() + (card_rect.height() - self.STATUS_BADGE_H) // 2
        status_badge = QRect(status_x, status_y, status_w, self.STATUS_BADGE_H)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(status_color))
        painter.drawRoundedRect(status_badge, theme.BADGE_RADIUS, theme.BADGE_RADIUS)

        painter.setFont(self._badge_font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(status_badge, Qt.AlignmentFlag.AlignCenter, status_text)

        # ── Game name ────────────────────────────────────────────
        text_x = badge_x + self.SYSTEM_BADGE_W + 12
        text_right = status_x - 12
        text_w = text_right - text_x
        name_h = card_rect.height() // 2
        name_rect = QRect(text_x, card_rect.top() + self.V_PAD - 2, text_w, name_h)

        painter.setFont(self._name_font)
        painter.setPen(QColor(theme.TEXT_PRIMARY))
        fm_name = QFontMetrics(self._name_font)
        name_elided = fm_name.elidedText(
            entry.display_name,
            Qt.TextElideMode.ElideRight,
            text_w,
        )
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name_elided)

        # ── Subtitle: emulator · size ────────────────────────────
        sub_parts = [entry.emulator]
        if entry.save_size:
            kb = entry.save_size / 1024
            sub_parts.append(f"{kb:.0f} KB" if kb >= 1 else f"{entry.save_size} B")
        if entry.save_path is None:
            sub_parts.append("no save")

        sub_text = "  ·  ".join(sub_parts)
        sub_rect = QRect(text_x, card_rect.top() + name_h, text_w, name_h - self.V_PAD + 2)

        painter.setFont(self._sub_font)
        painter.setPen(QColor(theme.TEXT_SECONDARY))
        painter.drawText(sub_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, sub_text)

        painter.restore()


class GameListView(QListView):
    """QListView configured for the game list."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = GameListModel(self)
        self._delegate = GameDelegate(self)
        self.setModel(self._model)
        self.setItemDelegate(self._delegate)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)

    def set_entries(self, entries: list[GameEntry]) -> None:
        selected = self.selected_entry()
        selected_title_id = selected.title_id if selected else None
        self._model.set_entries(entries)
        target_row = _find_selection_row(entries, selected_title_id)
        if target_row < 0:
            return

        new_idx = self._model.index(target_row, 0)
        self.setCurrentIndex(new_idx)
        self.scrollTo(new_idx, QAbstractItemView.ScrollHint.EnsureVisible)

    def selected_entry(self) -> GameEntry | None:
        idx = self.currentIndex()
        return self._model.entry_at(idx.row()) if idx.isValid() else None

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
        # Step by a whole viewport and land the new selection at the
        # top of the list so the viewport visibly shifts even when the
        # old selection was still visible inside it.
        step = self._viewport_rows()
        self.move_selection(-step, QAbstractItemView.ScrollHint.PositionAtTop)

    def page_down(self) -> None:
        step = self._viewport_rows()
        self.move_selection(step, QAbstractItemView.ScrollHint.PositionAtBottom)
