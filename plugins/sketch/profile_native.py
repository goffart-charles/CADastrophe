"""On-demand exact OCCT wires, derived from a validated immutable analysis."""
from dataclasses import dataclass
from math import pi, hypot, floor
from .model import Segment,Arc
from .profile_geometry import sample,ends


class ProfileError(ValueError):
    def __init__(self,message,analysis=None):
        super().__init__(message)
        self.analysis = analysis


@dataclass(frozen=True)
class NativeContour:
    id: str
    wire: object
    depth: int
    parent_id: str | None


@dataclass(frozen=True)
class NativeProfiles:
    contours: tuple[NativeContour,...]
    regions: tuple  # (outer NativeContour, tuple[hole NativeContour, ...])
    tolerance: float


def to_wires(sketch,analysis=None,*,tolerance=1e-7):
    from .profiles import analyze
    analysis = analyze(sketch,tolerance=tolerance) if analysis is None else analysis
    if analysis.sketch != sketch:
        raise ProfileError("Analyse périmée : vérifiez à nouveau l’esquisse.",analysis)
    if not analysis.valid:
        message = next((d.message for d in analysis.diagnostics if d.severity == "error"),"Aucun contour fermé valide.")
        raise ProfileError(message,analysis)
    sketch = analysis.geometry
    from OCC.Core.BRep import BRep_Builder,BRep_Tool
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeVertex,BRepBuilderAPI_MakeEdge,BRepBuilderAPI_MakeWire
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    from OCC.Core.Geom import Geom_Line,Geom_Circle
    from OCC.Core.TopoDS import topods
    from OCC.Core.gp import gp_Pnt,gp_Dir,gp_Ax2,gp_Circ
    frame = sketch.support.frame
    def world(p):
        return gp_Pnt(*frame.to_world(p.u,p.v).tuple())
    builder = BRep_Builder()
    vertex_buckets = {}
    def make_vertex(point):
        # Arrangement contours may touch (a disk tangent to its surrounding
        # region). Their common point must be one native topological vertex.
        cell = floor(point.u/analysis.tolerance), floor(point.v/analysis.tolerance)
        if analysis.profile_sketch is not None:
            from .profile_geometry import distance
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for other, vertex in vertex_buckets.get((cell[0]+dx, cell[1]+dy), ()):
                        if distance(point, other) <= analysis.tolerance:
                            return vertex
        vertex = BRepBuilderAPI_MakeVertex(world(point)).Vertex()
        builder.UpdateVertex(vertex,max(1e-7,analysis.tolerance*1.01))
        vertex_buckets.setdefault(cell, []).append((point, vertex))
        return vertex
    native = []
    try:
        for contour in analysis.contours:
            vertices = []
            for use in contour.edges:
                e = sketch.entity(use.entity_id)
                p = ends(e)[1 if use.reverse else 0] if ends(e) else sample(e,0.)
                # Merge only endpoints already certified within this tolerance;
                # keep the original analytic curves, no geometric healing.
                vertices.append(make_vertex(p))
            wire_builder = BRepBuilderAPI_MakeWire()
            for i,use in enumerate(contour.edges):
                e = sketch.entity(use.entity_id)
                a,b = vertices[i],vertices[(i+1)%len(vertices)]
                forward = not use.reverse
                if type(e) is Segment:
                    dx,dy = e.end.u-e.start.u,e.end.v-e.start.v
                    direction = frame.u*dx+frame.v*dy
                    curve = Geom_Line(world(e.start),gp_Dir(*direction.tuple()))
                    first,last = 0.,hypot(dx,dy)
                else:
                    curve = Geom_Circle(gp_Circ(gp_Ax2(world(e.center),gp_Dir(*frame.normal.tuple()),gp_Dir(*frame.u.tuple())),e.radius))
                    first,last = (min(e.start_angle,e.start_angle+e.sweep),max(e.start_angle,e.start_angle+e.sweep)) if type(e) is Arc else (0.,2*pi)
                    if type(e) is Arc and e.sweep < 0:
                        forward = not forward
                edge_builder = BRepBuilderAPI_MakeEdge(curve,a if forward else b,b if forward else a,first,last)
                if not edge_builder.IsDone():
                    raise ProfileError("Impossible de construire une arête du contour.",analysis)
                edge = edge_builder.Edge()
                if not forward:
                    edge = topods.Edge(edge.Reversed())
                wire_builder.Add(edge)
                if not wire_builder.IsDone():
                    raise ProfileError("Les arêtes du contour ne forment pas un fil connecté.",analysis)
            wire = wire_builder.Wire()
            if not BRep_Tool.IsClosed(wire) or not BRepCheck_Analyzer(wire,True).IsValid():
                raise ProfileError("Le contour construit n’est pas fermé ou n’est pas valide.",analysis)
            native.append(NativeContour(contour.id,wire,contour.depth,contour.parent_id))
    except (RuntimeError,ValueError) as exc:
        if isinstance(exc,ProfileError):
            raise
        raise ProfileError(f"Construction du profil impossible : {exc}",analysis) from exc
    by_id = {c.id:c for c in native}
    regions = tuple((by_id[outer.id],tuple(by_id[h.id] for h in holes)) for outer,holes in analysis.regions)
    return NativeProfiles(tuple(native),regions,analysis.tolerance)
