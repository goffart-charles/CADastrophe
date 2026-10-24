"""Associative sketch recipes and deterministic, disposable occurrences.

Occurrence zero is the source. Local UUIDs are scoped by the owning sketch;
neither coordinates nor collection order participate in their identity.
"""
from dataclasses import dataclass, field, replace
from math import cos, hypot, pi, sin
from uuid import UUID, uuid5

from .model import Vec2, finite, new_id, valid_id
from .limits import MAX_ENTITIES, MAX_OCCURRENCES, MAX_TOTAL_VARIABLES


@dataclass(frozen=True)
class SketchPattern:
    entity_ids: tuple[str, ...]
    mode: str = "linear_pattern"
    count: int = 3
    du: float = 20.
    dv: float = 0.
    center: Vec2 = Vec2(0., 0.)
    angle: float = 2*pi
    id: str = field(default_factory=new_id)

    def __post_init__(self):
        valid_id(self.id)
        ids = tuple(self.entity_ids)
        if not ids or len(ids) > MAX_ENTITIES or len(set(ids)) != len(ids):
            raise ValueError("Le motif exige des sources distinctes, dans la limite de 512 entités.")
        for key in ids:
            valid_id(key)
        object.__setattr__(self, "entity_ids", ids)
        if self.mode not in ("linear_pattern", "circular_pattern"):
            raise ValueError("Type de répétition d’esquisse inconnu.")
        if type(self.count) is not int or not 2 <= self.count <= MAX_OCCURRENCES:
            raise ValueError("Choisissez de 2 à 200 occurrences, source comprise.")
        for value in (self.du, self.dv, self.angle):
            finite(value)
        if type(self.center) is not Vec2:
            raise ValueError("Le centre du motif doit être un point local.")
        if self.mode == "linear_pattern" and hypot(self.du, self.dv) < 1e-9:
            raise ValueError("Le pas de répétition doit être non nul.")
        if self.mode == "circular_pattern" and not 1e-9 < abs(self.angle) <= 2*pi+1e-9:
            raise ValueError("L’angle total doit être non nul et inférieur ou égal à 360°.")

    def occurrence_id(self, source_id, index):
        return str(uuid5(UUID(self.id), f"entity:{source_id}:{index}"))

    @property
    def generated_ids(self):
        return tuple(self.occurrence_id(key, i) for i in range(1, self.count) for key in self.entity_ids)


def generate(entities, patterns, *, previous_ids=()):
    """Regenerate the entire bounded lot before publishing any of it.

    Previous derived IDs only remove the disposable materialization carried by
    dataclasses.replace. Codecs write source_entities and recipes exclusively.
    """
    from .reshape import transformed
    previous = set(previous_ids)
    sources = tuple(e for e in entities if e.id not in previous)
    if not patterns:
        return sources, ()
    if any(type(p) is not SketchPattern for p in patterns):
        raise ValueError("Recette de répétition d’esquisse invalide.")
    if len({p.id for p in patterns}) != len(patterns):
        raise ValueError("Identifiant de motif dupliqué.")
    if len(sources)+sum(len(p.entity_ids)*(p.count-1) for p in patterns) > MAX_ENTITIES:
        raise ValueError("Le motif dépasserait la limite de 512 entités d’esquisse.")
    by_id = {e.id: e for e in sources}
    from .model import Point, Segment, Spline, Arc, Ellipse
    def cost(entity):
        return 2*len(entity.points) if type(entity) is Spline else (2 if type(entity) is Point else
            4 if type(entity) is Segment else 5 if type(entity) in (Arc, Ellipse) else 3)
    if (sum(cost(e) for e in sources) + sum((p.count-1)*sum(cost(by_id[key]) for key in p.entity_ids if key in by_id)
                                          for p in patterns)) > MAX_TOTAL_VARIABLES:
        raise ValueError(f"Le motif dépasserait le budget de complexité de l’esquisse ({MAX_TOTAL_VARIABLES} paramètres).")
    generated = {key for p in patterns for key in p.generated_ids}
    if generated.intersection(by_id) or len(generated) != sum(len(p.generated_ids) for p in patterns):
        raise ValueError("Identifiant d’occurrence dupliqué ou confondu avec une source.")
    result = list(sources)
    for pattern in patterns:
        for key in pattern.entity_ids:
            if key in generated or key in previous:
                raise ValueError("Les motifs d’esquisse imbriqués ne sont pas pris en charge. Dissociez d’abord le motif source.")
            if key not in by_id:
                raise ValueError(f"Source du motif absente : {key}. Supprimez ou dissociez le motif avant sa source.")
        full = abs(abs(pattern.angle)-2*pi) < 1e-9
        for i in range(1, pattern.count):
            theta = i*pattern.angle/(pattern.count if full else pattern.count-1) if pattern.mode == "circular_pattern" else 0.
            def point(p):
                if pattern.mode == "linear_pattern":
                    return Vec2(p.u+i*pattern.du, p.v+i*pattern.dv)
                x, y = p.u-pattern.center.u, p.v-pattern.center.v
                return Vec2(pattern.center.u+x*cos(theta)-y*sin(theta),
                            pattern.center.v+x*sin(theta)+y*cos(theta))
            for key in pattern.entity_ids:
                source = by_id[key]
                copy = transformed(source, point, theta, identifier=pattern.occurrence_id(key, i))
                if source.text_group is not None:
                    copy = replace(copy, text_group=str(uuid5(UUID(pattern.id), f"text:{source.text_group}:{i}")))
                result.append(copy)
    return tuple(result), tuple(e.id for e in result[len(sources):])


def add_pattern(sketch, selected, mode, **parameters):
    pattern = SketchPattern(tuple(selected), mode, **parameters)
    return replace(sketch, patterns=(*sketch.patterns, pattern))


def detach(sketch, pattern_id):
    """Bake only this motif, keeping every local identity and measurement."""
    pattern = next((p for p in sketch.patterns if p.id == pattern_id), None)
    if pattern is None:
        raise ValueError("Motif d’esquisse absent.")
    keep = set(pattern.generated_ids)
    return replace(sketch, patterns=tuple(p for p in sketch.patterns if p.id != pattern_id),
                   _generated_ids=tuple(key for key in sketch._generated_ids if key not in keep))


def remove(sketch, pattern_id):
    pattern = next((p for p in sketch.patterns if p.id == pattern_id), None)
    if pattern is None:
        raise ValueError("Motif d’esquisse absent.")
    ids = set(pattern.generated_ids)
    return replace(sketch, patterns=tuple(p for p in sketch.patterns if p.id != pattern_id),
                   constraints=tuple(c for c in sketch.constraints if all(r.entity_id not in ids for r in c.refs)))
