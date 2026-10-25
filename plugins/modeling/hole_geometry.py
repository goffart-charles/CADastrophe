"""Checked removal volumes with semantic names per persistent sketch point."""
from math import radians, tan

from cadastrophe.core.operation_effects import VolumeEffect, checked_solid, checked_solids
from cadastrophe.core.feature_model import Operation
from cadastrophe.core.native_boolean import build_boolean
from cadastrophe.core.native_naming import subshapes


def suggested_hole_direction(shape, frame, points):
    """Choose the side reaching material first at every position, or leave it open."""
    from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector
    from OCC.Core.BRepClass3d import BRepClass3d_SolidClassifier
    from OCC.Core.TopAbs import TopAbs_IN
    from OCC.Core.gp import gp_Pnt, gp_Dir, gp_Lin
    intersector = IntCurvesFace_ShapeIntersector()
    intersector.Load(shape, 1e-7)
    scores = [[], []]
    for _, point in points:
        intersector.Perform(gp_Lin(gp_Pnt(*point.tuple()), gp_Dir(*frame.normal.tuple())), -1e100, 1e100)
        distances = sorted(set(intersector.WParameter(i) for i in range(1, intersector.NbPnt()+1)))
        for side, sign in enumerate((1, -1)):
            probe = point + frame.normal*(sign*1e-5)
            if BRepClass3d_SolidClassifier(shape, gp_Pnt(*probe.tuple()), 1e-7).State() == TopAbs_IN:
                scores[side].append(0.)
                continue
            ahead = sorted(sign*d for d in distances if sign*d > 1e-7)
            distance = float('inf')
            for first, last in zip(ahead, ahead[1:]):
                middle = point + frame.normal*(sign*(first+last)/2)
                if BRepClass3d_SolidClassifier(shape, gp_Pnt(*middle.tuple()), 1e-7).State() == TopAbs_IN:
                    distance = first
                    break
            scores[side].append(distance)
    if not points:
        return None
    forward, backward = (sum(values) for values in scores)
    if abs(forward-backward) < 1e-7 or forward == backward:
        return None
    return backward < forward


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
