"""Transient generating volumes and spatial instances, never final-body copies."""
from dataclasses import dataclass
from math import radians, isfinite

from .feature_model import Operation


@dataclass(frozen=True)
class VolumeEffect:
    operation: Operation
    shape: object
    topology: object = None
    # Stable recipe path; placement composes nested occurrences. These derived
    # receipts permit deduplication and directional through-all reconstruction.
    key: tuple = ()
    generator_id: str | None = None
    placement: object = None


def checked_solid(shape):
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps
    from OCC.Core.TopAbs import TopAbs_SOLID
    if shape.IsNull() or shape.ShapeType() != TopAbs_SOLID or not BRepCheck_Analyzer(shape, True).IsValid():
        raise ValueError("L’opération doit produire un solide fermé unique et valide.")
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.TopoDS import topods
    from OCC.Core.TopAbs import TopAbs_SHELL
    from OCC.Core.TopExp import TopExp_Explorer
    shells = TopExp_Explorer(shape, TopAbs_SHELL)
    if not shells.More():
        raise ValueError("Le solide ne possède pas de frontière fermée.")
    while shells.More():
        if not BRep_Tool.IsClosed(topods.Shell(shells.Current())):
            raise ValueError("La frontière du solide est ouverte.")
        shells.Next()
    props = GProp_GProps()
    brepgprop.VolumeProperties(shape, props, True)
    if not isfinite(props.Mass()) or props.Mass() <= 1e-18:
        raise ValueError("Le volume généré est nul, inversé ou non fini.")
    return shape


def compound(shapes):
    if len(shapes) == 1:
        return shapes[0]
    from OCC.Core.BRep import BRep_Builder
    from OCC.Core.TopoDS import TopoDS_Compound
    builder, result = BRep_Builder(), TopoDS_Compound()
    builder.MakeCompound(result)
    for shape in shapes:
        builder.Add(result, shape)
    return result


def checked_solids(shape):
    """Accept only a solid or a clean collection of closed, valid solids."""
    from OCC.Core.TopAbs import TopAbs_SOLID, TopAbs_COMPOUND, TopAbs_SHELL, TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX
    from OCC.Core.TopTools import TopTools_IndexedMapOfShape
    from OCC.Core.TopExp import topexp
    from OCC.Core.TopoDS import TopoDS_Shape
    from OCC.Core.BRepCheck import BRepCheck_Analyzer

    def items(native, kind):
        mapping = TopTools_IndexedMapOfShape()
        topexp.MapShapes(native, kind, mapping)
        return tuple(mapping.FindKey(i) for i in range(1, mapping.Size()+1))

    if not isinstance(shape, TopoDS_Shape) or shape.IsNull():
        raise ValueError("Aucun solide disponible.")
    if shape.ShapeType() == TopAbs_SOLID:
        checked_solid(shape)
        return (shape,)
    if shape.ShapeType() != TopAbs_COMPOUND or not BRepCheck_Analyzer(shape, True).IsValid():
        raise ValueError("Le résultat doit contenir uniquement des solides fermés valides.")
    solids = items(shape, TopAbs_SOLID)
    if not solids or any(len(items(shape, kind)) != sum(len(items(s, kind)) for s in solids)
                         for kind in (TopAbs_SHELL, TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX)):
        raise ValueError("Le résultat contient des composants parasites ou des contacts non manifold.")
    for solid in solids:
        checked_solid(solid)
    return solids


def occurrence_transforms(operation, params, reference):
    from OCC.Core.gp import gp_Trsf, gp_Vec, gp_Ax1, gp_Ax2, gp_Pnt, gp_Dir
    from ..geometry import WorkPlane
    from .reference_geometry import axis_value
    if operation is Operation.MIRROR:
        if not isinstance(reference, WorkPlane):
            raise ValueError("Choisissez un plan de symétrie.")
        transform = gp_Trsf()
        transform.SetMirror(gp_Ax2(gp_Pnt(*reference.origin.tuple()), gp_Dir(*reference.normal.tuple()), gp_Dir(*reference.u.tuple())))
        return (transform,)
    origin, direction = axis_value(reference)
    sign = -1 if params.reversed else 1
    transforms = []
    for index in range(1, params.count):
        transform = gp_Trsf()
        if operation is Operation.LINEAR_PATTERN:
            transform.SetTranslation(gp_Vec(*(direction*(index*params.spacing*sign)).tuple()))
        else:
            step = params.angle/(params.count if params.angle == 360 else params.count-1)
            transform.SetRotation(gp_Ax1(gp_Pnt(*origin.tuple()), gp_Dir(*direction.tuple())), radians(sign*index*step))
        transforms.append(transform)
    return tuple(transforms)


def transform_effect(effect, transform, trace, occurrence):
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
    builder = BRepBuilderAPI_Transform(effect.shape, transform, True)
    if not builder.IsDone():
        raise ValueError("La transformation du volume générateur a échoué.")
    shape = checked_solid(builder.Shape())
    from .native_naming import static_topology
    trace.transformed(builder, effect.topology or static_topology(effect.shape), shape, occurrence)
    placement = transform if effect.placement is None else transform.Multiplied(effect.placement)
    return VolumeEffect(effect.operation, shape, trace.topology,
                        (trace.feature_id, occurrence, *effect.key), effect.generator_id, placement)


def apply_effects(previous, effects, *, trace=None, merge_result=True):
    from .boss import fuse_boss
    from .cut import cut_solid
    if not effects:
        raise ValueError("Cette fonction ne fournit aucun volume générateur rejouable.")
    operation = effects[0].operation
    if any(e.operation is not operation for e in effects):
        raise ValueError("Les volumes d’une fonction doivent conserver la même opération.")
    if operation is Operation.NEW_BODY and previous is None:
        if len(effects) != 1:
            raise ValueError("Un nouveau corps exige un seul volume connexe.")
        return checked_solid(effects[0].shape)
    if previous is None:
        raise ValueError("Cette opération exige un corps précédent valide.")
    from OCC.Core.TopAbs import TopAbs_COMPOUND
    allow_disconnected = not merge_result or previous.ShapeType() == TopAbs_COMPOUND
    maximum_solids = len(checked_solids(previous)) if merge_result and allow_disconnected else None

    def accepted(shape):
        if maximum_solids is not None and len(checked_solids(shape)) > maximum_solids:
            raise ValueError("L’opération crée des solides distincts. Décochez « Fusionner le résultat » ou rapprochez les occurrences.")
        return shape

    if operation is Operation.CUT:
        result = cut_solid(previous, compound(tuple(e.shape for e in effects)), trace=trace, allow_disconnected=allow_disconnected)
        if not result.success:
            raise ValueError(result.message)
        return accepted(result.shape)
    shape, added = previous, False
    for effect in effects:
        result = fuse_boss(shape, effect.shape, trace=trace, allow_disconnected=allow_disconnected)
        if not result.success:
            if result.diagnostics[0].code == "no_added_material":
                continue
            raise ValueError(result.message)
        shape, added = accepted(result.shape), True
    if not added:
        raise ValueError("Aucune matière ajoutée par cette occurrence ; elle est entièrement superposée au corps.")
    return shape
