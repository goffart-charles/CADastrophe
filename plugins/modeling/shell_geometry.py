"""Constant wall thickness using OCCT's native thick-solid builder."""
from cadastrophe.core.native_naming import resolve, subshapes, static_topology, isolated_input
from cadastrophe.core.operation_effects import checked_solids, compound
from cadastrophe.core.native_boolean import build_boolean


def validate_wall(maker, source, result, selected, parameters):
    """OCCT can return a valid B-Rep after offset fronts have crossed.

    Certify the material side and minimum clearance of the offset boundary,
    instead of accepting a geometrically wrong but topologically valid solid.
    """
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Common
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps
    from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
    from OCC.Core.TopAbs import TopAbs_FACE

    def mass(shape):
        props = GProp_GProps()
        brepgprop.VolumeProperties(shape, props, True)
        return props.Mass()

    wrong_side = build_boolean(BRepAlgoAPI_Common if parameters.outward else BRepAlgoAPI_Cut, result, source)
    if not wrong_side.IsDone() or abs(mass(wrong_side.Shape())) > max(1e-6, mass(result)*1e-8):
        raise ValueError("Le décalage traverse la matière du côté opposé.")
    if not parameters.outward and mass(result) >= mass(source)-1e-6:
        raise ValueError("L’épaisseur ne laisse aucune cavité intérieure.")
    retained = tuple(f for f in subshapes(source, "Face") if not any(f.IsSame(s) for s in selected))
    if not retained:
        raise ValueError("La coque doit conserver au moins une face du corps source.")
    boundary = compound(retained)
    final = subshapes(result, "Face")
    for face in retained:
        for child in (*maker.Modified(face), *maker.Generated(face)):
            if (child.ShapeType() != TopAbs_FACE or child.IsSame(face)
                    or not any(child.IsSame(f) for f in final)):
                continue
            distance = BRepExtrema_DistShapeShape(child, boundary)
            if not distance.IsDone() or distance.Value() < parameters.thickness-max(1e-6, parameters.thickness*1e-7):
                raise ValueError("L’épaisseur provoque un croisement des parois ou une cavité trop étroite.")


def apply_shell(shape, parameters, *, topology=None, trace=None):
    from OCC.Core.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
    from OCC.Core.BRepOffset import BRepOffset_Skin
    from OCC.Core.GeomAbs import GeomAbs_Intersection
    from OCC.Core.TopTools import TopTools_ListOfShape
    if shape is None or len(checked_solids(shape)) != 1:
        raise ValueError("La coque exige un seul solide fermé valide.")
    topology = topology or static_topology(shape)
    # Outward offsets can mutate shared native TShapes. Keep prefixes isolated.
    shape, topology = isolated_input(shape, topology, trace)
    selected = tuple(resolve(shape, ref.topology, topology) for ref in parameters.faces)
    if any(face.IsSame(other) for i, face in enumerate(selected) for other in selected[:i]):
        raise ValueError("Une face à ouvrir est sélectionnée plusieurs fois.")
    closing = TopTools_ListOfShape()
    for face in selected:
        closing.Append(face)
    maker = BRepOffsetAPI_MakeThickSolid()
    try:
        maker.MakeThickSolidByJoin(shape, closing,
            parameters.thickness if parameters.outward else -parameters.thickness,
            1e-7, BRepOffset_Skin, False, False, GeomAbs_Intersection, False)
        if not maker.IsDone():
            raise ValueError("Le décalage des faces a échoué.")
        result = maker.Shape()
        if len(checked_solids(result)) != 1:
            raise ValueError("Le décalage ne conserve pas un solide unique.")
        validate_wall(maker, shape, result, selected, parameters)
    except (RuntimeError, ValueError) as exc:
        raise ValueError(f"Coque impossible : réduisez l’épaisseur ou modifiez les faces à ouvrir. {exc}") from exc
    if trace is not None:
        trace.thick_solid(maker, shape, result)
    return result
