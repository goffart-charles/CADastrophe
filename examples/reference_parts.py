"""Four editable reference parts, using only the existing modeling recipes."""
from dataclasses import dataclass, replace
from math import pi

from cadastrophe.document import ObjectType
from cadastrophe.geometry import Vec3
from cadastrophe.core.feature_model import ExtrusionParameters, Operation
from cadastrophe.core.operation_model import GeometryReference, ReplayParameters, RevolutionParameters
from examples.parametric_example import ParametricExample, polygon
from plugins.sketch.constraints import Constraint, Reference
from plugins.sketch.model import Circle, Segment, Vec2


@dataclass(frozen=True)
class ReferencePart:
    key: str
    name: str
    feature_count: int
    edited_feature: str
    initial_depth: float
    variant_depth: float

    def expected_volume(self, depth):
        # Independent analytic volumes, in mm³; no evaluator result is reused.
        if self.key == "boitier":
            return 100*70*depth - 92*62*27 - pi*3**2*(depth-27)
        if self.key == "support":
            return 80*50*depth + 8*50*(48-depth) - 2*pi*3**2*depth
        if self.key == "poignee":
            return 120*20*depth - 96*20*28 - 2*pi*3**2*depth
        if self.key == "raccord":
            return pi*((16**2-10**2)*40 + (22**2-16**2)*depth - (16**2-14**2)*4)
        raise ValueError(self.key)

    def expected_bounds(self, depth):
        return {
            "boitier": (0, 0, 0, 100, 70, depth),
            "support": (0, 0, 0, 80, 50, 48),
            "poignee": (0, 0, 0, 120, 20, depth),
            "raccord": (-22, -22, 0, 22, 22, 40),
        }[self.key]

    def probes(self, depth):
        """Interior/exterior material samples, away from all boundaries."""
        return {
            "boitier": (
                ((2, 35, depth-1), True), ((20, 20, (depth-27)/2), True),
                ((20, 20, depth-10), False), ((50, 35, (depth-27)/2), False),
                ((20, 20, 5), depth > 30),
            ),
            "support": (
                ((4, 25, 40), True), ((40, 25, depth/2), True),
                ((60, 12.5, depth/2), False), ((60, 37.5, depth/2), False),
                ((40, 25, 10), depth > 8),
            ),
            "poignee": (
                ((60, 10, 14), False), ((60, 10, depth-4), True),
                ((10, 10, 14), True), ((110, 10, 14), True),
                ((6, 10, depth/2), False), ((114, 10, depth/2), False),
                ((60, 10, 40), depth > 36),
            ),
            "raccord": (
                ((0, 0, 20), False), ((12, 0, 20), True),
                ((15, 0, 27), False), ((13, 0, 27), True),
                ((20, 0, depth/2), True), ((20, 0, 10), depth > 8),
            ),
        }[self.key]


PARTS = (
    ReferencePart("boitier", "Boîtier", 3, "base", 30, 40),
    ReferencePart("support", "Support mécanique", 4, "base", 8, 12),
    ReferencePart("poignee", "Poignée", 4, "base", 36, 44),
    ReferencePart("raccord", "Raccord", 3, "collar", 8, 12),
)


def rectangle(model, key, name, support, corners):
    """Place XY coordinates in the actual support frame, including face datums."""
    x0, y0, x1, y1 = corners
    z = support.plane.origin.z
    points = (support.plane.to_local(Vec3(x, y, z))[:2]
              for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)))
    return model.sketch(key, name, support, polygon(points))


def through_hole(model, support, center, radius=3):
    sketch = model.circle("hole_sketch", "03 · Perçage Ø6", support, center, radius)
    return model.append("hole", "03 · Perçage traversant", Operation.CUT,
                        ExtrusionParameters(depth=None, end_condition="through_all"), sketch)


def build_reference(spec):
    model = ParametricExample(f"{spec.name} · pièce de référence")
    xy = model.plane("XY")
    if spec.key == "boitier":
        outline = rectangle(model, "outline", "01 · Contour extérieur 100 × 70", xy, (0, 0, 100, 70))
        base = model.append("base", "01 · Volume extérieur", Operation.NEW_BODY,
                            ExtrusionParameters(depth=30), outline)
        top = model.face_datum("top_plane", "Plan · ouverture du boîtier", base, 30)
        pocket = rectangle(model, "pocket_sketch", "02 · Cavité 92 × 62", top, (4, 4, 96, 66))
        model.append("pocket", "02 · Poche intérieure 27 mm", Operation.CUT,
                     ExtrusionParameters(depth=27, reversed=True), pocket)
        through_hole(model, xy, (50, 35))
    elif spec.key == "support":
        outline = rectangle(model, "outline", "01 · Semelle 80 × 50", xy, (0, 0, 80, 50))
        base = model.append("base", "01 · Épaisseur de semelle", Operation.NEW_BODY,
                            ExtrusionParameters(depth=8), outline)
        top = model.face_datum("top_plane", "Plan · dessus de la semelle", base, 8)
        web = rectangle(model, "web_sketch", "02 · Montant 8 × 50", xy, (0, 0, 8, 50))
        model.append("web", "02 · Montant vertical 48 mm", Operation.BOSS,
                     ExtrusionParameters(depth=48), web)
        point = top.plane.to_local(Vec3(60, 12.5, 8))
        sketch = model.circle("hole_sketch", "03 · Fixation Ø6", top, point[:2], 3)
        hole = model.append("hole", "03 · Fixation traversante", Operation.CUT,
                            ExtrusionParameters(depth=None, end_condition="through_all", reversed=True), sketch)
        model.append("holes", "04 · Deux fixations · pas 25 mm", Operation.LINEAR_PATTERN,
                     ReplayParameters((hole.id,), GeometryReference("axis", values=(0, 0, 0, 0, 1, 0)),
                                      count=2, spacing=25))
    elif spec.key == "poignee":
        outline = rectangle(model, "outline", "01 · Contour 120 × 20", xy, (0, 0, 120, 20))
        model.append("base", "01 · Hauteur de poignée", Operation.NEW_BODY,
                     ExtrusionParameters(depth=36), outline)
        opening = rectangle(model, "opening_sketch", "02 · Passage de main 96 mm", xy, (12, -1, 108, 21))
        model.append("opening", "02 · Dégagement inférieur 28 mm", Operation.CUT,
                     ExtrusionParameters(depth=28), opening)
        hole = through_hole(model, xy, (6, 10))
        model.append("holes", "04 · Deux fixations · entraxe 108 mm", Operation.LINEAR_PATTERN,
                     ReplayParameters((hole.id,), GeometryReference("axis", values=(0, 0, 0, 1, 0, 0)),
                                      count=2, spacing=108))
    elif spec.key == "raccord":
        xz = model.plane("XZ")
        axis = Segment(Vec2(0, -5), Vec2(0, 45), construction=True)
        profile = model.sketch("turned_profile", "01 · Section Ø32 / alésage Ø20", xz,
            (*polygon(((10, 0), (16, 0), (16, 40), (10, 40))), axis),
            (Constraint("vertical", (Reference(axis.id),)),))
        turn = GeometryReference("sketch_line", profile.id, entity_id=axis.id)
        model.append("base", "01 · Corps tourné 40 mm", Operation.NEW_BODY, RevolutionParameters(turn), profile)
        circles = (Circle(Vec2(0, 0), 10), Circle(Vec2(0, 0), 22))
        constraints = tuple(c for circle in circles for c in (
            Constraint("fixed", (Reference(circle.id, "center"),), Vec2(0, 0)),
            Constraint("diameter", (Reference(circle.id),), 2*circle.radius),
        ))
        collar = model.sketch("collar_sketch", "02 · Collerette Ø44 / Ø20", xy, circles, constraints)
        model.append("collar", "02 · Épaisseur de collerette", Operation.BOSS,
                     ExtrusionParameters(depth=8), collar)
        groove = model.sketch("groove_sketch", "03 · Gorge largeur 4 / fond Ø28", xz,
                             polygon(((14, 25), (17, 25), (17, 29), (14, 29))))
        model.append("groove", "03 · Gorge annulaire", Operation.CUT, RevolutionParameters(turn), groove)
    else:
        raise ValueError(spec.key)
    model.handles["body"] = model.body.id
    for obj in tuple(model.document.objects.values()):
        if obj.kind is not ObjectType.BODY and obj.visible:
            model.document.set_visible(obj.id, False)
    model.document.history.clear()
    return model


def set_depth(model, spec, depth):
    """One ordinary document command; consumers rebuild through the common evaluator."""
    feature = model.document.objects[model.handles[spec.edited_feature]]
    model.document.replace_feature(replace(feature, parameters=replace(feature.parameters, depth=depth)))
