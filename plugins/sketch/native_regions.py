"""OCCT planar arrangement for sketches containing ellipses or B-splines.

The surrounding construction frame is temporary; faces touching it are excluded.
Region identities are derived from source UUIDs and split curve parameters.
"""
from dataclasses import replace
from functools import lru_cache
from uuid import uuid5, NAMESPACE_URL
from math import isfinite
from cadastrophe.geometry import Vec3

from .model import Ellipse, Spline, Point, Segment, Vec2
from .profiles import Analysis, Contour, EdgeUse, Diagnostic, MAX_CURVES
from .profile_faces import NativeFaces, FaceRegion
from .profile_native import ProfileError


def extended(sketch):
    return any(type(e) in (Ellipse, Spline) or e.text_group is not None for e in sketch.profile_entities)


def key(prefix, items):
    return str(uuid5(NAMESPACE_URL, "cadastrophe.native.region."+prefix+":"+",".join(sorted(items))))


@lru_cache(maxsize=64)
def arrangement(sketch, tolerance=1e-7):
    from .presentation import entity_shape
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Splitter
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeFace
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    from OCC.Core.BRepTools import breptools
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps
    from OCC.Core.TopTools import TopTools_ListOfShape
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_WIRE
    from OCC.Core.TopoDS import topods
    from OCC.Core.BRepClass import BRepClass_FaceClassifier
    from OCC.Core.TopAbs import TopAbs_IN
    if len(sketch.profile_entities) > MAX_CURVES:
        raise ValueError("Trop de courbes pour l’analyse locale (512 maximum).")
    if sketch.constraints:
        from .solver import diagnose
        state = diagnose(sketch)
        if not state.success:
            raise ValueError(state.message)
    from .curve_native import profile_edges
    sources = [(e, curve, span) for e in sorted(sketch.profile_entities, key=lambda e: e.id) if type(e) is not Point
               for span, curve in enumerate(profile_edges(sketch, e) if type(e) is Spline else (entity_shape(sketch, e),))]
    if not sources:
        raise ValueError("Aucune courbe à analyser.")
    text_masks = _text_masks(sketch, tolerance)
    points = []
    for e, _, _ in sources:
        if type(e) is Segment:
            points.extend((e.start, e.end))
        elif type(e) is Spline:
            points.extend(e.points)
        else:
            r = e.major_radius if type(e) is Ellipse else e.radius
            points.extend((Vec2(e.center.u-r, e.center.v-r), Vec2(e.center.u+r, e.center.v+r)))
    lo = min(p.u for p in points), min(p.v for p in points)
    hi = max(p.u for p in points), max(p.v for p in points)
    margin = 2*max(hi[0]-lo[0], hi[1]-lo[1], 1.)+1.
    lo, hi = tuple(v-margin for v in lo), tuple(v+margin for v in hi)
    corners = tuple(Vec2(*p) for p in ((lo[0], lo[1]), (hi[0], lo[1]), (hi[0], hi[1]), (lo[0], hi[1])))
    wire = BRepBuilderAPI_MakeWire()
    for i, p in enumerate(corners):
        wire.Add(entity_shape(sketch, Segment(p, corners[(i+1)%4])))
    from OCC.Core.gp import gp_Pln, gp_Ax3, gp_Pnt, gp_Dir
    support = sketch.support.frame
    plane = gp_Pln(gp_Ax3(gp_Pnt(*support.origin.tuple()), gp_Dir(*support.normal.tuple()), gp_Dir(*support.u.tuple())))
    frame = BRepBuilderAPI_MakeFace(plane, wire.Wire(), True).Face()
    splitter = BRepAlgoAPI_Splitter()
    arguments, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    arguments.Append(frame)
    for _, edge, _ in sources:
        tools.Append(edge)
    splitter.SetArguments(arguments)
    splitter.SetTools(tools)
    splitter.SetNonDestructive(True)
    splitter.SetFuzzyValue(tolerance)
    splitter.Build()
    if not splitter.IsDone():
        raise ValueError("Impossible de subdiviser ces courbes planes.")
    lineage = []
    for entity, original, span in sources:
        modified = [topods.Edge(s) for s in splitter.Modified(original) if s.ShapeType() == TopAbs_EDGE]
        fragments = modified or [original]
        fragments.sort(key=lambda e: BRepAdaptor_Curve(e).FirstParameter())
        for index, edge in enumerate(fragments):
            lineage.append((edge, f"{entity.id}:{span}:{index}"))
    def boundary(wire):
        parts, explorer = [], TopExp_Explorer(wire, TopAbs_EDGE)
        while explorer.More():
            edge = topods.Edge(explorer.Current())
            ids = [part for source, part in lineage if source.IsSame(edge)]
            if not ids:
                raise ValueError("Une frontière de zone a perdu ses références sources.")
            parts.append(key("edge", ids))
            explorer.Next()
        return tuple(sorted(parts))
    regions, contours = [], []
    explorer = TopExp_Explorer(splitter.Shape(), TopAbs_FACE)
    while explorer.More():
        face = topods.Face(explorer.Current())
        explorer.Next()
        edges = TopExp_Explorer(face, TopAbs_EDGE)
        exterior = False
        while edges.More():
            curve = BRepAdaptor_Curve(topods.Edge(edges.Current()))
            point = curve.Value((curve.FirstParameter()+curve.LastParameter())/2)
            u, v, _ = sketch.support.frame.to_local(Vec3(point.X(), point.Y(), point.Z()))
            if min(abs(u-lo[0]), abs(u-hi[0]), abs(v-lo[1]), abs(v-hi[1])) <= 10*tolerance:
                exterior = True
                break
            edges.Next()
        if exterior:
            continue
        if not BRepCheck_Analyzer(face, True).IsValid():
            raise ValueError("Une zone construite par les nouvelles courbes est invalide.")
        properties = GProp_GProps()
        brepgprop.SurfaceProperties(face, properties, 1e-10)
        surface = properties.Mass()
        if not isfinite(surface) or surface <= tolerance*tolerance:
            continue
        outer = breptools.OuterWire(face)
        if text_masks and _text_counter(outer, lineage, sketch, text_masks, tolerance, BRepClass_FaceClassifier, TopAbs_IN):
            continue
        outer_parts = boundary(outer)
        identity = key("cell", outer_parts)
        holes, hole_area = [], 0.
        wires = TopExp_Explorer(face, TopAbs_WIRE)
        while wires.More():
            candidate = topods.Wire(wires.Current())
            wires.Next()
            if not candidate.IsSame(outer):
                parts = boundary(candidate)
                hole = key("hole", (identity, *parts))
                holes.append(hole)
                properties = GProp_GProps()
                brepgprop.SurfaceProperties(BRepBuilderAPI_MakeFace(plane, candidate, True).Face(), properties, 1e-10)
                size = abs(properties.Mass())
                hole_area += size
                contours.append(Contour(hole, tuple(EdgeUse(p, True) for p in parts), -size, 1, identity))
        contours.append(Contour(identity, tuple(EdgeUse(p) for p in outer_parts), surface+hole_area))
        regions.append(FaceRegion(identity, tuple(holes), face, surface))
        if len(regions) > 2048:
            raise ValueError("Trop de zones dans cette esquisse.")
    if not regions:
        raise ValueError("L’esquisse ne contient aucune zone fermée.")
    return NativeFaces(tuple(sorted(regions, key=lambda r: r.id)), tolerance), tuple(sorted(contours, key=lambda c: c.id))


def _text_masks(sketch, tolerance):
    """Closed glyph loops carry an even/odd fill rule, including their counters."""
    from OCC.Core.ShapeAnalysis import ShapeAnalysis_FreeBounds
    from OCC.Core.TopTools import TopTools_HSequenceOfShape
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.TopoDS import topods
    from .presentation import entity_shape
    groups = {}
    for entity in sketch.profile_entities:
        if entity.text_group is not None:
            groups.setdefault(entity.text_group, []).append(entity)
    masks = {}
    for group, entities in groups.items():
        edges = TopTools_HSequenceOfShape()
        for entity in entities:
            edges.Append(entity_shape(sketch, entity))
        wires = ShapeAnalysis_FreeBounds.ConnectEdgesToWires(edges, tolerance, False, TopTools_HSequenceOfShape())
        loops = []
        for i in range(1, wires.Length()+1):
            wire = topods.Wire(wires.Value(i))
            # A later trim may open a letter. Open remnants become normal sketch
            # curves and never serve as an implicit filling boundary.
            if BRep_Tool.IsClosed(wire):
                maker = BRepBuilderAPI_MakeFace(wire, True)
                if maker.IsDone():
                    loops.append(maker.Face())
        masks[group] = tuple(loops)
    return masks


def _text_counter(outer, lineage, sketch, masks, tolerance, classifier, inside):
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopAbs import TopAbs_EDGE
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.TopoDS import topods
    groups, point = set(), None
    explorer = TopExp_Explorer(outer, TopAbs_EDGE)
    while explorer.More():
        edge = topods.Edge(explorer.Current())
        sources = [part.split(":")[0] for source, part in lineage if source.IsSame(edge)]
        for source in sources:
            groups.add(sketch.entity(source).text_group)
        if point is None:
            curve = BRepAdaptor_Curve(edge)
            point = curve.Value((curve.FirstParameter()+curve.LastParameter())/2)
        explorer.Next()
    if len(groups) != 1 or None in groups or point is None:
        return False
    group, = groups
    depth = sum(classifier(face, point, tolerance).State() == inside for face in masks[group])
    return depth % 2 == 1


def report(sketch, *, tolerance=1e-7, **options):
    analysis = Analysis(sketch, tolerance)
    try:
        _, contours = arrangement(sketch, tolerance)
        return replace(analysis, contours=contours)
    except (ValueError, RuntimeError) as exc:
        return replace(analysis, diagnostics=(Diagnostic("curve_regions", str(exc)),))


def faces(sketch, analysis, contour_ids=()):
    if analysis.sketch != sketch or not analysis.valid:
        raise ProfileError(next((d.message for d in analysis.diagnostics), "Analyse périmée."), analysis)
    if type(contour_ids) not in (tuple, list) or len(set(contour_ids)) != len(contour_ids):
        raise ProfileError("Sélection de zones invalide.", analysis)
    result, _ = arrangement(sketch, analysis.tolerance)
    available = {r.id for r in result.regions}
    if set(contour_ids)-available:
        raise ProfileError("La zone choisie n’existe plus.", analysis)
    return replace(result, regions=tuple(r for r in result.regions if not contour_ids or r.id in contour_ids))


def wires(sketch, analysis):
    from .profile_native import NativeProfiles, NativeContour
    from OCC.Core.BRepTools import breptools
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopAbs import TopAbs_WIRE
    from OCC.Core.TopoDS import topods
    native, regions = [], []
    for face in faces(sketch, analysis).regions:
        outer = NativeContour(face.id, breptools.OuterWire(face.face), 0, None)
        native.append(outer)
        inner, index = [], 0
        explorer = TopExp_Explorer(face.face, TopAbs_WIRE)
        while explorer.More():
            wire = topods.Wire(explorer.Current())
            explorer.Next()
            if wire.IsSame(outer.wire):
                continue
            hole = NativeContour(face.hole_ids[index], wire, 1, face.id)
            native.append(hole)
            inner.append(hole)
            index += 1
        regions.append((outer, tuple(inner)))
    return NativeProfiles(tuple(native), tuple(regions), analysis.tolerance)
