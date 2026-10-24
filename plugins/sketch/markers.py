"""Screen-sized native bitmap markers, drawn here rather than taken from a font.

Visual reference: SOLIDWORKS 2025 SP0, AulaVirtualTrust, Modulo 9.
The colored masks do not overlap, so rendering order cannot hide the symbol.
OCCT keeps the single assembly owner for selection of a relation.
"""
from functools import lru_cache

from OCC.Core.AIS import AIS_Point, AIS_MultipleConnectedInteractive
from OCC.Core.Aspect import Aspect_TOM_USERDEFINED
from OCC.Core.Geom import Geom_CartesianPoint
from OCC.Core.Graphic3d import Graphic3d_ZLayerId_Topmost
from OCC.Core.Prs3d import Prs3d_PointAspect
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_sRGB
from OCC.Core.TColStd import TColStd_HArray1OfByte
from OCC.Core.gp import gp_Pnt, gp_Trsf, gp_Vec


# Twelve logical pixels, including the green background. No font substitution.
SYMBOLS = {
    "horizontal": ((2, 5, 10, 7),),
    "vertical": ((5, 2, 7, 10),),
    "coincident": ((4, 4, 8, 8),),
    "point_on_line": ((2, 8, 10, 9), (5, 4, 7, 8)),
    "perpendicular": ((5, 2, 7, 8), (2, 8, 10, 10)),
}


def symbol_pixel(kind, x, y):
    if kind in SYMBOLS:
        return any(a <= x < c and b <= y < d for a,b,c,d in SYMBOLS[kind])
    if kind == "parallel":
        return (2 <= y < 10 and (abs(x-(y*.5+1)) < .7 or abs(x-(y*.5+5)) < .7))
    if kind == "fixed":
        # Anchor: ring, stem, flukes. Same symbol in the viewport and toolbar.
        return ((4 <= x < 8 and 1 <= y < 4 and (x in (4,7) or y in (1,3)))
                or (5 <= x < 7 and 4 <= y < 10)
                or (2 <= x < 10 and y == 9)
                or (x in (2,9) and 6 <= y < 9))
    raise ValueError(f"Symbole de relation inconnu : {kind}")


@lru_cache(maxsize=96)
def bitmap(kind, size, foreground=True):
    """OCCT 1-bit masks: MSB first, padded rows, bottom row first."""
    stride = (size+7)//8
    data = bytearray(stride*size)
    for y in range(size):
        for x in range(size):
            u,v = (x+.5)*12/size,(y+.5)*12/size
            if kind == "point":
                on = (u-6)**2+(v-6)**2 <= 36
            elif kind == "selected_point":
                on = True
            else:
                on = symbol_pixel(kind,int(u),int(v)) == foreground
            if on:
                data[(size-1-y)*stride+x//8] |= 1 << (7-x%8)
    result = TColStd_HArray1OfByte(0,len(data)-1)
    for i,value in enumerate(data):
        result.SetValue(i,value)
    return result


def point_aspect(color, ratio, *, selected=False, hover=False):
    size = max(1,round((7 if hover else 6 if selected else 5)*ratio))
    return Prs3d_PointAspect(color,size,size,bitmap("selected_point" if selected else "point",size))


def relation_marker(kind, position, ratio, theme, selected=False):
    size = max(1,round(12*ratio))
    colors = ((1.,.6,.1),(.12,.13,.15)) if selected else ((0.,.65,.18),(.08,.12,.09))
    assembly = AIS_MultipleConnectedInteractive()
    assembly.SetDisplayMode(0)  # Points have mode 0, even in a shaded viewport.
    placement = gp_Trsf()
    placement.SetTranslation(gp_Vec(position.X(),position.Y(),position.Z()))
    assembly.SetLocalTransformation(placement)
    for foreground,rgb in ((False,colors[0]),(True,colors[1])):
        part = AIS_Point(Geom_CartesianPoint(gp_Pnt()))
        part.SetMarker(Aspect_TOM_USERDEFINED)
        part.Attributes().SetPointAspect(Prs3d_PointAspect(
            Quantity_Color(*rgb,Quantity_TOC_sRGB),size,size,bitmap(kind,size,foreground)))
        part.SetInfiniteState(True)
        part.SetZLayer(Graphic3d_ZLayerId_Topmost)
        instance = assembly.Connect(part)
        instance.SetZLayer(Graphic3d_ZLayerId_Topmost)
    return assembly


@lru_cache(maxsize=48)
def relation_icon(kind, ratio, theme):
    """The same drawn symbols on transparent Qt buttons, without Unicode."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
    size = max(1,round(16*ratio))
    image = QImage(size,size,QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    ink = QColor("#e1e6ed" if theme == "dark" else "#202326")
    for y in range(size):
        for x in range(size):
            u,v = (x+.5)*16/size-2,(y+.5)*16/size-2
            if 0 <= u < 12 and 0 <= v < 12 and symbol_pixel(kind,int(u),int(v)):
                image.setPixelColor(x,y,ink)
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return QIcon(pixmap)
