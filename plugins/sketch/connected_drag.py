"""Rigid dragging of related internal entities, without solving during motion."""
from dataclasses import replace

from .editing import translated, control_points
from .constraints import LABELS
from .model import Vec2


def connected_entities(sketch, selected):
    graph = {e.id: set() for e in sketch.entities}
    for c in sketch.constraints:
        if c.driven:
            continue
        keys = [r.entity_id for r in c.refs if r.entity_id in graph]
        for key in keys:
            graph[key].update(keys)
    found, todo = set(), list(selected)
    while todo:
        key = todo.pop()
        if key in found or key not in graph:
            continue
        found.add(key)
        todo.extend(graph[key]-found)
    return tuple(e for e in sketch.entities if e.id in found)


def translated_group(sketch, entities, delta):
    sketch.require_editable(e.id for e in entities)
    moved = tuple(translated(e, delta) for e in entities)
    if sketch.constraints:
        from .solver import System
        import numpy as np
        values = {e.id: e for e in moved}
        candidate = replace(sketch, entities=tuple(values.get(e.id,e) for e in sketch.entities))
        system = System(candidate.independent(candidate.source_entities,
                        constraints=tuple(c for c in candidate.constraints if not c.driven), external=candidate.external))
        residual = system.residuals(system.x)
        bad = [c for c,r,t in zip(system.row_constraints,residual,system.tolerances) if abs(r) > t or not np.isfinite(r)]
        if bad:
            labels = ", ".join(dict.fromkeys(LABELS[c.kind] for c in bad))
            raise ValueError(f"Déplacement bloqué par les relations : {labels}. Modifier les cotes ou les fixations.")
    return moved


def translated_annotations(sketch, entities, delta):
    keys = {e.id for e in entities}
    return tuple(replace(c,label_position=Vec2(c.label_position.u+delta.u,c.label_position.v+delta.v))
                 if c.label_position is not None and any(r.entity_id in keys for r in c.refs) else c
                 for c in sketch.constraints)


def rigid_delta(original, moved):
    """Actual common translation; a deformation must not translate all labels."""
    delta = None
    for a,b in zip(original,moved):
        for (_,p),(_,q) in zip(control_points(a),control_points(b)):
            current = Vec2(q.u-p.u,q.v-p.v)
            if delta is None:
                delta = current
            elif abs(current.u-delta.u) > 1e-8 or abs(current.v-delta.v) > 1e-8:
                return None
    return delta
