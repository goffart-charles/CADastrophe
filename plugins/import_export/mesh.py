"""OCCT tessellation of detached solids; all coordinates remain in millimetres."""
from dataclasses import dataclass
from math import isfinite, sqrt, radians

from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Copy
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.BRepTools import breptools
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_SOLID, TopAbs_REVERSED
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.TopoDS import topods

from cadastrophe.core.operation_effects import checked_solids

MAX_TRIANGLES = 2_000_000
QUALITY = {"low": (.5, 30.), "standard": (.1, 15.), "high": (.02, 5.)}


@dataclass(frozen=True)
class MeshOptions:
    quality: str = "standard"
    deflection: float = .1
    angle: float = 15.

    def tolerances(self):
        if self.quality in QUALITY:
            return QUALITY[self.quality]
        if (self.quality != "custom" or not isfinite(self.deflection) or not isfinite(self.angle)
                or not 1e-6 <= self.deflection <= 1e4 or not .1 <= self.angle <= 90):
            raise ValueError("Qualité invalide : déviation de 0,000001 à 10 000 mm et angle de 0,1 à 90°.")
        return self.deflection, self.angle


def parts(shape, kind):
    explorer = TopExp_Explorer(shape, kind)
    while explorer.More():
        yield explorer.Current()
        explorer.Next()


def normal(a, b, c):
    u, v = tuple(y-x for x,y in zip(a,b)), tuple(y-x for x,y in zip(a,c))
    cross = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
    length = sqrt(sum(x*x for x in cross))
    if not isfinite(length) or length == 0:
        raise ValueError("Le maillage contient un triangle dégénéré.")
    return tuple(x/length for x in cross)


@dataclass(frozen=True)
class Mesh:
    vertices: tuple
    triangles: tuple

    def facets(self):
        for triangle in self.triangles:
            points = tuple(self.vertices[i] for i in triangle)
            yield normal(*points), points

    def validate_closed(self):
        edges = {}
        for triangle in self.triangles:
            for a,b in zip(triangle,(*triangle[1:],triangle[0])):
                key = tuple(sorted((a,b)))
                count,balance = edges.get(key,(0,0))
                edges[key] = count+1,balance+(1 if a<b else -1)
        if any(count != 2 or balance != 0 for count,balance in edges.values()):
            raise ValueError("Maillage non fermé ou non manifold ; export refusé. Essayez une autre qualité ou corrigez le corps.")


def triangulate(shape, options=MeshOptions()):
    linear, angular = options.tolerances()
    checked_solids(shape)
    # Even direct callers cannot mutate the source's triangulations or caches.
    detached = BRepBuilderAPI_Copy(shape, True, False).Shape()
    breptools.Clean(detached)
    mesher = BRepMesh_IncrementalMesh(detached, linear, False, radians(angular), False)
    if not mesher.IsDone():
        raise ValueError("Le maillage a échoué. Essayez une qualité moins élevée.")
    vertices, triangles, indices = [], [], {}
    # Mesh shared edges have identical samples; quantization only absorbs their
    # floating point evaluation noise. It never changes exported coordinates.
    def vertex(point):
        coords = tuple(point.Coord())
        if not all(isfinite(v) for v in coords):
            raise ValueError("Coordonnées non finies dans le maillage.")
        key = tuple(round(v,9) for v in coords)
        if key not in indices:
            indices[key] = len(vertices)
            vertices.append(coords)
        return indices[key]
    # Never weld distinct solids together, even when their vertices coincide.
    faces = []
    for solid_index,solid in enumerate(parts(detached,TopAbs_SOLID)):
        faces.extend((solid_index,face) for face in parts(solid,TopAbs_FACE))
    previous_solid = None
    for solid_index,raw in faces:
        if solid_index != previous_solid:
            indices.clear()
            previous_solid = solid_index
        face, location = topods.Face(raw), TopLoc_Location()
        triangulation = BRep_Tool.Triangulation(face, location)
        if triangulation is None or not triangulation.NbTriangles():
            raise ValueError("Une face n’a pas pu être triangulée ; export incomplet refusé.")
        if len(triangles)+triangulation.NbTriangles() > MAX_TRIANGLES:
            raise ValueError("Le maillage dépasse 2 millions de triangles ; réduisez la qualité.")
        transform = location.Transformation()
        nodes = [vertex(triangulation.Node(i).Transformed(transform)) for i in range(1,triangulation.NbNodes()+1)]
        for i in range(1,triangulation.NbTriangles()+1):
            a,b,c = (nodes[j-1] for j in triangulation.Triangle(i).Get())
            if face.Orientation() == TopAbs_REVERSED:
                b,c = c,b
            if len({a,b,c}) != 3:
                # OCCT may emit UV seam triangles collapsed at sphere/cone poles.
                # They have no surface area. The final closure check proves that
                # discarding them did not open or corrupt the actual mesh.
                continue
            normal(vertices[a],vertices[b],vertices[c])
            triangles.append((a,b,c))
    if not triangles:
        raise ValueError("Le corps ne contient aucune face exportable.")
    mesh = Mesh(tuple(vertices),tuple(triangles))
    mesh.validate_closed()
    return mesh
