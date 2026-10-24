"""Construction rules persisted with the primitive, never post-solve glue."""
from .constraints import Constraint, Reference
from math import hypot
from .model import Segment, Point, Arc, Circle, Ellipse


def creation_relations(sketch, entities, *, rectangle=False):
    parent = {}

    def root(ref):
        parent.setdefault(ref, ref)
        if parent[ref] != ref:
            parent[ref] = root(parent[ref])
        return parent[ref]

    def join(a, b):
        a, b = root(a), root(b)
        if a == b:
            return False
        parent[a] = b
        return True

    for c in sketch.constraints:
        if c.kind == "coincident":
            join(*c.refs)
    vertices, result = [(sketch.origin.position, Reference(sketch.id, "position"))], []

    def endpoints(e):
        if type(e) is Arc:
            return (("start", e.point_at(0)), ("end", e.point_at(1)), ("center", e.center))
        if type(e) in (Circle, Ellipse):
            return (("center", e.center),)
        return (("start", e.start), ("end", e.end)) if type(e) is Segment else (
            (("position", e.position),) if type(e) is Point else ())

    for e in sketch.entities:
        for part, position in endpoints(e):
            vertices.append((position, Reference(e.id, part)))
    for e in entities:
        for part, position in endpoints(e):
            ref = Reference(e.id, part)
            # Analytic arc endpoints contain trigonometric rounding. Persist
            # their intended joints now, rather than relying on visual contact.
            other = next((ref for p, ref in vertices
                          if hypot(p.u-position.u, p.v-position.v) <= 1e-8), None)
            if other is not None and join(ref, other):
                result.append(Constraint("coincident", (other, ref)))
            vertices.append((position, ref))
    if rectangle:
        result.extend(Constraint("horizontal" if abs(e.end.v-e.start.v) < abs(e.end.u-e.start.u) else "vertical", (Reference(e.id),))
                      for e in entities)
    return tuple(result)
