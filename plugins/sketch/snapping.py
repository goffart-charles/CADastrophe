"""Transient pixel-based sketch inference. No Qt, OCCT, constraints or mutations."""
from dataclasses import dataclass
from itertools import combinations
from math import atan2, cos, hypot, pi, sin, sqrt

from .model import Arc, Circle, Point, Segment, Vec2, Ellipse, Spline

LABELS = {"endpoint": "Extrémité", "origin": "Origine", "intersection": "Intersection",
          "midpoint": "Milieu", "center": "Centre", "horizontal": "Horizontal",
          "vertical": "Vertical", "grid": "Grille"}
PRIORITY = {key: index for index, key in enumerate(LABELS)}
PRIORITY["vertical"] = PRIORITY["horizontal"]


@dataclass(frozen=True)
class SnapSettings:
    enabled: bool = True
    inference: bool = True
    grid: bool = False
    grid_step: float = 10.


@dataclass(frozen=True)
class Snap:
    key: tuple
    point: Vec2
    kind: str

    @property
    def label(self):
        return LABELS[self.kind]


def _on(entity, point):
    if type(entity) is Arc:
        angle = atan2(point.v - entity.center.v, point.u - entity.center.u)
        delta = ((angle - entity.start_angle) * (1 if entity.sweep > 0 else -1)) % (2*pi)
        return delta <= abs(entity.sweep) + 1e-9 or 2*pi - delta < 1e-9
    return True


def intersections(first, second):
    """Finite curves only; coincident/overlapping curves have no unique target."""
    if type(first) is Segment and type(second) is Segment:
        a, b, c, d = first.start, first.end, second.start, second.end
        u, v = Vec2(b.u-a.u, b.v-a.v), Vec2(d.u-c.u, d.v-c.v)
        cross = u.u*v.v-u.v*v.u
        if abs(cross) <= 1e-12 * max(1e-300, hypot(u.u, u.v)*hypot(v.u, v.v)):
            return ()
        t = ((c.u-a.u)*v.v-(c.v-a.v)*v.u)/cross
        s = ((c.u-a.u)*u.v-(c.v-a.v)*u.u)/cross
        return (Vec2(a.u+t*u.u, a.v+t*u.v),) if -1e-10 <= t <= 1+1e-10 and -1e-10 <= s <= 1+1e-10 else ()
    if type(second) is Segment:
        first, second = second, first
    if type(first) is Segment and isinstance(second, Circle):
        a, b, c = first.start, first.end, second.center
        dx, dy, x, y = b.u-a.u, b.v-a.v, a.u-c.u, a.v-c.v
        aa, bb = dx*dx+dy*dy, 2*(x*dx+y*dy)
        disc = bb*bb-4*aa*(x*x+y*y-second.radius**2)
        tolerance = 1e-12 * max(1e-300, bb*bb, 4*aa*(x*x+y*y+second.radius**2))
        if disc < -tolerance:
            return ()
        roots = {(-bb+sign*sqrt(max(0., disc)))/(2*aa) for sign in (-1, 1)}
        return tuple(point for t in sorted(roots) if -1e-10 <= t <= 1+1e-10
                     if _on(second, point := Vec2(a.u+t*dx, a.v+t*dy)))
    if isinstance(first, Circle) and isinstance(second, Circle):
        a, b, r, s = first.center, second.center, first.radius, second.radius
        dx, dy = b.u-a.u, b.v-a.v
        distance = hypot(dx, dy)
        tolerance = 1e-10 * max(1., r, s, distance)
        if distance <= tolerance or distance > r+s+tolerance or distance < abs(r-s)-tolerance:
            return ()
        along = (r*r-s*s+distance*distance)/(2*distance)
        height = sqrt(max(0., r*r-along*along))
        points = {Vec2(a.u+along*dx/distance-sign*height*dy/distance,
                       a.v+along*dy/distance+sign*height*dx/distance) for sign in (-1, 1)}
        return tuple(point for point in sorted(points, key=lambda p: (p.u, p.v)) if _on(first, point) and _on(second, point))
    return ()


class SnapEngine:
    enter_pixels = 8.
    release_pixels = 12.

    def __init__(self):
        self.entities = None
        self.points = ()
        self.curves = ()
        self.cache = {}
        self.lock = None

    def reset(self):
        self.lock = None

    def update(self, entities):
        entities = tuple(entities)
        if entities == self.entities:
            return
        self.entities, self.cache, self.lock = entities, {}, None
        points, curves = [], []
        for entity in sorted(entities, key=lambda entity: entity.id):
            key = entity.id
            if type(entity) is Point:
                points.append(Snap((key, "point"), entity.position, "endpoint"))
            elif type(entity) is Segment:
                points.extend((Snap((key, "start"), entity.start, "endpoint"), Snap((key, "end"), entity.end, "endpoint"),
                               Snap((key, "mid"), Vec2((entity.start.u+entity.end.u)/2, (entity.start.v+entity.end.v)/2), "midpoint")))
                curves.append((entity, (min(entity.start.u, entity.end.u), min(entity.start.v, entity.end.v),
                                        max(entity.start.u, entity.end.u), max(entity.start.v, entity.end.v))))
            elif isinstance(entity, Circle):
                points.append(Snap((key, "center"), entity.center, "center"))
                if type(entity) is Arc:
                    points.extend(Snap((key, tag), entity.point_at(t), kind)
                                  for tag, t, kind in (("start", 0, "endpoint"), ("end", 1, "endpoint"), ("mid", .5, "midpoint")))
                curves.append((entity, (entity.center.u-entity.radius, entity.center.v-entity.radius,
                                        entity.center.u+entity.radius, entity.center.v+entity.radius)))
            elif type(entity) is Ellipse:
                points.append(Snap((key, "center"), entity.center, "center"))
                points.extend(Snap((key, "quadrant", i), entity.point_at(i/4), "endpoint") for i in range(4))
            elif type(entity) is Spline:
                points.extend(Snap((key, "pole", i), p, "endpoint") for i, p in enumerate(entity.points))
                if not entity.closed:
                    points.extend((Snap((key, "start"), entity.point_at(0), "endpoint"),
                                   Snap((key, "end"), entity.point_at(1), "endpoint")))
        self.points, self.curves = tuple(points), tuple(curves)

    def resolve(self, raw, project, settings=SnapSettings(), *, anchor=None, exclude=(), circle=None):
        """project(Vec2) -> logical-pixel (x,y). Thresholds never use world mm."""
        excluded = set(exclude)
        sx, sy = project(raw)
        def distance(point):
            x, y = project(point)
            return hypot(x-sx, y-sy)
        candidates = []
        if settings.enabled:
            unit_u, unit_v = project(Vec2(raw.u+1, raw.v)), project(Vec2(raw.u, raw.v+1))
            ux, uy = unit_u[0]-sx, unit_u[1]-sy
            vx, vy = unit_v[0]-sx, unit_v[1]-sy
            # Inverse projected basis: oblique U/V can become almost collinear.
            # Column lengths alone underestimate the visible world-space radius.
            determinant = abs(ux*vy-uy*vx)
            radius = self.release_pixels * max(hypot(ux, uy), hypot(vx, vy)) / max(determinant, 1e-24)
            candidates.extend(point for point in self.points if point.key[0] not in excluded
                              and abs(point.point.u-raw.u) <= radius and abs(point.point.v-raw.v) <= radius)
            candidates.append(Snap(("origin",), Vec2(0, 0), "origin"))
            near = [entity for entity, box in self.curves if entity.id not in excluded
                    and box[0]-radius <= raw.u <= box[2]+radius and box[1]-radius <= raw.v <= box[3]+radius]
            for first, second in combinations(near, 2):
                key = first.id, second.id
                if key not in self.cache:
                    if len(self.cache) >= 4096:
                        self.cache.pop(next(iter(self.cache)))
                    self.cache[key] = intersections(first, second)
                candidates.extend(Snap((*key, index), point, "intersection") for index, point in enumerate(self.cache[key]))
        if settings.inference and anchor is not None:
            candidates.extend((Snap(("horizontal", anchor.u, anchor.v), Vec2(raw.u, anchor.v), "horizontal"),
                               Snap(("vertical", anchor.u, anchor.v), Vec2(anchor.u, raw.v), "vertical")))
        if settings.grid:
            step = settings.grid_step
            point = Vec2(round(raw.u/step)*step, round(raw.v/step)*step)
            candidates.append(Snap(("grid", point.u, point.v), point, "grid"))
        if circle is not None:
            center, radius = circle
            candidates = [candidate for candidate in candidates if
                          abs(hypot(candidate.point.u-center.u, candidate.point.v-center.v)-radius) <= 1e-8*max(1., radius)]
        eligible = [candidate for candidate in candidates if distance(candidate.point) <= self.enter_pixels]
        best = min(eligible, key=lambda item: (PRIORITY[item.kind], distance(item.point), item.key), default=None)
        previous = next((candidate for candidate in candidates if self.lock and candidate.key == self.lock.key), None)
        if previous is not None and distance(previous.point) <= self.release_pixels:
            if best is None or PRIORITY[best.kind] >= PRIORITY[previous.kind]:
                best = previous
        self.lock = best
        return (best.point, best) if best else (raw, None)
