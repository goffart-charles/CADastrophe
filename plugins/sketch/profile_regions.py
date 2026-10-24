"""Exact bounded cells of planar segment/arc/circle arrangements, without natives.

Intersections split detached curves, coincident pieces are deduplicated and
bridges are discarded. The source sketch and strict E7 analysis stay unchanged.
"""
from dataclasses import replace
from functools import lru_cache
from math import atan2, cos, floor, sin
from time import perf_counter
from uuid import NAMESPACE_URL, uuid5

from .model import Segment, Arc, Circle, Point
from .profile_geometry import TAU, ends, intersections, distance, area, contains, sample, arc_contains
from .profiles import analyze, Contour, EdgeUse


def _id(prefix, sources):
    return str(uuid5(NAMESPACE_URL, "cadastrophe.region."+prefix+":"+",".join(sorted(sources))))


def analyze_regions(sketch, *, tolerance=1e-7, time_budget=1., report=None):
    deadline = perf_counter()+time_budget
    report = analyze(sketch, tolerance=tolerance, time_budget=time_budget) if report is None else report
    if report.valid:
        if not any(c.hole for c in report.contours):
            return report
        # Every enclosed area is selectable, including the inside of a hole.
        return _with_cells(report, tuple(_positive(c) for c in report.contours), sketch)
    allowed = {"branch", "intersection", "overlap", "open_end", "degenerate_area"}
    if any(d.code not in allowed for d in report.diagnostics):
        return report
    edges = tuple(e for e in sketch.profile_entities if type(e) is not Point)
    if not edges:
        return report
    try:
        return _cells(report, edges, deadline)
    except (ValueError, TimeoutError, ArithmeticError):
        return report  # Preserve diagnostics if no complete, exact cells exist.


@lru_cache(maxsize=128)
def additional_region_ids(sketch):
    from .model import Ellipse, Spline
    if any(type(e) in (Ellipse, Spline) for e in sketch.profile_entities):
        from .native_regions import report as native_report
        result = native_report(sketch)
        return tuple(c.id for c in result.contours) if result.valid else ()
    report = analyze_regions(sketch)
    return tuple(c.id for c in report.contours) if report.valid and report.profile_sketch is not None else ()


def _positive(contour):
    if contour.signed_area < 0:
        contour = replace(contour, edges=tuple(EdgeUse(u.entity_id, not u.reverse) for u in reversed(contour.edges)),
                          signed_area=-contour.signed_area)
    return replace(contour, depth=0, parent_id=None)


def _with_cells(report, bounded, geometry):
    """Positive cells plus separate reversed wires for their immediate holes."""
    entities = {e.id: e for e in geometry.entities}
    from .model import Vec2
    bounds = {}
    for contour in bounded:
        points = []
        for use in contour.edges:
            e = entities[use.entity_id]
            points.extend(ends(e))
            if type(e) is not Segment:
                for angle in (0., TAU/4, TAU/2, 3*TAU/4):
                    p = Vec2(e.center.u+e.radius*cos(angle), e.center.v+e.radius*sin(angle))
                    if arc_contains(e, p, report.tolerance):
                        points.append(p)
        bounds[contour.id] = (min(p.u for p in points), min(p.v for p in points),
                              max(p.u for p in points), max(p.v for p in points))
    contours = list(bounded)
    for contour in bounded:
        boundary = {u.entity_id for u in contour.edges}
        box = bounds[contour.id]
        parents = [other for other in bounded
                   if other.signed_area-contour.signed_area > max(report.tolerance**2, contour.signed_area*1e-12)
                   and all(bounds[other.id][i]-report.tolerance <= box[i] for i in (0, 1))
                   and all(bounds[other.id][i]+report.tolerance >= box[i] for i in (2, 3))
                   and not boundary.intersection(u.entity_id for u in other.edges)
                   and all(contains(other.edges, entities, sample(entities[u.entity_id]), report.tolerance)
                           for u in contour.edges)]
        if parents:
            parent = min(parents, key=lambda c: c.signed_area)
            contours.append(Contour(_id("hole", (parent.id, contour.id)),
                                    tuple(EdgeUse(u.entity_id, not u.reverse) for u in reversed(contour.edges)),
                                    -contour.signed_area, depth=1, parent_id=parent.id))
    return replace(report, contours=tuple(sorted(contours, key=lambda c: c.id)), diagnostics=(),
                   profile_sketch=geometry)


def _bridges(adjacency):
    """Iterative Tarjan walk; open branches and links between loops bound no cell."""
    discovered, low, bridges = {}, {}, set()
    for root in adjacency:
        if root in discovered:
            continue
        discovered[root] = low[root] = len(discovered)
        stack = [(root, None, None, iter(adjacency[root]))]
        while stack:
            a, parent, parent_edge, neighbors = stack[-1]
            item = next(neighbors, None)
            if item is None:
                stack.pop()
                if parent is not None:
                    low[parent] = min(low[parent], low[a])
                    if low[a] > discovered[parent]:
                        bridges.add(parent_edge)
                continue
            b, key = item
            if key == parent_edge:
                continue
            if b in discovered:
                low[a] = min(low[a], discovered[b])
            else:
                discovered[b] = low[b] = len(discovered)
                stack.append((b, a, key, iter(adjacency[b])))
    return bridges


def _cells(report, edges, deadline):
    tol = report.tolerance
    lengths = {e.id: distance(e.start, e.end) if type(e) is Segment else
               e.radius*(abs(e.sweep) if type(e) is Arc else TAU) for e in edges}
    if any(length <= tol for length in lengths.values()):
        raise ValueError("Arête dégénérée")
    cuts = {e.id: [0., .25, .5, .75, 1.] if type(e) is Circle else [0., 1.] for e in edges}

    def insert(edge, point):
        if type(edge) is Segment:
            dx, dy = edge.end.u-edge.start.u, edge.end.v-edge.start.v
            value = ((point.u-edge.start.u)*dx+(point.v-edge.start.v)*dy)/lengths[edge.id]**2
            if not -tol/lengths[edge.id] <= value <= 1+tol/lengths[edge.id]:
                return
        else:
            angle = atan2(point.v-edge.center.v, point.u-edge.center.u)
            first, sweep = (edge.start_angle, edge.sweep) if type(edge) is Arc else (0., TAU)
            delta = ((angle-first)*(1 if sweep > 0 else -1)) % TAU
            if TAU-delta <= tol/edge.radius:
                delta = 0.
            value = delta/abs(sweep)
            if value > 1+tol/lengths[edge.id]:
                return
        value = max(0., min(1., value))
        if distance(point, sample(edge, value)) <= tol:
            cuts[edge.id].append(value)

    for i, edge in enumerate(edges):
        if perf_counter() > deadline:
            raise TimeoutError()
        for other in edges[i+1:]:
            points, overlap = intersections(edge, other, tol)
            for point in points:
                insert(edge, point)
                insert(other, point)
            if overlap:
                # Also align the periodic seams of coincident circles/arcs.
                points = (*(ends(edge) or tuple(sample(edge, t) for t in (0., .25, .5, .75))),
                          *(ends(other) or tuple(sample(other, t) for t in (0., .25, .5, .75))))
                for point in points:
                    insert(edge, point)
                    insert(other, point)

    positions, buckets = [], {}
    def node(point):
        cell = floor(point.u/tol), floor(point.v/tol)
        matches = [n for x in (-1, 0, 1) for y in (-1, 0, 1)
                   for n in buckets.get((cell[0]+x, cell[1]+y), ()) if distance(point, positions[n]) <= tol]
        if len(matches) > 1:
            raise ValueError("Sommet ambigu")
        if matches:
            return matches[0]
        n = len(positions)
        positions.append(point)
        buckets.setdefault(cell, []).append(n)
        return n

    circles = []
    def support(edge):
        for index, other in enumerate(circles):
            if distance(edge.center, other.center) <= tol and abs(edge.radius-other.radius) <= tol:
                return index
        circles.append(edge)
        return len(circles)-1

    pieces = {}
    for edge in sorted(edges, key=lambda e: e.id):
        if perf_counter() > deadline:
            raise TimeoutError()
        values = []
        for value in sorted(cuts[edge.id]):
            if not values or (value-values[-1])*lengths[edge.id] > tol:
                values.append(value)
        circle = None if type(edge) is Segment else support(edge)
        for index, (first, last) in enumerate(zip(values, values[1:])):
            start, end = sample(edge, first), sample(edge, last)
            a, b = node(start), node(end)
            if a == b:
                raise ValueError("Sous-arête dégénérée")
            if type(edge) is Segment:
                canonical = index if (edge.start.u, edge.start.v) < (edge.end.u, edge.end.v) else len(values)-2-index
                signature = ("line", *sorted((a, b)))
                piece = Segment(positions[a], positions[b])
            else:
                sweep = edge.sweep if type(edge) is Arc else TAU
                canonical = index if sweep > 0 else len(values)-2-index
                signature = ("arc", circle, a, b) if sweep > 0 else ("arc", circle, b, a)
                angle = (edge.start_angle if type(edge) is Arc else 0.)+first*sweep
                piece = Arc(edge.center, edge.radius, angle, (last-first)*sweep)
            if signature not in pieces:
                pieces[signature] = (a, b, piece, [])
            pieces[signature][3].append(f"{edge.id}:{canonical}")
            if len(pieces) > 2048:
                raise ValueError("Trop de sous-arêtes")

    adjacency = {n: [] for n in range(len(positions))}
    entities, endpoints = {}, {}
    for a, b, piece, sources in pieces.values():
        key = _id("edge", sources)
        entities[key] = replace(piece, id=key)
        endpoints[key] = a, b
        adjacency[a].append((b, key))
        adjacency[b].append((a, key))
    bridges = _bridges(adjacency)
    entities = {key: e for key, e in entities.items() if key not in bridges}
    outgoing = {n: [] for n in adjacency}
    for key in entities:
        a, b = endpoints[key]
        outgoing[a].append((key, False))
        outgoing[b].append((key, True))

    def target(use):
        return endpoints[use[0]][0 if use[1] else 1]

    def direction(use):
        key, reverse = use
        e = entities[key]
        if type(e) is Segment:
            dx, dy = e.end.u-e.start.u, e.end.v-e.start.v
            if reverse:
                dx, dy = -dx, -dy
            curvature = 0.
        else:
            theta = e.start_angle+(e.sweep if reverse else 0.)
            sign = (1 if e.sweep > 0 else -1)*(-1 if reverse else 1)
            dx, dy = -sin(theta)*sign, cos(theta)*sign
            curvature = sign/e.radius
        # Curvature orders equal tangents, e.g. two circles touching at a point.
        angle = round(atan2(dy, dx) % TAU, 12)
        if abs(angle-TAU) < 1e-11:
            angle = 0.
        return angle, curvature, key

    successor = {}
    for uses in outgoing.values():
        uses.sort(key=direction)
        for index, use in enumerate(uses):
            successor[use[0], not use[1]] = uses[(index-1) % len(uses)]
    visited, bounded = set(), {}
    for start in sorted(successor):
        if start in visited:
            continue
        current, path = start, []
        while current not in visited:
            if perf_counter() > deadline:
                raise TimeoutError()
            visited.add(current)
            path.append(current)
            current = successor[current]
        if current != start:
            raise ValueError("Parcours ambigu")
        # Separate simple loops at tangent vertices. Negative components are
        # hole/exterior boundaries; positive components delimit selectable cells.
        chain, vertices = [], {endpoints[start[0]][1 if start[1] else 0]: 0}
        for use in path:
            chain.append(use)
            vertex = target(use)
            if vertex not in vertices:
                vertices[vertex] = len(chain)
                continue
            index = vertices[vertex]
            loop = chain[index:]
            uses = tuple(EdgeUse(*u) for u in loop)
            signed = area(uses, entities)
            if signed > tol**2:
                key = _id("cell", (u.entity_id for u in uses))
                bounded[key] = Contour(key, uses, signed)
            for item in chain[index:]:
                vertices.pop(target(item), None)
            chain = chain[:index]
            vertices[vertex] = index
    if not bounded:
        raise ValueError("Aucune zone fermée")
    geometry = report.sketch.independent(entities.values())
    return _with_cells(report, tuple(bounded.values()), geometry)
