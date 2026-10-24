"""Local mobility classification for display; no Qt or native geometry."""
from dataclasses import dataclass
from math import cos, sin
import numpy as np

from .model import Arc, Segment, Circle
from .editing import control_points
from .solver import System, MAX_VARIABLES, MAX_EQUATIONS, RANK_CUTOFF
from .constraint_components import components


@dataclass(frozen=True)
class Status:
    entities: frozenset = frozenset()
    points: frozenset = frozenset()
    valid: bool = True


def classify(sketch):
    if sketch.patterns or any(c.driven for c in sketch.constraints):
        source = sketch.independent(sketch.source_entities,
            constraints=tuple(c for c in sketch.constraints if not c.driven), external=sketch.external)
        state = classify(source)
        entities, points = set(state.entities), set(state.points)
        for pattern in sketch.patterns:
            for index in range(1, pattern.count):
                for key in pattern.entity_ids:
                    generated = pattern.occurrence_id(key, index)
                    if key in state.entities:
                        entities.add(generated)
                    points.update((generated, part) for entity_id, part in state.points if entity_id == key)
        return Status(frozenset(entities), frozenset(points), state.valid)
    if not sketch.constraints:
        return Status()
    try:
        islands = components(sketch)
        if len(islands) > 1:
            states = tuple(classify(island) for island in islands)
            if not all(state.valid for state in states):
                return Status(valid=False)
            return Status(frozenset(key for state in states for key in state.entities),
                          frozenset(key for state in states for key in state.points))
        system = System(sketch)
        if len(system.x) > MAX_VARIABLES or len(system.row_constraints) > MAX_EQUATIONS:
            return Status(valid=False)
        residuals = system.residuals(system.x)
        if not np.all(np.isfinite(residuals)) or np.any(np.abs(residuals) > system.tolerances):
            return Status(valid=False)
        matrix = system.rank_matrix(system.x)
        _,singular,vh = np.linalg.svd(matrix,full_matrices=matrix.shape[0] < matrix.shape[1])
        rank = int(np.count_nonzero(singular > max(1e-10,singular[0]*RANK_CUTOFF))) if len(singular) else 0
        basis = vh[rank:].T
        entities,points = set(),set()
        for e in sketch.entities:
            sl = system.slices.get(e.id)
            if sl is None:
                continue
            local = basis[sl]
            if not np.any(np.abs(local) > 1e-8):
                entities.add(e.id)
            motion = local*system.scales[sl,None]
            for part,p in control_points(e):
                derivative = motion[:2]
                if type(e) is Segment and part == "end":
                    derivative = motion[2:4]
                elif part == "radius" and isinstance(e,Circle):
                    derivative = motion[:2]+np.outer((1.,0.),motion[2])
                elif type(e) is Arc and part in {"start","end"}:
                    angle = e.start_angle+(e.sweep if part == "end" else 0)
                    direction = np.array((cos(angle),sin(angle)))
                    tangent = e.radius*np.array((-sin(angle),cos(angle)))
                    derivative = motion[:2]+np.outer(direction,motion[2])+np.outer(tangent,motion[3]+(motion[4] if part == "end" else 0))
                if not np.any(np.abs(derivative) > 1e-8):
                    points.add((e.id,part))
        return Status(frozenset(entities),frozenset(points))
    except (ValueError,ArithmeticError,np.linalg.LinAlgError):
        return Status(valid=False)
