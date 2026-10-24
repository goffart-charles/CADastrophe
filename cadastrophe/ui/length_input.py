"""A unit-aware editor retaining exact mm until the displayed value changes."""
from PySide6.QtCore import QSignalBlocker
from .equation_input import EquationSpinBox
from math import isfinite

from ..core.units import DisplayUnits


class LengthSpinBox(EquationSpinBox):
    def __init__(self, units: DisplayUnits, lower_mm, upper_mm, value_mm, parent=None):
        super().__init__(parent)
        self.units = units
        self._lower_mm, self._upper_mm = lower_mm, upper_mm
        self.setDecimals(units.editor_decimals)
        self.setRange(units.to_display(lower_mm), units.to_display(upper_mm))
        self.setSuffix(" " + units.unit)
        self.setKeyboardTracking(False)
        self.set_internal_value(value_mm)
        self.valueChanged.connect(self._changed)

    def _changed(self, value):
        self._internal_value = self.units.to_internal(value)

    def set_internal_value(self, millimetres):
        if (type(millimetres) not in (int, float) or not self._lower_mm <= millimetres <= self._upper_mm
                or not isfinite(millimetres)):
            raise ValueError("Longueur hors des limites du champ.")
        blocker = QSignalBlocker(self)
        self.setValue(self.units.to_display(millimetres))
        self._internal_value = millimetres
        del blocker

    @property
    def internal_value(self):
        return self._internal_value
