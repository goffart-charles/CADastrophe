"""Analytic 2D intersections, areas and containment for E7. No native imports."""
from math import atan2, cos, sin, hypot, pi, sqrt, fsum
from .model import Segment, Arc, Circle, Vec2

TAU = 2*pi


def ends(e):
    return (e.start,e.end) if type(e) is Segment else (e.point_at(0),e.point_at(1)) if type(e) is Arc else ()


def sample(e,t=.37):
    if type(e) is Segment:
        return Vec2(e.start.u+(e.end.u-e.start.u)*t,e.start.v+(e.end.v-e.start.v)*t)
    if type(e) is Arc:
        return e.point_at(t)
    return Vec2(e.center.u+e.radius*cos(TAU*t),e.center.v+e.radius*sin(TAU*t))


def distance(a,b):
    return hypot(a.u-b.u,a.v-b.v)


def arc_contains(e,p,tol,*,interior=False):
    if type(e) is Circle:
        return True
    angle = atan2(p.v-e.center.v,p.u-e.center.u)
    delta = ((angle-e.start_angle)*(1 if e.sweep > 0 else -1)) % TAU
    eps = tol/e.radius
    if TAU-delta <= eps:
        delta = 0.
    return eps < delta < abs(e.sweep)-eps if interior else delta <= abs(e.sweep)+eps


def intersections(a,b,tol):
    """Return isolated intersections and whether positive-length overlap exists."""
    if type(a) is not Segment and type(b) is Segment:
        return intersections(b,a,tol)
    if type(a) is Segment and type(b) is Segment:
        rx,ry = a.end.u-a.start.u,a.end.v-a.start.v
        sx,sy = b.end.u-b.start.u,b.end.v-b.start.v
        qx,qy = b.start.u-a.start.u,b.start.v-a.start.v
        la,lb = hypot(rx,ry),hypot(sx,sy)
        den = rx*sy-ry*sx
        if abs(den) <= 1e-12*la*lb:
            if abs(qx*ry-qy*rx)/la > tol:
                return (),False
            lo,hi = sorted(((qx*rx+qy*ry)/la,((b.end.u-a.start.u)*rx+(b.end.v-a.start.v)*ry)/la))
            lo,hi = max(0.,lo),min(la,hi)
            if hi < lo-tol:
                return (),False
            if hi-lo > tol:
                return (),True
            return (Vec2(a.start.u+rx*(lo+hi)/(2*la),a.start.v+ry*(lo+hi)/(2*la)),),False
        t,u = (qx*sy-qy*sx)/den,(qx*ry-qy*rx)/den
        if -tol/la <= t <= 1+tol/la and -tol/lb <= u <= 1+tol/lb:
            return (Vec2(a.start.u+rx*t,a.start.v+ry*t),),False
        return (),False
    if type(a) is Segment:
        dx,dy = a.end.u-a.start.u,a.end.v-a.start.v
        length = hypot(dx,dy)
        dx,dy = dx/length,dy/length
        qx,qy = a.start.u-b.center.u,a.start.v-b.center.v
        perpendicular = qx*dy-qy*dx
        if abs(perpendicular) > b.radius+tol:
            return (),False
        along = -(qx*dx+qy*dy)
        h = sqrt(max(0.,(b.radius-abs(perpendicular))*(b.radius+abs(perpendicular))))
        values = (along,) if h <= tol else (along-h,along+h)
        points = tuple(Vec2(a.start.u+dx*t,a.start.v+dy*t) for t in values if -tol <= t <= length+tol)
        return tuple(p for p in points if arc_contains(b,p,tol)),False
    dx,dy = b.center.u-a.center.u,b.center.v-a.center.v
    d = hypot(dx,dy)
    if d <= tol and abs(a.radius-b.radius) <= tol:
        if type(a) is Circle or type(b) is Circle:
            return (),True
        candidates = (*ends(a),*ends(b),sample(a,.5),sample(b,.5))
        overlap = any(arc_contains(a,p,tol,interior=True) and arc_contains(b,p,tol,interior=True) for p in candidates)
        points = tuple(p for p in (*ends(a),*ends(b)) if arc_contains(a,p,tol) and arc_contains(b,p,tol))
        return points,overlap
    if d <= tol or d > a.radius+b.radius+tol or d < abs(a.radius-b.radius)-tol:
        return (),False
    x = (d*d+(a.radius-b.radius)*(a.radius+b.radius))/(2*d)
    h = sqrt(max(0.,(a.radius-x)*(a.radius+x)))
    ux,uy = dx/d,dy/d
    center = Vec2(a.center.u+x*ux,a.center.v+x*uy)
    points = (center,) if h <= tol else (Vec2(center.u-h*uy,center.v+h*ux),Vec2(center.u+h*uy,center.v-h*ux))
    return tuple(p for p in points if arc_contains(a,p,tol) and arc_contains(b,p,tol)),False


def area(uses,entities):
    origin = sample(entities[uses[0].entity_id],0.)
    values = []
    for use in uses:
        e = entities[use.entity_id]
        if type(e) is Segment:
            a,b = e.start,e.end
            value = ((a.u-origin.u)*(b.v-origin.v)-(a.v-origin.v)*(b.u-origin.u))/2
        else:
            first,sweep = (e.start_angle,e.sweep) if type(e) is Arc else (0.,TAU)
            last = first+sweep
            value = .5*(e.radius*e.radius*sweep+e.radius*(e.center.u-origin.u)*(sin(last)-sin(first))+
                        e.radius*(e.center.v-origin.v)*(cos(first)-cos(last)))
        values.append(-value if use.reverse else value)
    return fsum(values)


def contains(uses,entities,p,tol):
    """Even/odd horizontal ray with half-open endpoint handling for arcs too."""
    count = 0
    for use in uses:
        e = entities[use.entity_id]
        if type(e) is Segment:
            a,b = e.start,e.end
            if (a.v > p.v) != (b.v > p.v):
                x = a.u+(p.v-a.v)*(b.u-a.u)/(b.v-a.v)
                count += x > p.u
            continue
        dy = p.v-e.center.v
        if abs(dy) >= e.radius:
            continue  # tangency does not cross the ray
        h = sqrt((e.radius-abs(dy))*(e.radius+abs(dy)))
        for x in (e.center.u-h,e.center.u+h):
            q = Vec2(x,p.v)
            if x <= p.u or not arc_contains(e,q,tol):
                continue
            if type(e) is Arc:
                start,end = ends(e)
                if distance(q,start) <= tol or distance(q,end) <= tol:
                    theta = atan2(q.v-e.center.v,q.u-e.center.u)
                    derivative = cos(theta)*(1 if e.sweep > 0 else -1)
                    enters_upper = derivative > 0 if distance(q,start) <= tol else derivative < 0
                    if abs(derivative) < 1e-12:
                        enters_upper = -sin(theta) > 0
                    if not enters_upper:
                        continue
            count += 1
    return bool(count%2)
