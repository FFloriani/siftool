"""The "Sobre" page (spec section 5.7).

What the program is and what it promises, in a few cards under a title
that stays put: the versions of the program and of the engine, the four
promises (one per line), the license and credits, and the note about the
formats still to come. Static text only -- nothing here reads files or
touches the network. The column is at most `MAX_CONTENT_WIDTH` wide and
centered, like the other pages.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    FluentIcon,
    IconWidget,
    ScrollArea,
    SimpleCardWidget,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
)

import siftool
import siftool_gui
from siftool_gui.drop_area import AccentIcon
from siftool_gui.presentation import display_version

TITLE_TEXT = "Sobre o Siftool"
APP_NAME = "Siftool"
# "2.0.0 beta 1", the way the installer and Siftool.exe say it -- not PEP 440's "2.0.0b1"
VERSION_TEXT = f"Versão {display_version(siftool_gui.__version__)}"  # the program
ENGINE_TEXT = f"Motor de limpeza {display_version(siftool.__version__)}"  # the engine it runs on (spec 5.7 asks for both)
TAGLINE_TEXT = "Limpa o que suas fotos escondem, sem mexer nas originais."
PROMISES_TITLE = "O que o Siftool garante"
PROMISES = (
    "Funciona 100% offline.",
    "Nunca altera o arquivo original.",
    "Nunca recomprime a imagem.",
    "Só dá o selo quando a verificação passa.",
)
LICENSE_TITLE = "Licença e créditos"
LICENSE_TEXT = "Licença: GPLv3"
CREDITS_TEXT = "Feito com PySide6/Qt (LGPL) e QFluentWidgets (GPLv3)."
COMING_SOON_TEXT = "Fotos HEIC e vídeos chegam numa próxima versão."

MAX_CONTENT_WIDTH = 960
SIDE_MARGIN = 36
TITLE_TOP = 28
HEADER_HEIGHT = 88  # the title's strip: the cards scroll under it

_SECONDARY_LIGHT = QColor(96, 96, 96)
_SECONDARY_DARK = QColor(206, 206, 206)


def _card(parent: QWidget) -> tuple[SimpleCardWidget, QVBoxLayout]:
    card = SimpleCardWidget(parent)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 16, 20, 18)
    layout.setSpacing(10)
    return card, layout


def _line(icon: QWidget, text: str, parent: QWidget) -> QHBoxLayout:
    """One icon-and-sentence row; the sentence wraps instead of being cut."""
    icon.setParent(parent)
    icon.setFixedSize(16, 16)
    label = BodyLabel(text, parent)
    label.setWordWrap(True)
    slot = QVBoxLayout()  # keeps the icon on the first line when the sentence wraps
    slot.setContentsMargins(0, 3, 0, 0)
    slot.addWidget(icon)
    slot.addStretch(1)
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(12)
    row.addLayout(slot)
    row.addWidget(label, 1)
    return row


class AboutPage(ScrollArea):
    """The "Sobre" page (see the module docstring)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("aboutPage")

        self.title_label = TitleLabel(TITLE_TEXT, self)
        self.scroll_widget = QWidget()
        self.scroll_widget.setObjectName("aboutScrollWidget")
        self._column = QWidget(self.scroll_widget)
        self._column.setMaximumWidth(MAX_CONTENT_WIDTH)
        column = QVBoxLayout(self._column)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(12)

        # What it is.
        card, box = _card(self._column)
        identity = QHBoxLayout()
        identity.setSpacing(16)
        logo = AccentIcon(FluentIcon.BROOM, card)
        logo.setFixedSize(36, 36)
        names = QVBoxLayout()
        names.setSpacing(2)
        self.name_label = SubtitleLabel(APP_NAME, card)
        self.version_label = BodyLabel(VERSION_TEXT, card)
        self.engine_label = BodyLabel(ENGINE_TEXT, card)
        names.addWidget(self.name_label)
        for label in (self.version_label, self.engine_label):
            label.setTextColor(_SECONDARY_LIGHT, _SECONDARY_DARK)
            names.addWidget(label)
        identity.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)
        identity.addLayout(names, 1)
        box.addLayout(identity)
        tagline = BodyLabel(TAGLINE_TEXT, card)
        tagline.setWordWrap(True)
        box.addWidget(tagline)
        column.addWidget(card)

        # What it promises, one promise per line.
        card, box = _card(self._column)
        box.addWidget(StrongBodyLabel(PROMISES_TITLE, card))
        for promise in PROMISES:
            box.addLayout(_line(AccentIcon(FluentIcon.ACCEPT), promise, card))
        column.addWidget(card)

        # License and credits.
        card, box = _card(self._column)
        box.addWidget(StrongBodyLabel(LICENSE_TITLE, card))
        box.addLayout(_line(IconWidget(FluentIcon.CERTIFICATE), LICENSE_TEXT, card))
        box.addLayout(_line(IconWidget(FluentIcon.CODE), CREDITS_TEXT, card))
        column.addWidget(card)

        # What is still to come.
        card, box = _card(self._column)
        box.addLayout(_line(IconWidget(FluentIcon.INFO), COMING_SOON_TEXT, card))
        column.addWidget(card)
        column.addStretch(1)

        self._outer = QHBoxLayout(self.scroll_widget)
        self._outer.setSpacing(0)
        self._outer.addStretch(1)
        self._outer.addWidget(self._column, 10_000)  # as wide as it may be; the rest centers it
        self._outer.addStretch(1)

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setViewportMargins(0, HEADER_HEIGHT, 0, 20)
        self.setWidget(self.scroll_widget)
        self.setWidgetResizable(True)
        self.setStyleSheet(
            "#aboutPage { border: none; background: transparent; } #aboutScrollWidget { background: transparent; }"
        )
        self._outer.setContentsMargins(SIDE_MARGIN, 4, SIDE_MARGIN, 0)
        self._place_title()

    def _place_title(self) -> None:
        self.title_label.move(max(SIDE_MARGIN, (self.width() - MAX_CONTENT_WIDTH) // 2), TITLE_TOP)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._place_title()
