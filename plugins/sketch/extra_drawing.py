"""Pure geometry for slots, polygons, ellipses and alternative arc modes."""
from math import atan2, cos, sin, pi, hypot
from .model import Segment, Arc, Ellipse, Spline, Vec2


def tangent_at(entity, point):
    if entity is None:
        raise ValueError("Sélectionnez d’abord un segment ou un arc.")
    a, b = (entity.start, entity.end) if type(entity) is Segment else (entity.point_at(0), entity.point_at(1))
    start = hypot(point.u-a.u, point.v-a.v) <= 1e-6
    if not start and hypot(point.u-b.u, point.v-b.v) > 1e-6:
        raise ValueError("Partez d’une extrémité de l’entité choisie.")
    if type(entity) is Segment:
        x, y = (a.u-b.u, a.v-b.v) if start else (b.u-a.u, b.v-a.v)
        length = hypot(x, y)
        return x/length, y/length
    angle = entity.start_angle+(0 if start else entity.sweep)
    sign = (1 if entity.sweep > 0 else -1)*(-1 if start else 1)
    return -sin(angle)*sign, cos(angle)*sign


def three_point_arc(a, b, c, key):
    # Endpoints a/b, third click lies on the desired arc.
    x, y, u, v = b.u-a.u, b.v-a.v, c.u-a.u, c.v-a.v
    den = 2*(x*v-y*u)
    if abs(den) < 1e-10*max(1., hypot(x, y)*hypot(u, v)):
        raise ValueError("Les trois points ne doivent pas être alignés.")
    dx = ((x*x+y*y)*v-(u*u+v*v)*y)/den
    dy = ((u*u+v*v)*x-(x*x+y*y)*u)/den
    center = Vec2(a.u+dx, a.v+dy)
    start = atan2(a.v-center.v, a.u-center.u)
    sweep = (atan2(b.v-center.v, b.u-center.u)-start) % (2*pi)
    via = (atan2(c.v-center.v, c.u-center.u)-start) % (2*pi)
    if via > sweep:
        sweep -= 2*pi
    return Arc(center, hypot(dx, dy), start, sweep, id=key)


def candidate(draft, point):
    tool, anchors, ids = draft.tool, draft.anchors, draft._ids
    first = anchors[0]
    if tool == "polygon":
        n = draft.polygon_sides
        if not 3 <= n <= 64:
            raise ValueError("Le polygone doit avoir de 3 à 64 côtés.")
        radius = hypot(point.u-first.u, point.v-first.v)
        if radius < 1e-9:
            raise ValueError("Le rayon du polygone doit être positif.")
        if draft.polygon_inscribed:
            radius /= cos(pi/n)
        angle = atan2(point.v-first.v, point.u-first.u)
        points = tuple(Vec2(first.u+radius*cos(angle+2*pi*i/n), first.v+radius*sin(angle+2*pi*i/n)) for i in range(n))
        return tuple(Segment(p, points[(i+1)%n], id=ids[i]) for i, p in enumerate(points))
    if tool == "spline":
        points = anchors if hypot(point.u-anchors[-1].u, point.v-anchors[-1].v) < 1e-9 else (*anchors, point)
        return (Spline(points, draft.spline_closed and len(points) >= 3, id=ids[0]),) if len(points) >= 2 else ()
    if tool == "arc_tangent":
        if draft.tangent is None:
            raise ValueError("Sélectionnez un segment ou un arc et partez d’une de ses extrémités.")
        tx, ty = draft.tangent
        dx, dy = point.u-first.u, point.v-first.v
        den = 2*(-ty*dx+tx*dy)
        if abs(den) < 1e-9:
            raise ValueError("Choisissez une fin qui détermine un arc tangent non nul.")
        offset = (dx*dx+dy*dy)/den
        center = Vec2(first.u-ty*offset, first.v+tx*offset)
        start = atan2(first.v-center.v, first.u-center.u)
        sweep = (atan2(point.v-center.v, point.u-center.u)-start) % (2*pi)
        if offset < 0:
            sweep -= 2*pi
        return (Arc(center, abs(offset), start, sweep, id=ids[0]),)
    if len(anchors) == 1:
        return (Segment(first, point, id=ids[0]),)
    second = anchors[1]
    if tool == "arc_three":
        return (three_point_arc(first, second, point, ids[0]),)
    dx, dy = second.u-first.u, second.v-first.v
    length = hypot(dx, dy)
    if length < 1e-9:
        raise ValueError("Les points de référence doivent être distincts.")
    nx, ny = -dy/length, dx/length
    width = abs((point.u-first.u)*nx+(point.v-first.v)*ny)
    if width < 1e-9:
        raise ValueError("Choisissez une largeur non nulle.")
    if tool == "ellipse":
        angle = atan2(dy, dx)
        return (Ellipse(first, max(length, width), min(length, width), angle if length >= width else angle+pi/2, id=ids[0]),)
    a = Vec2(first.u+nx*width, first.v+ny*width)
    b = Vec2(second.u+nx*width, second.v+ny*width)
    c = Vec2(second.u-nx*width, second.v-ny*width)
    d = Vec2(first.u-nx*width, first.v-ny*width)
    angle = atan2(ny, nx)
    return (Segment(a, b, id=ids[0]), Arc(second, width, angle, -pi, id=ids[1]),
            Segment(c, d, id=ids[2]), Arc(first, width, angle+pi, -pi, id=ids[3]))
