"""Capture actual OCCT vertices/edges as fixed projections into the sketch plane."""
from dataclasses import replace
from math import hypot, atan2, pi

from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.GeomAbs import GeomAbs_Line, GeomAbs_Circle
from OCC.Core.TopAbs import TopAbs_VERTEX, TopAbs_EDGE
from OCC.Core.TopExp import topexp
from OCC.Core.TopTools import TopTools_IndexedMapOfShape
from OCC.Core.TopoDS import topods

from cadastrophe.geometry import Vec3
from cadastrophe.plugin_api import ObjectType
from .model import ExternalGeometry, Point, Segment, Circle, Arc, Vec2


def shape_map(shape, topology):
    result = TopTools_IndexedMapOfShape()
    topexp.MapShapes(shape, TopAbs_VERTEX if topology == "Vertex" else TopAbs_EDGE, result)
    return result


def project(shape, topology, frame):
    def local(p):
        u, v, _ = frame.to_local(Vec3(*p.Coord()))
        return Vec2(u, v)
    if topology == "Vertex":
        return Point(local(BRep_Tool.Pnt(topods.Vertex(shape))))
    curve = BRepAdaptor_Curve(topods.Edge(shape))
    if curve.GetType() == GeomAbs_Line:
        return Segment(local(curve.Value(curve.FirstParameter())), local(curve.Value(curve.LastParameter())))
    if curve.GetType() == GeomAbs_Circle:
        circle = curve.Circle()
        normal = Vec3(*circle.Axis().Direction().Coord())
        if abs(abs(normal.dot(frame.normal))-1) > 1e-8:
            raise ValueError("La projection de cette arête circulaire est une ellipse, non prise en charge.")
        center = local(circle.Location())
        sweep = curve.LastParameter()-curve.FirstParameter()
        if abs(sweep-2*pi) < 1e-7:
            return Circle(center, circle.Radius())
        start = local(curve.Value(curve.FirstParameter()))
        return Arc(center, circle.Radius(), atan2(start.v-center.v, start.u-center.u),
                   sweep*(1 if normal.dot(frame.normal) > 0 else -1))
    raise ValueError("Seuls les sommets et les arêtes droites ou circulaires sont utilisables comme références.")


def capture(document, sketch, item):
    body = document.objects.get(item.object_id)
    if body is None or body.kind is not ObjectType.BODY or body.revision != item.revision:
        raise ValueError("La référence au solide est périmée.")
    if item.topology not in ("Vertex", "Edge"):
        raise ValueError("Sélectionnez un sommet ou une arête du solide.")
    from cadastrophe.core.feature_model import BodyStateRef
    from cadastrophe.core.body_history import result_at
    from cadastrophe.core.native_naming import capture as capture_reference
    from cadastrophe.core.history_position import current_state_ref
    producer = current_state_ref(document, body.id).producer_id
    obj = document.objects.get(sketch.id)
    for ref in getattr(obj, "producer_refs", ()):
        if ref.body_id == body.id:
            producer = ref.producer_id
    state = result_at(document, BodyStateRef(body.id, producer))
    if state.shape is None:
        raise ValueError("Le producteur de la référence doit être reconstruit.")
    shapes = shape_map(state.shape, item.topology)
    index = shapes.FindIndex(item.shape)
    if not index:
        raise ValueError("La sous-forme n’appartient pas au solide.")
    existing = next((e for e in sketch.external if (e.body_id, e.revision, e.topology, e.index) ==
                     (body.id, body.revision, item.topology, index)), None)
    reference = capture_reference(state.shape, item.shape, item.topology, state.topology)
    return existing or ExternalGeometry(body.id, body.revision, item.topology, index,
                                       project(shapes.FindKey(index), item.topology, sketch.support.frame), producer, reference)


def status(sketch, objects):
    for e in sketch.external:
        body = objects.get(e.body_id)
        if body is None or body.kind is not ObjectType.BODY:
            return "Référence externe non résolue : solide absent."
        try:
            shapes = shape_map(body.shape, e.topology)
            if e.index > shapes.Size():
                raise ValueError("Sous-forme absente")
            fresh = project(shapes.FindKey(e.index), e.topology, sketch.support.frame)
            # Same frozen projection is also required after a file roundtrip.
            fresh = replace(fresh, id=e.geometry.id)
            from .solver import parameters
            if type(fresh) is not type(e.geometry) or any(abs(a-b) > 1e-7 for a, b in zip(parameters(fresh), parameters(e.geometry))):
                raise ValueError("Projection modifiée")
            if body.revision != e.revision:
                raise ValueError("Révision modifiée")
        except (ValueError, RuntimeError):
            return "Référence externe non résolue : solide modifié ; supprimer puis sélectionner à nouveau sa référence."
    return ""
