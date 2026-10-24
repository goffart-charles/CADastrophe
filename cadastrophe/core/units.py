"""Length display conversions only: geometry always stays in millimetres."""
from dataclasses import dataclass
from math import ceil, log10

MM_PER_UNIT = {"mm": 1., "cm": 10., "m": 1000., "in": 25.4}


@dataclass(frozen=True)
class DisplayUnits:
    unit: str = "mm"
    decimals: int = 3

    def __post_init__(self):
        if self.unit not in MM_PER_UNIT or type(self.decimals) is not int or not 0 <= self.decimals <= 6:
            raise ValueError("Unité ou précision d’affichage invalide.")

    def to_display(self, millimetres):
        return millimetres / MM_PER_UNIT[self.unit]

    def to_internal(self, value):
        return value * MM_PER_UNIT[self.unit]

    @property
    def editor_decimals(self):
        return max(self.decimals, 3 + ceil(log10(MM_PER_UNIT[self.unit])))

    def format_length(self, millimetres):
        return f"{self.to_display(millimetres):.{self.decimals}f} {self.unit}"

    def format_area(self, square_millimetres):
        value = square_millimetres / MM_PER_UNIT[self.unit] ** 2
        return f"{value:.{self.decimals}f} {self.unit}²"

    def format_volume(self, cubic_millimetres):
        value = cubic_millimetres / MM_PER_UNIT[self.unit] ** 3
        return f"{value:.{self.decimals}f} {self.unit}³"

    def format_mass(self, grams):
        return f"{grams:.{self.decimals}f} g"
