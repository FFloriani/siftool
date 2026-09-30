"""The "Configurações" page (spec section 5.6).

QFluentWidgets' own settings layout: a `ScrollArea` whose widget holds the
`SettingCardGroup`s in an `ExpandLayout` (the expandable option cards need
that layout to grow), under a title that stays put above the scrolling
cards. Like the "Limpar" page, the column is at most `MAX_CONTENT_WIDTH`
wide and centered.

Two groups:

- "Limpeza": the default mode, where the copies are saved, and the folder
  for them (only when "Numa pasta que eu escolher" is selected);
- "Aparência": the theme, applied as soon as it is chosen.

Every card writes straight to the `SiftoolConfig` it is given, which saves
itself; nothing else is stored (spec 7). The "Limpar" page reads the
default mode when it is created: changing it here does not switch the mode
of a list that is already open.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QFileDialog, QWidget
from qfluentwidgets import (
    ExpandLayout,
    FluentIcon,
    OptionsSettingCard,
    PushSettingCard,
    ScrollArea,
    SettingCardGroup,
    TitleLabel,
    setTheme,
)

from siftool_gui.settings import SiftoolConfig

TITLE_TEXT = "Configurações"
CLEANING_GROUP_TEXT = "Limpeza"
APPEARANCE_GROUP_TEXT = "Aparência"

MODE_TITLE = "Modo padrão"
MODE_CONTENT = "O modo que já vem escolhido quando você abre o Siftool"
MODE_TEXTS = ["Privacidade", "Fotógrafo", "Máximo"]

SAVE_TITLE = "Onde salvar as cópias"
SAVE_CONTENT = "A cópia limpa nunca substitui a foto original"
SAVE_TEXTS = ["Ao lado do original", "Numa pasta que eu escolher"]

FOLDER_TITLE = "Pasta das cópias"
FOLDER_BUTTON_TEXT = "Escolher pasta"
NO_FOLDER_TEXT = "Nenhuma pasta escolhida"

THEME_TITLE = "Tema"
THEME_CONTENT = "Claro, escuro ou igual ao do Windows"
THEME_TEXTS = ["Claro", "Escuro", "Seguir o Windows"]

SHOW_OPTIONS_TEXT = "Mostrar as opções"
HIDE_OPTIONS_TEXT = "Esconder as opções"

MAX_CONTENT_WIDTH = 960
SIDE_MARGIN = 36
TITLE_TOP = 28
HEADER_HEIGHT = 88  # the title's strip: the cards scroll under it
_LAID_OUT_WIDTH = 300  # a card narrower than this has not been laid out yet
_FOLDER_CARD_CHROME = 96  # the folder card's icon, margins and spacings around its text and button


class _ResizeWatcher(QObject):
    """Calls `callback` whenever `widget` is resized.

    A separate object: `QScrollArea` already uses its own `eventFilter` for
    its scroll bars and viewport, so the page must not override it.
    """

    def __init__(self, widget: QWidget, callback: Callable[[], None]) -> None:
        super().__init__(widget)
        self._callback = callback
        widget.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            self._callback()
        return False


class SettingsPage(ScrollArea):
    """The "Configurações" page (see the module docstring)."""

    def __init__(self, config: SiftoolConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self._config = config

        self.title_label = TitleLabel(TITLE_TEXT, self)
        self.scroll_widget = QWidget()
        self.scroll_widget.setObjectName("settingsScrollWidget")
        self.expand_layout = ExpandLayout(self.scroll_widget)

        self.clean_group = SettingCardGroup(CLEANING_GROUP_TEXT, self.scroll_widget)
        self.mode_card = OptionsSettingCard(
            config.defaultMode, FluentIcon.BROOM, MODE_TITLE, MODE_CONTENT, texts=MODE_TEXTS, parent=self.clean_group
        )
        self.save_card = OptionsSettingCard(
            config.saveLocation, FluentIcon.SAVE, SAVE_TITLE, SAVE_CONTENT, texts=SAVE_TEXTS, parent=self.clean_group
        )
        self.folder_card = PushSettingCard(
            FOLDER_BUTTON_TEXT, FluentIcon.FOLDER, FOLDER_TITLE, NO_FOLDER_TEXT, parent=self.clean_group
        )
        self.appearance_group = SettingCardGroup(APPEARANCE_GROUP_TEXT, self.scroll_widget)
        self.theme_card = OptionsSettingCard(
            config.themeMode, FluentIcon.BRUSH, THEME_TITLE, THEME_CONTENT, texts=THEME_TEXTS,
            parent=self.appearance_group,
        )

        self._build_layout()
        self._show_folder()
        for card in (self.mode_card, self.save_card, self.theme_card):
            self._label_expand_button(card)
            # Connected after the card's own toggle, so this sees the new state.
            card.card.expandButton.clicked.connect(lambda *_, card=card: self._label_expand_button(card))

        _ResizeWatcher(self.folder_card, self._show_folder)  # its width decides how much of the path fits
        config.saveLocation.valueChanged.connect(self._show_folder)
        config.outputFolder.valueChanged.connect(self._show_folder)
        self.folder_card.clicked.connect(self._choose_folder)
        self.theme_card.optionChanged.connect(self._apply_theme)

    def _build_layout(self) -> None:
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setViewportMargins(0, HEADER_HEIGHT, 0, 20)
        self.setWidget(self.scroll_widget)
        self.setWidgetResizable(True)
        self.setStyleSheet(
            "#settingsPage { border: none; background: transparent; }"
            " #settingsScrollWidget { background: transparent; }"
        )

        self.clean_group.addSettingCards([self.mode_card, self.save_card, self.folder_card])
        self.appearance_group.addSettingCard(self.theme_card)
        self.expand_layout.setSpacing(28)
        self.expand_layout.addWidget(self.clean_group)
        self.expand_layout.addWidget(self.appearance_group)
        self._center_column()

    def _center_column(self) -> None:
        side = max(SIDE_MARGIN, (self.width() - MAX_CONTENT_WIDTH) // 2)
        self.expand_layout.setContentsMargins(side, 4, side, 0)
        self.title_label.move(side, TITLE_TOP)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._center_column()

    @staticmethod
    def _label_expand_button(card: OptionsSettingCard) -> None:
        """The chevron that opens a card's options is only an icon: it says what it does (spec 6)."""
        text = HIDE_OPTIONS_TEXT if card.isExpand else SHOW_OPTIONS_TEXT
        card.card.expandButton.setToolTip(text)
        card.card.expandButton.setAccessibleName(f"{text}: {card.card.titleLabel.text()}")

    # -- the folder for the copies ---------------------------------------------------------

    def _show_folder(self, *_) -> None:
        """The folder card says which folder is chosen, and is usable only when a folder is wanted.

        A path too long for the card is cut in the middle and whole in the
        tooltip (spec 6), so it never runs under the button.
        """
        cfg = self._config
        card = self.folder_card
        folder = cfg.get(cfg.outputFolder)
        text = folder or NO_FOLDER_TEXT
        if folder and card.width() >= _LAID_OUT_WIDTH:
            room = card.width() - card.button.width() - _FOLDER_CARD_CHROME
            text = card.contentLabel.fontMetrics().elidedText(folder, Qt.TextElideMode.ElideMiddle, room)
        card.setContent(text)
        card.setToolTip(folder)
        card.setEnabled(cfg.get(cfg.saveLocation) == "folder")

    def _choose_folder(self) -> None:
        cfg = self._config
        current = cfg.get(cfg.outputFolder)
        start = current if current and os.path.isdir(current) else ""
        folder = QFileDialog.getExistingDirectory(self, FOLDER_BUTTON_TEXT, start)
        if folder:
            cfg.set(cfg.outputFolder, os.path.normpath(folder))

    # -- theme --------------------------------------------------------------------------------

    def _apply_theme(self, item) -> None:
        setTheme(self._config.get(item))
