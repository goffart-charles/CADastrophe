"""Affine constraint elimination and bounded analytic Gauss–Newton in its nullspace.

Only NumPy is needed. No Qt, OCCT, documents, mutation or rendering. Rank is a
local numerical estimate, never a certificate of global rigidity/redundancy.
"""
from dataclasses import dataclass, replace
from math import cos, sin, pi, atan2
from time import perf_counter

import numpy as np

from .model import Arc, Circle, Point, Segment, Vec2, Ellipse, Spline
from .constraints import LABELS, DIMENSIONS
from .constraint_components import components
from .limits import MAX_TOTAL_VARIABLES

TOLERANCE = 1e-8  # mm, normalized linear residuals
RANK_CUTOFF = 1e-8
MAX_VARIABLES = 256
MAX_EQUATIONS = 512
MAX_TOTAL_EQUATIONS = 32768


@dataclass(frozen=True)
class Result:
    success: bool
    sketch: object
    dof: int | None
    rank: int | None
    residual: float
    message: str
    conflicts: tuple[str, ...] = ()
    redundant: tuple[str, ...] = ()
    conflict_minimal: bool = False
    termination: str = ""


class AffineConflict(ValueError):
    def __init__(self, conflicts, *, minimal=False):
        super().__init__("Relations linéaires contradictoires (coïncidence, orientation, fixation ou cote)")
        self.conflicts = tuple(dict.fromkeys(conflicts))
        self.minimal = minimal


def parameters(entity):
    if type(entity) is Ellipse:
        return (entity.center.u, entity.center.v, entity.major_radius, entity.minor_radius, entity.rotation)
    if type(entity) is Spline:
        return tuple(v for p in entity.points for v in (p.u, p.v))
    if type(entity) is Point:
        return (entity.position.u, entity.position.v)
    if type(entity) is Segment:
        return (entity.start.u, entity.start.v, entity.end.u, entity.end.v)
    values = (entity.center.u, entity.center.v, entity.radius)
    return (*values, entity.start_angle, entity.sweep) if type(entity) is Arc else values


class System:
    def __init__(self, sketch):
        self.sketch = sketch
        self.entities = {e.id: e for e in sketch.all_entities}
        self.entities[sketch.id] = sketch.origin
        active = {r.entity_id for c in sketch.constraints for r in c.refs}
        first = next((e for e in sketch.all_entities if e.id in active), None)
        self.origin = np.asarray(parameters(first)[:2] if first else (0., 0.), dtype=float)
        self.slices, values, scales = {}, [], []
        self.total = sum(len(parameters(e)) for e in sketch.entities)
        for e in sketch.entities:
            if e.id not in active:
                continue
            p = self.local_parameters(e)
            self.slices[e.id] = slice(len(values), len(values)+len(p))
            values.extend(p)
            scales.extend([1.] * len(p))
            if type(e) is Arc:
                scales[-2:] = [1/max(e.radius, 1e-6)]*2
        self.x = np.asarray(values, dtype=float)
        self.scales = np.asarray(scales)
        self.row_constraints = []
        self.signs = {}
        self.angle_scales = {}
        for c in sketch.constraints:
            size = 2 if c.kind in {"coincident", "fixed"} else 1
            self.row_constraints.extend([c] * size)
            if c.kind in {"horizontal_distance", "vertical_distance"}:
                delta = self.at(self.x, c.refs[0])-self.at(self.x, c.refs[1])
                self.signs[c.id] = 1 if delta[0 if c.kind == "horizontal_distance" else 1] >= 0 else -1
            elif c.kind == "line_distance":
                a = self.at(self.x, c.refs[0])
                q = self.values(self.x, c.refs[1].entity_id)
                d, v = q[2:4]-q[:2], a-q[:2]
                self.signs[c.id] = 1 if d[0]*v[1]-d[1]*v[0] >= 0 else -1
            elif c.kind in {"angle", "parallel", "perpendicular"}:
                p, q = (self.values(self.x, r.entity_id) for r in c.refs)
                a, b = p[2:4]-p[:2], q[2:4]-q[:2]
                a = a*(-1 if c.refs[0].part == "start" else 1)
                b = b*(-1 if c.refs[1].part == "start" else 1)
                self.signs[c.id] = 1 if a[0]*b[1]-a[1]*b[0] >= 0 else -1
                self.angle_scales[c.id] = (max(min(np.linalg.norm(a), np.linalg.norm(b)), 1e-6) if c.kind == "angle"
                                           else float(np.sqrt(np.linalg.norm(a)*np.linalg.norm(b))))
        magnitude = max((abs(v) for e in sketch.all_entities if e.id in active for v in
                         parameters(e)[:(4 if type(e) is Segment else 3 if isinstance(e, Circle) else 2)]), default=1.)
        precision_floor = 8*abs(float(np.spacing(magnitude)))
        self.linear_tolerance = max(TOLERANCE, precision_floor)
        self.tolerances = np.asarray([
            1e-9*self.angle_scales[c.id] if c.kind == "angle" else
            max(precision_floor,1e-9*self.angle_scales[c.id]) if c.kind in {"parallel", "perpendicular"} else
            min(TOLERANCE,c.value*1e-8) if c.kind in DIMENSIONS else self.linear_tolerance
            for c in self.row_constraints])

    def local_parameters(self, entity):
        p = np.asarray(parameters(entity), dtype=float)
        p[:2] -= self.origin
        if type(entity) is Segment:
            p[2:4] -= self.origin
        return p

    def values(self, x, key):
        return x[self.slices[key]] if key in self.slices else self.local_parameters(self.entities[key])

    def at(self, x, ref):
        p = self.values(x, ref.entity_id)
        if ref.part in ("position", "center", "start"):
            if ref.part != "start" or type(self.entities[ref.entity_id]) is Segment:
                return p[:2]
        if type(self.entities[ref.entity_id]) is Segment:
            return p[2:4]
        angle = p[3] + (p[4] if ref.part == "end" else 0)
        return p[:2] + p[2]*np.array((cos(angle), sin(angle)))

    def residuals(self, x):
        rows = []
        for c in self.sketch.constraints:
            kind, refs = c.kind, c.refs
            p = self.values(x, refs[0].entity_id)
            if kind in {"coincident", "distance", "fixed", "horizontal_distance", "vertical_distance"}:
                a = self.at(x, refs[0])
                b = np.array((c.value.u, c.value.v))-self.origin if kind == "fixed" else self.at(x, refs[1])
                delta = a-b
                if kind in {"horizontal_distance", "vertical_distance"}:
                    rows.append(self.signs[c.id]*delta[0 if kind == "horizontal_distance" else 1]-c.value)
                else:
                    rows.extend(delta if kind != "distance" else [np.linalg.norm(delta)-c.value])
            elif kind in {"point_on_line", "line_distance"}:
                a = self.at(x, refs[0])
                q = self.values(x, refs[1].entity_id)
                d, v = q[2:4]-q[:2], a-q[:2]
                distance = (d[0]*v[1]-d[1]*v[0])/max(float(np.linalg.norm(d)), 1e-12)
                rows.append(distance if kind == "point_on_line" else self.signs[c.id]*distance-c.value)
            elif kind in {"radius", "diameter"}:
                rows.append(p[2]*(2 if kind == "diameter" else 1)-c.value)
            else:
                delta = p[2:4]-p[:2]
                if kind in {"horizontal", "vertical"}:
                    rows.append(delta[1 if kind == "horizontal" else 0])
                elif kind == "length":
                    rows.append(np.linalg.norm(delta)-c.value)
                elif kind == "angle":
                    q = self.values(x, refs[1].entity_id)
                    other = q[2:4]-q[:2]
                    delta = delta*(-1 if refs[0].part == "start" else 1)
                    other = other*(-1 if refs[1].part == "start" else 1)
                    angle = atan2(self.signs[c.id]*(delta[0]*other[1]-delta[1]*other[0]), np.dot(delta, other))
                    rows.append((angle-c.value)*self.angle_scales[c.id])
                else:
                    q = self.values(x, refs[1].entity_id)
                    other = q[2:4]-q[:2]
                    scale = max(float(np.sqrt(np.linalg.norm(delta)*np.linalg.norm(other))), 1e-12)
                    rows.append((delta[0]*other[1]-delta[1]*other[0] if kind == "parallel"
                                 else np.dot(delta, other))/scale)
        return np.asarray(rows, dtype=float)

    def jacobian(self, x):
        """Analytic derivatives: no finite-difference noise in rank/nullspaces."""
        n = len(x)

        def variables(key):
            out = np.zeros((len(parameters(self.entities[key])), n))
            sl = self.slices.get(key)
            if sl is not None:
                out[:, sl] = np.eye(sl.stop-sl.start)
            return out

        def at(ref):
            e, p = self.entities[ref.entity_id], self.values(x, ref.entity_id)
            j = variables(ref.entity_id)
            if type(e) is Segment:
                return j[2:4] if ref.part == "end" else j[:2]
            if type(e) is Arc and ref.part in {"start", "end"}:
                angle = p[3] + (p[4] if ref.part == "end" else 0)
                direction = np.asarray((cos(angle), sin(angle)))
                tangent = p[2]*np.asarray((-sin(angle), cos(angle)))
                return j[:2] + np.outer(direction,j[2]) + np.outer(tangent,j[3]+(j[4] if ref.part == "end" else 0))
            return j[:2]

        rows = []
        for c in self.sketch.constraints:
            kind, refs = c.kind, c.refs
            p = self.values(x, refs[0].entity_id)
            j = variables(refs[0].entity_id)
            if kind in {"coincident", "fixed", "distance", "horizontal_distance", "vertical_distance"}:
                a = self.at(x, refs[0])
                b = np.asarray((c.value.u,c.value.v))-self.origin if kind == "fixed" else self.at(x,refs[1])
                dj = at(refs[0])-(np.zeros((2,n)) if kind == "fixed" else at(refs[1]))
                if kind in {"coincident", "fixed"}:
                    rows.extend(dj)
                elif kind == "distance":
                    rows.append((a-b)@dj/max(float(np.linalg.norm(a-b)),1e-12))
                else:
                    rows.append(self.signs[c.id]*dj[0 if kind == "horizontal_distance" else 1])
            elif kind in {"point_on_line", "line_distance"}:
                q = self.values(x, refs[1].entity_id)
                qj = variables(refs[1].entity_id)
                d, v = q[2:4]-q[:2], self.at(x,refs[0])-q[:2]
                dd, dv = qj[2:4]-qj[:2], at(refs[0])-qj[:2]
                length = max(float(np.linalg.norm(d)),1e-12)
                cross = d[0]*v[1]-d[1]*v[0]
                grad = (np.array((v[1],-v[0]))@dd + np.array((-d[1],d[0]))@dv)/length-cross*(d@dd)/length**3
                rows.append(grad*(self.signs[c.id] if kind == "line_distance" else 1))
            elif kind in {"radius", "diameter"}:
                rows.append(j[2]*(2 if kind == "diameter" else 1))
            else:
                a, da = p[2:4]-p[:2], j[2:4]-j[:2]
                la = max(float(np.linalg.norm(a)),1e-12)
                if kind in {"horizontal", "vertical"}:
                    rows.append(da[1 if kind == "horizontal" else 0])
                elif kind == "length":
                    rows.append(a@da/la)
                else:
                    q = self.values(x,refs[1].entity_id)
                    qj = variables(refs[1].entity_id)
                    b, db = q[2:4]-q[:2], qj[2:4]-qj[:2]
                    lb = max(float(np.linalg.norm(b)),1e-12)
                    if kind == "angle":
                        sa, sb = (-1 if r.part == "start" else 1 for r in refs)
                        a, da, b, db = a*sa, da*sa, b*sb, db*sb
                    cross, dot = a[0]*b[1]-a[1]*b[0], float(a@b)
                    dc = np.array((b[1],-b[0]))@da+np.array((-a[1],a[0]))@db
                    dp = b@da+a@db
                    if kind == "angle":
                        rows.append(self.signs[c.id]*(dot*dc-cross*dp)/max(dot*dot+cross*cross,1e-48)*self.angle_scales[c.id])
                    else:
                        value, grad = (cross,dc) if kind == "parallel" else (dot,dp)
                        scale = max(float(np.sqrt(la*lb)),1e-12)
                        rows.append((grad-value*.5*((a@da)/la**2+(b@db)/lb**2))/scale)
        return np.asarray(rows,dtype=float).reshape((-1,n)) * self.scales if n else np.empty((len(rows),0))

    def linear_space(self):
        """Eliminate mandatory affine equations before any nonlinear iteration.

        Coincident vertices, H/V, fixed points and radii cannot trade error for
        another dimension. Redundant equations share the same affine space.
        Arc endpoint coincidences remain nonlinear and are verified normally.
        """
        n = len(self.x)

        def at(ref):
            e = self.entities[ref.entity_id]
            if type(e) is Arc and ref.part in {"start", "end"}:
                return None
            out = np.zeros((2, n))
            if ref.entity_id in self.slices:
                start = self.slices[ref.entity_id].start + (2 if ref.part == "end" else 0)
                out[0, start] = 1
                out[1, start+1] = 1
            return out

        rows, indices, offset = [], [], 0
        for c in self.sketch.constraints:
            size = 2 if c.kind in {"coincident", "fixed"} else 1
            matrix = None
            if c.kind in {"coincident", "fixed", "horizontal_distance", "vertical_distance"}:
                a = at(c.refs[0])
                b = np.zeros((2, n)) if c.kind == "fixed" else at(c.refs[1])
                if a is not None and b is not None:
                    matrix = a-b
                    if c.kind in {"horizontal_distance", "vertical_distance"}:
                        matrix = matrix[[0 if c.kind == "horizontal_distance" else 1]]*self.signs[c.id]
            elif c.kind in {"horizontal", "vertical", "radius", "diameter"}:
                matrix = np.zeros((1, n))
                sl = self.slices.get(c.refs[0].entity_id)
                if sl is not None:
                    if c.kind in {"radius", "diameter"}:
                        matrix[0, sl.start+2] = 2 if c.kind == "diameter" else 1
                    else:
                        axis = 1 if c.kind == "horizontal" else 0
                        matrix[0, sl.start+axis] = -1
                        matrix[0, sl.start+axis+2] = 1
            if matrix is not None:
                rows.extend(matrix)
                indices.extend(range(offset, offset+size))
            offset += size
        if not rows:
            return self.x.copy(), np.eye(n)
        matrix = np.asarray(rows)*self.scales
        residual = self.residuals(self.x)[indices]
        u, singular, vh = np.linalg.svd(matrix, full_matrices=True)
        rank = int(np.count_nonzero(singular > max(1e-12, singular[0]*RANK_CUTOFF))) if len(singular) else 0
        delta = -(vh[:rank].T @ ((u[:, :rank].T @ residual)/singular[:rank]))
        base = self.x + delta*self.scales
        errors = np.abs(self.residuals(base)[indices]) > self.tolerances[indices]
        if np.any(errors):
            conflicts, minimal = self.affine_conflict(matrix, residual, indices)
            raise AffineConflict(conflicts, minimal=minimal)
        return base, vh[rank:].T

    def affine_conflict(self, matrix, residual, indices):
        """Deletion proof on affine equations, bounded independently of iteration.

        Infeasibility of a nonlinear local solve is never used as a proof here.
        If the diagnostic budget expires, report the verified contradictory
        subset without claiming that every remaining relation is necessary.
        """
        ids = tuple(self.row_constraints[index].id for index in indices)
        remaining = list(dict.fromkeys(ids))
        tolerances = self.tolerances[indices]
        deadline = perf_counter()+.1
        for key in tuple(remaining):
            if perf_counter() >= deadline:
                return tuple(remaining), False
            trial = [i for i, owner in enumerate(ids) if owner in remaining and owner != key]
            if not trial:
                continue
            a, b = matrix[trial], residual[trial]
            delta = np.linalg.lstsq(a, -b, rcond=RANK_CUTOFF)[0]
            if np.any(np.abs(a@delta+b) > tolerances[trial]):
                remaining.remove(key)
        return tuple(remaining), True

    def rank_matrix(self, x):
        matrix = self.jacobian(x)
        norms = np.linalg.norm(matrix, axis=1)
        return matrix/np.maximum(norms, 1e-30)[:, None]

    def redundant_relations(self, matrix):
        """Local differential redundancy, not a global geometric certificate."""
        basis, redundant, cursor = [], [], 0
        deadline = perf_counter()+.05
        for relation in self.sketch.constraints:
            if perf_counter() >= deadline:
                break
            count = 2 if relation.kind in {"fixed", "coincident"} else 1
            previous = len(basis)
            for row in matrix[cursor:cursor+count]:
                row = row.copy()
                # Reorthogonalize to avoid accumulating projection errors.
                for _ in range(2):
                    for direction in basis:
                        row -= float(row@direction)*direction
                norm = np.linalg.norm(row)
                if norm > RANK_CUTOFF:
                    basis.append(row/norm)
            if len(basis) == previous:
                redundant.append(relation.id)
            cursor += count
        return tuple(redundant)

    def unpack(self, x):
        entities = []
        for e in self.sketch.entities:
            if e.id not in self.slices:
                entities.append(e)
                continue
            p = x[self.slices[e.id]].copy()
            if np.array_equal(p, self.local_parameters(e)):
                entities.append(e)
                continue
            p[:2] += self.origin
            if type(e) is Segment:
                p[2:4] += self.origin
            # Keep exact unchanged snapshots, including integer/float values.
            if np.array_equal(p, np.array(parameters(e))):
                entities.append(e)
                continue
            if type(e) is Point:
                entity = replace(e, position=Vec2(*map(float, p)))
            elif type(e) is Segment:
                entity = replace(e, start=Vec2(*map(float, p[:2])), end=Vec2(*map(float, p[2:4])))
            elif type(e) is Ellipse:
                entity = replace(e, center=Vec2(*map(float, p[:2])))
            else:
                changes = dict(center=Vec2(*map(float, p[:2])), radius=float(p[2]))
                if type(e) is Arc:
                    if e.sweep*p[4] <= 0 or abs(p[4]) >= 2*pi:
                        raise ValueError("Le solveur ne peut pas inverser ou fermer cet arc.")
                    changes.update(start_angle=float(p[3]), sweep=float(p[4]))
                entity = replace(e, **changes)
            entities.append(entity)
        return replace(self.sketch, entities=tuple(entities))


def _result(system, x, *, update):
    candidate = system.unpack(x) if update else system.sketch
    # Verify the actual rounded snapshot that will enter the document too.
    verified = x.copy()
    if update:
        for e in candidate.entities:
            if e.id in system.slices:
                verified[system.slices[e.id]] = system.local_parameters(e)
    residuals = system.residuals(verified)
    residual = float(np.max(np.abs(residuals)))
    matrix = system.rank_matrix(verified)
    singular = np.linalg.svd(matrix, compute_uv=False)
    rank = int(np.count_nonzero(singular > max(1e-10, singular[0]*RANK_CUTOFF))) if len(singular) else 0
    dof = system.total-rank
    good = bool(np.all(np.isfinite(residuals)) and np.all(np.abs(residuals) <= system.tolerances))
    message = (f"Sous-contrainte · ≈ {dof} degré(s) de liberté" if dof else
               "Probablement entièrement contrainte · ≈ 0 degré de liberté") if good else "Échec : contraintes non satisfaites."
    conflicts = tuple(dict.fromkeys(c.id for c, r, tol in zip(system.row_constraints, residuals, system.tolerances) if abs(r) > tol))
    redundant = system.redundant_relations(matrix) if good else ()
    if redundant:
        message += f" · {len(redundant)} relation(s) localement redondante(s)"
    return Result(good, candidate if update and good else system.sketch, dof, rank, residual, message, conflicts,
                  redundant=redundant, termination="satisfied" if good else "unsatisfied")


def _run(sketch, *, update, max_iterations=60, time_budget=1.):
    system = System(sketch)
    if not sketch.constraints:
        return Result(True, sketch, system.total, 0, 0., f"Sous-contrainte · ≈ {system.total} degré(s) de liberté")
    residual = system.residuals(system.x)
    if len(system.x) > MAX_VARIABLES or len(residual) > MAX_EQUATIONS:
        return Result(False, sketch, None, None, float("inf"), "Résolution indisponible : composante connectée trop volumineuse.",
                      termination="limit")
    if not update:
        if np.any(np.abs(residual) > system.tolerances):
            system.linear_space()  # Prove affine conflicts instead of coloring arbitrary residual rows.
        return _result(system, system.x, update=False)
    if np.all(np.abs(residual) <= system.tolerances):
        return _result(system, system.x, update=True)
    deadline = perf_counter()+time_budget
    x, basis = system.linear_space()
    residual = system.residuals(x)
    iterations, stalled = 0, False
    for _ in range(max_iterations):
        if perf_counter() > deadline:
            break
        iterations += 1
        if np.all(np.abs(residual) <= system.tolerances):
            return _result(system, x, update=True)
        matrix = system.jacobian(x) @ basis
        step = (basis @ np.linalg.lstsq(matrix, -residual, rcond=RANK_CUTOFF)[0])*system.scales
        improved = False
        for backtrack in range(16):
            trial = x + step*(.5**backtrack)
            try:
                system.unpack(trial)  # reject collapsed/inverted geometry before accepting an iterate
                next_residual = system.residuals(trial)
                if np.all(np.isfinite(next_residual)) and np.linalg.norm(next_residual) < np.linalg.norm(residual):
                    x, residual, improved = trial, next_residual, True
                    break
            except ValueError:
                continue
        if not improved:
            stalled = True
            break
    conflicts = tuple(dict.fromkeys(c.id for c, r, tol in zip(system.row_constraints, residual, system.tolerances) if abs(r) > tol))
    labels = ", ".join(dict.fromkeys(LABELS[c.kind] for c in sketch.constraints if c.id in conflicts))
    limited = perf_counter() > deadline or (iterations >= max_iterations and not stalled)
    reason = "budget de résolution atteint" if limited else "convergence locale non obtenue"
    return Result(False, sketch, None, None, float(np.max(np.abs(residual))),
                  f"Échec : {reason} : {labels}. Aucun changement appliqué.", conflicts,
                  termination="limit" if limited else "nonconvergence")


def _components_run(sketch, *, update, max_iterations=60, time_budget=1.):
    if type(max_iterations) is not int or max_iterations < 0 or not np.isfinite(time_budget) or time_budget < 0:
        raise ValueError("Budgets de résolution invalides")
    total = sum(len(parameters(entity)) for entity in sketch.entities)
    equations = sum(2 if c.kind in {"fixed", "coincident"} else 1 for c in sketch.constraints)
    if total > MAX_TOTAL_VARIABLES or equations > MAX_TOTAL_EQUATIONS:
        return Result(False, sketch, None, None, float("inf"), "Résolution indisponible : esquisse trop volumineuse.",
                      termination="limit")
    islands = components(sketch)
    if len(islands) <= 1:
        return _run(sketch, update=update, max_iterations=max_iterations, time_budget=time_budget)
    deadline = perf_counter()+time_budget
    changed, rank, maximum, redundant = {}, 0, 0., []
    for island in islands:
        if update and perf_counter() >= deadline:
            return Result(False, sketch, None, None, float("inf"), "Échec : budget de résolution atteint. Aucun changement appliqué.",
                          termination="limit")
        result = _run(island, update=update, max_iterations=max_iterations,
                      time_budget=max(0., deadline-perf_counter()))
        if not result.success:
            return replace(result, sketch=sketch, dof=None, rank=None)
        rank += result.rank
        maximum = max(maximum, result.residual)
        redundant.extend(result.redundant)
        changed.update((entity.id, entity) for entity in result.sketch.entities)
    candidate = replace(sketch, entities=tuple(changed.get(e.id, e) for e in sketch.entities)) if update else sketch
    dof = total-rank
    message = f"Sous-contrainte · ≈ {dof} degré(s) de liberté" if dof else "Probablement entièrement contrainte · ≈ 0 degré de liberté"
    if redundant:
        message += f" · {len(redundant)} relation(s) localement redondante(s)"
    return Result(True, candidate, dof, rank, maximum, message, redundant=tuple(redundant), termination="satisfied")


def solve(sketch, **limits):
    try:
        return _resolved_run(sketch, update=True, **limits)
    except (ValueError, ArithmeticError, np.linalg.LinAlgError) as exc:
        return Result(False, sketch, None, None, float("inf"), f"Échec du solveur : {exc}. Aucun changement appliqué.",
                      getattr(exc, "conflicts", ()), conflict_minimal=getattr(exc, "minimal", False),
                      termination="conflict" if isinstance(exc, AffineConflict) else "invalid")


def diagnose(sketch):
    try:
        return _resolved_run(sketch, update=False)
    except (ValueError, ArithmeticError, np.linalg.LinAlgError) as exc:
        return Result(False, sketch, None, None, float("inf"), f"Diagnostic indisponible : {exc}",
                      getattr(exc, "conflicts", ()), conflict_minimal=getattr(exc, "minimal", False),
                      termination="conflict" if isinstance(exc, AffineConflict) else "invalid")


def _resolved_run(sketch, *, update, **limits):
    """Solve sources once, then materialize recipes and remeasure annotations.

    Generated occurrences introduce no independent degrees of freedom and no
    extra constraint equations. Existing component and time budgets still apply.
    """
    if not sketch.patterns and not any(c.driven for c in sketch.constraints):
        return _components_run(sketch, update=update, **limits)
    source = sketch.independent(sketch.source_entities,
        constraints=tuple(c for c in sketch.constraints if not c.driven), external=sketch.external)
    result = _components_run(source, update=update, **limits)
    if not result.success:
        return replace(result, sketch=sketch)
    fresh = replace(sketch, entities=result.sketch.entities) if update else sketch
    return replace(result, sketch=fresh)
