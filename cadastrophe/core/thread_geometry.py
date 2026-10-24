"""Derived profile, exact helix and checked cutting volumes of a thread."""
from math import pi, sin, cos, tan, radians

from .native_naming import subshapes, resolve, static_topology
from .operation_effects import checked_solid, checked_solids
from .native_boolean import build_boolean
from ..geometry import Vec3, WorkPlane


def cylindrical_support(shape, face, parameters=None):
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.GeomAbs import GeomAbs_Cylinder
    from OCC.Core.BRepTools import breptools
    from OCC.Core.TopoDS import topods
    from OCC.Core.BRepClass3d import BRepClass3d_SolidClassifier
    from OCC.Core.TopAbs import TopAbs_IN
    from OCC.Core.gp import gp_Pnt
    checked_solid(shape)
    surface = BRepAdaptor_Surface(topods.Face(face), True)
    if surface.GetType() != GeomAbs_Cylinder:
        raise ValueError("Le filetage exige une face cylindrique.")
    cylinder = surface.Cylinder()
    u0, u1, v0, v1 = breptools.UVBounds(topods.Face(face))
    if abs(u1 - u0 - 2 * pi) > 1e-6 or not 0 < v1 - v0 < 1e9:
        raise ValueError("Choisissez une portée cylindrique complète et de longueur finie.")
    # Establish the material side geometrically, independent of face orientation.
    point = surface.Value((u0 + u1) / 2, (v0 + v1) / 2)
    axis = Vec3(*cylinder.Axis().Direction().Coord())
    center = Vec3(*cylinder.Location().Coord()) + axis * ((v0 + v1) / 2)
    radial = (Vec3(*point.Coord()) - center).normalized()
    sample = Vec3(*point.Coord()) + radial * max(1e-5, cylinder.Radius() * 1e-5)
    internal = BRepClass3d_SolidClassifier(shape, gp_Pnt(*sample.tuple()), 1e-7).State() == TopAbs_IN
    if parameters is not None:
        p = parameters
        if internal != p.internal:
            raise ValueError("La face sélectionnée ne correspond pas au type interne/externe demandé.")
        radius = cylinder.Radius()
        if not internal and abs(2 * radius - p.diameter) > 1e-5:
            raise ValueError("Le diamètre de la tige doit correspondre au diamètre nominal ; modifiez le support ou le filetage.")
        if internal and not p.diameter / 2 - p.depth - 1e-6 <= radius < p.diameter / 2 - 1e-5:
            raise ValueError("Le diamètre de l’avant-trou est incompatible avec le profil choisi.")
        if p.offset + p.length > v1 - v0 + 1e-6:
            raise ValueError("La longueur filetée et son décalage dépassent la portée cylindrique.")
    return cylinder, v0, v1, internal


def thread_frame(cylinder, v0, v1, p):
    direction = Vec3(*cylinder.Axis().Direction().Coord())
    origin = Vec3(*cylinder.Location().Coord())
    u = Vec3(*cylinder.Position().XDirection().Coord())
    origin += direction * (v1 - p.offset if p.reversed else v0 + p.offset)
    direction = direction * (-1 if p.reversed else 1)
    return WorkPlane("Filetage", origin, u, direction.cross(u))


def thread_tools_steps(shape, parameters, *, topology=None, trace=None):
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeVertex
    from OCC.Core.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeCone
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
    from OCC.Core.gp import gp_Ax2, gp_Pnt, gp_Dir
    from OCC.Core.GC import GC_MakeArcOfCircle
    from OCC.Core.TopAbs import TopAbs_FACE
    p = parameters
    face = resolve(shape, p.face.topology, topology or static_topology(shape))
    cylinder, v0, v1, _ = cylindrical_support(shape, face, p)
    frame = thread_frame(cylinder, v0, v1, p)
    sign = -1 if p.internal else 1
    nominal = p.diameter / 2 + (p.clearance if p.internal else -p.clearance)
    floor = nominal if p.internal else nominal - p.depth
    angle = radians(p.angle / 2)
    pad = min(.1 * p.pitch, floor / 4, p.crest / (8 * tan(angle)))
    tangent_s = p.root_radius * (1 - sin(angle))
    half = p.root_radius * cos(angle) if p.root_radius else p.root_flat / 2
    top_s = p.depth + pad
    top_half = half + (top_s - tangent_s) * tan(angle)
    # Enlarge before clipping so the requested axial limits contain full profiles.
    margin = p.lead
    turns = (p.length + 2 * margin) / p.lead
    outputs = []

    def axis(z):
        return gp_Ax2(gp_Pnt(*(frame.origin + frame.normal * z).tuple()), gp_Dir(*frame.normal.tuple()), gp_Dir(*frame.u.tuple()))

    def record_primitive(solid, prefix):
        if trace is not None:
            from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
            from OCC.Core.GeomAbs import GeomAbs_Plane
            from OCC.Core.TopoDS import topods
            names = []
            for f in subshapes(solid, "Face"):
                surface = BRepAdaptor_Surface(topods.Face(f))
                role = "wall"
                if surface.GetType() == GeomAbs_Plane:
                    z = (Vec3(*surface.Plane().Location().Coord()) - frame.origin).dot(frame.normal)
                    role = "entry" if z < p.length / 2 else "exit"
                names.append((f"{trace.feature_id}:thread:{prefix}:{role}", f))
            trace._record(solid, names)
        return solid

    # Cylinder caps trim the helical profile at exact requested axial limits.
    clip = record_primitive(BRepPrimAPI_MakeCylinder(axis(0), p.diameter, p.length).Shape(), "limits")
    envelope = None
    if p.lead_in or p.lead_out:
        # Smooth depth run-in/out is an axial conical envelope, independent of
        # the phase of the helix. It also works inside blind holes.
        start_radius = cylinder.Radius()
        full_radius = nominal if p.internal else floor
        pieces = []
        sections = ((0., p.lead_in, start_radius, full_radius, "lead_in"),
                    (p.lead_in, p.length-p.lead_in-p.lead_out, full_radius, full_radius, "full"),
                    (p.length-p.lead_out, p.lead_out, full_radius, start_radius, "lead_out"))
        for z, height, r0, r1, role in sections:
            if height <= 1e-7:
                continue
            piece = (BRepPrimAPI_MakeCylinder(axis(z), r0, height).Shape() if abs(r0-r1) < 1e-10
                     else BRepPrimAPI_MakeCone(axis(z), r0, r1, height).Shape())
            pieces.append(record_primitive(piece, role))
        envelope = pieces[0]
        for piece in pieces[1:]:
            builder = build_boolean(BRepAlgoAPI_Fuse, envelope, piece)
            merged, = checked_solids(builder.Shape())
            if trace is not None:
                trace.operation(builder, (envelope, piece), merged)
            envelope = merged
    for start in range(p.starts):
        # Phase locates the mating ridge (external) / valley (internal).
        phase = p.phase % 360 + 360 * start / p.starts + (0 if p.internal else 180 / p.starts)
        theta = radians(phase)
        radial = frame.u * cos(theta) + frame.v * sin(theta)
        def point(s, z):
            return gp_Pnt(*(frame.origin + radial * (floor + sign * s) + frame.normal * (z - margin)).tuple())
        low, high = point(tangent_s, -half), point(tangent_s, half)
        low_vertex = BRepBuilderAPI_MakeVertex(low).Vertex()
        high_vertex = BRepBuilderAPI_MakeVertex(high).Vertex() if half else low_vertex
        edges = []
        roles = []
        if p.root_radius:
            edges.append(BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(low, point(0, 0), high).Value(), low_vertex, high_vertex).Edge())
            roles.append("root")
        elif p.root_flat:
            edges.append(BRepBuilderAPI_MakeEdge(low_vertex, high_vertex).Edge())
            roles.append("root")
        a, b = point(top_s, top_half), point(top_s, -top_half)
        a_vertex, b_vertex = BRepBuilderAPI_MakeVertex(a).Vertex(), BRepBuilderAPI_MakeVertex(b).Vertex()
        for first, last, role in ((high_vertex, a_vertex, "flank_plus"), (a_vertex, b_vertex, "opening"), (b_vertex, low_vertex, "flank_minus")):
            edges.append(BRepBuilderAPI_MakeEdge(first, last).Edge())
            roles.append(role)
        section = BRepBuilderAPI_MakeWire()
        for edge in edges:
            section.Add(edge)
        # Frenet transport of a cylindrical helix is a screw displacement; keeping
        # the initial axial section oblique to the tangent preserves its angle.
        from .curve_geometry import helix_edge
        helix_frame = WorkPlane(frame.name, frame.origin - frame.normal * margin, frame.u, frame.v)
        path = BRepBuilderAPI_MakeWire(helix_edge(helix_frame, max(floor, 1e-4), p.lead, turns,
                                               start_angle=phase, clockwise=p.left_handed, tolerance=1e-9)).Wire()
        maker = BRepOffsetAPI_MakePipeShell(path)
        maker.SetTolerance(1e-8, 1e-9, 1e-7)
        maker.SetMode(True)
        maker.Add(section.Wire(), False, False)
        yield start, p.starts, "Construction du filet hélicoïdal"
        maker.Build()
        if not maker.IsDone() or not maker.MakeSolid():
            raise ValueError("Impossible de fermer le volume de coupe du filetage.")
        tool = checked_solid(maker.Shape())
        if trace is not None:
            names = []
            for edge, role in zip(edges, roles):
                names.extend((f"{trace.feature_id}:thread:{start}:{role}", f)
                             for f in maker.Generated(edge) if f.ShapeType() == TopAbs_FACE)
            # End caps are outside the axial clip, so only side faces survive.
            trace._record(tool, names)
        yield start, p.starts, "Ajustement des extrémités du filet"
        cut = build_boolean(BRepAlgoAPI_Common, tool, clip)
        if not cut.IsDone():
            raise ValueError("Impossible de limiter la longueur du filetage.")
        solids = checked_solids(cut.Shape())
        if len(solids) != 1:
            raise ValueError("Le filet doit produire un volume de coupe continu.")
        result = solids[0]
        if trace is not None:
            trace.operation(cut, (tool, clip), result)
        if envelope is not None:
            builder = build_boolean(BRepAlgoAPI_Common if p.internal else BRepAlgoAPI_Cut, result, envelope)
            trimmed, = checked_solids(builder.Shape())
            if trace is not None:
                trace.operation(builder, (result, envelope), trimmed)
            result = trimmed
        outputs.append(result)
    if p.clearance:
        bore = record_primitive(BRepPrimAPI_MakeCylinder(axis(0), cylinder.Radius()+p.clearance if p.internal else nominal,
                                                       p.length).Shape(), "clearance")
        if p.internal:
            crest_tool = bore
        else:
            clearance_limit = record_primitive(BRepPrimAPI_MakeCylinder(axis(0), cylinder.Radius()+pad,
                                                                       p.length).Shape(), "clearance_limit")
            builder = build_boolean(BRepAlgoAPI_Cut, clearance_limit, bore)
            crest_tool, = checked_solids(builder.Shape())
            if trace is not None:
                trace.operation(builder, (clearance_limit, bore), crest_tool)
        if envelope is not None:
            builder = build_boolean(BRepAlgoAPI_Common if p.internal else BRepAlgoAPI_Cut, crest_tool, envelope)
            trimmed, = checked_solids(builder.Shape())
            if trace is not None:
                trace.operation(builder, (crest_tool, envelope), trimmed)
            crest_tool = trimmed
        # Union the relief with the groove to avoid overlapping cut volumes.
        for tool in outputs:
            builder = build_boolean(BRepAlgoAPI_Fuse, crest_tool, tool)
            merged, = checked_solids(builder.Shape())
            if trace is not None:
                trace.operation(builder, (crest_tool, tool), merged)
            crest_tool = merged
        outputs = [crest_tool]
    if not p.internal:
        # The small overlap needed by a boolean tool must not cut another nearby
        # region of the same body. Keep a geometric scope check rather than
        # clipping the swept tool on an almost tangent cylindrical surface.
        from .mass_properties import precise_volume
        from .operation_effects import compound
        yield p.starts, p.starts+1, "Vérification de la portée du filetage"
        domain = BRepPrimAPI_MakeCylinder(axis(0), cylinder.Radius()+1e-6, p.length).Shape()
        outside = build_boolean(BRepAlgoAPI_Cut, shape, domain)
        if not outside.IsDone():
            raise ValueError("Impossible de vérifier la portée du filetage.")
        overlap = build_boolean(BRepAlgoAPI_Common, outside.Shape(), compound(tuple(outputs)))
        if not overlap.IsDone() or abs(precise_volume(overlap.Shape())) > max(1e-7, precise_volume(shape)*1e-8):
            raise ValueError("Le volume de coupe touche une autre zone du corps ; modifiez la portée ou le profil du filetage.")
    return tuple(outputs)
