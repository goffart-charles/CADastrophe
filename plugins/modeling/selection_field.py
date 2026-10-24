"""A focused selection collector, populated by picking instead of a dropdown."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLineEdit, QToolButton


class SelectionField(QWidget):
    activated = Signal()
    cleared = Signal()

    def __init__(self, placeholder):
        super().__init__()
        self.value = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.edit = _Collector()
        self.edit.setReadOnly(True)
        self.edit.setPlaceholderText(placeholder)
        self.edit.activated.connect(self.activated.emit)
        self.clear_button = QToolButton()
        self.clear_button.setText("×")
        self.clear_button.setToolTip("Effacer la sélection")
        self.clear_button.clicked.connect(self.cleared.emit)
        layout.addWidget(self.edit)
        layout.addWidget(self.clear_button)
        self.setFocusProxy(self.edit)

    def set_value(self, value, label=""):
        self.value = value
        self.edit.setText(label)
        self.edit.setToolTip(label or self.edit.placeholderText())
        self.clear_button.setEnabled(value is not None)

    def set_active(self, active):
        self.edit.setStyleSheet("QLineEdit { border: 2px solid #53a56d; }" if active else "")


class _Collector(QLineEdit):
    activated = Signal()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.activated.emit()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.activated.emit()
