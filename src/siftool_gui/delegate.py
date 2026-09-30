"""`FileItemDelegate`: paints the list's rows in the Fluent style (spec sections 5.2 and 6).

Everything a row shows comes from `siftool_gui.model`'s roles; the only
text this module owns is the "+N" of the category tags. Row kinds:

- `FILE` (64 px): a rounded card (QFluentWidgets' own card colors) with a
  48 x 48 rounded thumbnail -- or `FluentIcon.PHOTO` until a preview
  exists -- the file name in bold above its folder in secondary text (both
  elided in the middle, so the extension stays visible), up to three
  category tags plus "+N", and the state on the right as text *and* icon.
  Hover and selection tint the card with a translucent `themeColor()`;
  selection also draws Fluent's accent bar.
- `CATEGORY` (32 px): ✖ (sai) or ✔ (fica), the category name, its human
  value in secondary text, and "sai"/"fica" aligned right.
- `NOTE`: one elided line; the model puts the full text in the tooltip.
- `TECH_HEADER`: "Detalhes técnicos"; `TECH`: small "label — detail — where".

Every color is a light/dark pair read at paint time (`isDarkTheme()`), so
a theme switch only needs a repaint. The outcome badges use
QFluentWidgets' InfoBar family: a filled green ✓ for Verificado (the seal,
only ever drawn for that state), an amber ! for Com ressalvas, a red ✖ for
Não limpou / errors and a gray i for formats not supported yet, on
`FluentSystemColor` tints, always next to the model's text -- the state
is never told by color alone (spec 6). `FluentIcon.CANCEL` is an undo
arrow in QFluentWidgets 1.11, so it is not used for "cancelled/failed".

The tree view draws its expand arrow in the first 16 px of each row
(QFluentWidgets' `TreeView` shifts the branch area right by 15 px);
every row kind keeps that gutter free.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTreeView, QWidget
from qfluentwidgets import FluentIcon, FluentSystemColor, InfoBarIcon, Theme, getFont, isDarkTheme, themeColor

from siftool.model import Category

from siftool_gui.entries import EntryState
from siftool_gui.model import (
    CATEGORY_ROLE,
    FINDING_ROLE,
    KIND_ROLE,
    OUTCOME_ROLE,
    STATE_ROLE,
    THUMBNAIL_ROLE,
    VALUE_ROLE,
    RowKind,
    finding_text,
)
from siftool_gui.presentation import Outcome, category_name

FILE_ROW_HEIGHT = 64
CHILD_ROW_HEIGHT = 32
MAX_TAGS = 3

GUTTER = 16  # left of every row: the tree view's expand arrow lives here
CARD_RADIUS = 6.0
PADDING = 12.0
THUMB_SIZE = 48
THUMB_RADIUS = 6.0
TAG_HEIGHT = 22.0
TAG_PADDING = 8.0
TAG_GAP = 6.0
BADGE_HEIGHT = 24.0
BADGE_PADDING = 8.0
ICON_SIZE = 16
CHILD_ICON_SIZE = 14
CHILD_ICON_LEFT = 12  # clear of the 3 px selection bar
NAME_MIN_WIDTH = 120.0

_SELECTED = QStyle.StateFlag.State_Selected
_HOVER = QStyle.StateFlag.State_MouseOver
_FOCUS = QStyle.StateFlag.State_HasFocus
_DISPLAY = Qt.ItemDataRole.DisplayRole
_TOOLTIP = Qt.ItemDataRole.ToolTipRole


# -- colors --------------------------------------------------------------------


@dataclass(frozen=True)
class _Palette:
    text: QColor
    secondary: QColor
    card: QColor
    card_border: QColor
    tag_fill: QColor
    tag_border: QColor
    placeholder: QColor
    accent: QColor
    theme: Theme


def _palette() -> _Palette:
    """QFluentWidgets' own light/dark pairs (`CardWidget`, `PillPushButton`, caption labels)."""
    dark = isDarkTheme()
    return _Palette(
        text=QColor(255, 255, 255) if dark else QColor(0, 0, 0),
        secondary=QColor(206, 206, 206) if dark else QColor(96, 96, 96),
        card=QColor(255, 255, 255, 13) if dark else QColor(255, 255, 255, 170),
        card_border=QColor(0, 0, 0, 48) if dark else QColor(0, 0, 0, 15),
        tag_fill=QColor(255, 255, 255, 15) if dark else QColor(243, 243, 243, 194),
        tag_border=QColor(255, 255, 255, 18) if dark else QColor(0, 0, 0, 15),
        placeholder=QColor(255, 255, 255, 15) if dark else QColor(0, 0, 0, 10),
        accent=themeColor(),
        theme=Theme.DARK if dark else Theme.LIGHT,
    )


@dataclass(frozen=True)
class _Badge:
    icon: object | None
    icon_color: QColor | None  # None: the icon's own colors
    icon_indexes: tuple[int, ...] | None  # which SVG paths `icon_color` repaints
    text: QColor
    fill: QColor | None


def status_icon(state: EntryState):
    """The icon drawn next to a file row's status text; None when the text stands alone."""
    return _STATUS_ICONS.get(state)


_STATUS_ICONS = {
    EntryState.QUEUED: FluentIcon.HISTORY,
    EntryState.ANALYZING: FluentIcon.SYNC,
    EntryState.CLEANING: FluentIcon.SYNC,
    EntryState.VERIFIED: InfoBarIcon.SUCCESS,
    EntryState.WARNINGS: InfoBarIcon.WARNING,
    EntryState.FAILED: InfoBarIcon.ERROR,
    EntryState.ERROR: InfoBarIcon.ERROR,
    EntryState.UNSUPPORTED: InfoBarIcon.INFORMATION,
}


def _badge(state: EntryState, colors: _Palette) -> _Badge:
    theme = colors.theme
    icon = status_icon(state)
    if state is EntryState.VERIFIED:
        return _Badge(icon, None, None, FluentSystemColor.SUCCESS_FOREGROUND.color(theme),
                      FluentSystemColor.SUCCESS_BACKGROUND.color(theme))
    if state is EntryState.WARNINGS:
        return _Badge(icon, None, None, FluentSystemColor.CAUTION_FOREGROUND.color(theme),
                      FluentSystemColor.CAUTION_BACKGROUND.color(theme))
    if state in (EntryState.FAILED, EntryState.ERROR):
        return _Badge(icon, None, None, FluentSystemColor.CRITICAL_FOREGROUND.color(theme),
                      FluentSystemColor.CRITICAL_BACKGROUND.color(theme))
    if state is EntryState.UNSUPPORTED:  # a gray disc behind the InfoBar "i"
        return _Badge(icon, colors.secondary, (0,), colors.secondary, colors.tag_fill)
    if state in (EntryState.ANALYZING, EntryState.CLEANING):
        return _Badge(icon, colors.accent, None, colors.secondary, None)
    return _Badge(icon, colors.secondary, None, colors.secondary, None)


# -- cached pixmaps ------------------------------------------------------------

_CACHE_LIMIT = 256
_icon_cache: dict[tuple, QPixmap] = {}
_thumb_cache: dict[tuple, QPixmap] = {}


def _icon_pixmap(icon, size: int, ratio: float, theme: Theme, color: QColor | None = None,
                 indexes: tuple[int, ...] | None = None) -> QPixmap:
    """`icon` rendered once per (theme, color, size, device pixel ratio), then reused."""
    key = (icon, theme, None if color is None else color.rgba(), size, ratio, indexes)
    pixmap = _icon_cache.get(key)
    if pixmap is None:
        if len(_icon_cache) >= _CACHE_LIMIT:
            _icon_cache.clear()
        side = max(1, round(size * ratio))
        pixmap = QPixmap(side, side)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        attributes = {} if color is None else {"fill": color.name()}
        icon.render(painter, QRectF(0, 0, side, side), theme, list(indexes) if indexes else None, **attributes)
        painter.end()
        pixmap.setDevicePixelRatio(ratio)
        _icon_cache[key] = pixmap
    return pixmap


def _thumbnail_pixmap(source: QPixmap, size: int, ratio: float, radius: float) -> QPixmap:
    """`source` center-cropped to a square, scaled to `size` and rounded (antialiased edges)."""
    key = (source.cacheKey(), size, ratio, radius)
    pixmap = _thumb_cache.get(key)
    if pixmap is None:
        if len(_thumb_cache) >= _CACHE_LIMIT:
            _thumb_cache.clear()
        side = max(1, round(size * ratio))
        edge = min(source.width(), source.height())
        square = source.copy((source.width() - edge) // 2, (source.height() - edge) // 2, edge, edge)
        square.setDevicePixelRatio(1.0)
        scaled = square.scaled(side, side, Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        pixmap = QPixmap(side, side)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(scaled))
        painter.drawRoundedRect(QRectF(0, 0, side, side), radius * ratio, radius * ratio)
        painter.end()
        pixmap.setDevicePixelRatio(ratio)
        _thumb_cache[key] = pixmap
    return pixmap


# -- layout helpers ------------------------------------------------------------


def fit_tags(names: Sequence[str], available: float, width_of: Callable[[str], float],
             gap: float = TAG_GAP, limit: int = MAX_TAGS) -> tuple[list[str], int]:
    """Which category tags to draw in `available` px: `(shown, hidden)`.

    At most `limit` names, in order; the others are summarized by one
    "+hidden" tag. As many names as fit are shown; the result always fits
    (`width_of` gives a tag's full width), and when not even the "+N" tag
    fits, nothing is drawn: `([], 0)`.
    """
    for count in range(min(limit, len(names)), -1, -1):
        shown = list(names[:count])
        hidden = len(names) - count
        widths = [width_of(name) for name in shown]
        if hidden:
            widths.append(width_of(f"+{hidden}"))
        if not widths:
            return [], 0
        if sum(widths) + gap * (len(widths) - 1) <= available:
            return shown, hidden
    return [], 0


def _single_line(text: str) -> str:
    return " ".join(str(text).split())


def _elided(text: str, font: QFont, width: float, mode=Qt.TextElideMode.ElideRight) -> str:
    if width <= 0:
        return ""
    return QFontMetricsF(font).elidedText(_single_line(text), mode, width)


class FileItemDelegate(QStyledItemDelegate):
    """Paints `FileListModel` rows (see the module docstring)."""

    FILE_ROW_HEIGHT = FILE_ROW_HEIGHT
    CHILD_ROW_HEIGHT = CHILD_ROW_HEIGHT

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name_font = getFont(14, QFont.Weight.DemiBold)
        self._folder_font = getFont(12)
        self._tag_font = getFont(12)
        self._badge_font = getFont(12, QFont.Weight.DemiBold)
        self._body_font = getFont(13)
        self._strong_font = getFont(13, QFont.Weight.DemiBold)
        self._small_font = getFont(12)
        metrics = QFontMetricsF(self._strong_font)
        self._category_column = max(metrics.horizontalAdvance(category_name(c)) for c in Category) + 16

    # -- Qt -------------------------------------------------------------------

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        kind = index.data(KIND_ROLE)
        if kind is None:
            return super().sizeHint(option, index)
        height = FILE_ROW_HEIGHT if kind == RowKind.FILE else CHILD_ROW_HEIGHT
        return QSize(max(option.rect.width(), 0), height)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex | QPersistentModelIndex) -> None:
        kind = index.data(KIND_ROLE)
        if kind is None:
            super().paint(painter, option, index)
            return

        colors = _palette()
        ratio = painter.device().devicePixelRatioF() if painter.device() is not None else 1.0
        painter.save()
        try:
            painter.setRenderHints(
                QPainter.RenderHint.Antialiasing
                | QPainter.RenderHint.TextAntialiasing
                | QPainter.RenderHint.SmoothPixmapTransform
            )
            if kind == RowKind.FILE:
                self._paint_file(painter, option, index, colors, ratio)
            else:
                rect = QRectF(option.rect).adjusted(GUTTER, 0, -4, 0)
                self._paint_child_background(painter, option, rect, colors)
                if kind == RowKind.CATEGORY:
                    self._paint_category(painter, rect, index, colors, ratio)
                elif kind == RowKind.NOTE:
                    self._paint_note(painter, rect, index, colors)
                elif kind == RowKind.TECH_HEADER:
                    self._paint_tech_header(painter, rect, index, colors, ratio)
                else:
                    self._paint_tech(painter, option, rect, index, colors)
        finally:
            painter.restore()

    # -- file rows --------------------------------------------------------------

    def _paint_file(self, painter: QPainter, option: QStyleOptionViewItem, index, colors: _Palette,
                    ratio: float) -> None:
        card = QRectF(option.rect).adjusted(GUTTER, 2, -4, -2)
        if card.width() <= 2 * PADDING or card.height() <= 0:
            return

        painter.setPen(QPen(colors.card_border, 1))
        painter.setBrush(colors.card)
        painter.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), CARD_RADIUS, CARD_RADIUS)

        selected = bool(option.state & _SELECTED)
        if selected or option.state & _HOVER:
            tint = QColor(colors.accent)
            tint.setAlpha(36 if selected else 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(tint)
            painter.drawRoundedRect(card, CARD_RADIUS, CARD_RADIUS)
        if selected:
            painter.setBrush(colors.accent)
            painter.drawRoundedRect(QRectF(card.left() + 3, card.center().y() - 9, 3, 18), 1.5, 1.5)
        elif option.state & _FOCUS:  # keyboard focus away from the selection
            painter.setPen(QPen(colors.accent, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(card.adjusted(1, 1, -1, -1), CARD_RADIUS - 1, CARD_RADIUS - 1)

        center_y = card.center().y()
        thumb = QRectF(card.left() + PADDING, center_y - THUMB_SIZE / 2, THUMB_SIZE, THUMB_SIZE)
        self._paint_thumbnail(painter, thumb, index.data(THUMBNAIL_ROLE), colors, ratio)

        right = card.right() - PADDING
        text_left = thumb.right() + PADDING

        status = index.data(VALUE_ROLE) or ""
        state = index.data(STATE_ROLE)
        if status and isinstance(state, EntryState):
            limit = max(0.0, (right - text_left) * 0.45)
            width = self._paint_badge(painter, right, center_y, limit, _single_line(status),
                                      _badge(state, colors), colors, ratio)
            right -= width + PADDING

        categories = index.data(CATEGORY_ROLE) or ()
        names = [category_name(category) for category in categories]
        if names:
            # The file name comes first: its full width (up to half the room), then the tags.
            free = right - text_left
            natural = QFontMetricsF(self._name_font).horizontalAdvance(index.data(_DISPLAY) or "")
            name_room = min(natural, max(NAME_MIN_WIDTH, free / 2))
            used = self._paint_tags(painter, right, center_y, free - name_room - PADDING, names, colors)
            if used:
                right -= used + PADDING

        text_width = right - text_left
        name = _elided(index.data(_DISPLAY) or "", self._name_font, text_width, Qt.TextElideMode.ElideMiddle)
        folder = _elided(os.path.dirname(index.data(_TOOLTIP) or ""), self._folder_font, text_width,
                         Qt.TextElideMode.ElideMiddle)
        name_height = QFontMetricsF(self._name_font).height()
        folder_height = QFontMetricsF(self._folder_font).height()
        top = center_y - (name_height + 2 + folder_height) / 2
        painter.setFont(self._name_font)
        painter.setPen(colors.text)
        painter.drawText(QRectF(text_left, top, text_width, name_height),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        painter.setFont(self._folder_font)
        painter.setPen(colors.secondary)
        painter.drawText(QRectF(text_left, top + name_height + 2, text_width, folder_height),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, folder)

    def _paint_thumbnail(self, painter: QPainter, rect: QRectF, pixmap, colors: _Palette, ratio: float) -> None:
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            painter.drawPixmap(rect.topLeft(), _thumbnail_pixmap(pixmap, THUMB_SIZE, ratio, THUMB_RADIUS))
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colors.placeholder)
        painter.drawRoundedRect(rect, THUMB_RADIUS, THUMB_RADIUS)
        icon = _icon_pixmap(FluentIcon.PHOTO, 24, ratio, colors.theme, colors.secondary)
        painter.drawPixmap(QPointF(rect.center().x() - 12, rect.center().y() - 12), icon)

    def _paint_badge(self, painter: QPainter, right: float, center_y: float, limit: float, text: str,
                     badge: _Badge, colors: _Palette, ratio: float) -> float:
        """Draw the state (icon + text) ending at `right`; return the width used."""
        padding = BADGE_PADDING if badge.fill is not None else 0.0
        icon_room = ICON_SIZE + 6 if badge.icon is not None else 0.0
        metrics = QFontMetricsF(self._badge_font)
        text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(0.0, limit - 2 * padding - icon_room))
        width = 2 * padding + icon_room + metrics.horizontalAdvance(text)
        rect = QRectF(right - width, center_y - BADGE_HEIGHT / 2, width, BADGE_HEIGHT)
        if badge.fill is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(badge.fill)
            painter.drawRoundedRect(rect, BADGE_HEIGHT / 2, BADGE_HEIGHT / 2)
        x = rect.left() + padding
        if badge.icon is not None:
            icon = _icon_pixmap(badge.icon, ICON_SIZE, ratio, colors.theme, badge.icon_color, badge.icon_indexes)
            painter.drawPixmap(QPointF(x, center_y - ICON_SIZE / 2), icon)
            x += icon_room
        painter.setFont(self._badge_font)
        painter.setPen(badge.text)
        painter.drawText(QRectF(x, rect.top(), rect.right() - padding - x + 1, rect.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        return width

    def _paint_tags(self, painter: QPainter, right: float, center_y: float, available: float,
                    names: list[str], colors: _Palette) -> float:
        """Draw the category tags right-aligned at `right`; return the width used."""
        metrics = QFontMetricsF(self._tag_font)

        def width_of(text: str) -> float:
            return metrics.horizontalAdvance(text) + 2 * TAG_PADDING

        shown, hidden = fit_tags(names, available, width_of)
        labels = shown + ([f"+{hidden}"] if hidden else [])
        if not labels:
            return 0.0
        widths = [width_of(label) for label in labels]
        total = sum(widths) + TAG_GAP * (len(widths) - 1)
        x = right - total
        painter.setFont(self._tag_font)
        for label, width in zip(labels, widths):
            rect = QRectF(x, center_y - TAG_HEIGHT / 2, width, TAG_HEIGHT)
            painter.setPen(QPen(colors.tag_border, 1))
            painter.setBrush(colors.tag_fill)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), TAG_HEIGHT / 2, TAG_HEIGHT / 2)
            painter.setPen(colors.text)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
            x += width + TAG_GAP
        return total

    # -- child rows ----------------------------------------------------------------

    def _paint_child_background(self, painter: QPainter, option: QStyleOptionViewItem, rect: QRectF,
                                colors: _Palette) -> None:
        selected = bool(option.state & _SELECTED)
        area = rect.adjusted(0, 2, 0, -2)
        if selected or option.state & _HOVER:
            tint = QColor(colors.accent)
            tint.setAlpha(30 if selected else 14)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(tint)
            painter.drawRoundedRect(area, 4, 4)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colors.accent)
            painter.drawRoundedRect(QRectF(area.left() + 2, area.center().y() - 7, 3, 14), 1.5, 1.5)
        elif option.state & _FOCUS:
            painter.setPen(QPen(colors.accent, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(area.adjusted(1, 1, -1, -1), 3, 3)

    def _text_left(self, rect: QRectF) -> float:
        """Where child-row text starts: after an icon slot, so names, notes and details line up."""
        return rect.left() + CHILD_ICON_LEFT + CHILD_ICON_SIZE + 10

    def _paint_category(self, painter: QPainter, rect: QRectF, index, colors: _Palette, ratio: float) -> None:
        removed = index.data(OUTCOME_ROLE) == Outcome.REMOVED
        color = colors.accent if removed else colors.secondary
        icon = FluentIcon.CLOSE if removed else FluentIcon.ACCEPT
        center_y = rect.center().y()
        painter.drawPixmap(QPointF(rect.left() + CHILD_ICON_LEFT, center_y - CHILD_ICON_SIZE / 2),
                           _icon_pixmap(icon, CHILD_ICON_SIZE, ratio, colors.theme, color))

        outcome = Outcome.REMOVED.value if removed else Outcome.KEPT.value
        painter.setFont(self._strong_font)
        outcome_width = QFontMetricsF(self._strong_font).horizontalAdvance(outcome)
        outcome_left = rect.right() - PADDING - outcome_width
        painter.setPen(color)
        painter.drawText(QRectF(outcome_left, rect.top(), outcome_width + 1, rect.height()),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, outcome)

        left = self._text_left(rect)
        name_width = min(self._category_column, max(0.0, outcome_left - PADDING - left))
        painter.setPen(colors.text)
        painter.drawText(QRectF(left, rect.top(), name_width, rect.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         _elided(index.data(_DISPLAY) or "", self._strong_font, name_width))

        value_left = left + name_width
        value_width = outcome_left - PADDING - value_left
        painter.setFont(self._body_font)
        painter.setPen(colors.secondary)
        painter.drawText(QRectF(value_left, rect.top(), max(0.0, value_width), rect.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         _elided(index.data(VALUE_ROLE) or "", self._body_font, value_width))

    def _paint_note(self, painter: QPainter, rect: QRectF, index, colors: _Palette) -> None:
        left = self._text_left(rect)
        width = rect.right() - PADDING - left
        painter.setFont(self._body_font)
        painter.setPen(colors.text)
        painter.drawText(QRectF(left, rect.top(), max(0.0, width), rect.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         _elided(index.data(_DISPLAY) or "", self._body_font, width))

    def _paint_tech_header(self, painter: QPainter, rect: QRectF, index, colors: _Palette, ratio: float) -> None:
        center_y = rect.center().y()
        painter.drawPixmap(QPointF(rect.left() + CHILD_ICON_LEFT, center_y - CHILD_ICON_SIZE / 2),
                           _icon_pixmap(FluentIcon.CODE, CHILD_ICON_SIZE, ratio, colors.theme, colors.secondary))
        left = self._text_left(rect)
        width = rect.right() - PADDING - left
        painter.setFont(self._strong_font)
        painter.setPen(colors.secondary)
        painter.drawText(QRectF(left, rect.top(), max(0.0, width), rect.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         _elided(index.data(_DISPLAY) or "", self._strong_font, width))

    def _paint_tech(self, painter: QPainter, option: QStyleOptionViewItem, rect: QRectF, index,
                    colors: _Palette) -> None:
        finding = index.data(FINDING_ROLE)
        text = finding_text(finding) if finding is not None else (index.data(_DISPLAY) or "")
        # One level deeper than "Detalhes técnicos": start under its text, not under its icon.
        indentation = option.widget.indentation() if isinstance(option.widget, QTreeView) else 20
        left = max(rect.left() + 10, self._text_left(rect) - indentation)
        width = rect.right() - PADDING - left
        painter.setFont(self._small_font)
        painter.setPen(colors.secondary)
        painter.drawText(QRectF(left, rect.top(), max(0.0, width), rect.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         _elided(text, self._small_font, width))
