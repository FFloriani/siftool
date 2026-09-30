"""siftool_gui: PySide6 + QFluentWidgets front-end for the siftool engine.

Deliberately Qt-free at import time (spec section 8, "Isolamento" and
architecture rule 4.1): this module, `entries.py` and `presentation.py`
never import PySide6 or qfluentwidgets, so their logic stays testable
without a display and `siftool` itself never gains a Qt dependency.
"""

from __future__ import annotations

from siftool import __version__

__all__ = ["__version__"]
