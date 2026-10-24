"""Native construction curves; no tessellation used as the model geometry."""
from functools import lru_cache
from math import ceil, cos, sin, radians, pi, sqrt

from ..geometry import Vec3
from .curve_model import CurveObject


TOLERANCE = 1e-6


def curve_edge(document, obj):
    if type(obj) is not CurveObject:
        raise ValueError('La référence ne désigne pas une courbe de construction.')
    frame = None
    if obj.parameters.plane_id is not None:
        source = document.objects.get(obj.parameters.plane_id)
        if source is None or source.plane is None:
            raise ValueError('Le plan de départ de la courbe est absent.')
        from .reference_geometry import resolve_plane
        frame = resolve_plane(document, source)
    return build_curve(obj.parameters, frame)


@lru_cache(maxsize=64)
def build_curve(parameters, frame):
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    from OCC.Core.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Pnt2d, gp_Dir2d
    p = parameters
    if p.kind == 'helix':
        from dataclasses import replace
        edge = helix_edge(replace(frame, origin=frame.to_world(p.center_u, p.center_v)),
                          p.radius, p.pitch, p.turns, conical_angle=p.conical_angle,
                          start_angle=p.start_angle, clockwise=p.clockwise)
    elif p.kind == 'points':
        edge = _interpolate(p.points, p.closed)
    else:
        direction = -1 if p.clockwise else 1
        angle = radians(p.start_angle)
        def point(t):
            radius = p.radius+p.radial_pitch*p.turns*t
            theta = angle+direction*2*pi*p.turns*t
            return frame.to_world(p.center_u+radius*cos(theta), p.center_v+radius*sin(theta))
        # A smooth native spline is refined and checked against its analytic
        # recipe. No sampled polyline is accepted as a spiral.
        count = max(16, ceil(p.turns*24))
        while True:
            if count > 8192:
                raise ValueError('La spirale dépasse la précision ou la complexité autorisée ; réduisez ses dimensions ou ses tours.')
            points = tuple(point(i/count) for i in range(count+1))
            edge, curve = _interpolate(points, False, parameters=tuple(i/count for i in range(count+1)), return_curve=True)
            error = max(gp_Pnt(*point((i+.5)/count).tuple()).Distance(curve.Value((i+.5)/count)) for i in range(count))
            if error <= TOLERANCE:
                break
            count *= 2
    if edge.IsNull() or not BRepCheck_Analyzer(edge, True).IsValid():
        raise ValueError('La courbe calculée n’est pas une géométrie valide.')
    return edge


def helix_edge(frame, radius, pitch, turns, *, conical_angle=0., start_angle=0., clockwise=False, tolerance=TOLERANCE):
    """One exact surface-based helix constructor for curves and thread tools."""
    from OCC.Core.Geom import Geom_CylindricalSurface, Geom_ConicalSurface
    from OCC.Core.Geom2d import Geom2d_Line
    from OCC.Core.BRepLib import breplib
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCC.Core.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Pnt2d, gp_Dir2d
    axes = gp_Ax3(gp_Pnt(*frame.origin.tuple()), gp_Dir(*frame.normal.tuple()), gp_Dir(*frame.u.tuple()))
    angle = radians(conical_angle)
    surface = Geom_ConicalSurface(axes, angle, radius) if angle else Geom_CylindricalSurface(axes, radius)
    advance = pitch / cos(angle)
    line = Geom2d_Line(gp_Pnt2d(radians(start_angle), 0.), gp_Dir2d(-2*pi if clockwise else 2*pi, advance))
    length = turns * sqrt((2*pi)**2 + advance**2)
    edge = BRepBuilderAPI_MakeEdge(line, surface, 0., length).Edge()
    if not breplib.BuildCurves3d(edge, tolerance):
        raise ValueError('Impossible de construire la courbe 3D de l’hélice.')
    return edge


def _interpolate(points, closed, *, parameters=None, return_curve=False):
    from OCC.Core.GeomAPI import GeomAPI_Interpolate
    from OCC.Core.TColgp import TColgp_HArray1OfPnt
    from OCC.Core.TColStd import TColStd_HArray1OfReal
    from OCC.Core.gp import gp_Pnt
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    array = TColgp_HArray1OfPnt(1, len(points))
    for index, point in enumerate(points, 1):
        array.SetValue(index, gp_Pnt(*point.tuple()))
    if parameters is None:
        interpolator = GeomAPI_Interpolate(array, closed, 1e-8)
    else:
        values = TColStd_HArray1OfReal(1, len(parameters))
        for index, value in enumerate(parameters, 1):
            values.SetValue(index, value)
        interpolator = GeomAPI_Interpolate(array, values, closed, 1e-8)
    interpolator.Perform()
    if not interpolator.IsDone():
        raise ValueError('Impossible d’interpoler les points de la courbe.')
    curve = interpolator.Curve()
    edge = BRepBuilderAPI_MakeEdge(curve).Edge()
    return (edge, curve) if return_curve else edge
