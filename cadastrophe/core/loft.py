"""OCCT section lofts, with explicit contour correspondence and no input mutation."""
from math import isfinite

from .native_naming import NamingTrace, subshapes
from .operation_effects import checked_solid, checked_solids
from .native_boolean import build_boolean


def _mass(shape):
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps
    props = GProp_GProps()
    brepgprop.VolumeProperties(shape, props, True)
    return props.Mass()


def _checked(shape):
    from OCC.Core.BOPAlgo import BOPAlgo_ArgumentAnalyzer
    checked_solid(shape)
    checker = BOPAlgo_ArgumentAnalyzer()
    checker.SetShape1(shape)
    checker.SetArgumentTypeMode(False)
    checker.SetSelfInterMode(True)
    checker.Perform()
    if checker.HasErrors() or checker.HasFaulty():
        raise ValueError("Le lissage présente une auto-intersection ou une frontière incohérente.")
    return shape


def section_wires(face, source_id, sources):
    """Attach source entity UUIDs to boundaries before OCCT compatibility work."""
    from OCC.Core.BRepTools import breptools, BRepTools_WireExplorer
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopAbs import TopAbs_WIRE
    from OCC.Core.TopoDS import topods
    outer = breptools.OuterWire(face)
    wires, iterator = [outer], TopExp_Explorer(face, TopAbs_WIRE)
    while iterator.More():
        wire = topods.Wire(iterator.Current())
        if not wire.IsSame(outer):
            wires.append(wire)
        iterator.Next()
    trace, result = NamingTrace(source_id, sources=sources), []
    for wire in wires:
        explorer, edges = BRepTools_WireExplorer(wire), []
        while explorer.More():
            edge = explorer.Current()
            edges.append((edge, trace._source(edge, face)))
            explorer.Next()
        result.append((wire, tuple(edges)))
    return tuple(result)


def aligned_wires(face, section, sources, matching):
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeWire
    from OCC.Core.TopoDS import topods
    raw = section_wires(face, section.source.sketch_id, sources)
    if not section.anchors:
        if len(raw) > 2:
            raise ValueError("Plusieurs trous : indiquez leur correspondance par des ancres de courbes.")
        return raw
    if len(section.anchors) != len(raw):
        raise ValueError("La section a changé de nombre de contours ; réassociez ses ancres.")
    ordered, used = [], set()
    for i, (anchor, reverse) in enumerate(zip(section.anchors, section.reversed)):
        matches = [j for j, (_, edges) in enumerate(raw) if any(anchor in key.split(",") for _, key in edges)]
        if len(matches) != 1 or matches[0] in used or (i == 0) != (matches[0] == 0):
            raise ValueError("Ancre de contour absente ou ambiguë ; choisissez une courbe de la frontière concernée.")
        index = matches[0]
        used.add(index)
        wire, edges = raw[index]
        if matching == "explicit":
            starts = [j for j, (_, key) in enumerate(edges) if anchor in key.split(",")]
            if len(starts) != 1:
                raise ValueError("L’ancre est fractionnée ; choisissez une courbe non ambiguë.")
            start = starts[0]
            edges = (*edges[start:], *edges[:start])
            if reverse:
                # Reversing the first edge preserves the chosen edge anchor;
                # neighbouring boundaries are then traversed in reverse order.
                edges = tuple((topods.Edge(e.Reversed()), key) for e, key in (edges[0], *reversed(edges[1:])))
            maker = BRepBuilderAPI_MakeWire()
            for edge, _ in edges:
                maker.Add(edge)
            if not maker.IsDone():
                raise ValueError("Le contour orienté ne peut pas être assemblé.")
            wire = maker.Wire()
        ordered.append((wire, edges))
    return tuple(ordered)


def loft_faces(faces, sources, parameters, *, trace=None, tolerance=1e-7):
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    from OCC.Core.GeomAbs import GeomAbs_Plane
    from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
    from OCC.Core.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCC.Core.TopoDS import topods

    if len(faces) != len(parameters.sections) or len(sources) != len(faces):
        raise ValueError("Sections géométriques incomplètes.")
    for i, face in enumerate(faces):
        if BRepAdaptor_Surface(topods.Face(face)).GetType() != GeomAbs_Plane or not BRepCheck_Analyzer(face, True).IsValid():
            raise ValueError("Chaque section doit être une région plane fermée et valide.")
        for other in faces[:i]:
            distance = BRepExtrema_DistShapeShape(face, other)
            if not distance.IsDone() or distance.Value() <= tolerance:
                raise ValueError("Les sections doivent être distinctes, sans contact ni intersection.")
    sections = tuple(aligned_wires(face, section, edges, parameters.matching)
                     for face, section, edges in zip(faces, parameters.sections, sources))
    if len({len(wires) for wires in sections}) != 1:
        raise ValueError("Chaque section doit conserver le même nombre de trous.")
    result = None
    try:
        for boundary in range(len(sections[0])):
            wires = tuple(section[boundary] for section in sections)
            if parameters.matching == "explicit" and len({len(edges) for _, edges in wires}) != 1:
                raise ValueError("La correspondance explicite exige le même nombre d’arêtes par contour.")
            maker = BRepOffsetAPI_ThruSections(True, parameters.mode == "ruled", tolerance)
            maker.SetMutableInput(False)
            maker.CheckCompatibility(parameters.matching == "automatic")
            for wire, _ in wires:
                maker.AddWire(wire)
            maker.Build()
            if not maker.IsDone():
                raise ValueError("Impossible de construire le volume entre ces sections.")
            solid = _checked(maker.Shape())
            if trace is not None:
                trace.loft(maker, wires, parameters, solid)
            if result is None:
                result = solid
            else:
                old_mass, cavity_mass = _mass(result), _mass(solid)
                cut = build_boolean(BRepAlgoAPI_Cut, result, solid)
                if not cut.IsDone():
                    raise ValueError("Les ouvertures des sections ne peuvent pas être conservées.")
                solids = checked_solids(cut.Shape())
                if len(solids) != 1:
                    raise ValueError("Les ouvertures divisent le lissage en plusieurs solides.")
                shape = _checked(solids[0])
                removed = old_mass-_mass(shape)
                if not isfinite(removed) or abs(removed-cavity_mass) > max(cavity_mass*1e-7, old_mass*1e-10, tolerance**3):
                    raise ValueError("Les trous se croisent ou sortent de l’enveloppe du lissage.")
                if trace is not None:
                    trace.operation(cut, (result, solid), shape)
                result = shape
    except (ValueError, RuntimeError) as exc:
        raise ValueError(f"Lissage impossible : vérifiez l’ordre, l’orientation et la correspondance des sections. {exc}") from exc
    return result
