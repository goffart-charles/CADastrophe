"""Persistent, immutable geometric constraints; no GUI or native geometry."""
from dataclasses import dataclass, field
from math import pi

from .model import Arc, Circle, Point, Segment, Vec2, Ellipse, finite, new_id, valid_id

LABELS = {"coincident": "Coïncidence", "horizontal": "Horizontal", "vertical": "Vertical",
          "length": "Longueur", "distance": "Distance entre points", "radius": "Rayon",
          "diameter": "Diamètre", "fixed": "Fixer un point", "parallel": "Parallèle",
          "perpendicular": "Perpendiculaire", "horizontal_distance": "Distance horizontale",
          "vertical_distance": "Distance verticale", "point_on_line": "Point sur une droite",
          "line_distance": "Distance à une droite", "angle": "Angle"}
DIMENSIONS = {"length", "distance", "radius", "diameter", "horizontal_distance", "vertical_distance", "line_distance", "angle"}
POINT_KINDS = {"coincident", "distance", "fixed", "horizontal_distance", "vertical_distance"}
PAIR_KINDS = {"coincident", "distance", "parallel", "perpendicular", "horizontal_distance", "vertical_distance",
              "point_on_line", "line_distance", "angle"}


def fixed_point_aliases(constraints):
    """Logical point identity from persistent coincidence, never proximity."""
    parent = {}
    def root(ref):
        parent.setdefault(ref, ref)
        while parent[ref] != ref:
            parent[ref] = parent[parent[ref]]
            ref = parent[ref]
        return ref
    for c in constraints:
        if c.kind == "coincident":
            a, b = (root(r) for r in c.refs)
            if a != b:
                first, second = sorted((a, b), key=lambda r: (r.entity_id, r.part))
                parent[second] = first
    return {ref: root(ref) for ref in parent}


def relation_key(constraint, fixed_points=None):
    """Identity of a driving relation, independent of value, label and UUID."""
    kind, refs = constraint.kind, constraint.refs
    if kind == "fixed" and fixed_points:
        refs = tuple(fixed_points.get(ref, ref) for ref in refs)
    if kind in {"coincident", "distance", "parallel", "perpendicular", "horizontal_distance", "vertical_distance", "angle"}:
        refs = tuple(sorted(refs, key=lambda r: (r.entity_id, r.part)))
    if kind in {"radius", "diameter"}:
        kind = "circle_size"
    return kind, refs


def reject_new_duplicates(previous, current):
    """Keep legacy redundant sketches readable; refuse adding another relation.

    The solver still handles redundancy. Editing an existing constraint by its
    UUID remains valid, including changing its value.
    """
    aliases = fixed_point_aliases(current)
    old = {c.id: relation_key(c, aliases) for c in previous}
    seen = {}
    for c in current:
        key = relation_key(c, aliases)
        seen.setdefault(key, []).append(c)
    for key, group in seen.items():
        if len(group) > 1 and any(old.get(c.id) != key for c in group):
            raise ValueError("Échec : cette contrainte existe déjà. Modifiez la relation existante.")


@dataclass(frozen=True)
class Reference:
    entity_id: str
    part: str = "entity"

    def __post_init__(self):
        valid_id(self.entity_id)
        if self.part not in ("entity", "position", "start", "end", "center"):
            raise ValueError("Référence de point inconnue.")


@dataclass(frozen=True)
class Constraint:
    kind: str
    refs: tuple[Reference, ...]
    value: float | Vec2 | None = None
    id: str = field(default_factory=new_id)
    label_position: Vec2 | None = None
    driven: bool = False

    def __post_init__(self):
        valid_id(self.id)
        if self.label_position is not None and type(self.label_position) is not Vec2:
            raise ValueError("Position de cote invalide.")
        if type(self.kind) is not str or self.kind not in LABELS:
            raise ValueError("Type de contrainte inconnu.")
        if type(self.driven) is not bool or self.driven and self.kind not in DIMENSIONS:
            raise ValueError("Seule une cotation peut être une mesure de référence.")
        refs = tuple(self.refs)
        if len(refs) != (2 if self.kind in PAIR_KINDS else 1) or any(type(r) is not Reference for r in refs):
            raise ValueError("Références de contrainte invalides.")
        if len(set(refs)) != len(refs):
            raise ValueError("Une contrainte ne peut pas référencer deux fois le même élément.")
        object.__setattr__(self, "refs", refs)
        if self.kind in DIMENSIONS:
            finite(self.value)
            if self.value <= 0:
                raise ValueError("La cote doit être strictement positive.")
            if self.kind == "angle" and self.value >= pi:
                raise ValueError("L’angle doit être compris strictement entre 0° et 180°.")
        elif self.kind == "fixed":
            if type(self.value) is not Vec2:
                raise ValueError("Une position de fixation est requise.")
        elif self.value is not None:
            raise ValueError("Cette relation ne possède pas de valeur.")


def point(entity, part):
    if type(entity) is Ellipse and part == "center":
        return entity.center
    if type(entity) is Point and part == "position":
        return entity.position
    if type(entity) is Segment and part in ("start", "end"):
        return getattr(entity, part)
    if isinstance(entity, Circle):
        if part == "center":
            return entity.center
        if type(entity) is Arc and part in ("start", "end"):
            return entity.point_at(0 if part == "start" else 1)
    raise ValueError("La référence ne désigne pas un point de cette entité.")


def validate(sketch):
    ids = {entity.id for entity in sketch.all_entities} | {sketch.id} | {p.id for p in sketch.patterns}
    by_id = {entity.id: entity for entity in sketch.all_entities}
    by_id[sketch.id] = sketch.origin
    for constraint in sketch.constraints:
        if type(constraint) is not Constraint or constraint.id in ids:
            raise ValueError("Contrainte invalide ou identifiant dupliqué.")
        ids.add(constraint.id)
        for index, ref in enumerate(constraint.refs):
            entity = by_id.get(ref.entity_id)
            if entity is None:
                raise ValueError("Référence de cotation absente de l’esquisse : une occurrence référencée a peut-être été supprimée par la réduction du motif.")
            if ref.entity_id in sketch._generated_ids and not constraint.driven:
                raise ValueError("Une occurrence de motif accepte uniquement une cote de mesure ; les contraintes pilotantes sont refusées. Modifiez la source ou dissociez le motif.")
            if constraint.kind == "angle":
                if type(entity) is not Segment or ref.part not in {"entity", "start", "end"}:
                    raise ValueError("Un angle exige deux directions de segments.")
            elif constraint.kind in POINT_KINDS or (constraint.kind in {"point_on_line", "line_distance"} and index == 0):
                point(entity, ref.part)
            elif ref.part != "entity":
                raise ValueError("Cette contrainte porte sur une entité entière.")
            elif constraint.kind in {"radius", "diameter"}:
                if not isinstance(entity, Circle):
                    raise ValueError("Cette cote exige un cercle ou un arc.")
            elif type(entity) is not Segment:
                raise ValueError("Cette relation exige un segment.")


def encode(constraint):
    value = constraint.value
    if type(value) is Vec2:
        value = [value.u, value.v]
    return dict(id=constraint.id, kind=constraint.kind, value=value, driven=constraint.driven,
                label_position=None if constraint.label_position is None else [constraint.label_position.u, constraint.label_position.v],
                refs=[dict(entity_id=r.entity_id, part=r.part) for r in constraint.refs])


def decode(data):
    from .entity_codec import _fields, _vec2
    _fields(data, "id kind value refs label_position" + (" driven" if "driven" in data else ""))
    if type(data["refs"]) is not list or len(data["refs"]) not in (1, 2):
        raise ValueError("Liste de références invalide.")
    refs = []
    for ref in data["refs"]:
        _fields(ref, "entity_id part")
        refs.append(Reference(**ref))
    value = _vec2(data["value"]) if data["kind"] == "fixed" else data["value"]
    position = None if data["label_position"] is None else _vec2(data["label_position"])
    return Constraint(data["kind"], tuple(refs), value, id=data["id"], label_position=position, driven=data.get("driven", False))
