"""Resolve persistent geometric inputs; never substitute a cached face index."""
from math import cos, sin, radians

from ..geometry import Vec3, WorkPlane, natural_plane_up
from .feature_model import BodyStateRef, ResultStatus
from .operation_model import GeometryReference
from .reference_objects import DatumPlaneObject


def state_for(document, ref):
    from .body_history import result_at
    state = getattr(document, "rebuild_states", {}).get(ref)
    if state is None:
        state = result_at(document, ref)
    if state.status is not ResultStatus.READY:
        from .feature_objects import ConstructionFeature
        from .feature_model import InactiveReferenceError
        roots = tuple(dict.fromkeys(key for d in state.diagnostics for key in (d.root_ids or (d.feature_id,))
                      if isinstance(document.objects.get(key), ConstructionFeature) and not document.objects[key].enabled))
        if roots:
            raise InactiveReferenceError("Référence indisponible : " + state.message, roots)
        raise ValueError("Le producteur de la référence est en erreur ou doit être reconstruit. " + state.message)
    return state


def inactive_ancestors(document, ref):
    from .feature_objects import BodyObject
    body = document.objects.get(ref.body_id)
    if not isinstance(body, BodyObject) or ref.producer_id is None:
        return ()
    if ref.producer_id not in body.features:
        return ()
    return tuple(key for key in body.features[:body.features.index(ref.producer_id)+1]
                 if not document.objects[key].enabled)


def resolve_topology(document, ref, topology, *, state=None):
    """Resolve the exact current prefix, refusing unproved inactive legacy inputs."""
    from .native_naming import resolve
    from .feature_objects import ConstructionFeature
    from .feature_model import InactiveReferenceError
    producer = document.objects.get(ref.producer_id)
    inactive = isinstance(producer, ConstructionFeature) and not producer.enabled
    legacy_roots = inactive_ancestors(document, ref) if not topology.labels else ()
    if legacy_roots:
        raise InactiveReferenceError("Ancienne référence après désactivation : choisissez une géométrie nommée.", legacy_roots)
    try:
        state = state_for(document, ref) if state is None else state
        if inactive and not topology.labels:
            raise ValueError("Cette ancienne référence ne possède pas de noms persistants.")
        return resolve(state.shape, topology, state.topology)
    except ValueError as exc:
        roots = tuple(obj.id for obj in document.objects.values()
                      if isinstance(obj, ConstructionFeature) and not obj.enabled
                      and any(obj.id in label for label in topology.labels))
        if not roots and inactive:
            roots = (producer.id,)
        if roots:
            names = ", ".join(document.objects[key].name for key in roots)
            raise InactiveReferenceError(
                f"Référence indisponible après désactivation de {names} : {exc}", roots) from exc
        raise


def face_plane(face, name="Face"):
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.GeomAbs import GeomAbs_Plane
    from OCC.Core.TopAbs import TopAbs_REVERSED
    from OCC.Core.TopoDS import topods
    surface = BRepAdaptor_Surface(topods.Face(face), True)
    if surface.GetType() != GeomAbs_Plane:
        raise ValueError("La référence doit être une face plane.")
    axes = surface.Plane().Position()
    normal = Vec3(*axes.Direction().Coord()) * (-1 if face.Orientation() == TopAbs_REVERSED else 1)
    v = natural_plane_up(normal)
    return WorkPlane(name, Vec3(*axes.Location().Coord()), v.cross(normal), v)


def resolve_reference(document, ref, *, visiting=()):
    if ref.kind == "axis":
        return Vec3(*ref.values[:3]), Vec3(*ref.values[3:]).normalized()
    if ref.kind == "point":
        return Vec3(*ref.values)
    obj = document.objects.get(ref.object_id)
    if obj is None:
        raise ValueError("Référence géométrique perdue : objet supprimé.")
    if ref.kind == "plane":
        if obj.plane is None:
            raise ValueError("La référence ne désigne pas un plan.")
        return resolve_plane(document, obj, visiting=visiting)
    if ref.kind.startswith("sketch_"):
        from .feature_objects import SketchSourceObject
        if not isinstance(obj, SketchSourceObject):
            raise ValueError("La référence ne désigne pas une esquisse.")
        # The profile provider owns placement and external-constraint solving.
        resolver = getattr(document, "resolve_sketch", None)
        obj = resolver(obj) if callable(resolver) else obj.resolved(document)
        sketch = obj.sketch
        entity = next((e for e in sketch.all_entities if e.id == ref.entity_id),None)
        if entity is None:
            raise ValueError("Référence d’esquisse perdue : ligne ou point supprimé.")
        if ref.kind == "sketch_line":
            if entity.kind != "segment":
                raise ValueError("Choisissez une ligne droite comme axe.")
            a, b = sketch.to_world(entity.start), sketch.to_world(entity.end)
            return a, (b-a).normalized()
        point = getattr(entity, ref.part, None)
        if point is None:
            raise ValueError("Le point d’esquisse n’existe plus.")
        return sketch.to_world(point)
    from .native_naming import resolve
    state = state_for(document, BodyStateRef(ref.object_id, ref.producer_id))
    shape = resolve_topology(document, BodyStateRef(ref.object_id, ref.producer_id), ref.topology, state=state)
    if ref.kind == "face":
        return face_plane(shape)
    if ref.kind == "vertex":
        from OCC.Core.BRep import BRep_Tool
        from OCC.Core.TopoDS import topods
        return Vec3(*BRep_Tool.Pnt(topods.Vertex(shape)).Coord())
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.GeomAbs import GeomAbs_Line
    from OCC.Core.TopoDS import topods
    curve = BRepAdaptor_Curve(topods.Edge(shape))
    if curve.GetType() != GeomAbs_Line:
        raise ValueError("Choisissez une arête rectiligne comme axe ou direction.")
    line = curve.Line()
    return Vec3(*line.Location().Coord()), Vec3(*line.Direction().Coord())


def resolve_plane(document, obj, *, visiting=()):
    if not isinstance(obj, DatumPlaneObject):
        return obj.plane
    if obj.id in visiting:
        raise ValueError("Dépendance circulaire entre les plans et leurs références.")
    p = obj.parameters
    refs = [(resolve_axis_reference(document, r, visiting=(*visiting, obj.id)) if p.method == "inclined" and i == 1
             else resolve_reference(document, r, visiting=(*visiting, obj.id))) for i, r in enumerate(p.references)]
    sign = -1 if p.reversed else 1
    if p.method == "three_points":
        a, b, c = refs
        if not all(isinstance(point, Vec3) for point in refs):
            raise ValueError("Choisissez trois points distincts et non alignés.")
        if (b-a).norm() < 1e-9:
            raise ValueError("Les trois points sont confondus ou alignés.")
        u, normal = (b-a).normalized(), (b-a).cross(c-a)
        if normal.norm() <= max(1e-9, (b-a).norm()*(c-a).norm()*1e-10):
            raise ValueError("Les trois points sont confondus ou alignés.")
        return WorkPlane(obj.name, a, u, normal.normalized().cross(u))
    plane = refs[0]
    if not isinstance(plane, WorkPlane):
        raise ValueError("Choisissez un plan ou une face plane.")
    if p.method == "offset":
        return WorkPlane(obj.name, plane.origin+plane.normal*(sign*p.distance), plane.u, plane.v)
    origin, direction = axis_value(refs[1])
    if abs(direction.dot(plane.normal)) > 1e-8 or abs((origin-plane.origin).dot(plane.normal)) > 1e-6:
        raise ValueError("La charnière doit appartenir au plan de référence.")
    angle = radians(sign*p.angle)
    def rotate(vector):
        return vector*cos(angle)+direction.cross(vector)*sin(angle)+direction*(direction.dot(vector)*(1-cos(angle)))
    return WorkPlane(obj.name, origin+rotate(plane.origin-origin), rotate(plane.u), rotate(plane.v))


def axis_value(value):
    if isinstance(value, WorkPlane):
        return value.origin, value.normal
    if type(value) is not tuple or len(value) != 2 or not all(isinstance(v, Vec3) for v in value):
        raise ValueError("Choisissez une ligne, un axe ou une arête rectiligne.")
    return value


def resolve_axis_reference(document, ref, *, visiting=()):
    """Resolve an axis from its exact referenced curve or analytic surface.

    The saved reference remains a named edge/face, so its centre and direction
    are recomputed after an upstream change rather than frozen as coordinates.
    """
    if ref.kind not in {"face", "edge"}:
        return axis_value(resolve_reference(document, ref, visiting=visiting))
    from .native_naming import resolve
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
    from OCC.Core.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Torus, GeomAbs_Line, GeomAbs_Circle
    from OCC.Core.TopoDS import topods
    state = state_for(document, BodyStateRef(ref.object_id, ref.producer_id))
    shape = resolve_topology(document, BodyStateRef(ref.object_id, ref.producer_id), ref.topology, state=state)
    if ref.kind == "face":
        surface = BRepAdaptor_Surface(topods.Face(shape), True)
        if surface.GetType() == GeomAbs_Plane:
            return axis_value(face_plane(shape))
        if surface.GetType() == GeomAbs_Cylinder:
            axis = surface.Cylinder().Axis()
        elif surface.GetType() == GeomAbs_Cone:
            axis = surface.Cone().Axis()
        elif surface.GetType() == GeomAbs_Torus:
            axis = surface.Torus().Axis()
        else:
            raise ValueError("Cette face ne définit pas un axe unique : choisissez une face cylindrique ou une arête droite/circulaire.")
    else:
        curve = BRepAdaptor_Curve(topods.Edge(shape))
        if curve.GetType() == GeomAbs_Line:
            line = curve.Line()
            return Vec3(*line.Location().Coord()), Vec3(*line.Direction().Coord())
        if curve.GetType() != GeomAbs_Circle:
            raise ValueError("Choisissez une arête droite ou circulaire pour définir l’axe.")
        axis = curve.Circle().Axis()
    return Vec3(*axis.Location().Coord()), Vec3(*axis.Direction().Coord())


def plane_shape(plane, extent=100.):
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCC.Core.gp import gp_Ax3, gp_Dir, gp_Pln, gp_Pnt
    return BRepBuilderAPI_MakeFace(gp_Pln(gp_Ax3(gp_Pnt(*plane.origin.tuple()), gp_Dir(*plane.normal.tuple()),
                                               gp_Dir(*plane.u.tuple()))), -extent, extent, -extent, extent).Face()


def capture_reference(document, item):
    from ..document import ObjectType
    from .native_naming import capture
    obj = document.objects.get(item.object_id)
    if obj is None or obj.revision != item.revision:
        raise ValueError("La sélection géométrique est périmée.")
    if obj.kind is ObjectType.REFERENCE_PLANE:
        return GeometryReference("plane", obj.id)
    kind = {"Arête": "Edge", "Sommet": "Vertex"}.get(item.topology, item.topology)
    if obj.kind is not ObjectType.BODY or kind not in {"Face", "Edge", "Vertex"}:
        raise ValueError("Sélectionnez une face plane, une arête droite ou un sommet.")
    from .history_position import current_state_ref
    producer = current_state_ref(document, obj.id).producer_id
    state = state_for(document, BodyStateRef(obj.id, producer))
    return GeometryReference(kind.lower(), obj.id, producer, capture(state.shape, item.shape, kind, state.topology))
