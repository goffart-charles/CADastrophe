"""Capture planar supports on an explicit producer, with persistent face names.

Legacy indexes are checked against their exact frozen frame before migration.
"""
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Plane
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCC.Core.TopExp import topexp
from OCC.Core.TopTools import TopTools_IndexedMapOfShape
from OCC.Core.TopoDS import TopoDS_Shape, topods

from cadastrophe.geometry import Vec3, WorkPlane, natural_plane_up
from cadastrophe.plugin_api import ObjectType
from .model import PlaneSupport


def face_map(shape):
    if not isinstance(shape, TopoDS_Shape) or shape.IsNull():
        raise ValueError("Le corps ne possède pas de forme native valide.")
    faces = TopTools_IndexedMapOfShape()
    topexp.MapShapes(shape, TopAbs_FACE, faces)
    return faces


def face_frame(face, name, *, natural=True):
    surface = BRepAdaptor_Surface(topods.Face(face), True)
    if surface.GetType() != GeomAbs_Plane:
        raise ValueError("Choisissez une face plane ; les surfaces courbes ne sont pas des supports d’esquisse 2D.")
    axes = surface.Plane().Position()  # Includes the face's TopLoc_Location.
    u, v = Vec3(*axes.XDirection().Coord()), Vec3(*axes.YDirection().Coord())
    if face.Orientation() == TopAbs_REVERSED:
        v = v * -1  # Keep a right-handed frame, with the oriented face normal.
    if natural:
        normal = u.cross(v)
        v = natural_plane_up(normal)
        u = v.cross(normal).normalized()
    return WorkPlane(name, Vec3(*axes.Location().Coord()), u, v)


def from_selection(document, items):
    if len(items) != 1:
        raise ValueError("Sélectionnez un plan de référence ou une seule face plane d’un solide.")
    item = items[0]
    target = document.objects.get(item.object_id)
    if target is None or target.revision != item.revision:
        raise ValueError("La sélection du support est périmée.")
    if target.kind is ObjectType.REFERENCE_PLANE:
        return PlaneSupport(target.id, target.plane, target.revision)
    if (target.kind is not ObjectType.BODY or item.topology != "Face"
            or not isinstance(item.shape, TopoDS_Shape) or item.shape.IsNull()
            or item.shape.ShapeType() != TopAbs_FACE):
        raise ValueError("Cliquez sur une face plane du solide, loin des arêtes et des sommets.")
    from cadastrophe.core.history_position import current_state_ref
    producer = current_state_ref(document, target.id).producer_id
    return from_face_state(document, target.id, item.shape, producer)


def from_face_state(document, body_id, face, producer_id):
    """Capture only the explicitly chosen prefix, including during support repair."""
    from cadastrophe.core.feature_model import BodyStateRef, ResultStatus
    from cadastrophe.core.body_history import result_at
    from cadastrophe.core.native_naming import capture
    target = document.objects[body_id]
    state = result_at(document, BodyStateRef(body_id, producer_id))
    if state.status is not ResultStatus.READY:
        raise ValueError("L’état producteur du nouveau support n’est pas disponible.")
    faces = face_map(state.shape)
    index = faces.FindIndex(face)
    if not index:
        raise ValueError("Cette face n’appartient pas au corps sélectionné.")
    frame = face_frame(faces.FindKey(index), f"Face {index} · {target.name}")
    reference = capture(state.shape, face, "Face", state.topology)
    return PlaneSupport(target.id, frame, target.revision, index, producer_id, reference,
                        face_frame(faces.FindKey(index), frame.name, natural=False))


def face_support_status(support, target):
    if target.kind is not ObjectType.BODY:
        return "Support non résolu : l’objet n’est pas un corps."
    if target.revision != support.revision:
        return "Support non résolu : corps modifié ; la face doit être réassociée."
    try:
        faces = face_map(target.shape)
        if support.face_index > faces.Size():
            return "Support non résolu : face absente."
        face = faces.FindKey(support.face_index)
        frozen = support.frame
        if support.reference is not None and support.attachment is not None:
            native = face_frame(face, frozen.name, natural=False)
            if ((native.normal-frozen.normal).norm() <= 1e-10
                    and abs((frozen.origin-native.origin).dot(native.normal)) <= 1e-7):
                return ""
            return "Support non résolu : repère hors de la face support."
        # Existing documents retain their exact native UV frame and geometry.
        # Accept only the two frames actually produced by this plugin.
        frames = (face_frame(face,frozen.name),face_frame(face,frozen.name,natural=False))
        if not any((frame.origin-frozen.origin).norm() <= 1e-7 and
                   (frame.u-frozen.u).norm() <= 1e-10 and (frame.v-frozen.v).norm() <= 1e-10
                   for frame in frames):
            return "Support non résolu : repère de face modifié."
    except (ValueError, RuntimeError):
        return "Support non résolu : face plane indisponible."
    return ""
