"""Convert shaped Qt glyph outlines to portable, exact sketch primitives."""
from math import cos, sin
from dataclasses import replace

from PySide6.QtGui import QFont, QPainterPath
from .model import Segment, Spline, Vec2, new_id


def text_entities(text, family, height, position=Vec2(0, 0), rotation=0.):
    if not text.strip() or len(text) > 100:
        raise ValueError("Saisissez un texte de 1 à 100 caractères visibles.")
    if not 1e-6 < height <= 1e6:
        raise ValueError("La hauteur des contours doit être positive.")
    font = QFont(family)
    font.setPixelSize(1000)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    path = QPainterPath()
    path.addText(0., 0., font, text)
    bounds = path.boundingRect()
    if bounds.height() <= 0:
        raise ValueError("Cette police ne fournit pas de contours pour ce texte.")
    scale = height/bounds.height()
    def point(element):
        x, y = (element.x-bounds.left())*scale, (bounds.bottom()-element.y)*scale
        return Vec2(position.u+x*cos(rotation)-y*sin(rotation), position.v+x*sin(rotation)+y*cos(rotation))
    result, current, start = [], None, None
    i = 0
    while i < path.elementCount():
        item = path.elementAt(i)
        p = point(item)
        if item.isMoveTo():
            if current is not None and current != start:
                result.append(Segment(current, start))
            current = start = p
        elif item.isLineTo():
            if current != p:
                result.append(Segment(current, p))
            current = p
        elif item.isCurveTo():
            second, end = point(path.elementAt(i+1)), point(path.elementAt(i+2))
            result.append(Spline((current, p, second, end), degree=3, knots=(0.,)*4+(1.,)*4))
            current = end
            i += 2
        else:
            raise ValueError("Contour de caractère non pris en charge.")
        if len(result) > 512:
            raise ValueError("Le texte dépasse 512 courbes ; raccourcissez-le ou changez de police.")
        i += 1
    if current is not None and current != start:
        result.append(Segment(current, start))
    if not result:
        raise ValueError("Le texte ne contient aucun contour.")
    group = new_id()
    return tuple(replace(e, text_group=group) for e in result)
