"""Exact native ellipse and cubic B-spline edges, built on demand."""
from math import cos, sin
from .model import Ellipse, Spline


def edge(sketch, entity):
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCC.Core.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Elips
    from OCC.Core.Geom import Geom_BSplineCurve
    from OCC.Core.TColgp import TColgp_Array1OfPnt
    from OCC.Core.TColStd import TColStd_Array1OfReal, TColStd_Array1OfInteger
    def point(p):
        return gp_Pnt(*sketch.to_world(p).tuple())
    if type(entity) is Ellipse:
        frame = sketch.support.frame
        direction = frame.u*cos(entity.rotation)+frame.v*sin(entity.rotation)
        axis = gp_Ax2(point(entity.center), gp_Dir(*frame.normal.tuple()), gp_Dir(*direction.tuple()))
        maker = BRepBuilderAPI_MakeEdge(gp_Elips(axis, entity.major_radius, entity.minor_radius))
    elif type(entity) is Spline:
        degree, points, expanded = entity.basis
        poles = TColgp_Array1OfPnt(1, len(points))
        for i, p in enumerate(points, 1):
            poles.SetValue(i, point(p))
        values = sorted(set(expanded))
        knots, mults = TColStd_Array1OfReal(1, len(values)), TColStd_Array1OfInteger(1, len(values))
        for i, value in enumerate(values, 1):
            knots.SetValue(i, value)
            mults.SetValue(i, expanded.count(value))
        # This native module is loaded lazily. An implicit spline can originate
        # from the earlier model already loaded by a running plugin session.
        rational_weights = getattr(entity, "weights", ())
        if rational_weights:
            weights = TColStd_Array1OfReal(1, len(points))
            for i, weight in enumerate(rational_weights, 1):
                weights.SetValue(i, weight)
            curve = Geom_BSplineCurve(poles, weights, knots, mults, degree, False)
        else:
            curve = Geom_BSplineCurve(poles, knots, mults, degree, False)
        maker = BRepBuilderAPI_MakeEdge(curve)
    else:
        raise ValueError("Courbe avancée inconnue.")
    if not maker.IsDone():
        raise ValueError("Impossible de construire cette courbe avec ces paramètres.")
    return maker.Edge()


def profile_edges(sketch, entity):
    result = edge(sketch, entity)
    if type(entity) is not Spline:
        return (result,)
    # Keep exact spline geometry but expose each polynomial span separately.
    # This also makes OCCT mass integration reliable at internal knot breaks.
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    adaptor = BRepAdaptor_Curve(result)
    curve = adaptor.Curve().Curve()
    degree, poles, expanded = entity.basis
    values = sorted(set(t for t in expanded if expanded[degree] <= t <= expanded[len(poles)]))
    return tuple(BRepBuilderAPI_MakeEdge(curve, a, b).Edge() for a, b in zip(values, values[1:]))
