"""Detached sketch edits. Native curves stay exact, copies have independent IDs."""
from dataclasses import replace
from math import atan2, cos, hypot, isfinite, pi, sin

from .model import Arc, Circle, Ellipse, Point, Segment, Spline, Vec2, new_id


def sources(sketch, selected):
    selected = tuple(selected)
    local = {e.id: e for e in sketch.entities}
    if not selected or len(set(selected)) != len(selected) or any(k not in local for k in selected):
        raise ValueError("Sélectionnez des entités locales de l’esquisse.")
    return tuple(local[k] for k in selected)


def transformed(entity, point, angle, reflected=False, *, identifier=None):
    """An orthogonal transform, including the orientation of arcs and ellipses."""
    values = {"id": identifier or new_id()}
    if type(entity) is Point:
        values["position"] = point(entity.position)
    elif type(entity) is Segment:
        values.update(start=point(entity.start), end=point(entity.end))
    elif type(entity) is Spline:
        values["points"] = tuple(point(p) for p in entity.points)
    else:
        values["center"] = point(entity.center)
        if type(entity) is Arc:
            values.update(start_angle=angle-entity.start_angle if reflected else angle+entity.start_angle,
                          sweep=-entity.sweep if reflected else entity.sweep)
        elif type(entity) is Ellipse:
            values["rotation"] = angle-entity.rotation if reflected else angle+entity.rotation
    return replace(entity, **values)


def copies(sketch, selected, mode, *, count=3, du=20., dv=0., center=Vec2(0, 0), angle=2*pi,
           axis_start=Vec2(0, 0), axis_end=Vec2(0, 10)):
    entities = sources(sketch, selected)
    for value in (du, dv, angle):
        if not isfinite(value):
            raise ValueError("Paramètre de répétition invalide.")
    if mode == "mirror":
        dx, dy = axis_end.u-axis_start.u, axis_end.v-axis_start.v
        length = hypot(dx, dy)
        if length < 1e-9:
            raise ValueError("L’axe de symétrie exige deux points distincts.")
        dx, dy = dx/length, dy/length
        def reflect(p):
            x, y = p.u-axis_start.u, p.v-axis_start.v
            dot = x*dx+y*dy
            return Vec2(axis_start.u+2*dot*dx-x, axis_start.v+2*dot*dy-y)
        result = tuple(transformed(e, reflect, 2*atan2(dy, dx), True) for e in entities)
        result = regroup_text(result)
    else:
        if type(count) is not int or not 2 <= count <= 200 or len(entities)*count > 512:
            raise ValueError("Choisissez de 2 à 200 occurrences, dans la limite de 512 entités.")
        if mode == "linear_pattern" and hypot(du, dv) < 1e-9:
            raise ValueError("Le pas de répétition doit être non nul.")
        if mode == "circular_pattern" and not 1e-9 < abs(angle) <= 2*pi+1e-9:
            raise ValueError("L’angle total doit être non nul et inférieur ou égal à 360°.")
        if mode not in ("linear_pattern", "circular_pattern"):
            raise ValueError("Transformation inconnue.")
        result = []
        full = abs(abs(angle)-2*pi) < 1e-9
        for i in range(1, count):
            theta = i*angle/(count if full else count-1)
            def transform(p):
                if mode == "linear_pattern":
                    return Vec2(p.u+i*du, p.v+i*dv)
                x, y = p.u-center.u, p.v-center.v
                return Vec2(center.u+x*cos(theta)-y*sin(theta), center.v+x*sin(theta)+y*cos(theta))
            result.extend(regroup_text(tuple(transformed(e, transform, theta if mode == "circular_pattern" else 0.) for e in entities)))
        result = tuple(result)
    if len(sketch.entities)+len(result) > 512:
        raise ValueError("L’opération dépasserait la limite de 512 entités.")
    return replace(sketch, entities=(*sketch.entities, *result))


def regroup_text(entities):
    groups = {e.text_group: new_id() for e in entities if e.text_group is not None}
    return tuple(replace(e, text_group=groups[e.text_group]) if e.text_group is not None else e for e in entities)


def edge_geometry(sketch, edge, *, identifier=None, construction=False):
    """Encode a native result in the support frame, without sampling the curve."""
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.GeomAbs import GeomAbs_Line, GeomAbs_Circle
    from OCC.Core.Geom import Geom_TrimmedCurve
    from OCC.Core.GeomConvert import geomconvert
    from cadastrophe.geometry import Vec3
    curve = BRepAdaptor_Curve(edge)
    a, b = curve.FirstParameter(), curve.LastParameter()
    def local(p):
        return sketch.to_local(Vec3(p.X(), p.Y(), p.Z()))
    common = dict(id=identifier or new_id(), construction=construction)
    if curve.GetType() == GeomAbs_Line:
        return Segment(local(curve.Value(a)), local(curve.Value(b)), **common)
    if curve.GetType() == GeomAbs_Circle:
        circle = curve.Circle()
        center, start = local(circle.Location()), local(curve.Value(a))
        if abs(b-a-2*pi) < 1e-7:
            return Circle(center, circle.Radius(), **common)
        normal = sketch.support.frame.normal
        direction = circle.Axis().Direction()
        sign = 1 if normal.x*direction.X()+normal.y*direction.Y()+normal.z*direction.Z() > 0 else -1
        return Arc(center, circle.Radius(), atan2(start.v-center.v, start.u-center.u), sign*(b-a), **common)
    native = curve.Curve().Curve()
    bspline = geomconvert.CurveToBSplineCurve(Geom_TrimmedCurve(native, a, b))
    if bspline.IsPeriodic():
        bspline.SetNotPeriodic()
    points = tuple(local(bspline.Pole(i)) for i in range(1, bspline.NbPoles()+1))
    knots = tuple(bspline.Knot(i) for i in range(1, bspline.NbKnots()+1) for _ in range(bspline.Multiplicity(i)))
    weights = tuple(bspline.Weight(i) for i in range(1, bspline.NbPoles()+1)) if bspline.IsRational() else ()
    return Spline(points, False, bspline.Degree(), knots, weights, **common)


def trimmed(sketch, selected, position, *, remove_relations=False):
    """Remove the clicked interval between exact intersections with other curves."""
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCC.Core.GeomAPI import GeomAPI_ProjectPointOnCurve
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopAbs import TopAbs_VERTEX, TopAbs_EDGE
    from OCC.Core.TopoDS import topods
    from OCC.Core.gp import gp_Pnt
    from .presentation import entity_shape
    sketch.require_editable((selected,))
    entity, = sources(sketch, (selected,))
    if type(entity) is Point:
        raise ValueError("Choisissez une courbe à rogner.")
    attached = tuple(c for c in sketch.constraints if any(r.entity_id == selected for r in c.refs))
    if attached and not remove_relations:
        raise ValueError(f"{len(attached)} cote(s) ou relation(s) concernée(s). Autorisez leur suppression pour rogner.")
    edge = entity_shape(sketch, entity)
    adaptor = BRepAdaptor_Curve(edge)
    first, last = adaptor.FirstParameter(), adaptor.LastParameter()
    native = adaptor.Curve().Curve()
    def parameter(p):
        projection = GeomAPI_ProjectPointOnCurve(p, native, first, last)
        if not projection.NbPoints():
            raise ValueError("Le point choisi ne peut pas être projeté sur cette courbe.")
        return projection.LowerDistanceParameter()
    cuts = []
    for other in sketch.all_entities:
        if other.id == selected or type(other) is Point:
            continue
        section = BRepAlgoAPI_Section(edge, entity_shape(sketch, other), False)
        section.Build()
        if not section.IsDone():
            raise ValueError("Intersection des courbes impossible.")
        overlap = TopExp_Explorer(section.Shape(), TopAbs_EDGE)
        if overlap.More():
            raise ValueError("Des courbes superposées rendent le rognage ambigu.")
        vertices = TopExp_Explorer(section.Shape(), TopAbs_VERTEX)
        while vertices.More():
            t = parameter(BRep_Tool.Pnt(topods.Vertex(vertices.Current())))
            if not any(abs(t-x) < 1e-7 for x in cuts):
                cuts.append(t)
            vertices.Next()
    t = parameter(gp_Pnt(*sketch.to_world(position).tuple()))
    closed = adaptor.IsClosed()
    cuts = sorted(x for x in cuts if closed or first+1e-7 < x < last-1e-7)
    if any(abs(t-x) < 1e-7 for x in cuts):
        raise ValueError("Cliquez à l’intérieur du tronçon, à distance de l’intersection.")
    intervals = []
    if closed and len(cuts) >= 2:
        bounds = [*cuts, cuts[0]+last-first]
        hit = t if t >= cuts[0] else t+last-first
        intervals = [(a, b) for a, b in zip(bounds, bounds[1:]) if not a < hit < b]
    elif not closed:
        bounds = [first, *cuts, last]
        intervals = [(a, b) for a, b in zip(bounds, bounds[1:]) if not a-1e-9 <= t <= b+1e-9]
    pieces = tuple(edge_geometry(sketch, BRepBuilderAPI_MakeEdge(native, a, b).Edge(),
                                identifier=selected if i == 0 else None, construction=entity.construction)
                   for i, (a, b) in enumerate(intervals) if b-a > 1e-8)
    entities = tuple(p for e in sketch.entities for p in (pieces if e.id == selected else (e,)))
    constraints = tuple(c for c in sketch.constraints if c not in attached)
    used = {r.entity_id for c in constraints for r in c.refs}
    return replace(sketch, entities=entities, constraints=constraints,
                   external=tuple(e for e in sketch.external if e.geometry.id in used))


def offset(sketch, selected, distance):
    from OCC.Core.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
    from OCC.Core.ShapeAnalysis import ShapeAnalysis_FreeBounds
    from OCC.Core.TopTools import TopTools_HSequenceOfShape
    from OCC.Core.GeomAbs import GeomAbs_Arc
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopAbs import TopAbs_EDGE
    from OCC.Core.TopoDS import topods
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCC.Core.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Pln
    from .presentation import entity_shape
    if not isfinite(distance) or abs(distance) < 1e-7:
        raise ValueError("La distance signée doit être non nulle.")
    entities = sources(sketch, selected)
    if any(type(e) is Point for e in entities):
        raise ValueError("Les points ne définissent pas de contour à décaler.")
    if any(type(e) in (Ellipse, Spline) for e in entities):
        raise ValueError("Le décalage accepte les segments, cercles et arcs. Les ellipses et splines ne sont pas encore prises en charge.")
    edges, wires = TopTools_HSequenceOfShape(), TopTools_HSequenceOfShape()
    for entity in entities:
        edges.Append(entity_shape(sketch, entity))
    wires = ShapeAnalysis_FreeBounds.ConnectEdgesToWires(edges, 1e-7, False, wires)
    if wires.Length() != 1:
        raise ValueError("Sélectionnez un seul contour continu, ouvert ou fermé.")
    wire = topods.Wire(wires.Value(1))
    frame = sketch.support.frame
    plane = gp_Pln(gp_Ax3(gp_Pnt(*frame.origin.tuple()), gp_Dir(*frame.normal.tuple()), gp_Dir(*frame.u.tuple())))
    if BRep_Tool.IsClosed(wire):
        maker = BRepOffsetAPI_MakeOffset(wire, GeomAbs_Arc, False)
    else:
        if distance < 0:
            wire = topods.Wire(wire.Reversed())
        maker = BRepOffsetAPI_MakeOffset(BRepBuilderAPI_MakeFace(plane).Face(), GeomAbs_Arc, True)
        maker.AddWire(wire)
    maker.Perform(distance if BRep_Tool.IsClosed(wire) else abs(distance))
    if not maker.IsDone() or maker.Shape().IsNull() or not BRepCheck_Analyzer(maker.Shape()).IsValid():
        raise ValueError("Ce décalage ne produit pas de contour valide. Essayez une autre distance.")
    result, explorer = [], TopExp_Explorer(maker.Shape(), TopAbs_EDGE)
    while explorer.More():
        result.append(edge_geometry(sketch, topods.Edge(explorer.Current()), construction=all(e.construction for e in entities)))
        explorer.Next()
    if not result or len(sketch.entities)+len(result) > 512:
        raise ValueError("Le décalage est vide ou trop complexe.")
    return replace(sketch, entities=(*sketch.entities, *result))
