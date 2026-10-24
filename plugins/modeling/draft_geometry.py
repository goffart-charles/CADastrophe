"""Native neutral-plane draft; no approximation or saved result solid."""
from math import radians

from cadastrophe.geometry import WorkPlane, Vec3
from cadastrophe.core.native_naming import resolve, static_topology, isolated_input
from cadastrophe.core.operation_effects import checked_solids


def apply_draft(shape, parameters, neutral, direction, *, topology=None, trace=None):
    from OCC.Core.BRepOffsetAPI import BRepOffsetAPI_DraftAngle
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone
    from OCC.Core.TopoDS import topods
    from OCC.Core.gp import gp_Dir, gp_Pln, gp_Pnt

    if shape is None or len(checked_solids(shape)) != 1:
        raise ValueError("La dépouille exige un seul solide fermé valide.")
    if not isinstance(neutral, WorkPlane) or not isinstance(direction, Vec3):
        raise ValueError("Choisissez un plan neutre et une direction valides.")
    direction = direction.normalized()
    if abs(direction.dot(neutral.normal)) < 1e-8:
        raise ValueError("La direction de dépouille ne doit pas être parallèle au plan neutre.")
    topology = topology or static_topology(shape)
    # DraftAngle can modify shared source TShapes even during a failed preview.
    shape, topology = isolated_input(shape, topology, trace)
    faces = tuple(resolve(shape, r.topology, topology) for r in parameters.faces)
    if any(face.IsSame(other) for i, face in enumerate(faces) for other in faces[:i]):
        raise ValueError("Une face à dépouiller est sélectionnée plusieurs fois.")
    if any(BRepAdaptor_Surface(topods.Face(f)).GetType() not in {GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone} for f in faces):
        raise ValueError("La dépouille accepte les faces planes, cylindriques ou coniques.")
    plane = gp_Pln(gp_Pnt(*neutral.origin.tuple()), gp_Dir(*neutral.normal.tuple()))
    maker = BRepOffsetAPI_DraftAngle(shape)
    try:
        for face in faces:
            # OCCT propagates across tangential neighbours. Add each group once.
            if any(face.IsSame(added) for added in maker.ModifiedFaces()):
                continue
            maker.Add(topods.Face(face), gp_Dir(*direction.tuple()),
                      radians(-parameters.angle if parameters.reversed else parameters.angle), plane)
            if not maker.AddDone():
                raise ValueError("Une face ne peut pas être inclinée autour du plan neutre.")
        maker.Build()
        if not maker.IsDone():
            raise ValueError("La reconstruction des faces, arêtes ou sommets a échoué.")
        result = maker.Shape()
        if len(checked_solids(result)) != 1:
            raise ValueError("La dépouille ne conserve pas un solide unique.")
    except (ValueError, RuntimeError) as exc:
        raise ValueError(f"Dépouille impossible : réduisez l’angle ou modifiez les faces et le plan neutre. {exc}") from exc
    if trace is not None:
        trace.modified_shape(maker, shape, result)
    return result
