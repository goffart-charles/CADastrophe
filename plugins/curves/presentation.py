"""Owned native edge presentation; parameters stay in the document."""
from OCC.Core.AIS import AIS_Shape
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_sRGB
from cadastrophe.core.curve_geometry import curve_edge


class CurvePresentation:
    def __init__(self, surface):
        self.surface = surface
        self.ais = None
        self.shape = None
        self._hovered = False

    def owns(self, ais):
        return ais == self.ais

    def set_hover(self, hovered):
        self._hovered = hovered
        if self.ais is not None:
            color = (1., .5, 0.) if hovered else self._color
            self.ais.SetColor(Quantity_Color(*color, Quantity_TOC_sRGB))
            self.surface.context.Redisplay(self.ais, False)

    def update(self, obj, *, selected=False, editing=False):
        try:
            shape = curve_edge(self.surface.document, obj)
        except (ValueError, RuntimeError):
            self.clear()
            return
        if self.shape is None or not shape.IsSame(self.shape):
            self.clear()
            self.shape = shape
            self.ais = AIS_Shape(shape)
            self.ais.SetWidth(2*self.surface.pixel_ratio)
        self._color = (.1, .7, 1.) if selected else obj.color
        color = (1., .5, 0.) if self._hovered else self._color
        self.ais.SetColor(Quantity_Color(*color, Quantity_TOC_sRGB))
        if obj.visible:
            self.surface.context.Display(self.ais, 0, 0, False)
        else:
            self.surface.context.Erase(self.ais, False)

    def clear(self):
        if self.ais is not None:
            self.surface.context.Remove(self.ais, False)
        self.ais = self.shape = None
