"""Local joint dragging by initial coincidence, without persistent constraints."""
from dataclasses import replace
from math import atan2, hypot, tan

from .editing import control_points, moved_handle
from .model import Arc, Point, Segment, Vec2, Spline

JOINT_TOLERANCE = 1e-7  # internal mm, independent of zoom and display units


def endpoints(entity):
    if type(entity) is Spline and not entity.closed:
        return (("pole0", entity.points[0]), (f"pole{len(entity.points)-1}", entity.points[-1]))
    if type(entity) is Point:
        return (("position", entity.position),)
    if type(entity) is Segment:
        return (("start", entity.start), ("end", entity.end))
    if type(entity) is Arc:
        return (("start", entity.point_at(0)), ("end", entity.point_at(1)))
    return ()


def joint_handles(entities, handle):
    """Freeze only the dragged node; centers/radius handles remain independent."""
    key, part = handle
    source = next(entity for entity in entities if entity.id == key)
    anchor = dict(endpoints(source)).get(part)
    if anchor is None:
        return (handle,)
    return tuple((entity.id, name) for entity in entities for name, point in endpoints(entity)
                 if hypot(point.u-anchor.u, point.v-anchor.v) <= JOINT_TOLERANCE)


def _arc_endpoint(entity, part, point):
    # Keep the opposite endpoint and signed sweep (bulge); center/radius adapt
    # to the new chord. No fixed-radius constraint exists at this stage.
    first, last = (point, entity.point_at(1)) if part == "start" else (entity.point_at(0), point)
    dx, dy = last.u-first.u, last.v-first.v
    if hypot(dx, dy) <= 1e-10:
        raise ValueError("Déplacement impossible : les extrémités de l’arc seraient confondues.")
    offset = .5/tan(entity.sweep/2)
    center = Vec2((first.u+last.u)/2-dy*offset, (first.v+last.v)/2+dx*offset)
    return replace(entity, center=center, radius=hypot(first.u-center.u, first.v-center.v),
                   start_angle=atan2(first.v-center.v, first.u-center.u))


def moved_joint(entities, handle, point, targets):
    """One detached batch. The selected arc retains its existing radius policy."""
    key, part = handle
    source = next(entity for entity in entities if entity.id == key)
    primary = moved_handle(source, part, point)
    # Arc direction handles project onto their supporting circle. All attached
    # endpoints follow the REAL endpoint, rather than an unreachable raw cursor.
    actual = dict(control_points(primary))[part]
    result = []
    for entity in entities:
        parts = [name for target, name in targets if target == entity.id]
        if not parts:
            continue
        if len(parts) > 1:
            raise ValueError("Jonction ambiguë : deux extrémités d’une même entité sont confondues.")
        if entity.id == key:
            changed = primary
        elif type(entity) is Arc:
            changed = _arc_endpoint(entity, parts[0], actual)
        elif type(entity) is Spline:
            changed = moved_handle(entity, parts[0], actual)
        else:
            changed = replace(entity, **{name: actual for name in parts})
        result.append(changed)
    return tuple(result)
