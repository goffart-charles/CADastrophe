"""Exact, checked OCCT revolution of a bounded planar profile."""
from math import radians, isfinite


def revolve_face(face, axis, angle=360., *, reversed=False, tolerance=1e-7, trace=None):
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeRevol
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.BRepBndLib import brepbndlib
    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.GeomAbs import GeomAbs_Plane
    from OCC.Core.GProp import GProp_GProps
    from OCC.Core.gp import gp_Ax1, gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf
    from OCC.Core.TopoDS import topods
    from ..geometry import Vec3
    from .operation_effects import checked_solid
    if type(angle) not in (int, float) or not isfinite(angle) or not 0 < angle <= 360 or type(reversed) is not bool:
        raise ValueError("L’angle de révolution doit appartenir à ]0°, 360°].")
    surface = BRepAdaptor_Surface(topods.Face(face), True)
    if surface.GetType() != GeomAbs_Plane or not BRepCheck_Analyzer(face, True).IsValid():
        raise ValueError("La révolution exige un profil plan fermé et valide.")
    origin, direction = axis
    direction = direction.normalized()
    normal = Vec3(*surface.Plane().Axis().Direction().Coord())
    location = Vec3(*surface.Plane().Location().Coord())
    if abs(normal.dot(direction)) > 1e-9 or abs((origin-location).dot(normal)) > tolerance:
        raise ValueError("L’axe de révolution doit appartenir au plan du profil.")
    radial = direction.cross(normal).normalized()
    frame = gp_Ax3(gp_Pnt(*origin.tuple()), gp_Dir(*normal.tuple()), gp_Dir(*radial.tuple()))
    transform = gp_Trsf()
    transform.SetTransformation(frame)
    aligned = BRepBuilderAPI_Transform(face, transform, True).Shape()
    bounds = Bnd_Box()
    bounds.SetGap(0.)
    brepbndlib.AddOptimal(aligned, bounds, False, False)
    low, _, _, high, _, _ = bounds.Get()
    if low < -tolerance and high > tolerance:
        raise ValueError("Le profil traverse l’axe de révolution : volume auto-intersecté refusé.")
    builder = BRepPrimAPI_MakeRevol(face, gp_Ax1(gp_Pnt(*origin.tuple()), gp_Dir(*direction.tuple())),
                                   radians(angle)*(-1 if reversed else 1), True)
    if not builder.IsDone():
        raise ValueError("Impossible de construire la révolution.")
    solid = checked_solid(builder.Shape())
    area, volume = GProp_GProps(), GProp_GProps()
    brepgprop.SurfaceProperties(face, area, 1e-10)
    brepgprop.VolumeProperties(solid, volume, 1e-10, True)
    centroid = Vec3(*area.CentreOfMass().Coord())
    expected = abs((centroid-origin).dot(radial))*area.Mass()*radians(angle)
    if expected <= tolerance**3 or abs(volume.Mass()-expected) > max(1e-7*expected, tolerance**3):
        raise ValueError("Le volume de la révolution est nul ou incohérent avec le profil.")
    if trace is not None:
        trace.revolution(builder, face, solid)
    return solid
