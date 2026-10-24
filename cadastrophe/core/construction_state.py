"""Immutable construction contracts; no geometry, UI or parallel model state."""
from dataclasses import dataclass
from uuid import UUID, uuid5

from .feature_model import BodyStateRef, ReferenceSlot, uuid
from .feature_objects import ConstructionFeature, ReplayFeature, BodyObject, EdgeFeature


@dataclass(frozen=True)
class ConfigurationIdentity:
    id: str
    name: str = "Configuration par défaut"

    def __post_init__(self):
        uuid(self.id)
        if type(self.name) is not str or not self.name.strip():
            raise ValueError("Nom de configuration invalide.")


def default_configuration(document_id):
    """Old files derive the same default identity on every reopening.

    Configuration codecs retain this identity when migrating old documents,
    independently of their path or the configuration's displayed name.
    """
    return ConfigurationIdentity(str(uuid5(UUID(uuid(document_id)), "configuration.default")))


@dataclass(frozen=True)
class FeatureDependencies:
    predecessor: BodyStateRef | None
    references: tuple[ReferenceSlot, ...]
    evaluation_states: tuple[BodyStateRef, ...]
    object_ids: frozenset[str]

    def __post_init__(self):
        if self.predecessor is not None and not isinstance(self.predecessor, BodyStateRef):
            raise ValueError("Prédécesseur de construction invalide.")
        if type(self.references) is not tuple or any(not isinstance(r, ReferenceSlot) for r in self.references):
            raise ValueError("Emplacements de références invalides.")
        if type(self.evaluation_states) is not tuple or any(not isinstance(r, BodyStateRef) for r in self.evaluation_states):
            raise ValueError("États requis de construction invalides.")
        if type(self.object_ids) is not frozenset:
            raise ValueError("Les dépendances documentaires doivent être immuables.")
        for key in self.object_ids:
            uuid(key)

    @property
    def explicit_producers(self):
        return frozenset(slot.state.producer_id for slot in self.references
                         if slot.state is not None and slot.state.producer_id is not None)


def feature_dependencies(objects, feature):
    """Separate structural order from semantic references, retaining old inputs.

    evaluation_states includes replay prerequisites needed to calculate effects;
    it must not be interpreted as a list of semantic geometry selections.
    """
    from .reference_graph import feature_inputs
    if not isinstance(feature, ConstructionFeature):
        raise ValueError("Une fonction de construction est requise.")
    body = objects.get(feature.body_id)
    if not isinstance(body, BodyObject) or feature.id not in body.features:
        raise ValueError("Fonction absente de l'historique de son corps.")
    index = body.features.index(feature.id)
    predecessor = (BodyStateRef(body.id, body.features[index-1]) if index else
                   BodyStateRef(body.id) if body.source_kind is not None else None)
    states, _, ids = feature_inputs(objects, feature)
    slots = []
    for i, (source, contours) in enumerate(feature.profile_sections):
        path = f"profiles.{i}"
        slots.append(ReferenceSlot(feature.id, path, source.sketch_id, entity_ids=contours))
        slots.extend(objects[source.sketch_id].reference_slots)
        for j, state in enumerate(source.supports):
            slots.append(ReferenceSlot(feature.id, f"{path}.supports.{j}", state.body_id, state))
    for i, ref in enumerate(getattr(feature, "geometry_references", ())):
        if ref.object_id is None:
            continue
        state = BodyStateRef(ref.object_id, ref.producer_id) if ref.kind in {"face", "edge", "vertex"} else None
        slots.append(ReferenceSlot(feature.id, f"geometry_references.{i}", ref.object_id,
                                   state, ref.topology, () if ref.entity_id is None else (ref.entity_id,)))
    if isinstance(feature, ReplayFeature):
        for i, key in enumerate(feature.parameters.sources):
            slots.append(ReferenceSlot(feature.id, f"parameters.sources.{i}", feature.body_id,
                                       BodyStateRef(feature.body_id, key)))
    if isinstance(feature, EdgeFeature):
        for i in range(len(feature.parameters.edges)):
            topology = feature.parameters.references[i] if feature.parameters.references else None
            slots.append(ReferenceSlot(feature.id, f"parameters.edges.{i}", feature.body_id, predecessor, topology))
    return FeatureDependencies(predecessor, tuple(slots), states, frozenset(ids))


def construction_diagnostics(document, body_id):
    """Current failures, ordered by construction, never the last valid result."""
    from .body_history import result_at
    body = document.objects.get(body_id)
    if not isinstance(body, BodyObject):
        raise ValueError("Un corps de construction est requis.")
    return tuple(d for key in body.features for d in result_at(document, BodyStateRef(body_id, key)).diagnostics)
