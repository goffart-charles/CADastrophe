"""Document state and identity, independent of AIS handles and OCCT imports.

Objects are immutable snapshots. Mutations go through Document so every view
receives the same change. Shape payloads remain real B-Reps, not display objects.
"""
from dataclasses import dataclass, field, fields, replace
from enum import Enum
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

from PySide6.QtCore import QObject, Signal

from .geometry import WorkPlane
from .core.history import DocumentHistory
from .core.document_commands import (CreateObject, InsertObjects, RenameObject, SetVisibility,
                                     SetTransparency, RemoveObjects, ReplaceShape, ReplaceFeature, ClearDocument)
from .core.document_commands import UpdateConstruction
from .core.materials import Material
from .core.document_commands import SetMaterials


class ObjectType(str, Enum):
    BODY = "body"
    REFERENCE_PLANE = "reference_plane"
    FEATURE = "feature"  # Plugin-owned logical data; no required B-Rep.

    @property
    def label(self):
        return {self.BODY: "Corps solide", self.REFERENCE_PLANE: "Plan de référence",
                self.FEATURE: "Fonction de plugin"}[self]


@dataclass(frozen=True)
class SceneObject:
    name: str
    shape: Any
    color: tuple[float, float, float]
    plane: WorkPlane | None = None
    visible: bool = True
    id: str = field(default_factory=lambda: str(uuid4()))
    revision: int = 0
    kind: ObjectType | None = field(default=None, kw_only=True)
    transparency: float | None = field(default=None, kw_only=True)
    expressions: tuple = field(default=(), kw_only=True)
    material: Material | None = field(default=None, kw_only=True)

    def __post_init__(self):
        object.__setattr__(self, "id", str(UUID(self.id)))
        if not self.name.strip():
            raise ValueError("Object name cannot be empty")
        inferred = ObjectType.REFERENCE_PLANE if self.plane is not None else ObjectType.BODY
        kind = inferred if self.kind is None else ObjectType(self.kind)
        if kind is ObjectType.FEATURE:
            if self.plane is not None or self.shape is not None:
                raise ValueError("Un objet logique ne doit pas posséder de B-Rep ni être un plan de référence.")
        elif kind is not inferred:
            raise ValueError("Object type and reference plane disagree")
        if self.revision < 0:
            raise ValueError("Shape revision cannot be negative")
        object.__setattr__(self, "kind", kind)
        if self.material is not None and (kind is not ObjectType.BODY or type(self.material) is not Material):
            raise ValueError('Un matériau physique peut être affecté uniquement à un corps solide.')
        value = (0.82 if self.plane is not None else 0.) if self.transparency is None else self.transparency
        if type(value) not in (int, float) or not isfinite(value) or not 0 <= value <= 1:
            raise ValueError("La transparence doit être un nombre fini entre 0 et 1.")
        object.__setattr__(self, "transparency", float(value))
        from .core.expressions import Expression
        if type(self.expressions) is not tuple or any(not isinstance(e, Expression) for e in self.expressions):
            raise ValueError("Équations de fonction invalides.")


@dataclass(frozen=True)
class SelectionItem:
    object_id: str
    topology: str
    shape: Any
    revision: int
    # shape is a real TopoDS_Shape, or None for a tree-only logical object.
    # This reference is valid for this revision
    # only: a future naming service must resolve it after feature regeneration.


class Document(QObject):
    @property
    def default_configuration(self):
        from .core.construction_state import ConfigurationIdentity
        config = self.configuration_state.configuration(self.configuration_state.default_id)
        return ConfigurationIdentity(config.id, config.name)

    @property
    def configuration_state(self):
        return self._configuration_state

    @property
    def configurations(self):
        return self.configuration_state.configurations

    @property
    def active_configuration(self):
        return self.configuration_state.configuration()

    @property
    def parameter_scope(self):
        return self._parameter_scope

    def set_parameter_scope(self, scope):
        if scope not in ("active", "common"):
            raise ValueError("Portée attendue : configuration active ou valeurs communes.")
        if scope != self._parameter_scope:
            self._parameter_scope = scope
            self.parameter_scope_changed.emit(scope)
            self.metadata_changed.emit()

    object_added = Signal(str)
    object_changed = Signal(str, object)  # id, tuple of changed field names
    object_removed = Signal(str)
    body_result_changed = Signal(str)
    cleared = Signal()
    selection_changed = Signal(object)  # tuple of parent object UUIDs
    modified_changed = Signal(bool)
    metadata_changed = Signal()
    parameter_scope_changed = Signal(str)

    def __init__(self, name="Document", *, document_id=None):
        super().__init__()
        if not name.strip():
            raise ValueError("Document name cannot be empty")
        self._id = str(UUID(document_id)) if document_id is not None else str(uuid4())
        self._name = name.strip()
        self._file_path = None
        self._file_digest = None
        self._objects: dict[str, SceneObject] = {}
        self._objects_view = MappingProxyType(self._objects)
        self._selected_ids: tuple[str, ...] = ()
        self._body_results = {}
        self._body_last_valid_results = {}
        self._body_prefixes = {}
        self._body_prefix_signatures = {}
        self._body_evaluation_cache = {}
        self._variables = ()
        self._variable_source = None
        from .core.configuration_model import initial_state
        self._configuration_state = initial_state(self.id)
        self._parameter_scope = "active"
        self.configuration_editing = False
        self.equation_evaluator = None
        self.history = DocumentHistory(self)
        from .core.history_position import HistoryContext
        self.history_context = HistoryContext(self)
        self._last_modified = False
        self.history.changed.connect(self._history_changed)

    @property
    def is_modified(self):
        return not self.history.is_clean

    @property
    def variables(self):
        return self._variables

    @property
    def variable_source(self):
        return self._variable_source

    def _history_changed(self):
        modified = self.is_modified
        if modified != self._last_modified:
            self._last_modified = modified
            self.modified_changed.emit(modified)

    @property
    def id(self):
        return self._id

    @property
    def name(self):
        return self._name

    @property
    def file_path(self):
        return self._file_path

    @property
    def file_digest(self):
        """Fingerprint of the exact loaded/saved file, not persisted model data."""
        return self._file_digest

    @property
    def display_name(self):
        return self._file_path.name if self._file_path is not None else self.name

    def mark_saved(self, path, *, file_digest=None):
        """Associate a successfully written/read native file with this revision."""
        self.history._check_idle()
        self._file_path = Path(path).resolve()
        self._file_digest = file_digest
        self.history.mark_clean()
        self.metadata_changed.emit()

    def mark_recovered(self):
        """Require Save As; never associate a recovery copy with the user's file."""
        self.history._check_idle()
        self._file_path = None
        self._file_digest = None
        self._name += " (récupéré)"
        self.history.mark_dirty()
        self.metadata_changed.emit()

    @property
    def length_unit(self):
        """Internal canonical length unit; display conversions are a later task."""
        return "mm"

    @property
    def objects(self):
        return self._objects_view

    @property
    def selected_ids(self):
        return self._selected_ids

    def add(self, obj: SceneObject):
        self.history._check_idle(allow_transaction=True)
        if not isinstance(obj, SceneObject):
            raise TypeError("Document objects must be SceneObject records")
        if obj.id in self.objects:
            raise ValueError("Duplicate object id")
        if obj.expressions:
            return self.update_construction((obj,), title=f"Créer {obj.name}")
        from .core.body_history import validate_graph
        from .core.history_position import insertion_candidate
        validate_graph(insertion_candidate(self, {**self.objects, obj.id: obj}))
        self.history.execute(CreateObject(self, obj))

    def update_construction(self, objects=(), *, remove=(), title="Modifier la construction", results=None, prefixes=None, variables=None, variable_source=..., allow_errors=False, scope=None):
        """Validate a detached whole graph before touching the common undo stack.

        Explicit repair may retain native calculation failures with their
        diagnostics; expression and dependency errors always remain strict.
        """
        from .core.body_history import validate_graph
        from .core.feature_objects import BodyObject, ConstructionFeature, SketchSourceObject
        from .core.reference_objects import DatumPlaneObject
        from .core.curve_model import CurveObject
        self.history._check_idle(allow_transaction=True)
        from .core.variable_files import VariableSource
        linked_source = self.variable_source if variable_source is ... else variable_source
        if linked_source is not None and type(linked_source) is not VariableSource:
            raise ValueError('Lien vers le fichier de variables invalide.')
        objects, remove = tuple(objects), set(remove)
        if len({obj.id for obj in objects}) != len(objects):
            raise ValueError("Identifiants dupliqués dans la construction.")
        if any(not isinstance(obj, (BodyObject, ConstructionFeature, DatumPlaneObject, SketchSourceObject, CurveObject)) for obj in objects):
            raise ValueError("Cette commande accepte uniquement corps, esquisses et fonctions de construction.")
        if remove & {obj.id for obj in objects}:
            raise ValueError("Un objet ne peut être supprimé et remplacé simultanément.")
        for key in remove:
            if not isinstance(self.objects[key], (BodyObject, ConstructionFeature, DatumPlaneObject)):
                raise ValueError("Suppression réservée aux objets de construction.")
        candidate = {key: obj for key, obj in self.objects.items() if key not in remove}
        for obj in objects:
            old = self.objects.get(obj.id)
            if old is not None and isinstance(old, (BodyObject, ConstructionFeature)) and type(old) is not type(obj):
                raise ValueError("Le type d'un objet de construction doit être conservé.")
            if isinstance(old, BodyObject) and (old.source_shape is not obj.source_shape or old.source_kind != obj.source_kind):
                raise ValueError("La base statique d'un corps est immuable.")
            if old is not None and not isinstance(old, BodyObject) and type(old) is not SceneObject and type(old) is not type(obj):
                raise ValueError("Un objet d'un autre plugin ne peut pas devenir une fonction.")
            if old is not None and type(old) is SceneObject and (old.kind is not ObjectType.BODY or not isinstance(obj, BodyObject)):
                raise ValueError("Seul un corps statique peut être adopté.")
            if type(old) is SceneObject and (obj.source_shape is not old.shape or obj.source_kind is None):
                raise ValueError("L'adoption doit conserver explicitement la B-Rep de base originale.")
            candidate[obj.id] = obj
        from .core.history_position import insertion_candidate
        candidate = insertion_candidate(self, candidate)
        if self.history_context.position is not None and any(isinstance(o, ConstructionFeature) and o.id not in self.objects for o in objects):
            allow_errors = True  # The tool previews downstream failures before publication.
        from .core.parameters import prepare
        for obj in objects:
            if not isinstance(obj, ConstructionFeature):
                continue
            old = self.objects.get(obj.id)
            selections = tuple((source.sketch_id, contours) for source, contours in obj.profile_sections)
            previous = tuple((source.sketch_id, contours) for source, contours in old.profile_sections) if isinstance(old, ConstructionFeature) else None
            if selections != previous:
                for source, contours in obj.profile_sections:
                    sketch = candidate.get(source.sketch_id)
                    if isinstance(sketch, SketchSourceObject) and set(contours) - set(sketch.profile_source_ids):
                        raise ValueError("La sélection de contours référence une entité source absente de l'esquisse.")
        new_variables = self.variables if variables is None else tuple(variables)
        from .core.configurations import stage_edits
        candidate, new_variables, configuration_state = stage_edits(self, candidate, new_variables, scope or self.parameter_scope)
        equation_active = bool(variables is not None or self.variables or any(o.expressions for o in candidate.values()))
        if equation_active:
            candidate, new_variables, _ = prepare(candidate, new_variables)
        validate_graph(candidate)
        changed = {key for key, obj in candidate.items() if obj != self.objects.get(key)} | remove
        changed.update(key for key, obj in candidate.items()
                       if getattr(obj, "reference_error_message", "") !=
                          getattr(self.objects.get(key), "reference_error_message", ""))
        geometry_changed = {key for key in changed if key in remove or key not in self.objects
                            or replace(candidate[key], expressions=self.objects[key].expressions) != self.objects[key]}
        configured_owners = {s.target.split(":", 1)[0] for s in configuration_state.common_values}
        datum_changed = any(isinstance(candidate.get(key), DatumPlaneObject)
                            and (len(self.configurations) > 1 or key in configured_owners) for key in geometry_changed)
        if (equation_active or datum_changed) and geometry_changed and self.equation_evaluator is not None:
            from .core.body_history import affected_bodies, preview_construction
            resolved = {}
            results, prefixes = preview_construction(self, candidate, affected_bodies(candidate, geometry_changed),
                                                    self.equation_evaluator, strict=not allow_errors,
                                                    resolved_objects=resolved, materialized=True)
            candidate.update(resolved)
            changed.update(resolved)
        from .core.body_history import result_receipts
        receipts = result_receipts(candidate, results or {}, prefixes)
        if changed or new_variables != self.variables or linked_source != self.variable_source or configuration_state != self.configuration_state:
            self.history.execute(UpdateConstruction(self, candidate.values(), changed, title, receipts,
                                                    variables=new_variables, variable_source=linked_source,
                                                    configuration_state=configuration_state))

    def set_variables(self, variables):
        self.update_construction(variables=variables, title="Modifier les variables et équations")

    def set_equation(self, object_id, key, text, *, scale=1.):
        from .core.expressions import Expression
        from .core.parameters import set_expression
        obj = set_expression(self.objects[object_id], key,
                             Expression(key, text, scale=scale) if text else None)
        self.update_construction((obj,), title="Modifier une équation")

    def insert_graph(self, objects, *, results=None, prefixes=None, selection=(), title="Coller la construction"):
        """Insert an entirely new, prevalidated graph as one ordinary undo step."""
        from .core.body_history import validate_graph, result_receipts
        self.history._check_idle(allow_transaction=True)
        objects = tuple(objects)
        ids = {o.id for o in objects}
        if len(ids) != len(objects) or ids.intersection(self.objects) or any(not isinstance(o, SceneObject) for o in objects):
            raise ValueError("Le graphe à insérer exige des objets et UUID nouveaux distincts.")
        candidate = {**self.objects, **{o.id: o for o in objects}}
        if self.variables or any(o.expressions for o in candidate.values()):
            from .core.parameters import prepare
            candidate, variables, _ = prepare(candidate, self.variables)
        else:
            variables = self.variables
        validate_graph(candidate)
        receipts = result_receipts(candidate, results or {}, prefixes)
        if objects:
            changed = ids | {key for key, obj in candidate.items() if key in self.objects and obj != self.objects[key]}
            self.history.execute(UpdateConstruction(self, candidate.values(), changed, title, receipts,
                                                    selection=selection, variables=variables))

    def remove_graph(self, object_ids, *, title="Couper la construction"):
        from .core.body_history import validate_graph, result_receipts
        from .core.reference_objects import DatumPlaneObject
        self.history._check_idle(allow_transaction=True)
        ids = set(object_ids)
        if not ids <= self.objects.keys() or any(self.objects[k].kind is ObjectType.REFERENCE_PLANE
                and not isinstance(self.objects[k], DatumPlaneObject) for k in ids):
            raise ValueError("Sélection à retirer invalide ou contenant un plan de référence global.")
        candidate = {k: o for k, o in self.objects.items() if k not in ids}
        if self.variables or any(o.expressions for o in self.objects.values()):
            from .core.parameters import prepare
            prepare(candidate, self.variables)
        validate_graph(candidate)
        if ids:
            self.history.execute(UpdateConstruction(self, candidate.values(), ids, title,
                                 result_receipts(self.objects, self._body_results, self._body_prefixes),
                                 selection=tuple(k for k in self.selected_ids if k not in ids)))

    def _apply_construction(self, snapshots, changed_ids, receipts=None):
        from .core.body_history import invalidate, publish_result, result_signature
        from .core.feature_objects import BodyObject
        previous = dict(self.objects)
        current = {}
        for obj in snapshots:
            old = previous.get(obj.id)
            if obj.id not in changed_ids and old is not None:
                obj = old
            elif old is not None:
                adoption = (old.kind is ObjectType.BODY and obj.kind is ObjectType.BODY
                            and type(old) is not type(obj) and old.shape is obj.shape
                            and not getattr(old, "features", ()) and not getattr(obj, "features", ()))
                obj = replace(obj, revision=old.revision + (0 if adoption else 1))
            if isinstance(obj, BodyObject) and obj.features and obj.id in changed_ids:
                obj = replace(obj, shape=None)
            current[obj.id] = obj
        # Every observer sees a complete valid graph, including during undo.
        self._objects.clear()
        self._objects.update(current)
        self.select_objects(key for key in self.selected_ids if key in current)
        geometry_changed = {key for key in changed_ids if key not in current or key not in previous
                            or replace(current[key], expressions=previous[key].expressions,
                                       revision=previous[key].revision) != previous[key]}
        shape_changes, affected = invalidate(self, geometry_changed, notify=False)
        for key, (signature, result, prefixes) in (receipts or {}).items():
            if key in self.objects and signature == result_signature(self.objects, key):
                publish_result(self, key, result, notify=False, prefixes=prefixes)
                shape_changes.append(key)
                affected.add(key)
        for key in previous.keys() - current.keys():
            self._body_results.pop(key, None)
            self._body_last_valid_results.pop(key, None)
            self._body_prefixes.pop(key, None)
            self.object_removed.emit(key)
        for key in current:
            if key not in previous:
                self.object_added.emit(key)
            elif key in changed_ids or key in shape_changes:
                self.object_changed.emit(key, tuple(item.name for item in fields(current[key])))
        for key in affected:
            self.body_result_changed.emit(key)

    def _insert(self, obj, position):
        from .core.feature_objects import BodyObject
        if isinstance(obj, BodyObject) and not obj.features and obj.source_shape is not None:
            obj = replace(obj, shape=obj.source_shape)
        items = list(self._objects.items())
        items.insert(position, (obj.id, obj))
        self._objects.clear()
        self._objects.update(items)
        from .core.body_history import invalidate
        invalidate(self, (obj.id,))
        self.object_added.emit(obj.id)

    def insert_objects(self, objects, *, title="Coller les objets"):
        """Validate a complete body batch before entering native undo redo."""
        self.history._check_idle(allow_transaction=True)
        objects = tuple(objects)
        ids = set(self.objects)
        for obj in objects:
            if not isinstance(obj, SceneObject) or obj.kind is not ObjectType.BODY:
                raise ValueError("Seuls les corps peuvent être insérés en lot.")
            if obj.id in ids:
                raise ValueError("Identifiant d’objet dupliqué.")
            ids.add(obj.id)
        from .core.body_history import validate_graph
        validate_graph({**self.objects, **{obj.id: obj for obj in objects}})
        if objects:
            self.history.execute(InsertObjects(self, objects, title))

    def rename(self, object_id, name):
        self.history._check_idle(allow_transaction=True)
        name = name.strip()
        if not name:
            raise ValueError("Object name cannot be empty")
        if self._objects[object_id].name != name:
            self.history.execute(RenameObject(self, object_id, name))

    def set_visible(self, object_id, visible):
        self.set_visibilities({object_id: visible})

    def set_visibilities(self, changes, *, title="Modifier la visibilité"):
        self.history._check_idle(allow_transaction=True)
        changes = dict(changes)
        for key, value in changes.items():
            if key not in self._objects:
                raise KeyError(key)
            if not isinstance(value, bool):
                raise TypeError("Visibility must be a boolean")
        changes = {key: value for key, value in changes.items() if self._objects[key].visible != value}
        if changes:
            self.history.execute(SetVisibility(self, changes, title))

    def isolate(self, object_ids):
        """Show the requested bodies only; undo restores every previous visibility."""
        self.history._check_idle(allow_transaction=True)
        ids = set(object_ids)
        if not ids or any(self.objects[key].kind is not ObjectType.BODY for key in ids):
            raise ValueError("Sélectionnez au moins un corps à isoler.")
        self.set_visibilities({key: key in ids for key in self.objects}, title="Isoler les corps sélectionnés")

    def set_transparencies(self, changes):
        self.history._check_idle(allow_transaction=True)
        changes = dict(changes)
        for key, value in changes.items():
            if self.objects[key].kind is not ObjectType.BODY:
                raise ValueError("La transparence modifiable est réservée aux corps.")
            if type(value) not in (int, float) or not isfinite(value) or not 0 <= value <= 1:
                raise ValueError("La transparence doit être un nombre fini entre 0 et 1.")
        changes = {key: float(value) for key, value in changes.items() if self.objects[key].transparency != value}
        if changes:
            self.history.execute(SetTransparency(self, changes))

    def set_materials(self, changes):
        self.history._check_idle(allow_transaction=True)
        changes = dict(changes)
        for key, material in changes.items():
            if self.objects[key].kind is not ObjectType.BODY:
                raise ValueError('Sélectionnez des corps solides pour affecter un matériau.')
            if material is not None and type(material) is not Material:
                raise ValueError('Matériau physique invalide.')
        changes = {key: value for key, value in changes.items() if self.objects[key].material != value}
        if changes:
            self.history.execute(SetMaterials(self, changes))

    def replace_shape(self, object_id, shape):
        """Invalidate transient subshape references when replacing a B-Rep."""
        self.history._check_idle(allow_transaction=True)
        from .core.feature_objects import BodyObject
        if isinstance(self.objects[object_id], BodyObject):
            raise ValueError("La forme d'un corps paramétrique est un résultat dérivé, pas une source modifiable.")
        if self._objects[object_id].kind is ObjectType.FEATURE:
            raise ValueError("Un objet logique se modifie par replace_feature, sans B-Rep.")
        self.history.execute(ReplaceShape(self, object_id, shape))

    def replace_feature(self, obj: SceneObject, *, title="Modifier la fonction"):
        """Replace validated plugin data in the common undo stack, not its AIS cache."""
        self.history._check_idle(allow_transaction=True)
        if not isinstance(obj, SceneObject):
            raise TypeError("Un instantané documentaire est requis.")
        previous = self._objects[obj.id]
        from .core.feature_objects import ConstructionFeature, SketchSourceObject
        from .core.reference_objects import DatumPlaneObject
        from .core.body_history import validate_graph
        if isinstance(obj, (ConstructionFeature, SketchSourceObject, DatumPlaneObject)):
            if isinstance(obj, SketchSourceObject) and hasattr(obj, "equation_dimensions"):
                keys = {d.key for d in obj.equation_dimensions()}
                obj = replace(obj, expressions=tuple(e for e in obj.expressions if e.target in keys))
            return self.update_construction((obj,), title=title)
        datum = isinstance(obj, DatumPlaneObject) and isinstance(previous, DatumPlaneObject)
        if (not datum and (obj.kind is not ObjectType.FEATURE or previous.kind is not ObjectType.FEATURE)
                or type(previous) is not type(obj)):
            raise ValueError("Le remplacement exige le même type de fonction et le même UUID.")
        if obj != previous:
            validate_graph({**self.objects, obj.id: obj})
            self.history.execute(ReplaceFeature(self, previous, obj, title))

    def _replace_feature(self, snapshot):
        previous = self._objects[snapshot.id]
        current = replace(snapshot, revision=previous.revision + 1)
        changed = tuple(item.name for item in fields(current)
                        if getattr(previous, item.name) != getattr(current, item.name))
        self.select_objects(key for key in self._selected_ids if key != snapshot.id)
        self._objects[snapshot.id] = current
        from .core.body_history import invalidate
        invalidate(self, (snapshot.id,))
        self.object_changed.emit(snapshot.id, changed)

    def _replace_shape(self, object_id, shape):
        obj = self._objects[object_id]
        self.select_objects(key for key in self._selected_ids if key != object_id)
        self._change(object_id, shape=shape, revision=obj.revision + 1)

    def _change(self, object_id, **values):
        obj = self._objects[object_id]
        fields = tuple(key for key, value in values.items()
                       if key == "shape" or getattr(obj, key) != value)
        if not fields:
            return
        self._objects[object_id] = replace(obj, **values)
        if "shape" in values:
            from .core.body_history import invalidate
            invalidate(self, (object_id,))
        if values.get("visible") is False:
            self.select_objects(key for key in self._selected_ids if key != object_id)
        self.object_changed.emit(object_id, fields)

    def select_objects(self, object_ids):
        ids = tuple(dict.fromkeys(object_ids))
        for object_id in ids:
            if object_id not in self._objects:
                raise KeyError(object_id)
        if ids != self._selected_ids:
            self._selected_ids = ids
            self.selection_changed.emit(ids)

    def remove(self, object_id: str):
        obj = self._objects[object_id]
        self.remove_objects([object_id])
        return obj

    def remove_objects(self, object_ids):
        self.history._check_idle(allow_transaction=True)
        ids = set(object_ids)
        from .core.feature_objects import BodyObject, ConstructionFeature
        from .core.body_history import validate_graph
        from .core.reference_objects import DatumPlaneObject
        from .core.curve_model import CurveObject
        if any(isinstance(self.objects[key], (ConstructionFeature, DatumPlaneObject, CurveObject)) for key in ids):
            from .core.body_history import without_features
            if any(self.objects[key].kind is ObjectType.REFERENCE_PLANE and not isinstance(self.objects[key], DatumPlaneObject) for key in ids):
                raise ValueError("Global reference planes are protected")
            candidate = without_features(self.objects, ids)
            if self.variables or any(o.expressions for o in self.objects.values()):
                from .core.parameters import prepare
                prepare(candidate, self.variables)
            changed = (set(self.objects)-set(candidate)) | {
                key for key, obj in candidate.items() if obj != self.objects[key]}
            self.history.execute(UpdateConstruction(self, candidate.values(), changed, "Supprimer les fonctions"))
            return
        for key in tuple(ids):
            if isinstance(self.objects[key], BodyObject):
                ids.update(self.objects[key].features)
        validate_graph({key: obj for key, obj in self.objects.items() if key not in ids})
        if self.variables or any(o.expressions for o in self.objects.values()):
            from .core.parameters import prepare
            prepare({key: obj for key, obj in self.objects.items() if key not in ids}, self.variables)
        for key in ids:
            if self._objects[key].kind is ObjectType.REFERENCE_PLANE:
                raise ValueError("Global reference planes are protected")
        if ids:
            if self.configuration_state.common_values or any(isinstance(self.objects[key], (BodyObject, ConstructionFeature)) for key in ids):
                self.history.execute(UpdateConstruction(self, (obj for key, obj in self.objects.items() if key not in ids), ids, "Supprimer la construction"))
            else:
                self.history.execute(RemoveObjects(self, ids))

    def _remove(self, object_id):
        del self._objects[object_id]
        self._body_results.pop(object_id, None)
        self._body_last_valid_results.pop(object_id, None)
        self._body_prefixes.pop(object_id, None)
        from .core.body_history import invalidate
        invalidate(self, (object_id,))
        self.select_objects(key for key in self._selected_ids if key != object_id)
        self.object_removed.emit(object_id)

    def clear(self):
        self.history._check_idle(allow_transaction=True)
        if self._objects or self.variables or self.variable_source is not None or len(self.configurations) > 1:
            self.history.execute(ClearDocument(self))

    def _clear(self):
        if not self._objects:
            return
        self._objects.clear()
        self._body_results.clear()
        self._body_last_valid_results.clear()
        self._body_prefixes.clear()
        self._body_prefix_signatures.clear()
        self._body_evaluation_cache.clear()
        self.select_objects(())
        self.cleared.emit()
