"""Native decimal editors accepting either decimal separator without grouping."""
from PySide6.QtCore import QLocale, QSignalBlocker
from PySide6.QtWidgets import QDoubleSpinBox


class DecimalSpinBox(QDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        locale = self.locale()
        locale.setNumberOptions(locale.numberOptions() | QLocale.NumberOption.RejectGroupSeparator
                                | QLocale.NumberOption.OmitGroupSeparator)
        self.setLocale(locale)

    def decimal_text(self, text):
        return text.replace(",", ".").replace(".", self.locale().decimalPoint())

    def validate(self, text, pos):
        return super().validate(self.decimal_text(text), pos)

    def valueFromText(self, text):
        return super().valueFromText(self.decimal_text(text))

    def fixup(self, text):
        return super().fixup(self.decimal_text(text))

    def interpretText(self):
        editor = self.lineEdit()
        editing = self.hasFocus() or editor.hasFocus()
        text, cursor = editor.text(), editor.cursorPosition()
        start, selected = editor.selectionStart(), editor.selectedText()
        modified = editor.isModified()
        super().interpretText()
        # A live preview may accept the value while the user is still typing.
        # Keep their decimal separator and cursor until focus leaves the field.
        if editing and editor.text() != text:
            with QSignalBlocker(editor):
                editor.setText(text)
                editor.setCursorPosition(cursor)
                if start >= 0:
                    if cursor == start:
                        editor.setSelection(start+len(selected), -len(selected))
                    else:
                        editor.setSelection(start, len(selected))
                editor.setModified(modified)
