"""Pure, bounded closed-contour analysis. Does not heal or mutate the sketch."""
from dataclasses import dataclass,replace
from math import floor, isfinite, ulp
from time import perf_counter
from .model import Segment, Arc, Circle, Point, Ellipse, Spline
from .profile_geometry import ends, sample, distance, intersections, area, contains

MAX_CURVES = 512


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    entity_ids: tuple = ()
    points: tuple = ()
    distance: float | None = None
    severity: str = "error"


@dataclass(frozen=True)
class EdgeUse:
    entity_id: str
    reverse: bool = False


@dataclass(frozen=True)
class Contour:
    id: str
    edges: tuple[EdgeUse,...]
    signed_area: float
    depth: int = 0
    parent_id: str | None = None

    @property
    def hole(self):
        return bool(self.depth%2)


@dataclass(frozen=True)
class Analysis:
    sketch: object
    tolerance: float
    contours: tuple[Contour,...] = ()
    diagnostics: tuple[Diagnostic,...] = ()
    ignored_points: tuple = ()
    profile_sketch: object = None  # Detached cell edges; original sketch remains the snapshot.

    @property
    def geometry(self):
        return self.sketch if self.profile_sketch is None else self.profile_sketch

    @property
    def valid(self):
        return bool(self.contours) and not any(d.severity == "error" for d in self.diagnostics)

    @property
    def regions(self):
        return tuple((c,tuple(h for h in self.contours if h.parent_id == c.id and h.hole))
                     for c in self.contours if not c.hole)


def analyze(sketch,*,tolerance=1e-7,time_budget=1.):
    if type(tolerance) not in (float,int) or not isfinite(tolerance) or tolerance <= 0:
        raise ValueError("La tolérance doit être un nombre positif fini en mm.")
    if not isfinite(time_budget) or time_budget <= 0:
        raise ValueError("Le budget d’analyse doit être positif et fini.")
    entities = tuple(e for e in sketch.profile_entities if type(e) is not Point)
    ignored = tuple(e.id for e in sketch.entities if type(e) is Point)
    result = Analysis(sketch,float(tolerance),ignored_points=ignored)
    if not entities:
        return replace(result,diagnostics=(Diagnostic("no_curves","L’esquisse ne contient aucun contour à vérifier."),))
    if len(entities) > MAX_CURVES:
        return replace(result,diagnostics=(Diagnostic("limit","Trop de courbes pour l’analyse locale (512 maximum)."),))
    if any(type(e) in (Ellipse, Spline) or e.text_group is not None for e in entities):
        return replace(result, diagnostics=(Diagnostic("native_curve", "Ce profil utilise l’analyse native des ellipses et splines."),))
    deadline = perf_counter()+time_budget
    try:
        magnitude = max((abs(v) for e in entities for p in (ends(e) or (e.center,)) for v in (p.u,p.v)),default=1.)
        magnitude = max(magnitude,max((max(abs(e.center.u),abs(e.center.v))+e.radius for e in entities if isinstance(e,Circle)),default=0.))
        if not isfinite(magnitude):
            raise ValueError("Les coordonnées dépassent la précision numérique disponible.")
        result = replace(result,tolerance=max(float(tolerance),8*ulp(magnitude)))
        return _analyze(result,entities,deadline)
    except TimeoutError:
        return replace(result,diagnostics=(Diagnostic("time_limit","Analyse interrompue : budget de calcul dépassé."),))
    except (ValueError,ArithmeticError) as exc:
        return replace(result,diagnostics=(Diagnostic("numerical_error",f"Analyse géométrique impossible : {exc}"),))


def _analyze(result,curves,deadline):
    sketch,tol = result.sketch,result.tolerance
    diagnostics = []
    if sketch.constraints:
        from .solver import diagnose
        state = diagnose(sketch)
        if not state.success:
            diagnostics.append(Diagnostic("constraint_conflict",state.message,tuple(
                dict.fromkeys(r.entity_id for c in sketch.constraints if c.id in state.conflicts for r in c.refs))))
    endpoints = [(e.id,i,p) for e in curves for i,p in enumerate(ends(e))]
    parent = list(range(len(endpoints)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    buckets = {}
    for i,(_,_,p) in enumerate(endpoints):
        cell = floor(p.u/tol),floor(p.v/tol)
        for dx in (-1,0,1):
            for dy in (-1,0,1):
                for j in buckets.get((cell[0]+dx,cell[1]+dy),()):
                    if distance(p,endpoints[j][2]) <= tol:
                        parent[root(i)] = root(j)
        buckets.setdefault(cell,[]).append(i)
    nodes,at = {},{}
    for i,(key,part,p) in enumerate(endpoints):
        node = root(i)
        at[key,part] = node
        nodes.setdefault(node,[]).append((key,part,p))
    for node,refs in nodes.items():
        ids = tuple(dict.fromkeys(key for key,_,_ in refs))
        if len(refs) == 1:
            p = refs[0][2]
            nearby = [distance(p,rs[0][2]) for other,rs in nodes.items() if other != node and len(rs) == 1]
            diagnostics.append(Diagnostic("open_end","Extrémité ouverte : le contour n’est pas fermé.",ids,(p,),min(nearby,default=None)))
        elif len(refs) > 2:
            diagnostics.append(Diagnostic("branch","Jonction ambiguë : plus de deux courbes se rencontrent.",ids,tuple(p for _,_,p in refs)))
        if any(distance(a[2],b[2]) > tol for a in refs for b in refs):
            diagnostics.append(Diagnostic("ambiguous_node","Chaîne de points trop étendue pour une jonction unique.",ids))
    for i,a in enumerate(curves):
        if perf_counter() > deadline:
            raise TimeoutError()
        if ((type(a) is Segment and distance(a.start,a.end) <= tol) or
                (isinstance(a,Circle) and a.radius <= tol) or
                (type(a) is Arc and a.radius*abs(a.sweep) <= tol)):
            diagnostics.append(Diagnostic("degenerate","Courbe trop petite pour la tolérance du document.",(a.id,)))
        for b in curves[i+1:]:
            points,overlap = intersections(a,b,tol)
            if overlap:
                diagnostics.append(Diagnostic("overlap","Des courbes se superposent sur une longueur non nulle.",(a.id,b.id)))
            bad = []
            for p in points:
                ea,eb = ends(a),ends(b)
                joint = any(distance(p,pa) <= tol and distance(p,pb) <= tol and at[a.id,ia] == at[b.id,ib] and
                            len(nodes[at[a.id,ia]]) == 2 for ia,pa in enumerate(ea) for ib,pb in enumerate(eb))
                if not joint and not any(distance(p,q) <= tol for q in bad):
                    bad.append(p)
            if bad:
                diagnostics.append(Diagnostic("intersection","Intersection ou contact ambigu entre contours.",(a.id,b.id),tuple(bad)))
    entities = {e.id:e for e in curves}
    contours,visited = [],set()
    for key in sorted(entities):
        if key in visited:
            continue
        entity = entities[key]
        if type(entity) is Circle:
            uses = [EdgeUse(key)]
            visited.add(key)
        else:
            component,todo = set(),[key]
            while todo:
                current = todo.pop()
                if current in component:
                    continue
                component.add(current)
                for part in (0,1):
                    todo.extend(k for k,_,_ in nodes[at[current,part]] if k not in component)
            visited.update(component)
            if any(len(nodes[at[k,p]]) != 2 for k in component for p in (0,1)):
                continue
            uses,start,current,node = [],at[key,0],key,at[key,0]
            while True:
                reverse = at[current,0] != node
                uses.append(EdgeUse(current,reverse))
                node = at[current,0 if reverse else 1]
                if node == start:
                    break
                current = next(k for k,_,_ in nodes[node] if k != current)
                if len(uses) > len(component):
                    raise ValueError("Parcours du contour ambigu.")
        signed = area(uses,entities)
        if not isfinite(signed) or abs(signed) <= tol*tol:
            diagnostics.append(Diagnostic("degenerate_area","Contour d’aire nulle ou numériquement indéterminée.",tuple(u.entity_id for u in uses)))
        contours.append(Contour(min(u.entity_id for u in uses),tuple(uses),signed))
    # Nest only simple, disjoint closed contours. Contacts are never guessed.
    if not diagnostics:
        nested = []
        for c in contours:
            p = sample(entities[c.edges[0].entity_id])
            parents = [other for other in contours if abs(other.signed_area) > abs(c.signed_area) and contains(other.edges,entities,p,tol)]
            parent_id = min(parents,key=lambda other: abs(other.signed_area)).id if parents else None
            c = replace(c,depth=len(parents),parent_id=parent_id)
            if (c.signed_area > 0) == c.hole:
                c = replace(c,edges=tuple(EdgeUse(u.entity_id,not u.reverse) for u in reversed(c.edges)),signed_area=-c.signed_area)
            nested.append(c)
        contours = nested
    return replace(result,contours=tuple(contours),diagnostics=tuple(diagnostics))
