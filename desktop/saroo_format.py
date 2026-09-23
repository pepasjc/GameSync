"""Backwards-compatible alias for :mod:`shared.saturn_format`.

The Saturn save format logic moved into ``shared/`` so the MiSTer on-device
client can use the same implementation as the desktop client instead of
re-deriving it. Existing ``from saroo_format import ...`` call sites keep
working, including the private helpers the tests reach for, because this
module *is* the shared one after import.
"""

import sys

from shared import saturn_format as _saturn_format

sys.modules[__name__] = _saturn_format
