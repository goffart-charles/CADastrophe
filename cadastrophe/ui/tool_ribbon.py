"""A compact native Qt command strip; presentation only, no CAD logic."""
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QTabWidget, QScrollArea, QWidget, QHBoxLayout,
    QVBoxLayout, QToolButton, QSizePolicy, QLabel)
from .themes import theme_tokens


class ToolRibbon(QTabWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toolRibbon")
        self.setAccessibleName("Ateliers de conception")
        self.setDocumentMode(True)
        self.setTabPosition(QTabWidget.TabPosition.North)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        tokens = theme_tokens()
        self._pages: dict[str, QHBoxLayout] = {}
        for title in ("Fonctions", "Esquisse", "Affichage", "Évaluation"):
            scroll = QScrollArea(self)
            scroll.setProperty("role", "commandPage")
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setWidgetResizable(True)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            page = QWidget()
            page.setProperty("role", "commandContent")
            row = QHBoxLayout(page)
            row.setContentsMargins(tokens.space, tokens.space, tokens.space, tokens.space)
            row.setSpacing(tokens.space)
            row.addStretch()
            scroll.setWidget(page)
            self._pages[title] = row
            self.addTab(scroll, title)
        self.setCurrentIndex(0)

    def sizeHint(self) -> QSize:
        # Reserve a scrollbar row even before a narrow-window overflow occurs.
        height = max(theme_tokens().command_height, self.fontMetrics().height() * 4 + 44)
        return QSize(700, height)

    def minimumSizeHint(self) -> QSize:
        return QSize(300, self.sizeHint().height())

    def add_group(self, tab: str, title: str) -> QHBoxLayout:
        group = QWidget()
        group.setProperty("role", "commandGroup")
        group.setAccessibleName(title)
        group.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(5, 3, 5, 3)
        layout.setSpacing(2)
        label = QLabel(title.upper())
        label.setProperty("role", "commandGroupTitle")
        layout.addWidget(label)
        body = QWidget(group)
        body.setObjectName("groupTools")
        row = QHBoxLayout(body)
        space = theme_tokens().space
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(space)
        row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(body, 1)
        page = self._pages[tab]
        page.insertWidget(page.count() - 1, group)
        return row

    @staticmethod
    def remove_group(row):
        group = row.parentWidget().parentWidget()
        group.parentWidget().layout().removeWidget(group)
        group.hide()
        group.deleteLater()

    @staticmethod
    def add_action(row: QHBoxLayout, action: QAction) -> QToolButton:
        button = QToolButton()
        button.setProperty("commandButton", True)
        size = theme_tokens().icon_command
        button.setIconSize(QSize(size, size))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setDefaultAction(action)
        ToolRibbon._configure_button(button, action)
        button.setFixedHeight(max(66, button.fontMetrics().height() * 2 + size + 8))
        row.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        return button

    @staticmethod
    def add_column(row: QHBoxLayout, actions: tuple[QAction, ...]) -> QWidget:
        column = QWidget()
        column.setProperty("role", "commandColumn")
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for action in actions:
            button = QToolButton()
            button.setProperty("commandCompact", True)
            button.setDefaultAction(action)
            button.setIconSize(QSize(theme_tokens().icon_small, theme_tokens().icon_small))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            ToolRibbon._configure_button(button, action)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setFixedHeight(max(24 if button.property("sketchTool") else 22, button.fontMetrics().height() + 6))
            layout.addWidget(button)
        row.addWidget(column, 0, Qt.AlignmentFlag.AlignVCenter)
        return column

    @staticmethod
    def _configure_button(button, action):
        button.setAccessibleName(action.text())
        if action.property("iconOnly"):
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        if action.property("sketchTool"):
            button.setProperty("sketchTool", True)
            button.setIconSize(QSize(18, 18))
            button.setMinimumWidth(30)
        menu = action.property("commandMenu") or action.menu()
        if menu is not None:
            if action.property("sketchTool"):
                button.setProperty("hasToolMenu", True)
                button.setMinimumWidth(46)
            if menu.parent() is None:
                menu.setParent(button, menu.windowFlags())
            button.setMenu(menu)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            button.setDefaultAction(action)
