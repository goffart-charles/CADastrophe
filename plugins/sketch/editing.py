"""Pure E4 transformations. UUIDs/types survive edits; no implicit constraints."""
from dataclasses import replace
from math import atan2, hypot, pi

from .model import Arc, Circle, Point, Segment, Vec2, Ellipse, Spline


def translated(entity, delta: Vec2):
    def point(value):
        return Vec2(value.u + delta.u, value.v + delta.v)
    if type(entity) is Point:
        return replace(entity, position=point(entity.position))
    if type(entity) is Segment:
        return replace(entity, start=point(entity.start), end=point(entity.end))
    if type(entity) is Spline:
        return replace(entity, points=tuple(point(p) for p in entity.points))
    return replace(entity, center=point(entity.center))


def control_points(entity):
    if type(entity) is Point:
        return (("position", entity.position),)
    if type(entity) is Segment:
        return (("start", entity.start), ("end", entity.end))
    if type(entity) is Arc:
        return (("center", entity.center), ("start", entity.point_at(0)),
                ("end", entity.point_at(1)), ("radius", entity.point_at(.5)))
    if type(entity) is Ellipse:
        return (("center", entity.center), ("major", entity.point_at(0)), ("minor", entity.point_at(.25)))
    if type(entity) is Spline:
        return tuple((f"pole{i}", p) for i, p in enumerate(entity.points))
    return (("center", entity.center), ("radius", Vec2(entity.center.u + entity.radius, entity.center.v)))


def moved_handle(entity, part: str, point: Vec2):
    if type(entity) is Point and part == "position":
        return replace(entity, position=point)
    if type(entity) is Segment and part in ("start", "end"):
        return replace(entity, **{part: point})
    if type(entity) is Ellipse:
        if part == "center":
            return replace(entity, center=point)
        if part in ("major", "minor"):
            radius = hypot(point.u-entity.center.u, point.v-entity.center.v)
            return replace(entity, **{"major_radius" if part == "major" else "minor_radius": radius})
    if type(entity) is Spline and part.startswith("pole"):
        points = list(entity.points)
        points[int(part[4:])] = point
        return replace(entity, points=tuple(points))
    if isinstance(entity, Circle):
        if part == "center":
            return replace(entity, center=point)
        if part == "radius":
            return replace(entity, radius=hypot(point.u - entity.center.u, point.v - entity.center.v))
        if type(entity) is Arc and part in ("start", "end"):
            angle = atan2(point.v - entity.center.v, point.u - entity.center.u)
            if hypot(point.u - entity.center.u, point.v - entity.center.v) <= 1e-9:
                raise ValueError("La direction de la poignée doit être distincte du centre.")
            first = angle if part == "start" else entity.start_angle
            last = entity.start_angle + entity.sweep if part == "start" else angle
            sweep = ((last - first) % (2*pi) if entity.sweep > 0 else -((first - last) % (2*pi)))
            if min(abs(sweep), 2*pi - abs(sweep)) <= 1e-9:
                raise ValueError("Un arc ne peut pas être nul ou constituer un cercle complet.")
            return replace(entity, start_angle=first, sweep=sweep)
    raise ValueError("Poignée incompatible avec cette entité.")
