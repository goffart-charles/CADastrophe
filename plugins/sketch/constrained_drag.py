"""Cursor motion in the real constraint nullspace, with feasible retraction.

The cursor is an objective, never an extra persistent constraint. Existing
relations remain mandatory, including during detached previews. No Qt/OCCT.
"""
from dataclasses import replace
from math import cos, sin, hypot
from time import perf_counter

import numpy as np

from .model import Arc, Segment
from .constraints import Constraint, Reference
from . import solver
from .solver import System, parameters, MAX_VARIABLES, MAX_EQUATIONS, RANK_CUTOFF


class ConstrainedDrag:
    def __init__(self, sketch, entities, handle, anchor, *, joints=()):
        keys = {e.id for e in entities}
        sketch.require_editable(keys)
        self.sketch = sketch.independent(entities, external=sketch.external, constraints=tuple(
            c for c in sketch.constraints if not c.driven and any(r.entity_id in keys for r in c.refs)))
        # E4 legacy joints have no stored coincidence. Keep their frozen node
        # joined mathematically for this gesture, including mixed old/new data.
        # A spanning forest avoids duplicating any persistent coincidence chain.
        if len(joints) > 1:
            parents = {}
            def root(ref):
                parents.setdefault(ref,ref)
                if parents[ref] != ref:
                    parents[ref] = root(parents[ref])
                return parents[ref]
            for c in self.sketch.constraints:
                if c.kind == "coincident":
                    parents[root(c.refs[0])] = root(c.refs[1])
            source = Reference(*handle)
            temporary = []
            for joint in joints:
                other = Reference(*joint)
                a,b = root(source),root(other)
                if a != b:
                    temporary.append(Constraint("coincident",(source,other)))
                    parents[a] = b
            self.sketch = replace(self.sketch,constraints=(*self.sketch.constraints,*temporary))
        self.system = System(self.sketch)
        self.handle = handle
        self.anchor = anchor
        self.weight = .5
        entity = self.sketch.entity(handle[0])
        if handle[1] == "entity" and type(entity) is Segment:
            dx,dy = entity.end.u-entity.start.u,entity.end.v-entity.start.v
            self.weight = max(0.,min(1.,((anchor.u-entity.start.u)*dx+(anchor.v-entity.start.v)*dy)/(dx*dx+dy*dy)))
        if len(self.system.x) > MAX_VARIABLES or len(self.system.row_constraints) > MAX_EQUATIONS:
            raise ValueError("Déplacement indisponible : système trop volumineux pour le solveur local.")
        if not self.valid(self.system.x):
            raise ValueError("Déplacement refusé : les relations initiales ne sont pas satisfaites.")
        self.x = self.system.x.copy()
        self.last_target = None
        self.last_entities = self.sketch.entities

    def valid(self,x):
        residuals = self.system.residuals(x)
        return bool(np.all(np.isfinite(residuals)) and np.all(np.abs(residuals) <= self.system.tolerances))

    def goal(self,x):
        """Grabbed point (or radius), plus its analytic scaled derivative."""
        system = self.system
        key,part = self.handle
        e,p = system.entities[key],system.values(x,key)
        j = np.zeros((len(parameters(e)),len(x)))
        if key in system.slices:
            j[:,system.slices[key]] = np.eye(len(p))
        if part == "radius":
            return p[2:3],j[2:3]*system.scales
        if type(e) is Segment:
            t = self.weight if part == "entity" else 1. if part == "end" else 0.
            return p[:2]*(1-t)+p[2:4]*t,(j[:2]*(1-t)+j[2:4]*t)*system.scales
        if type(e) is Arc and part in {"start","end"}:
            angle = p[3]+(p[4] if part == "end" else 0)
            direction = np.array((cos(angle),sin(angle)))
            tangent = p[2]*np.array((-sin(angle),cos(angle)))
            return (p[:2]+p[2]*direction,(j[:2]+np.outer(direction,j[2])+
                    np.outer(tangent,j[3]+(j[4] if part == "end" else 0)))*system.scales)
        return p[:2],j[:2]*system.scales

    def move(self,point):
        if point == self.last_target:
            return self.last_entities
        system = self.system
        source = system.entities[self.handle[0]]
        if self.handle[1] == "radius":
            desired = np.array((hypot(point.u-source.center.u,point.v-source.center.v),))
        else:
            desired = np.array((point.u,point.v))-system.origin
        x = self.x.copy()
        deadline = perf_counter()+.08
        for _ in range(24):
            value,gradient = self.goal(x)
            error = desired-value
            score = float(error@error)
            if score < 1e-14 or perf_counter() > deadline:
                break
            matrix = system.rank_matrix(x)
            _,singular,vh = np.linalg.svd(matrix,full_matrices=True)
            rank = int(np.count_nonzero(singular > max(1e-10,singular[0]*RANK_CUTOFF))) if len(singular) else 0
            basis = vh[rank:].T
            if not basis.shape[1]:
                break
            u,values,v = np.linalg.svd(gradient@basis,full_matrices=False)
            count = int(np.count_nonzero(values > max(1e-10,values[0]*RANK_CUTOFF))) if len(values) else 0
            if not count:
                break
            step = (basis@v[:count].T@((u[:,:count].T@error)/values[:count]))*system.scales
            if np.linalg.norm(step) < 1e-10:
                break
            improved = False
            for backtrack in range(10):
                if perf_counter() > deadline:
                    break
                try:
                    trial_x = x+step*(.5**backtrack)
                    trial = system.unpack(trial_x)
                    # Affine/nullspace moves need no nonlinear solve at all.
                    if not self.valid(trial_x):
                        result = solver.solve(trial,max_iterations=30,time_budget=max(.001,deadline-perf_counter()))
                        if not result.success:
                            continue
                        trial = result.sketch
                    candidate = x.copy()
                    for e in trial.entities:
                        if e.id in system.slices:
                            candidate[system.slices[e.id]] = system.local_parameters(e)
                    # Verify the original signed branches and rounded geometry.
                    if not self.valid(candidate):
                        continue
                    delta = desired-self.goal(candidate)[0]
                    if float(delta@delta) >= score-1e-12:
                        continue
                    x,improved = candidate,True
                    break
                except (ValueError,ArithmeticError,np.linalg.LinAlgError):
                    continue
            if not improved:
                break
        if not self.valid(x):
            raise ValueError("Déplacement refusé : les contraintes ne sont pas satisfaites.")
        if np.allclose(x,system.x,rtol=0.,atol=1e-10):
            if np.linalg.norm(desired-self.goal(x)[0]) > system.linear_tolerance:
                raise ValueError("Déplacement bloqué : le point saisi est immobilisé par les contraintes.")
            entities = self.sketch.entities
        else:
            entities = system.unpack(x).entities
        self.x,self.last_target,self.last_entities = x,point,entities
        return entities
