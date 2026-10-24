"""Checked removal volumes with semantic names per persistent sketch point."""
from math import radians, tan

from cadastrophe.core.operation_effects import VolumeEffect, checked_solid, checked_solids
from cadastrophe.core.feature_model import Operation
from cadastrophe.core.native_boolean import build_boolean
from cadastrophe.core.native_naming import subshapes


def generating_holes(service, document, feature, previous, trace, *, position_id=None):
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeCone
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Fuse
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.GeomAbs import GeomAbs_Plane
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps
    from OCC.Core.TopoDS import topods
    from OCC.Core.gp import gp_Ax2, gp_Pnt, gp_Dir
    from cadastrophe.core.cut import through_all_depth
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCC.Core.gp import gp_Pln

    if previous is None:
        raise ValueError("Le perçage exige un corps cible existant.")
    p = feature.parameters
    frame, points = service._profiles.position_points(document, feature.sketch_id,
                                                      tuple(r.entity_id for r in p.positions))
    if position_id is not None:
        points = tuple(item for item in points if item[0] == position_id)
        if not points:
            raise ValueError("Point de positionnement du motif absent.")
    direction = frame.normal * (-1 if p.reversed else 1)
    effects, visited = [], []

    def primitive(shape, key, role, origin):
        checked_solid(shape)
        named = []
        for face in subshapes(shape, "Face"):
            surface = BRepAdaptor_Surface(topods.Face(face))
            label = "wall"
            if surface.GetType() == GeomAbs_Plane:
                from cadastrophe.geometry import Vec3
                distance = (Vec3(*surface.Plane().Location().Coord()) - origin).dot(direction)
                label = "entry" if abs(distance) < 1e-7 else "bottom"
            named.append((f"{feature.id}:hole:{key}:{role}:{label}", face))
        trace._record(shape, named)
        return shape

    for key, point in points:
        if any((point - old).norm() < 1e-7 for old in visited):
            raise ValueError("Deux points de perçage sont superposés.")
        visited.append(point)
        axis = gp_Ax2(gp_Pnt(*point.tuple()), gp_Dir(*direction.tuple()))
        depth = p.depth
        if p.end_condition == "through_all":
            face = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(*point.tuple()), gp_Dir(*direction.tuple())),
                                           -p.diameter, p.diameter, -p.diameter, p.diameter).Face()
            depth = through_all_depth(previous, face)
        shape = primitive(BRepPrimAPI_MakeCylinder(axis, p.diameter / 2, depth).Shape(), key, "bore", point)
        if p.form != "simple":
            head_depth = (p.head_depth if p.form == "counterbore" else
                          (p.head_diameter - p.diameter) / (2 * tan(radians(p.angle / 2))))
            if head_depth >= depth - 1e-7:
                raise ValueError("L’entrée du perçage doit être moins profonde que le trou.")
            head = (BRepPrimAPI_MakeCylinder(axis, p.head_diameter / 2, head_depth).Shape()
                    if p.form == "counterbore" else
                    BRepPrimAPI_MakeCone(axis, p.head_diameter / 2, p.diameter / 2, head_depth).Shape())
            primitive(head, key, p.form, point)
            union = build_boolean(BRepAlgoAPI_Fuse, shape, head)
            if not union.IsDone():
                raise ValueError("Impossible de construire l’entrée du perçage.")
            solids = checked_solids(union.Shape())
            if len(solids) != 1:
                raise ValueError("L’entrée du perçage doit former un volume unique.")
            result = solids[0]
            trace.operation(union, (shape, head), result)
            shape = result
        # Validate every location independently before the batch can publish.
        common = build_boolean(BRepAlgoAPI_Common, previous, shape)
        if not common.IsDone():
            raise ValueError("Impossible de vérifier une position de perçage.")
        props = GProp_GProps()
        brepgprop.VolumeProperties(common.Shape(), props, True)
        if props.Mass() <= 1e-12:
            raise ValueError("Un point de perçage n’enlève aucune matière : vérifiez position, profondeur et sens.")
        effects.append(VolumeEffect(Operation.CUT, shape, trace.topology, (feature.id, key), feature.id))
    return tuple(effects)
