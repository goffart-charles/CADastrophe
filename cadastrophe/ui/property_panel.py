"""Common parameter panel and collapsible sections, hosted by interactive tools."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QToolButton,
    QLabel, QDialogButtonBox, QScrollArea, QFrame, QSizePolicy)
from .icons import icon
from .themes import theme_tokens
from .feedback import set_feedback


class PropertySection(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.header = QToolButton()
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.setProperty("role", "sectionHeader")
        self.header.setText(title)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setCheckable(True)
        self.header.setChecked(True)
        self.header.setArrowType(Qt.ArrowType.DownArrow)
        self.content = QWidget()
        self.form = QFormLayout(self.content)
        space = theme_tokens().space
        self.form.setContentsMargins(space * 2, space * 2, space * 2, space * 2)
        self.form.setSpacing(space)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(self.content)
        self.header.toggled.connect(self.set_expanded)

    def set_expanded(self, expanded: bool):
        self.header.setChecked(expanded)
        self.header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.content.setVisible(expanded)


class PropertyPanel(QWidget):
    accepted = Signal()
    rejected = Signal()

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setProperty("role", "propertyPanel")
        space = theme_tokens().space
        layout = QVBoxLayout(self)
        layout.setContentsMargins(space * 2, space, space * 2, space * 2)
        layout.setSpacing(space)
        heading = QLabel(title)
        heading.setProperty("role", "panelTitle")
        layout.addWidget(heading)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setProperty("role", "propertyContent")
        self.sections = QVBoxLayout(content)
        self.sections.setContentsMargins(0, 0, 0, 0)
        self.sections.setSpacing(space)
        self.sections.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        accept = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        accept.setText("Valider")
        accept.setProperty("role", "accept")
        accept.setIcon(icon("check"))
        cancel = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel.setText("Annuler")
        cancel.setProperty("role", "cancel")
        cancel.setIcon(icon("cancel"))
        self.buttons.accepted.connect(self.accepted.emit)
        self.buttons.rejected.connect(self.rejected.emit)
        layout.addWidget(self.buttons)

    def set_editor(self, editor):
        self.sections.insertWidget(0, editor)

    def set_validation(self, enabled, message, severity="info"):
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(enabled)
        set_feedback(self.status, message, severity)

    def add_section(self, title: str) -> PropertySection:
        section = PropertySection(title)
        self.sections.insertWidget(self.sections.count() - 1, section)
        return section
