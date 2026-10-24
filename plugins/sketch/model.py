"""Immutable 2D business model. No Qt, OCCT, document or rendering imports.

Lengths: mm. Angles: radians, positive counterclockwise in local U/V.
Independent endpoints are parameters, not implicit coincident constraints.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import cos, hypot, isfinite, pi, sin
from typing import ClassVar, TypeAlias
from uuid import UUID, uuid4

from cadastrophe.geometry import Ray, Vec3, WorkPlane


def new_id() -> str:
    return str(uuid4())


def valid_id(value: str) -> None:
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError("Un UUID canonique est requis.")


def finite(value: float) -> None:
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError("Une valeur numérique finie est requise.")


@dataclass(frozen=True)
class Vec2:
    u: float
    v: float

    def __post_init__(self):
        finite(self.u)
        finite(self.v)


@dataclass(frozen=True, kw_only=True)
class _Entity:
    id: str = field(default_factory=new_id)
    construction: bool = False
    text_group: str | None = None

    def __post_init__(self):
        valid_id(self.id)
        if self.text_group is not None:
            valid_id(self.text_group)
        if type(self.construction) is not bool:
            raise ValueError("Le statut de construction doit être booléen.")


def vector(value: Vec2) -> None:
    if not isinstance(value, Vec2):
        raise ValueError("Des coordonnées locales Vec2 sont requises.")


@dataclass(frozen=True)
class Point(_Entity):
    position: Vec2
    kind: ClassVar[str] = "point"

    def __post_init__(self):
        super().__post_init__()
        vector(self.position)


@dataclass(frozen=True)
class Segment(_Entity):
    start: Vec2
    end: Vec2
    kind: ClassVar[str] = "segment"

    def __post_init__(self):
        super().__post_init__()
        vector(self.start)
        vector(self.end)
        if hypot(self.end.u - self.start.u, self.end.v - self.start.v) <= 1e-10:
            raise ValueError("Un segment doit avoir une longueur non nulle.")


@dataclass(frozen=True)
class Circle(_Entity):
    center: Vec2
    radius: float
    kind: ClassVar[str] = "circle"

    def __post_init__(self):
        super().__post_init__()
        vector(self.center)
        finite(self.radius)
        if self.radius <= 0:
            raise ValueError("Le rayon doit être strictement positif.")


@dataclass(frozen=True)
class Arc(Circle):
    start_angle: float
    sweep: float
    kind: ClassVar[str] = "arc"

    def __post_init__(self):
        super().__post_init__()
        finite(self.start_angle)
        finite(self.sweep)
        if not 0 < abs(self.sweep) < 2 * pi:
            raise ValueError("Un arc exige un balayage signé non nul inférieur à un tour.")

    def point_at(self, fraction: float) -> Vec2:
        finite(fraction)
        if not 0 <= fraction <= 1:
            raise ValueError("La fraction doit être comprise entre 0 et 1.")
        angle = self.start_angle + fraction * self.sweep
        return Vec2(self.center.u + self.radius * cos(angle), self.center.v + self.radius * sin(angle))


@dataclass(frozen=True)
class Ellipse(_Entity):
    center: Vec2
    major_radius: float
    minor_radius: float
    rotation: float = 0.
    kind: ClassVar[str] = "ellipse"

    def __post_init__(self):
        super().__post_init__()
        vector(self.center)
        for v in (self.major_radius, self.minor_radius, self.rotation):
            finite(v)
        if not 0 < self.minor_radius <= self.major_radius:
            raise ValueError("Les demi-axes doivent être positifs ; le grand axe doit être le plus long.")

    def point_at(self, fraction):
        finite(fraction)
        if not 0 <= fraction <= 1:
            raise ValueError("La fraction doit être comprise entre 0 et 1.")
        angle = 2*pi*fraction
        x, y = self.major_radius*cos(angle), self.minor_radius*sin(angle)
        return Vec2(self.center.u+x*cos(self.rotation)-y*sin(self.rotation),
                    self.center.v+x*sin(self.rotation)+y*cos(self.rotation))


@dataclass(frozen=True)
class Spline(_Entity):
    points: tuple[Vec2, ...]
    closed: bool = False
    degree: int | None = None
    knots: tuple[float, ...] = ()
    weights: tuple[float, ...] = ()
    kind: ClassVar[str] = "spline"

    def __post_init__(self):
        super().__post_init__()
        object.__setattr__(self, "points", tuple(self.points))
        if not 2 <= len(self.points) <= (256 if self.degree is not None else 64) or (self.closed and len(self.points) < 3):
            raise ValueError("Une spline exige de 2 à 64 pôles, ou au moins 3 pour être fermée.")
        if type(self.closed) is not bool:
            raise ValueError("L’option de fermeture doit être booléenne.")
        for p in self.points:
            vector(p)
        object.__setattr__(self, "knots", tuple(self.knots))
        object.__setattr__(self, "weights", tuple(self.weights))
        if self.degree is not None:
            if type(self.degree) is not int or not 1 <= self.degree < len(self.points) or self.degree > 25:
                raise ValueError("Degré de spline invalide.")
            if self.closed or len(self.knots) != len(self.points)+self.degree+1:
                raise ValueError("Base de spline invalide.")
            for value in (*self.knots, *self.weights):
                finite(value)
            if any(a > b for a, b in zip(self.knots, self.knots[1:])) or self.knots[self.degree] >= self.knots[len(self.points)]:
                raise ValueError("Les nœuds doivent définir un intervalle croissant.")
            if any(self.knots.count(k) > self.degree+1 for k in set(self.knots)):
                raise ValueError("Multiplicité de nœud excessive.")
            if self.weights and (len(self.weights) != len(self.points) or min(self.weights) <= 0):
                raise ValueError("Les poids doivent être positifs, un par pôle.")
        elif self.knots or self.weights:
            raise ValueError("Une base explicite exige son degré.")
        if self.degree is None and any(hypot(a.u-b.u, a.v-b.v) < 1e-9 for a, b in zip(self.points, self.points[1:])):
            raise ValueError("Deux pôles successifs doivent être distincts.")

    @property
    def basis(self):
        if self.degree is not None:
            return self.degree, self.points, self.knots
        degree = min(3, len(self.points)-1)
        poles = (*self.points, *self.points[:degree]) if self.closed else self.points
        knots = tuple(float(i) for i in range(len(poles)+degree+1)) if self.closed else (
            (0.,)*(degree+1)+tuple(float(i) for i in range(1, len(poles)-degree))+(float(len(poles)-degree),)*(degree+1))
        return degree, poles, knots

    def point_at(self, fraction):
        finite(fraction)
        if not 0 <= fraction <= 1:
            raise ValueError("La fraction doit être comprise entre 0 et 1.")
        degree, poles, knots = self.basis
        t = knots[degree]+fraction*(knots[len(poles)]-knots[degree])
        span = len(poles)-1 if fraction >= 1 else next(i for i in range(degree, len(poles)) if knots[i] <= t < knots[i+1])
        weights = self.weights or (1.,)*len(poles)
        values = [[p.u*w, p.v*w, w] for p, w in zip(poles[span-degree:span+1], weights[span-degree:span+1])]
        for r in range(1, degree+1):
            for j in range(degree, r-1, -1):
                i = span-degree+j
                alpha = (t-knots[i])/(knots[i+degree-r+1]-knots[i])
                values[j] = [(1-alpha)*a+alpha*b for a, b in zip(values[j-1], values[j])]
        u, v, w = values[degree]
        return Vec2(u/w, v/w)


Entity: TypeAlias = Point | Segment | Circle | Arc | Ellipse | Spline
ENTITY_TYPES = (Point, Segment, Circle, Arc, Ellipse, Spline)


@dataclass(frozen=True)
class ExternalGeometry:
    """Projection recipe attached to an explicit producer (or legacy revision)."""
    body_id: str
    revision: int
    topology: str
    index: int
    geometry: Entity
    producer_id: str | None = None
    reference: object = None

    def __post_init__(self):
        valid_id(self.body_id)
        _topological_input(self.producer_id, self.reference, self.topology)
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("Révision externe invalide.")
        if self.topology not in ("Vertex", "Edge") or type(self.index) is not int or self.index < 1:
            raise ValueError("Référence topologique externe invalide.")
        if type(self.geometry) not in ENTITY_TYPES or (self.topology == "Vertex" and type(self.geometry) is not Point):
            raise ValueError("Projection externe invalide.")


@dataclass(frozen=True)
class PlaneSupport:
    plane_id: str
    frame: WorkPlane
    revision: int = 0
    face_index: int | None = None  # Legacy/advisory only when a persistent reference is present.
    producer_id: str | None = None
    reference: object = None
    attachment: WorkPlane | None = None

    def __post_init__(self):
        valid_id(self.plane_id)
        if self.attachment is not None and not isinstance(self.attachment, WorkPlane):
            raise ValueError("Repère d’attachement invalide.")
        _topological_input(self.producer_id, self.reference, "Face")
        if self.face_index is None and (self.reference is not None or self.producer_id is not None):
            raise ValueError("Un plan de référence n’a pas de producteur de face.")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("Révision de support invalide.")
        if not isinstance(self.frame, WorkPlane):
            raise ValueError("Un repère WorkPlane est requis.")
        if self.face_index is not None and (type(self.face_index) is not int or self.face_index < 1):
            raise ValueError("Indice de face invalide.")


def _topological_input(producer_id, reference, kind):
    from cadastrophe.core.topology_refs import TopologyReference
    if producer_id is not None:
        valid_id(producer_id)
    if reference is not None and (not isinstance(reference, TopologyReference) or reference.kind != kind):
        raise ValueError("Référence de support topologique invalide.")


@dataclass(frozen=True)
class Sketch:
    support: PlaneSupport
    entities: tuple[Entity, ...] = ()
    id: str = field(default_factory=new_id)
    constraints: tuple = ()
    external: tuple[ExternalGeometry, ...] = ()
    patterns: tuple = ()
    # Disposable materialization, never encoded. Carried by replace so a count
    # reduction removes previous occurrences rather than baking stale copies.
    _generated_ids: tuple[str, ...] = field(default=(), kw_only=True, repr=False, compare=False)

    def __post_init__(self):
        valid_id(self.id)
        if not isinstance(self.support, PlaneSupport):
            raise ValueError("Un support de plan est requis.")
        # Copy mutable input; the snapshot cannot be mutated behind undo's back.
        entities = tuple(self.entities)
        if any(type(entity) not in ENTITY_TYPES for entity in entities):
            raise ValueError("Type d’entité non pris en charge.")
        external = tuple(self.external)
        if any(type(e) is not ExternalGeometry for e in external):
            raise ValueError("Référence externe invalide.")
        object.__setattr__(self, "external", external)
        object.__setattr__(self, "patterns", tuple(self.patterns))
        from .patterns import generate
        entities, generated = generate(entities, self.patterns, previous_ids=self._generated_ids)
        object.__setattr__(self, "entities", entities)
        object.__setattr__(self, "_generated_ids", generated)
        ids = [entity.id for entity in self.all_entities]
        ids.extend(p.id for p in self.patterns)
        if len(set(ids)) != len(ids) or self.id in ids:
            raise ValueError("Identifiant d’entité dupliqué ou confondu avec l’esquisse.")
        object.__setattr__(self, "constraints", tuple(self.constraints))
        from .constraints import validate
        validate(self)
        from .dimension_geometry import measure
        object.__setattr__(self, "constraints", tuple(
            replace(c, value=measure(self, c.kind, c.refs)) if c.driven else c for c in self.constraints))

    @property
    def source_entities(self):
        generated = set(self._generated_ids)
        return tuple(e for e in self.entities if e.id not in generated)

    def pattern_for(self, entity_id):
        return next((p for p in self.patterns if entity_id in p.generated_ids), None)

    def independent(self, entities, *, constraints=(), external=()):
        """A local working subset for solvers and native presentations only."""
        return replace(self, entities=tuple(entities), constraints=constraints, external=external,
                       patterns=(), _generated_ids=())

    def require_editable(self, entity_ids):
        entity_ids = set(entity_ids)
        if self.id in entity_ids:
            raise ValueError("L’origine de l’esquisse est une référence fixe et ne peut pas être modifiée.")
        if entity_ids.intersection(self._generated_ids):
            raise ValueError("Occurrence pilotée par un motif : modifiez ses sources ou ses paramètres, ou dissociez le motif.")

    def entity(self, entity_id: str) -> Entity:
        if entity_id == self.id:
            return self.origin
        for entity in self.all_entities:
            if entity.id == entity_id:
                return entity
        raise KeyError(entity_id)

    @property
    def origin(self):
        """Fixed reference derived from the sketch identity, never profile data."""
        return Point(Vec2(0., 0.), id=self.id, construction=True)

    @property
    def all_entities(self):
        return (*self.entities, *(e.geometry for e in self.external))

    @property
    def profile_entities(self):
        """Local contour geometry; construction remains in the constraint model."""
        return tuple(e for e in self.entities if not e.construction)

    def with_added(self, entity: Entity) -> Sketch:
        return replace(self, entities=(*self.entities, entity))

    def with_replaced(self, entity: Entity) -> Sketch:
        self.require_editable((entity.id,))
        previous = self.entity(entity.id)
        if type(previous) is not type(entity):
            raise ValueError("Le remplacement doit conserver le type d’entité.")
        return replace(self, entities=tuple(entity if item.id == entity.id else item for item in self.entities))

    def without(self, entity_id: str) -> Sketch:
        self.require_editable((entity_id,))
        self.entity(entity_id)
        constraints = tuple(c for c in self.constraints if all(r.entity_id != entity_id for r in c.refs))
        used = {r.entity_id for c in constraints for r in c.refs}
        return replace(self, entities=tuple(item for item in self.entities if item.id != entity_id),
                       constraints=constraints, external=tuple(e for e in self.external if e.geometry.id in used))

    def to_world(self, point: Vec2) -> Vec3:
        vector(point)
        return self.support.frame.to_world(point.u, point.v)

    def to_local(self, point: Vec3) -> Vec2:
        u, v, normal = self.support.frame.to_local(point)
        if abs(normal) > 1e-7:
            raise ValueError("Le point n’appartient pas au plan de l’esquisse.")
        return Vec2(u, v)

    def project_ray(self, ray: Ray) -> Vec2 | None:
        """Shared screen-ray → support intersection; parallel/behind returns None."""
        hit = self.support.frame.intersect(ray)
        return None if hit is None else self.to_local(hit)
