"""Angle UI in degrees; persistent values and solver equations use radians."""
from math import degrees, radians, isfinite
from PySide6.QtCore import QSignalBlocker
from cadastrophe.plugin_api import EquationSpinBox


class AngleSpinBox(EquationSpinBox):
    radian_value = True
    def __init__(self, value=1.):
        super().__init__()
        self.setDecimals(6)
        self.setRange(.000001, 179.999999)
        self.setSuffix("°")
        self.setKeyboardTracking(False)
        self.set_internal_value(value)
        self.valueChanged.connect(self._changed)

    def _changed(self, value):
        self._internal_value = radians(value)

    @property
    def internal_value(self):
        return self._internal_value

    def set_internal_value(self, value):
        if type(value) not in (int,float) or not isfinite(value) or not self.minimum() <= degrees(value) <= self.maximum():
            raise ValueError("Angle hors des limites du champ (0,000001° à 179,999999°).")
        blocker = QSignalBlocker(self)
        self.setValue(degrees(value))
        self._internal_value = value
        del blocker
