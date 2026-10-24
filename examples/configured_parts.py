"""Configured extensions of the delivered native parts, using their UUIDs."""
from dataclasses import dataclass, replace
from math import pi, sqrt
from pathlib import Path

from cadastrophe.document import ObjectType
from cadastrophe.core.body_history import body_result, result_at
from cadastrophe.core.configurations import create, set_override
from cadastrophe.core.expressions import Expression, Variable
from cadastrophe.core.feature_model import BodyStateRef, EdgeParameters, ExtrusionParameters, Operation, SketchInput
from cadastrophe.core.feature_objects import BodyObject, EdgeFeature, ExtrusionFeature, HoleFeature
from cadastrophe.core.hole_model import HoleParameters
from cadastrophe.core.native_naming import capture, subshapes
from cadastrophe.core.operation_model import GeometryReference, PlaneParameters
from cadastrophe.core.parameters import set_expression
from cadastrophe.core.persistence import load_document
from cadastrophe.core.reference_geometry import face_plane, plane_shape, resolve_plane
from cadastrophe.core.reference_objects import DatumPlaneObject
from examples.parametric_example import registry, polygon
from plugins.modeling.edge_geometry import edge_reference
from plugins.modeling.extrusion_service import Extrusions
from plugins.sketch import commands
from plugins.sketch.model import Circle, Point, Vec2
from plugins.sketch.patterns import add_pattern
from plugins.sketch.profile_service import SketchProfiles


ROOT = Path(__file__).resolve().parents[1] / "tests" / "pieces"


@dataclass(frozen=True)
class PartCase:
    key: str
    name: str
    center: tuple
    pitch: float
    count: int
    thickness: float
    height: float
    reinforced_height: float


CASES = (
    PartCase("equerre-fixation", "Équerre", (35., 10.), 15., 3, 8., 8., 10.),
    PartCase("boitier", "Boîtier", (35., 20.), 15., 3, 2., 30., 35.),
    PartCase("bride-atlas", "Bride Atlas", (40/sqrt(2), 40/sqrt(2)), 0., 4, 8., 6., 8.),
)


@dataclass
class ConfiguredPart:
    case: PartCase
    document: object
    service: object
    body_id: str
    original_ids: frozenset
    original_features: tuple
    height_feature: str
    height_variable: str
    diameter_variable: str
    points_id: str
    pattern_id: str
    hole_id: str
    finish_id: str
    support_id: str
    child_id: str
    independent_id: str
    configurations: tuple

    @property
    def codecs(self):
        return registry(self.service)


def build_configured_part(case):
    """Enrich a delivered recipe; never overwrite its before/after projects."""
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.GeomAbs import GeomAbs_Circle
    from OCC.Core.TopoDS import topods
    service = Extrusions(SketchProfiles())
    doc = load_document(ROOT / case.key / "01-piece.cada", registry=registry(service))
    doc.equation_evaluator = service.evaluator
    body, = (o for o in doc.objects.values() if isinstance(o, BodyObject))
    original_ids, original_features = frozenset(doc.objects), body.features
    xy = next(o for o in doc.objects.values() if o.plane and o.plane.name == "XY")
    varying = (next(doc.objects[k] for k in body.features if doc.objects[k].name.startswith("04 · Plot radial"))
               if case.key == "bride-atlas" else doc.objects[body.features[0]])
    height = Variable("Hauteur", Expression("value", "="+str(case.height)))
    diameter = Variable("DiametreGuide", Expression("value", "=4"))
    doc.update_construction((set_expression(varying, "depth", Expression("depth", "=Hauteur")),),
        variables=(height, diameter), scope="common")

    points = commands.create_sketch(doc, xy.id, name="Positions des perçages guidés")
    point = Point(Vec2(*case.center))
    commands.add_entity(doc, points.id, point)
    points = doc.objects[points.id]
    params = dict(count=case.count, du=0., dv=case.pitch) if case.pitch else dict(count=case.count, center=Vec2(0., 0.), angle=2*pi)
    sketch = add_pattern(points.sketch, (point.id,), "linear_pattern" if case.pitch else "circular_pattern", **params)
    doc.update_construction((replace(points, sketch=sketch),), scope="common")
    points = doc.objects[points.id]
    hole = HoleFeature("Perçages guidés associatifs", None, body.color, kind=ObjectType.FEATURE,
        body_id=body.id, operation=Operation.CUT, source=SketchInput(points.id, points.producer_refs),
        parameters=HoleParameters(diameter=4., end_condition="through_all"))
    hole = set_expression(hole, "diameter", Expression("diameter", "=DiametreGuide"))
    doc.update_construction((replace(doc.objects[body.id], features=(*original_features, hole.id)), hole), scope="common")
    state = body_result(doc, body.id)
    matches = []
    for edge in subshapes(state.shape, "Edge"):
        curve = BRepAdaptor_Curve(topods.Edge(edge))
        if curve.GetType() == GeomAbs_Circle:
            circle = curve.Circle()
            p = circle.Location()
            if abs(p.X()-case.center[0]) < 1e-6 and abs(p.Y()-case.center[1]) < 1e-6 and abs(p.Z()) < 1e-6:
                matches.append(edge)
    if len(matches) != 1:
        raise ValueError(f"Entrée du premier perçage non unique : {len(matches)}.")
    edge, = matches
    finish = EdgeFeature("Chanfrein du premier perçage guidé", None, body.color, kind=ObjectType.FEATURE,
        body_id=body.id, operation=Operation.CHAMFER,
        parameters=EdgeParameters((edge_reference(state.shape, edge),), size=.25,
                                 references=(capture(state.shape, edge, "Edge", state.topology),)))
    doc.update_construction((replace(doc.objects[body.id], features=(*doc.objects[body.id].features, finish.id)), finish), scope="common")

    # A consumer of the exact initial prefix, even when hollowing removes that
    # face from the final body. Coordinates never substitute for its identity.
    producer = original_features[0]
    prefix = result_at(doc, BodyStateRef(body.id, producer))
    z = 8. if case.key == "bride-atlas" else case.height
    caps = []
    for face in subshapes(prefix.shape, "Face"):
        try:
            plane = face_plane(face)
        except ValueError:
            continue
        if plane.normal.z > .99 and abs(plane.origin.z-z) < 1e-6:
            caps.append(face)
    if len(caps) != 1:
        raise ValueError("Le support supérieur doit être unique.")
    reference = GeometryReference("face", body.id, producer,
        capture(prefix.shape, caps[0], "Face", prefix.topology))
    support = DatumPlaneObject("Support partagé du second corps", None, (.4, .6, .7),
        plane=xy.plane, parameters=PlaneParameters("offset", (reference,), distance=0), visible=False)
    frame = resolve_plane(doc, support)
    support = replace(support, plane=frame, shape=plane_shape(frame))
    doc.add(support)
    child_profile = commands.create_sketch(doc, support.id, name="Profil du second corps")
    commands.add_entity(doc, child_profile.id, Circle(Vec2(15., 15.), 3.))
    child_profile = doc.objects[child_profile.id]
    child = BodyObject("Second corps lié", None, (.7, .45, .2))
    feature = ExtrusionFeature("Hauteur partagée du second corps", None, child.color, kind=ObjectType.FEATURE,
        body_id=child.id, operation=Operation.NEW_BODY, source=SketchInput(child_profile.id, child_profile.producer_refs),
        parameters=ExtrusionParameters(depth=case.height/4))
    feature = set_expression(feature, "depth", Expression("depth", "=Hauteur/4"))
    doc.update_construction((replace(child, features=(feature.id,)), feature), scope="common")
    separate = commands.create_sketch(doc, xy.id, name="Profil indépendant")
    commands.add_entities(doc, separate.id, polygon(((250,0), (255,0), (255,6), (250,6))), title="Profil indépendant")
    independent = BodyObject("Corps indépendant", None, (.6, .6, .65))
    extrusion = ExtrusionFeature("Extrusion indépendante", None, independent.color, kind=ObjectType.FEATURE,
        body_id=independent.id, operation=Operation.NEW_BODY, source=SketchInput(separate.id),
        parameters=ExtrusionParameters(depth=7.))
    doc.update_construction((replace(independent, features=(extrusion.id,)), extrusion), scope="common")
    reinforced = create(doc, "Renforcée")
    set_override(doc, reinforced, height.id, case.reinforced_height)
    set_override(doc, reinforced, diameter.id, 5.)
    set_override(doc, reinforced, points.id+":"+sketch.patterns[0].id+".count", case.count-1)
    set_override(doc, reinforced, finish.id+":enabled", False)
    undrilled = create(doc, "Sans perçages guidés")
    set_override(doc, undrilled, hole.id+":enabled", False)
    set_override(doc, undrilled, finish.id+":enabled", False)
    for obj in tuple(doc.objects.values()):
        if obj.kind is not ObjectType.BODY and obj.visible:
            doc.set_visible(obj.id, False)
    doc.history.clear()
    return ConfiguredPart(case, doc, service, body.id, original_ids, original_features, varying.id, height.id,
        diameter.id, points.id, sketch.patterns[0].id, hole.id, finish.id, support.id, child.id, independent.id,
        (doc.default_configuration.id, reinforced, undrilled))
