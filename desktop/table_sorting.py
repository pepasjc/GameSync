"""Click-to-sort column headers for the desktop client's tables.

Behaves like Windows Explorer: clicking a header sorts ascending by that
column, clicking the same header again flips to descending.  Tables start
unsorted (in the order they were populated) until a header is clicked.

Text is compared case-insensitively with digit runs compared as numbers, so
"Disc 10" sorts after "Disc 2" and "1,024" after "512".  A cell can carry an
explicit key in ``SORT_ROLE`` (e.g. a byte count or timestamp) when its
display text doesn't sort well.
"""

from __future__ import annotations

import re
from contextlib import contextmanager

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem

SORT_ROLE = Qt.ItemDataRole.UserRole + 100

_NUM_RE = re.compile(r"(\d[\d,]*)")


def _natural_key(text: str) -> tuple:
    parts = []
    for i, chunk in enumerate(_NUM_RE.split(text.casefold())):
        if i % 2:
            parts.append((0, int(chunk.replace(",", ""))))
        elif chunk:
            parts.append((1, chunk))
    return tuple(parts)


class SortableItem(QTableWidgetItem):
    """``QTableWidgetItem`` that sorts naturally or by its ``SORT_ROLE`` key."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        mine = self.data(SORT_ROLE)
        theirs = other.data(SORT_ROLE)
        if mine is not None and theirs is not None:
            try:
                return mine < theirs
            except TypeError:
                pass
        return _natural_key(self.text()) < _natural_key(other.text())


def make_sortable(table: QTableWidget) -> None:
    """Enable header-click sorting without re-ordering the current rows."""
    table.setItemPrototype(SortableItem())
    header = table.horizontalHeader()
    header.setSectionsClickable(True)
    header.setSortIndicatorShown(True)
    # Qt defaults the indicator to column 0 descending, which would sort the
    # table the moment sorting is enabled.  -1 means "not sorted yet".
    header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
    table.setSortingEnabled(True)


@contextmanager
def sorting_suspended(table: QTableWidget):
    """Hold rows still while filling or editing them by row index.

    With sorting on, every ``setItem`` can move its row, so code that writes
    several cells of one row must suspend sorting first.  The current sort is
    re-applied on exit.
    """
    was_enabled = table.isSortingEnabled()
    table.setSortingEnabled(False)
    try:
        yield
    finally:
        if was_enabled:
            table.setSortingEnabled(True)
