"""Per-Mode whitelist of Categories that are allowed to remain after cleaning.

Every Category not listed for a Mode is removed.
"""

from __future__ import annotations

from .model import Category, Mode

ALLOWED: dict[Mode, frozenset[Category]] = {
    Mode.PRIVACY: frozenset({Category.APPEARANCE}),
    Mode.PHOTOGRAPHER: frozenset(
        {Category.APPEARANCE, Category.DEVICE, Category.CAMERA_SETTINGS}
    ),
    Mode.MAXIMUM: frozenset(),
}
