"""Pure measurement geometry shared by interactive dimensions and annotations."""
from math import hypot, atan2

from .constraints import point
from .model import Vec2


def angle_geometry(sketch, refs):
    """Two oriented rays and their infinite-line intersection (middle point)."""
    a, b = (sketch.entity(r.entity_id) for r in refs)
    du, dv = a.end.u-a.start.u, a.end.v-a.start.v
    eu, ev = b.end.u-b.start.u, b.end.v-b.start.v
    det = du*ev-dv*eu
    if abs(det) <= 1e-10*hypot(du,dv)*hypot(eu,ev):
        raise ValueError("Deux lignes parallèles ne définissent pas de cote angulaire.")
    t = ((b.start.u-a.start.u)*ev-(b.start.v-a.start.v)*eu)/det
    center = Vec2(a.start.u+t*du, a.start.v+t*dv)
    first_sign, second_sign = (-1 if r.part == "start" else 1 for r in refs)
    return (Vec2(center.u+first_sign*du, center.v+first_sign*dv), center,
            Vec2(center.u+second_sign*eu, center.v+second_sign*ev))


def points(sketch, kind, refs):
    entity = sketch.entity(refs[0].entity_id)
    if kind == "length":
        return entity.start, entity.end
    a = point(entity, refs[0].part)
    if kind == "line_distance":
        line = sketch.entity(refs[1].entity_id)
        du, dv = line.end.u-line.start.u, line.end.v-line.start.v
        t = ((a.u-line.start.u)*du+(a.v-line.start.v)*dv)/(du*du+dv*dv)
        return a, Vec2(line.start.u+t*du, line.start.v+t*dv)
    return a, point(sketch.entity(refs[1].entity_id), refs[1].part)


def measure(sketch, kind, refs):
    if kind == "angle":
        a, center, b = angle_geometry(sketch, refs)
        du, dv, eu, ev = a.u-center.u, a.v-center.v, b.u-center.u, b.v-center.v
        return atan2(abs(du*ev-dv*eu), du*eu+dv*ev)
    if kind in {"radius", "diameter"}:
        return sketch.entity(refs[0].entity_id).radius*(2 if kind == "diameter" else 1)
    a, b = points(sketch, kind, refs)
    if kind == "horizontal_distance":
        return abs(a.u-b.u)
    if kind == "vertical_distance":
        return abs(a.v-b.v)
    return hypot(a.u-b.u, a.v-b.v)


def label_position(sketch, constraint):
    if constraint.label_position is not None:
        return constraint.label_position
    if constraint.kind == "angle":
        a, center, b = angle_geometry(sketch, constraint.refs)
        return Vec2((a.u+b.u)/2, (a.v+b.v)/2)
    if constraint.kind in {"radius", "diameter"}:
        e = sketch.entity(constraint.refs[0].entity_id)
        return Vec2(e.center.u+e.radius*1.4, e.center.v+e.radius*.8)
    if constraint.kind in {"length", "distance", "horizontal_distance", "vertical_distance", "line_distance"}:
        a, b = points(sketch, constraint.kind, constraint.refs)
        length = max(hypot(b.u-a.u, b.v-a.v), 1e-8)
        return Vec2((a.u+b.u)/2-(b.v-a.v)/length*8, (a.v+b.v)/2+(b.u-a.u)/length*8)
    e = sketch.entity(constraint.refs[0].entity_id)
    if constraint.refs[0].part == "entity":
        if hasattr(e, "start"):
            return Vec2((e.start.u+e.end.u)/2, (e.start.v+e.end.v)/2+2)
        return e.center
    p = point(e, constraint.refs[0].part)
    return Vec2(p.u+2, p.v+2)
