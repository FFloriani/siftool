"""`python -m siftool`: run the CLI and exit with its return code."""

from __future__ import annotations

from .cli import main

raise SystemExit(main())
