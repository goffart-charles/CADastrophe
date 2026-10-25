"""Shared detached previews and atomic publication in the existing tool manager."""
from dataclasses import fields, replace
from uuid import uuid4

from cadastrophe.document import SceneObject
from cadastrophe.core.feature_objects import BodyObject
from cadastrophe.core.body_history import preview_construction_steps, affected_bodies, rollback_hidden_ids
from cadastrophe.core.computation import complete, cooperative_required


def unique_name(document, prefix):
    names = {o.name for o in document.objects.values()}
    index = 1
    while f"{prefix}{index}" in names:
        index += 1
    return f"{prefix}{index}"


def inherited_faces(result, feature_id):
    """Keep the base's appearance using native face provenance, including splits."""
    if result.topology is None:
        return ()
    faces = []
    for label, face in result.topology.faces:
        if not label.startswith(feature_id+":") and not any(face.IsSame(old) for old in faces):
            faces.append(face)
    return tuple(faces)


def adopted(body):
    if type(body) is not SceneObject and not isinstance(body,BodyObject):
        raise ValueError("Ce type de corps appartient à un autre plugin et ne peut pas être adopté.")
    return body if isinstance(body, BodyObject) else BodyObject(**{f.name: getattr(body,f.name) for f in fields(SceneObject)},
                                                              source_shape=body.shape, source_kind="static")


class RecipePreview:
    preview_opaque = True
    preview_added_only = False

    @property
    def cooperative_preview(self):
        return cooperative_required(self.document.objects)

    def preview(self):
        return complete(self.preview_steps())

    def invalidate_preview(self):
        self._key = self._cache_key = None

    def initialize(self, services, extrusions, feature=None):
        self.services, self.extrusions, self.feature = services, extrusions, feature
        self.document = services.active_document
        self.snapshot = dict(self.document.objects)
        self.original_selection = self.document.selected_ids
        self.feature_id = feature.id if feature else str(uuid4())
        self._key = None
        self.preview_parts, self.preview_hidden_ids = None, ()
        self._objects = self._results = self._prefixes = None
        self.surface = self.active_reference = None
        self._pick_error = ""
        self.committed = self._connected = False
        self.rebuild_warning = ""
        self._preview_cache = {}

    def check_snapshot(self):
        if self.services.active_document is not self.document or set(self.document.objects) != set(self.snapshot) or any(self.document.objects[k] is not v for k,v in self.snapshot.items()):
            raise ValueError("Le document source a changé depuis l’ouverture de l’outil.")
        if self._pick_error:
            raise ValueError(self._pick_error)

    def calculate(self, objects, feature):
        return complete(self.calculate_steps(objects, feature))

    def calculate_steps(self, objects, feature):
        candidate = {**self.document.objects, **{o.id:o for o in objects}}
        affected = affected_bodies(candidate, (o.id for o in objects))
        results, prefixes = yield from preview_construction_steps(self.document, candidate, affected,
            self.extrusions.evaluator, strict=False, cache=self._preview_cache)
        from cadastrophe.core.feature_model import BodyStateRef, ResultStatus
        current = prefixes[feature.body_id][BodyStateRef(feature.body_id, feature.id)]
        if current.status is not ResultStatus.READY:
            raise ValueError(current.message)
        self._results, self._prefixes = results, prefixes
        self._objects = tuple(objects)
        self.rebuild_warning = next((r.message for r in self._results.values() if r.shape is None), "")
        self._current = current
        from cadastrophe.core.history_position import insertion_candidate
        candidate = insertion_candidate(self.document, candidate)
        self.preview_hidden_ids = rollback_hidden_ids(candidate, feature) if self.feature or self.document.history_context.position is not None else (feature.body_id,)
        body = candidate[feature.body_id]
        self.preview_parts = ((self._current.shape, body.color, inherited_faces(self._current, feature.id), body.transparency),)
        return SceneObject("Aperçu", self._current.shape, body.color)

    def commit(self, document, preview):
        self.check_snapshot()
        if document is not self.document:
            raise ValueError("Le document actif a changé.")
        from cadastrophe.plugin_api import equation_recipe
        objects = tuple(equation_recipe(self, o) if hasattr(o, "parameters") else o for o in self._objects)
        document.update_construction(objects, title=self.title, results=self._results, prefixes=self._prefixes)
        document.select_objects((self.feature_id,))
        self.committed = True

    def attach(self, surface):
        self.surface = surface
        self.refresh_reference_guides()

    def refresh_reference_guides(self):
        if self.surface is not None:
            self.surface.show_reference_guides(self.active_reference.kind if self.active_reference else None)

    def activate_reference(self, picker):
        if self.active_reference is not None:
            self.active_reference.pick.setChecked(False)
        from .selection_field import SelectionField
        for name in ("source", "region", "body", "sources"):
            field = getattr(self, name, None)
            if isinstance(field, SelectionField):
                field.set_active(False)
        self.active_reference = picker
        picker.pick.setChecked(True)
        self.refresh_reference_guides()
        self._pick_error = ""
        if hasattr(self,"target"):
            self.target = "reference"
        self.changed()

    def pick_reference_at(self, x, y):
        picker = self.active_reference
        if picker is None:
            return False
        try:
            ref = self.surface.pick_reference(x,y,picker.kind)
            if ref is not None:
                picker.set_reference(ref)
                self._pick_error = ""
        except (ValueError, RuntimeError) as exc:
            self._pick_error = str(exc)
        self.changed()
        return True

    def clicked(self,x,y):
        return self.pick_reference_at(x,y)

    def selection_reference(self):
        if self.active_reference is None:
            return False
        from cadastrophe.core.reference_geometry import capture_reference
        try:
            items = self.services.selection_items
            if len(items) == 1:
                self.active_reference.set_reference(capture_reference(self.document, items[0]))
                self._pick_error = ""
        except (ValueError, RuntimeError) as exc:
            self._pick_error = str(exc)
        self.changed()
        return True

    def reference_for_edit(self,reference):
        """Map a picked face/edge to a proven prefix before its consumers.

        A final-body pick must never introduce a forward reference while
        repairing an older feature. Persistent naming must prove the mapping.
        """
        if self.feature is None or reference.kind not in {"face","edge","vertex"}:
            return reference
        body = self.document.objects[reference.object_id]
        if not isinstance(body,BodyObject) or reference.producer_id is None:
            return reference
        from cadastrophe.core.feature_objects import ConstructionFeature
        from cadastrophe.core.reference_objects import DatumPlaneObject
        cutoff = None
        if isinstance(self.feature,ConstructionFeature) and self.feature.body_id == body.id:
            cutoff = body.features.index(self.feature.id)
        elif isinstance(self.feature,DatumPlaneObject):
            from cadastrophe.core.reference_graph import feature_inputs
            cutoff = next((i for i,key in enumerate(body.features) if self.feature.id in feature_inputs(self.document.objects,self.document.objects[key])[2]),None)
        if cutoff is None or body.features.index(reference.producer_id) < cutoff:
            return reference
        if not cutoff and body.source_kind is None:
            raise ValueError("Cette référence n’existe pas avant la fonction à modifier.")
        from cadastrophe.core.feature_model import BodyStateRef
        from cadastrophe.core.reference_geometry import state_for
        from cadastrophe.core.native_naming import resolve
        producer = body.features[cutoff-1] if cutoff else None
        state = state_for(self.document,BodyStateRef(body.id,producer))
        resolve(state.shape,reference.topology,state.topology)
        return replace(reference,producer_id=producer)

    def dispose(self):
        if self._connected:
            self.document.selection_changed.disconnect(self.selection_changed)
        if not self.committed:
            self.document.select_objects(k for k in self.original_selection if k in self.document.objects)
        from .reference_picker import ReferencePicker
        if self.editor is not None:
            for picker in self.editor.findChildren(ReferencePicker):
                picker.dispose()
        self.snapshot = {}
        self._preview_cache.clear()
        self.preview_parts = self._objects = self._results = self._prefixes = None
        self._current = self._key = None
        self._result = self._faces = self._cache_key = None
        self.editor = self.surface = self.active_reference = self.changed = None
