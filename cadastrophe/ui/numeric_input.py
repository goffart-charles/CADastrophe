"""Native decimal editors accepting either decimal separator without grouping."""
from PySide6.QtCore import QLocale
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
