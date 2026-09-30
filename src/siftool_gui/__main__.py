"""`python -m siftool_gui` entry point.

DANGER: run this from outside the repository root (or pass `-P`) -- the
legacy `siftool.py` at the root would otherwise shadow this package
(spec section 4.1).
"""

from __future__ import annotations

from siftool_gui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
