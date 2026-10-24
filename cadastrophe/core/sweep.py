"""Checked OCCT pipe shells with explicit section orientation and frame law."""
from math import radians

from ..geometry import Vec3
from .native_naming import subshapes
from .operation_effects import checked_solid, checked_solids
from .native_boolean import build_boolean


def checked_sweep(shape):
    from OCC.Core.BOPAlgo import BOPAlgo_ArgumentAnalyzer
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.GeomAbs import GeomAbs_Torus
    from OCC.Core.TopoDS import topods
    checked_solid(shape)
    for face in subshapes(shape, "Face"):
        surface = BRepAdaptor_Surface(topods.Face(face))
        if surface.GetType() == GeomAbs_Torus and surface.Torus().MinorRadius() >= surface.Torus().MajorRadius()-1e-7:
            raise ValueError("Le rayon du profil atteint le rayon de courbure du parcours : tore singulier refusé.")
    check = BOPAlgo_ArgumentAnalyzer()
    check.SetShape1(shape)
    check.SetArgumentTypeMode(False)
    check.SetSelfInterMode(True)
    check.Perform()
    if check.HasErrors() or check.HasFaulty():
        raise ValueError("Le balayage présente une auto-intersection ou une frontière incohérente.")
    return shape


def sweep_face(face, path, parameters, *, tolerance=1e-7, trace=None):
    from .computation import complete
    return complete(sweep_face_steps(face, path, parameters, tolerance=tolerance, trace=trace))


def sweep_face_steps(face, path, parameters, *, tolerance=1e-7, trace=None):
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.BRepBuilderAPI import (BRepBuilderAPI_Transform, BRepBuilderAPI_RightCorner, BRepBuilderAPI_RoundCorner)
    from OCC.Core.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
    from OCC.Core.BRepTools import BRepTools_WireExplorer, breptools
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.GeomAbs import GeomAbs_Plane
    from OCC.Core.TopAbs import TopAbs_FORWARD, TopAbs_REVERSED, TopAbs_WIRE
    from OCC.Core.TopExp import topexp, TopExp_Explorer
    from OCC.Core.TopoDS import topods
    from OCC.Core.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut

    surface = BRepAdaptor_Surface(topods.Face(face), True)
    if surface.GetType() != GeomAbs_Plane or not BRepCheck_Analyzer(face, True).IsValid():
        raise ValueError("Le balayage exige un profil plan fermé et valide.")
    explorer = BRepTools_WireExplorer(path)
    first = explorer.Current()
    first_vertex = explorer.CurrentVertex()
    last = first
    while explorer.More():
        last = explorer.Current()
        explorer.Next()
    curve = BRepAdaptor_Curve(first)
    backwards = first.Orientation() == TopAbs_REVERSED
    start = curve.Value(curve.LastParameter() if backwards else curve.FirstParameter())
    derivative = gp_Vec()
    curve.D1(curve.LastParameter() if backwards else curve.FirstParameter(), gp_Pnt(), derivative)
    tangent = Vec3(*derivative.Coord()) * (-1 if backwards else 1)
    if tangent.norm() < tolerance:
        raise ValueError("La tangente du parcours est indéfinie au départ.")
    tangent = tangent.normalized()
    plane = surface.Plane()
    normal = Vec3(*plane.Axis().Direction().Coord())
    location = Vec3(*plane.Location().Coord())
    end = BRep_Tool.Pnt(topexp.LastVertex(last, True))
    original_start = end if parameters.reversed else start
    if abs((Vec3(*original_start.Coord())-location).dot(normal)) > tolerance:
        raise ValueError("Le début du parcours doit appartenir au plan du profil.")
    if parameters.orientation == "keep" and abs(normal.dot(tangent)) < 1e-8:
        raise ValueError("Le profil conservé est parallèle au parcours ; choisissez l’orientation normale.")

    original_edges = subshapes(face, "Edge")
    keys = tuple(trace._source(edge, face) for edge in original_edges) if trace is not None else ()
    # Rotation is about the explicit path anchor, never the profile centroid.
    rotation = gp_Trsf()
    rotation.SetRotation(gp_Ax1(original_start, gp_Dir(*normal.tuple())), radians(parameters.roll))
    translation = gp_Trsf()
    translation.SetTranslation(gp_Vec(original_start, start))
    transform = translation.Multiplied(rotation)
    placement = BRepBuilderAPI_Transform(face, transform, True)
    face = topods.Face(placement.Shape())
    source_keys = tuple((placement.ModifiedShape(edge), key) for edge, key in zip(original_edges, keys))
    outer = breptools.OuterWire(face)
    wires, iterator = [outer], TopExp_Explorer(face, TopAbs_WIRE)
    while iterator.More():
        wire = topods.Wire(iterator.Current())
        if not wire.IsSame(outer):
            wires.append(wire)
        iterator.Next()
    result = None
    try:
        for wire in wires:
            wire = topods.Wire(wire.Oriented(TopAbs_FORWARD))
            maker = BRepOffsetAPI_MakePipeShell(path)
            maker.SetTolerance(tolerance, tolerance, 1e-7)
            if parameters.transport == "fixed":
                axis = tangent if parameters.orientation == "normal" else normal
                maker.SetMode(gp_Ax2(start, gp_Dir(*axis.tuple())))
            else:
                maker.SetMode(parameters.transport == "frenet")
            maker.SetTransitionMode(BRepBuilderAPI_RightCorner if parameters.transition == "right" else BRepBuilderAPI_RoundCorner)
            maker.Add(wire, first_vertex, False, parameters.orientation == "normal")
            yield 0, 4, "Construction du balayage"
            maker.Build()
            if not maker.IsDone() or not maker.MakeSolid():
                raise ValueError("Impossible de fermer le volume balayé avec ce transport et ce raccord.")
            yield 1, 4, "Vérification du volume balayé"
            solid = checked_sweep(maker.Shape())
            if trace is not None:
                trace.sweep(maker, wire, solid, source_keys)
            if result is None:
                result = solid
            else:
                yield 2, 4, "Conservation des ouvertures du profil"
                cut = build_boolean(BRepAlgoAPI_Cut, result, solid)
                if not cut.IsDone():
                    raise ValueError("Les ouvertures du profil ne peuvent pas être conservées.")
                solids = checked_solids(cut.Shape())
                if len(solids) != 1:
                    raise ValueError("Les ouvertures divisent le balayage en plusieurs solides.")
                shape = checked_sweep(solids[0])
                if trace is not None:
                    trace.operation(cut, (result, solid), shape)
                result = shape
            yield 3, 4, "Volume balayé vérifié"
    except (ValueError, RuntimeError) as exc:
        raise ValueError(f"Balayage impossible : vérifiez le profil, le parcours et les raccords. {exc}") from exc
    return result
