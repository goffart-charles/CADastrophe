"""Session-only historical context over the ordinary construction graph."""
from dataclasses import dataclass, replace
from weakref import ref

from PySide6.QtCore import QObject, Signal

from .feature_model import BodyStateRef, ResultStatus
from .feature_objects import BodyObject, ConstructionFeature, SketchSourceObject, ShellFeature, DraftFeature
from .reference_objects import DatumPlaneObject


@dataclass(frozen=True)
class HistoryPosition:
    feature_id: str
    after: bool = True

    def __post_init__(self):
        from .feature_model import uuid
        uuid(self.feature_id)
        if type(self.after) is not bool:
            raise ValueError("Le côté du repère doit être un booléen.")


class HistoryContext(QObject):
    changed = Signal()

    def __init__(self, document):
        super().__init__(document)
        self._document = ref(document)
        self.position = None
        self.evaluator = None
        self._display_key = self._display_value = None
        document.object_removed.connect(self._removed)
        document.body_result_changed.connect(self._updated)
        document.object_changed.connect(self._updated)
        document.cleared.connect(self._updated)

    @property
    def document(self):
        return self._document()

    def _removed(self, key):
        if self.position is not None and self.position.feature_id == key:
            self.position = None
            self.changed.emit()

    def _updated(self, *_):
        if self.position is not None:
            if self.position.feature_id not in self.document.objects:
                self.position = None
            self.changed.emit()

    def set_position(self, feature_id=None, *, after=True):
        if self.document.history.blocked:
            raise ValueError("Terminez l’outil en cours avant de déplacer le repère d’historique.")
        position = HistoryPosition(feature_id, after) if feature_id is not None else None
        if position is not None and not isinstance(self.document.objects.get(feature_id), ConstructionFeature):
            raise ValueError("Choisissez une fonction de construction.")
        if position == self.position:
            return
        previous = self.position
        self.position = position
        try:
            for state in self.display()[0].values():
                if state is not None:
                    self.ensure_prefix(state)
        except Exception:
            self.position = previous
            raise
        self.changed.emit()

    def display(self):
        """Return exact available prefixes and unavailable object identities."""
        if self.position is None:
            return {}, frozenset()
        from .body_history import validate_graph
        from .reference_graph import reference_inputs
        objects = self.document.objects
        cache_key = (self.position, tuple((key, id(obj)) for key, obj in objects.items()))
        if cache_key == self._display_key:
            return self._display_value
        anchor = objects.get(self.position.feature_id)
        if not isinstance(anchor, ConstructionFeature):
            return {}, frozenset()
        body = objects[anchor.body_id]
        cutoff = body.features.index(anchor.id) + int(self.position.after)
        unavailable = set(body.features[cutoff:])
        edges = validate_graph(objects)
        while True:
            before = set(unavailable)
            unavailable.update(key for key, deps in edges.items() if deps & unavailable)
            for obj in objects.values():
                if isinstance(obj, SketchSourceObject):
                    states, _, ids = reference_inputs(objects, sketch_id=obj.id)
                elif isinstance(obj, DatumPlaneObject):
                    states, _, ids = reference_inputs(objects, obj.geometry_references)
                else:
                    continue
                if ids & unavailable or any(ref.producer_id in unavailable for ref in states):
                    unavailable.add(obj.id)
                elif isinstance(obj, SketchSourceObject) and not obj.producer_refs and body.id in obj.body_dependencies and cutoff < len(body.features):
                    unavailable.add(obj.id)  # Legacy support does not prove an earlier producer.
            # Derived planes/sketches can in turn feed other bodies.
            from .reference_graph import feature_inputs
            unavailable.update(obj.id for obj in objects.values() if isinstance(obj, ConstructionFeature)
                               and feature_inputs(objects, obj)[2] & unavailable)
            if before == unavailable:
                break
        states = {}
        for obj in objects.values():
            if not isinstance(obj, BodyObject):
                continue
            index = next((i for i, key in enumerate(obj.features) if key in unavailable), None)
            if obj.id == body.id:
                index = cutoff
            if index is not None and index < len(obj.features):
                states[obj.id] = (BodyStateRef(obj.id, obj.features[index-1]) if index else
                                  BodyStateRef(obj.id) if obj.source_shape is not None else None)
                if states[obj.id] is None:
                    unavailable.add(obj.id)
        self._display_key, self._display_value = cache_key, (states, frozenset(unavailable))
        return self._display_value

    def ensure_prefix(self, ref):
        from .body_history import result_at, preview_construction
        state = result_at(self.document, ref)
        if state.status is ResultStatus.DIRTY and self.evaluator is not None and ref.producer_id is not None:
            _, prefixes = preview_construction(self.document, dict(self.document.objects), (ref.body_id,),
                                               self.evaluator, stop_at=ref.producer_id, strict=False)
            # Cache only: final body/result, recipes and history remain untouched.
            for key, values in prefixes.items():
                self.document._body_prefixes.setdefault(key, {}).update(values)
            state = result_at(self.document, ref)
        return state


def current_state_ref(document, body_id):
    context = getattr(document, "history_context", None)
    if context is not None and context.position is not None:
        states, unavailable = context.display()
        if body_id in unavailable:
            raise ValueError("Ce corps n’existe pas à cette étape de construction.")
        if body_id in states:
            return states[body_id]
    body = document.objects[body_id]
    return BodyStateRef(body_id, body.features[-1] if getattr(body, "features", ()) else None)


def context_object(document, obj):
    """Geometry for tools; persistent recipes and feature order stay complete."""
    from ..document import ObjectType
    context = getattr(document, "history_context", None)
    if context is None or context.position is None:
        return obj
    states, unavailable = context.display()
    if obj.id in unavailable:
        return replace(obj, visible=False, shape=None) if obj.kind is not ObjectType.FEATURE else replace(obj, visible=False)
    if obj.id in states:
        state = context.ensure_prefix(states[obj.id])
        return replace(obj, shape=state.shape if state.status is ResultStatus.READY else None)
    return obj


def insertion_candidate(document, candidate):
    """Splice new functions, rebinding only explicit structural predecessors."""
    context = getattr(document, "history_context", None)
    if context is None or context.position is None:
        return candidate
    original = document.objects
    anchor = original.get(context.position.feature_id)
    if not isinstance(anchor, ConstructionFeature):
        raise ValueError("Le repère d’insertion a disparu.")
    _, unavailable = context.display()
    candidate = dict(candidate)
    for obj in tuple(candidate.values()):
        if not isinstance(obj, BodyObject) or obj.id not in original:
            continue
        old = original[obj.id]
        added = tuple(key for key in obj.features if key not in getattr(old, "features", ()))
        if not added:
            continue
        if obj.id != anchor.body_id:
            raise ValueError("Insérez dans le corps du repère ou revenez à la fin de l’historique.")
        if tuple(key for key in obj.features if key in old.features) != old.features:
            raise ValueError("Une insertion doit conserver l’ordre et toutes les fonctions existantes.")
        index = old.features.index(anchor.id) + int(context.position.after)
        if index == 0 and old.source_shape is None:
            raise ValueError("Une fonction additive exige une base antérieure au repère.")
        order = (*old.features[:index], *added, *old.features[index:])
        candidate[obj.id] = replace(obj, features=order)
        previous = None
        for key in order:
            feature = candidate.get(key)
            if not isinstance(feature, ConstructionFeature):
                raise ValueError("Fonction à insérer absente de la construction.")
            if isinstance(feature, (ShellFeature, DraftFeature)):
                candidate[key] = replace(feature, input_state=BodyStateRef(obj.id, previous))
            previous = key
    from .reference_graph import feature_inputs, reference_inputs
    for key, obj in candidate.items():
        if key in original:
            continue
        if isinstance(obj, ConstructionFeature):
            refs, _, ids = feature_inputs(candidate, obj)
        elif isinstance(obj, DatumPlaneObject):
            refs, _, ids = reference_inputs(candidate, obj.geometry_references)
        elif isinstance(obj, SketchSourceObject):
            refs, _, ids = reference_inputs(candidate, sketch_id=key)
        else:
            continue
        if ids & unavailable or any(ref.producer_id in unavailable for ref in refs):
            raise ValueError("Cette référence appartient à une étape future de la construction.")
    return candidate
