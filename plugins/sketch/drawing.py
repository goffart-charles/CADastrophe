"""Click-based drafting state, independent of Qt/OCCT and document mutations.

Arc convention: center, start, end direction; CCW in the support's U/V frame.
An accepted candidate is advanced only AFTER the document commit succeeds.
"""
from math import atan2, hypot, pi

from .model import Arc, Circle, Entity, Point, Segment, Vec2, new_id

TOOLS = ("point", "line", "construction_line", "rectangle", "circle", "arc", "slot", "polygon", "ellipse", "spline", "arc_three", "arc_tangent")
LINE_TOOLS = ("line", "construction_line")
LENGTH_EPSILON = 1e-9  # mm, degeneracy guard, NOT snapping or an inferred constraint


def distance(first: Vec2, second: Vec2) -> float:
    return hypot(second.u - first.u, second.v - first.v)


class DrawingController:
    def __init__(self):
        self.tool: str | None = None
        self.anchors: tuple[Vec2, ...] = ()
        self.current: Vec2 | None = None
        self._ids = tuple(new_id() for _ in range(64))
        self.polygon_sides = 6
        self.polygon_inscribed = False
        self.spline_closed = False
        self.tangent = None

    @property
    def state(self) -> str:
        return "drawing" if self.tool is not None and self.anchors else "editing"

    def select(self, tool: str | None):
        if tool is not None and tool not in TOOLS:
            raise ValueError("Outil de dessin inconnu.")
        self.tool = tool
        self.reset()

    def reset(self):
        """Discard the pending primitive, but keep the chosen tool."""
        self.anchors = ()
        self.current = None
        self._ids = tuple(new_id() for _ in range(64))

    def candidate(self, point: Vec2) -> tuple[Entity, ...]:
        if self.tool == "point":
            return (Point(point, id=self._ids[0]),)
        if not self.anchors or self.tool is None:
            return ()
        first = self.anchors[0]
        if self.tool not in (*LINE_TOOLS, "rectangle", "circle", "arc"):
            from .extra_drawing import candidate
            return candidate(self, point)
        if self.tool == "rectangle":
            if abs(point.u - first.u) <= LENGTH_EPSILON or abs(point.v - first.v) <= LENGTH_EPSILON:
                raise ValueError("Choisir deux coins distincts en U et V.")
            corners = (first, Vec2(point.u, first.v), point, Vec2(first.u, point.v))
            return tuple(Segment(corners[i], corners[(i+1) % 4], id=self._ids[i]) for i in range(4))
        radius = distance(first, point)
        if radius <= LENGTH_EPSILON:
            raise ValueError("Choisir un point distinct du précédent.")
        if self.tool in LINE_TOOLS:
            return (Segment(first, point, id=self._ids[0], construction=self.tool == "construction_line"),)
        if self.tool == "circle":
            return (Circle(first, radius, id=self._ids[0]),)
        if len(self.anchors) == 1:
            return (Segment(first, point, id=self._ids[0]),)  # temporary radius guide
        start = self.anchors[1]
        start_angle = atan2(start.v - first.v, start.u - first.u)
        end_angle = atan2(point.v - first.v, point.u - first.u)
        sweep = (end_angle - start_angle) % (2*pi)
        if min(sweep, 2*pi - sweep) <= 1e-9:
            raise ValueError("La fin doit définir un arc non nul ; pour un tour complet, utiliser Cercle.")
        return (Arc(first, distance(first, start), start_angle, sweep, id=self._ids[0]),)

    def click(self, point: Vec2) -> tuple[Entity, ...]:
        """Collect anchors or propose a complete primitive. Never mutate a document."""
        if self.tool is None:
            return ()
        self.current = point
        if self.tool == "point":
            self.anchors = (point,)
            return self.candidate(point)
        if self.tool == "spline":
            if self.anchors and distance(self.anchors[-1], point) <= LENGTH_EPSILON:
                raise ValueError("Choisissez un pôle distinct ; Entrée termine la spline.")
            if len(self.anchors) >= 64:
                raise ValueError("Une spline est limitée à 64 pôles ; terminez-la avec Entrée.")
            self.anchors = (*self.anchors, point)
            return ()
        if not self.anchors:
            self.anchors = (point,)
            return ()
        result = self.candidate(point)  # invalid clicks keep all accepted anchors
        if self.tool in ("arc", "arc_three", "slot", "ellipse") and len(self.anchors) == 1:
            self.anchors = (*self.anchors, point)
            return ()
        return result

    def committed(self, point: Vec2):
        if self.tool is None or not self.anchors:
            raise ValueError("Aucune primitive à poursuivre.")
        chained = self.tool in LINE_TOOLS
        self.reset()
        self.anchors = (point,) if chained else ()
        self.current = point
