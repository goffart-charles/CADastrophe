"""Compact CAD light theme. No selectors target the OCCT viewport."""
from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, QEvent
from PySide6.QtGui import QPalette, QColor, QFont
from PySide6.QtWidgets import QApplication

from ..icons import ASSETS, command_icon, command_caption


@dataclass(frozen=True)
class ThemeTokens:
    window: str = "#e8eeed"
    panel: str = "#fbfcfa"
    strip: str = "#f2f5f2"
    text: str = "#233a3f"
    muted: str = "#62787b"
    disabled: str = "#899b9d"
    border: str = "#b6c7c7"
    separator: str = "#d2dedd"
    accent: str = "#146e78"
    hover: str = "#e0efec"
    pressed: str = "#b9ddda"
    selected: str = "#d1e9e5"
    selected_text: str = "#124f57"
    inactive_selected: str = "#e5eeeb"
    positive: str = "#327b4b"
    negative: str = "#b44444"
    warning: str = "#8d5700"
    font_family: str = "Segoe UI"
    font_points: int = 9
    space: int = 4
    control_height: int = 23
    tab_height: int = 24
    row_height: int = 19
    command_height: int = 144
    command_button_height: int = 48
    icon_small: int = 16
    icon_command: int = 24
    tree_indent: int = 16
    scroll_extent: int = 12


LIGHT = ThemeTokens()
DARK = replace(LIGHT, window="#18272d", panel="#203238", strip="#1b2d33", text="#e4eeeb",
               muted="#a5bcba", disabled="#819693", border="#526e71", separator="#365057",
               accent="#79d6cf", hover="#2b484e", pressed="#315d63", selected="#31575b",
               selected_text="#dcf8ef", inactive_selected="#30474c", positive="#89d6a1", negative="#ffad94", warning="#ffd080")


def theme_tokens() -> ThemeTokens:
    app = QApplication.instance()
    manager = getattr(app, "_cad_theme", None)
    return manager.tokens if manager else LIGHT


def palette(t: ThemeTokens) -> QPalette:
    result = QPalette()
    roles = {"Window": t.window, "WindowText": t.text, "Base": t.panel,
        "AlternateBase": t.strip, "Text": t.text, "Button": t.strip,
        "ButtonText": t.text, "Highlight": t.selected, "HighlightedText": t.selected_text,
        "ToolTipBase": t.panel, "ToolTipText": t.text, "Link": t.accent,
        "Light": t.panel, "Midlight": t.separator, "Mid": t.border, "Dark": t.muted,
        "PlaceholderText": t.muted}
    for name, value in roles.items():
        result.setColor(getattr(QPalette.ColorRole, name), QColor(value))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        result.setColor(QPalette.ColorGroup.Disabled, role, QColor(t.disabled))
    result.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Highlight, QColor(t.inactive_selected))
    return result


def stylesheet(t: ThemeTokens) -> str:
    arrow_style = "dark" if t == DARK else "light"
    up = (ASSETS / f"spin-up-{arrow_style}.svg").as_posix()
    down = (ASSETS / f"spin-down-{arrow_style}.svg").as_posix()
    return f"""
QMainWindow, QDialog {{ background: {t.window}; }}
QLabel[severity="error"] {{ color: {t.negative}; background: {t.panel};
    border-left: 3px solid {t.negative}; padding: 6px; font-weight: 600; }}
QLabel[severity="warning"] {{ color: {t.warning}; background: {t.panel};
    border-left: 3px solid {t.warning}; padding: 6px; font-weight: 600; }}
QWidget#applicationHeader {{ background: {t.strip}; border-bottom: 1px solid {t.border}; }}
QMenuBar, QToolBar {{ background: {t.strip}; border: none; }}
QMenuBar {{ border-bottom: 1px solid {t.border}; }}
QLabel#applicationMark {{ padding: 4px 0px 4px 10px; }}
QLabel#applicationBrand {{ color: {t.text}; font-family: "{t.font_family}"; font-size: 15px; font-weight: 600;
    font-style: normal; padding: 3px 20px 3px 6px; }}
QFrame#quickAccess {{ background: {t.strip}; }}
QFrame#quickAccess QToolButton {{ padding: 2px 4px; }}
QFrame#viewTools {{ background: {t.panel}; border: 1px solid {t.border}; border-radius: 7px; }}
QFrame#viewTools QToolButton {{ padding: 3px; border-radius: 0px; }}
QFrame[role="stripSeparator"] {{ color: {t.border}; max-width: 1px; margin: 3px; }}
QSplitter::handle {{ background: {t.separator}; }}
QSplitter::handle:hover {{ background: {t.accent}; }}
QMenuBar::item {{ padding: 3px 8px; background: transparent; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {t.selected}; color: {t.selected_text}; }}
QMenuBar::item:pressed {{ background: {t.pressed}; }}
QMenu {{ background: {t.panel}; border: 1px solid {t.border}; padding: 3px; }}
QMenu::item {{ padding: 4px 24px 4px 24px; min-height: 17px; }}
QMenu::item:disabled {{ color: {t.disabled}; }}
QMenu::separator {{ height: 1px; background: {t.separator}; margin: 4px 6px; }}
QToolBar {{ spacing: 2px; padding: 2px; border-bottom: 1px solid {t.border}; }}
QToolBar::separator {{ background: {t.border}; width: 1px; margin: 3px 5px; }}
QToolButton {{ padding: 3px; border: 1px solid transparent; border-radius: 1px; background: transparent; }}
QToolButton:hover {{ background: {t.hover}; border-color: {t.border}; }}
QToolButton:pressed {{ background: {t.pressed}; border-color: {t.accent}; }}
QToolButton:checked {{ background: {t.selected}; border-color: {t.accent}; }}
QToolButton:disabled {{ color: {t.disabled}; background: transparent; border-color: transparent; }}
QToolButton[commandButton="true"] {{ min-height: {t.command_button_height}px; padding: 2px 7px; }}
QToolButton[commandCompact="true"] {{ padding: 1px 3px; text-align: left; }}
QToolButton[hasToolMenu="true"]::menu-button {{ width: 14px;
    border-left: 1px solid {t.separator}; }}
QPushButton {{ min-height: {t.control_height - 6}px; padding: 3px 10px; background: {t.strip};
    border: 1px solid {t.border}; border-radius: 1px; }}
QPushButton:hover {{ background: {t.hover}; border-color: {t.accent}; }}
QPushButton:pressed, QPushButton:checked {{ background: {t.pressed}; }}
QPushButton:default {{ border-color: {t.accent}; }}
QPushButton:disabled {{ color: {t.disabled}; border-color: {t.separator}; }}
QPushButton[role="accept"] {{ color: {t.positive}; border-color: {t.positive}; }}
QPushButton[role="cancel"] {{ color: {t.negative}; }}
QComboBox, QLineEdit, QAbstractSpinBox {{ background: {t.panel}; color: {t.text};
    min-height: {t.control_height - 6}px; padding: 2px 5px; border: 1px solid {t.border}; border-radius: 1px; }}
QComboBox:hover, QLineEdit:hover, QAbstractSpinBox:hover {{ border-color: {t.muted}; }}
QComboBox:focus, QLineEdit:focus, QAbstractSpinBox:focus {{ border-color: {t.accent}; }}
QComboBox:disabled, QLineEdit:disabled, QAbstractSpinBox:disabled {{ color: {t.disabled}; background: {t.strip}; }}
QAbstractSpinBox {{ padding-right: 20px; }}
QAbstractSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right;
    width: 18px; border-left: 1px solid {t.border}; background: {t.strip}; }}
QAbstractSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right;
    width: 18px; border-left: 1px solid {t.border}; background: {t.strip}; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {t.hover}; }}
QAbstractSpinBox::up-arrow {{ image: url('{up}'); width: 8px; height: 5px; }}
QAbstractSpinBox::down-arrow {{ image: url('{down}'); width: 8px; height: 5px; }}
QComboBox QAbstractItemView {{ background: {t.panel}; selection-background-color: {t.selected};
    selection-color: {t.selected_text}; border: 1px solid {t.border}; }}
QTextEdit, QPlainTextEdit {{ background: {t.panel}; border: 1px solid {t.border}; }}
QCheckBox, QRadioButton {{ spacing: {t.space}px; min-height: {t.control_height}px; }}
QCheckBox:disabled, QRadioButton:disabled, QLabel:disabled {{ color: {t.disabled}; }}
QDockWidget::title {{ background: {t.window}; border-bottom: 1px solid {t.border};
    padding: 5px 7px; text-align: left; }}
QMainWindow::separator {{ background: {t.separator}; width: 4px; height: 4px; }}
QMainWindow::separator:hover {{ background: {t.accent}; }}
QTreeView {{ background: {t.panel}; border: none; outline: none; show-decoration-selected: 1; }}
QTreeView::item {{ min-height: {t.row_height}px; padding: 0px 2px; border: 1px solid transparent; }}
QTreeView::item:hover {{ background: {t.hover}; }}
QTreeView::item:selected {{ background: {t.selected}; color: {t.selected_text}; border-color: {t.selected}; }}
QTreeView::item:selected:!active {{ background: {t.inactive_selected}; color: {t.text}; }}
QListWidget[role="selectionReferences"] {{ background: {t.selected}; color: {t.selected_text};
    border: 1px solid {t.border}; }}
QHeaderView::section {{ background: {t.strip}; color: {t.muted}; padding: 3px 6px;
    border: none; border-bottom: 1px solid {t.separator}; }}
QTabWidget::pane {{ border: 1px solid {t.border}; background: {t.panel}; }}
QTabBar::tab {{ background: {t.window}; color: {t.text}; border: 1px solid {t.border};
    min-height: {t.tab_height - 8}px; padding: 4px 12px; margin-right: -1px; }}
QTabBar::tab:selected {{ background: {t.panel}; color: {t.accent}; border-top: 2px solid {t.accent}; padding-top: 3px; }}
QTabBar::tab:hover:!selected {{ background: {t.hover}; }}
QTabWidget#toolRibbon::pane {{ border: none; background: {t.window}; }}
QTabWidget#toolRibbon QTabBar {{ background: {t.strip}; }}
QTabWidget#toolRibbon QTabBar::tab {{ background: {t.strip}; border: none;
    border-bottom: 3px solid transparent; padding: 4px 18px; min-height: 18px; margin-right: 3px; }}
QTabWidget#toolRibbon QTabBar::tab:selected {{ background: {t.hover}; color: {t.accent};
    border-bottom: 3px solid {t.accent}; padding: 4px 18px; font-weight: 600; }}
QTabWidget#managerPane::pane {{ border: none; border-top: 1px solid {t.border}; background: {t.panel}; }}
QTabWidget#managerPane QTabBar {{ background: {t.window}; }}
QTabWidget#managerPane QTabBar::tab {{ min-width: 28px; min-height: 30px; padding: 4px;
    border: none; background: {t.window}; }}
QTabWidget#managerPane QTabBar::tab:selected {{ background: {t.selected}; border-top: 1px solid {t.border};
    border-left: 3px solid {t.accent}; padding: 4px; }}
QLineEdit#treeFilter {{ border: none; border-bottom: 1px solid {t.border}; border-radius: 0px;
    padding: 4px 3px; background: {t.panel}; }}
QTabBar#documentTabs {{ background: {t.window}; border-top: 1px solid {t.border}; }}
QTabBar#documentTabs::tab {{ min-height: 16px; padding: 2px 24px; border-top: none; }}
QTabBar#documentTabs::tab:selected {{ background: {t.panel}; color: {t.text}; border-top: none; padding-top: 2px; font-weight: 600; }}
QLabel[role="secondaryText"] {{ color: {t.muted}; padding: 0px 8px; }}
QLabel[role="units"] {{ color: {t.muted}; padding: 0px 16px; }}
QScrollArea[role="commandPage"], QWidget[role="commandContent"] {{ background: {t.window}; border: none; }}
QGroupBox {{ border: 1px solid {t.border}; margin-top: 10px; padding: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; padding: 0px 4px; color: {t.muted}; }}
QWidget[role="commandGroup"] {{ background: {t.panel}; border: 1px solid {t.separator}; border-radius: 5px; }}
QLabel[role="commandGroupTitle"] {{ color: {t.muted}; font-size: 10px; font-weight: 600;
    padding: 0px 3px; border: none; background: transparent; }}
QWidget#groupTools {{ background: transparent; border: none; }}
QWidget[role="propertyPanel"], QWidget[role="propertyContent"] {{ background: {t.panel}; }}
QLabel[role="panelTitle"] {{ color: {t.selected_text}; font-weight: 600; padding: 5px 2px; }}
QToolButton[role="sectionHeader"] {{ text-align: left; background: {t.strip}; border: none;
    border-top: 1px solid {t.separator}; border-bottom: 1px solid {t.separator}; padding: 5px; }}
QStatusBar {{ background: {t.strip}; border-top: 1px solid {t.border}; }}
QStatusBar::item {{ border: none; }}
QToolTip {{ background: {t.panel}; color: {t.text}; border: 1px solid {t.border}; padding: 5px 7px; }}
QScrollBar:horizontal {{ height: {t.scroll_extent}px; }}
QScrollBar:vertical {{ width: {t.scroll_extent}px; }}
"""


class _Theme(QObject):
    def __init__(self, app: QApplication, tokens: ThemeTokens):
        super().__init__(app)
        self.tokens = tokens
        app.installEventFilter(self)

    def eventFilter(self, watched, event):
        # Registered actions, including later contributions, acquire an icon
        # without changing the command registry or overwriting supplied icons.
        if event.type() == QEvent.Type.ActionAdded:
            action = event.action()
            key = action.objectName()
            if "." in key:
                if action.icon().isNull():
                    action.setIcon(command_icon(key))
                if action.iconText() == action.text():
                    action.setIconText(command_caption(key, action.text()))
        return super().eventFilter(watched, event)


def apply_theme(app: QApplication, tokens: ThemeTokens = LIGHT):
    """Install once before creating windows; all future Qt widgets inherit it."""
    manager = getattr(app, "_cad_theme", None)
    qss = stylesheet(tokens)
    if manager is not None and manager.tokens == tokens and app.styleSheet() == qss:
        return
    if manager is None:
        manager = app._cad_theme = _Theme(app, tokens)
        app.setStyle("Fusion")
    manager.tokens = tokens
    app.setFont(QFont(tokens.font_family, tokens.font_points))
    app.setPalette(palette(tokens))
    app.setStyleSheet(qss)
