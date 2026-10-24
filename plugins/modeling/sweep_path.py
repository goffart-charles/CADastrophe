"""Ordered continuous paths resolved from explicit UUIDs or producer names."""
from cadastrophe.core.feature_model import BodyStateRef
from cadastrophe.core.reference_geometry import state_for, resolve_topology


def resolve_path(profiles, document, parameters, face):
    edges = []
    for ref in parameters.path:
        if ref.kind == "sketch_curve":
            edges.extend(profiles.path_edges(document, ref.object_id, (ref.entity_id,)))
        elif ref.kind == 'curve':
            from cadastrophe.core.curve_geometry import curve_edge
            from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Copy
            from OCC.Core.TopoDS import topods
            edges.append(topods.Edge(BRepBuilderAPI_Copy(curve_edge(document, document.objects.get(ref.object_id)), True, False).Shape()))
        else:
            state = state_for(document, BodyStateRef(ref.object_id, ref.producer_id))
            edges.append(resolve_topology(document, BodyStateRef(ref.object_id, ref.producer_id), ref.topology, state=state))
    # The profile plane determines which end is the anchor. The native
    # orientation of a named edge is not a durable direction in a recipe.
    from cadastrophe.core.reference_geometry import face_plane
    from cadastrophe.geometry import Vec3
    from OCC.Core.BRepTools import BRepTools_WireExplorer
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.TopExp import topexp
    wire = ordered_wire(edges)
    explorer = BRepTools_WireExplorer(wire)
    a = BRep_Tool.Pnt(explorer.CurrentVertex())
    last = explorer.Current()
    while explorer.More():
        last = explorer.Current()
        explorer.Next()
    b = BRep_Tool.Pnt(topexp.LastVertex(last, True))
    plane = face_plane(face)
    distance = lambda p: abs((Vec3(*p.Coord())-plane.origin).dot(plane.normal))
    flip = distance(a) > 1e-7 and distance(b) <= 1e-7
    return ordered_wire(edges, reverse=flip != parameters.reversed)


def ordered_wire(edges, *, reverse=False, tolerance=1e-7):
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeWire
    from OCC.Core.TopExp import topexp
    from OCC.Core.TopoDS import topods
    from OCC.Core.BRepCheck import BRepCheck_Analyzer

    def ends(edge):
        return tuple(BRep_Tool.Pnt(v) for v in (topexp.FirstVertex(edge, True), topexp.LastVertex(edge, True)))

    edges = tuple(topods.Edge(e) for e in edges)
    if not edges or any(e.IsSame(other) for i, e in enumerate(edges) for other in edges[:i]):
        raise ValueError("Le parcours doit contenir des arêtes distinctes.")
    # Order is part of the recipe. Only direction of the first edge may need
    # reversal to join the second, with a unique endpoint match.
    if len(edges) > 1:
        a, b = ends(edges[0])
        c, d = ends(edges[1])
        at_start = min(a.Distance(c), a.Distance(d)) <= tolerance
        at_end = min(b.Distance(c), b.Distance(d)) <= tolerance
        if not at_start and not at_end:
            raise ValueError("Début du parcours ambigu ou arêtes discontinues ; réordonnez la sélection.")
        if at_start and not at_end:
            edges = (topods.Edge(edges[0].Reversed()), *edges[1:])
    oriented, visited = [], []
    for edge in edges:
        a, b = ends(edge)
        if oriented:
            previous = ends(oriented[-1])[1]
            if previous.Distance(a) > tolerance:
                if previous.Distance(b) > tolerance:
                    raise ValueError("Le parcours est discontinu ; sélectionnez les courbes dans leur ordre.")
                edge = topods.Edge(edge.Reversed())
                a, b = b, a
            if any(a.Distance(old) <= tolerance for old in visited[:-1]):
                raise ValueError("Le parcours contient une branche ou revient sur un sommet déjà parcouru.")
        oriented.append(edge)
        visited.append(a)
    if reverse:
        oriented = [topods.Edge(e.Reversed()) for e in reversed(oriented)]
    maker = BRepBuilderAPI_MakeWire()
    for edge in oriented:
        maker.Add(edge)
        if not maker.IsDone():
            raise ValueError("Impossible d’assembler le parcours continu.")
    wire = maker.Wire()
    if not BRepCheck_Analyzer(wire, True).IsValid():
        raise ValueError("Le parcours n’est pas un fil valide.")
    return wire
