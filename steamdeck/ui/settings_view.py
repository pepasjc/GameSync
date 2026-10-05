"""Settings tab.

Every GameSync client has Settings as a top-level tab rather than a modal
reached by START: a list of entries where A acts on the highlighted one.
Editing the connection and folder fields still happens in the existing
``SettingsDialog`` (one entry opens it); the others are the maintenance
actions the console clients carry too, above all *Refresh catalog*.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from . import theme

_RowRole = Qt.ItemDataRole.UserRole + 1


@dataclass
class SettingsRow:
    key: str
    title: str
    value: str = ""
    description: str = ""
    danger: bool = False


class _Model(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[SettingsRow] = []

    def set_rows(self, rows: list[SettingsRow]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        row = self._rows[index.row()]
        if role == _RowRole:
            return row
        if role == Qt.ItemDataRole.DisplayRole:
            return row.title
        return None

    def row_at(self, index: int) -> Optional[SettingsRow]:
        return self._rows[index] if 0 <= index < len(self._rows) else None


class _Delegate(QStyledItemDelegate):
    ROW_H = 58

    def __init__(self, parent=None):
        super().__init__(parent)
        self._title_font = QFont()
        self._title_font.setPointSize(13)
        self._title_font.setBold(True)
        self._value_font = QFont()
        self._value_font.setPointSize(10)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(option.rect.width(), self.ROW_H)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        row: Optional[SettingsRow] = index.data(_RowRole)
        if row is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(option.state & option.state.State_Selected)
        card = option.rect.adjusted(4, 3, -4, -3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(theme.PANEL_HI if selected else theme.PANEL)))
        painter.drawRoundedRect(card, theme.CARD_RADIUS, theme.CARD_RADIUS)
        if selected:
            painter.setBrush(QBrush(QColor(theme.ACCENT)))
            painter.drawRoundedRect(QRect(card.left(), card.top(), 4, card.height()), 2, 2)

        text_x = card.left() + 18
        value_w = 0
        if row.value:
            painter.setFont(self._value_font)
            fm = QFontMetrics(self._value_font)
            value_w = min(fm.horizontalAdvance(row.value), card.width() // 2)
            painter.setPen(QColor(theme.DIM))
            painter.drawText(
                QRect(card.right() - 18 - value_w, card.top(), value_w, card.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                fm.elidedText(row.value, Qt.TextElideMode.ElideMiddle, value_w),
            )
        painter.setFont(self._title_font)
        painter.setPen(QColor(theme.ERR if row.danger else theme.TEXT))
        painter.drawText(
            QRect(text_x, card.top(), card.width() - 54 - value_w, card.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            row.title,
        )
        painter.restore()


class SettingsView(QListView):
    """The Settings tab list.  ``activated_key`` fires on A / double click."""

    activated_key = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = _Model(self)
        self.setModel(self._model)
        self.setItemDelegate(_Delegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setContentsMargins(12, 8, 12, 8)
        self.doubleClicked.connect(self._on_double_click)

    def set_rows(self, rows: list[SettingsRow]) -> None:
        current = self.selected_row()
        key = current.key if current else None
        self._model.set_rows(rows)
        target = 0
        for i, row in enumerate(rows):
            if row.key == key:
                target = i
                break
        if rows:
            self.setCurrentIndex(self._model.index(target, 0))

    def selected_row(self) -> Optional[SettingsRow]:
        idx = self.currentIndex()
        return self._model.row_at(idx.row()) if idx.isValid() else None

    def move_selection(self, delta: int) -> None:
        count = self._model.rowCount()
        if not count:
            return
        idx = self.currentIndex()
        row = max(0, min(count - 1, (idx.row() if idx.isValid() else 0) + delta))
        new = self._model.index(row, 0)
        self.setCurrentIndex(new)
        self.scrollTo(new, QAbstractItemView.ScrollHint.EnsureVisible)

    def page_up(self) -> None:
        self.move_selection(-self._model.rowCount())

    def page_down(self) -> None:
        self.move_selection(self._model.rowCount())

    def activate(self) -> None:
        row = self.selected_row()
        if row is not None:
            self.activated_key.emit(row.key)

    def _on_double_click(self, index: QModelIndex) -> None:
        row = self._model.row_at(index.row())
        if row is not None:
            self.activated_key.emit(row.key)
