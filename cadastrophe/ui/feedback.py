"""Theme-aware, explicit severity for inline problem messages."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel


def set_feedback(label, text, severity="error"):
    label.setTextFormat(Qt.TextFormat.PlainText)
    QLabel.setText(label, text)
    level = severity if text else "info"
    if label.property("severity") != level:
        label.setProperty("severity", level)
        label.style().unpolish(label)
        label.style().polish(label)
        label.updateGeometry()


class ErrorLabel(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setProperty("severity", "error" if text else "info")

    def setText(self, text):
        set_feedback(self, text)

    def clear(self):
        self.setText("")


class FeedbackLabel(QLabel):
    def setText(self, text):
        set_feedback(self, text, "info")

    def clear(self):
        self.setText("")
