"""Stable adapters for the existing entity parameter panel and driving cotes."""
from dataclasses import replace
from math import degrees, radians
from cadastrophe.core.parameters import Dimension
from .constraints import DIMENSIONS
from .model import Point, Segment, Circle, Arc, Ellipse, Spline, Vec2


def entity_values(entity):
    values = {}
    def vector(prefix, point):
        values[prefix+"u"], values[prefix+"v"] = point.u, point.v
    if isinstance(entity, Point):
        vector("p", entity.position)
    elif isinstance(entity, Segment):
        vector("a", entity.start)
        vector("b", entity.end)
    elif isinstance(entity, Spline):
        for i, point in enumerate(entity.points):
            vector(f"p{i}", point)
    else:
        vector("c", entity.center)
        if isinstance(entity, Ellipse):
            values.update(major_radius=entity.major_radius, minor_radius=entity.minor_radius,
                          rotation=degrees(entity.rotation))
        else:
            values["radius"] = entity.radius
            if isinstance(entity, Arc):
                values.update(start_angle=degrees(entity.start_angle), sweep=degrees(entity.sweep))
    return values


ANGLES = {"start_angle", "sweep", "rotation"}


def dimensions(obj):
    result = [Dimension(obj.id, c.id, f"D{i}@{obj.name}",
                        degrees(c.value) if c.kind == "angle" else c.value,
                        "angle" if c.kind == "angle" else "length")
              for i, c in enumerate((c for c in obj.sketch.constraints if c.kind in DIMENSIONS), 1) if not c.driven]
    for i, entity in enumerate(obj.sketch.source_entities, 1):
        result.extend(Dimension(obj.id, entity.id+"."+key, f"E{i}.{key}@{obj.name}", value,
                                "angle" if key in ANGLES else "length")
                      for key, value in entity_values(entity).items())
    for i, pattern in enumerate(obj.sketch.patterns, 1):
        result.extend(Dimension(obj.id, pattern.id+"."+key, f"Motif{i}.{key}@{obj.name}", value,
                                "number" if key == "count" else "angle" if key == "angle" else "length",
                                integer=key == "count") for key, value in pattern_values(pattern).items())
    return tuple(result)


def with_values(obj, changes):
    obj.sketch.require_editable(key.split(".")[0] for key in changes)
    def entity_update(entity):
        values = entity_values(entity)
        edited = {key.removeprefix(entity.id+"."): value for key, value in changes.items()
                  if key.startswith(entity.id+".")}
        if not edited:
            return entity
        values.update(edited)
        def vector(prefix):
            return Vec2(values[prefix+"u"], values[prefix+"v"])
        if isinstance(entity, Point):
            return replace(entity, position=vector("p"))
        if isinstance(entity, Segment):
            return replace(entity, start=vector("a"), end=vector("b"))
        if isinstance(entity, Spline):
            return replace(entity, points=tuple(vector(f"p{i}") for i in range(len(entity.points))))
        if isinstance(entity, Ellipse):
            return replace(entity, center=vector("c"), major_radius=values["major_radius"],
                           minor_radius=values["minor_radius"], rotation=radians(values["rotation"]))
        extras = {key: radians(values[key]) for key in ("start_angle", "sweep")} if isinstance(entity, Arc) else {}
        return replace(entity, center=vector("c"), radius=values["radius"], **extras)
    constraints = tuple(replace(c, value=radians(changes[c.id]) if c.kind == "angle" else changes[c.id])
                        if c.id in changes else c for c in obj.sketch.constraints)
    patterns = []
    for pattern in obj.sketch.patterns:
        values = pattern_values(pattern)
        values.update({key.removeprefix(pattern.id+"."): value for key, value in changes.items() if key.startswith(pattern.id+".")})
        extras = dict(du=values["du"], dv=values["dv"]) if pattern.mode == "linear_pattern" else dict(
            center=Vec2(values["center_u"], values["center_v"]), angle=radians(values["angle"]))
        patterns.append(replace(pattern, count=values["count"], **extras))
    sketch = replace(obj.sketch, entities=tuple(entity_update(e) for e in obj.sketch.source_entities),
                     constraints=constraints, patterns=tuple(patterns))
    if constraints:
        from .solver import solve
        result = solve(sketch)
        if not result.success:
            raise ValueError(result.message)
        sketch = result.sketch
    from .presentation import entity_shape
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    for entity in sketch.entities:
        if not isinstance(entity, Point) and not BRepCheck_Analyzer(entity_shape(sketch, entity)).IsValid():
            raise ValueError("Une équation produit une courbe d’esquisse invalide.")
    return replace(obj, sketch=sketch)


def pattern_values(pattern):
    return dict(count=pattern.count, **(dict(du=pattern.du, dv=pattern.dv) if pattern.mode == "linear_pattern"
        else dict(center_u=pattern.center.u, center_v=pattern.center.v, angle=degrees(pattern.angle))))
