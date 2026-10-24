"""Repair a sketch plane/face through the host's ordinary preview and transaction."""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QLabel, QCheckBox, QComboBox

from cadastrophe.document import SceneObject, ObjectType
from cadastrophe.core.body_history import result_at, preview_construction, affected_bodies
from cadastrophe.core.feature_model import BodyStateRef, ResultStatus
from cadastrophe.core.feature_objects import BodyObject, ConstructionFeature
from cadastrophe.core.reference_geometry import plane_shape, resolve_plane
from .model import PlaneSupport
from .supports import from_face_state
from .support_repair import reassociation_objects


class SupportRepairSession:
    preview_opaque = True

    def __init__(self, services, profiles, sketch):
        self.services, self.profiles, self.sketch = services, profiles, sketch
        self.document = services.active_document
        self.snapshot = dict(self.document.objects)
        self.original_selection = self.document.selected_ids
        self.title = "Réassocier le support · "+sketch.name
        self.support = self.surface = None
        self.committed = False
        self.commit_error = "Choisissez un nouveau plan ou une nouvelle face."
        self.preview_parts, self.preview_hidden_ids = None, ()
        self._connected = False
        self._key = self._objects = self._results = self._prefixes = None
        self.available = {}
        for body in self.document.objects.values():
            if body.kind is not ObjectType.BODY:
                continue
            keys = list(getattr(body, "features", ()))
            cutoff = min((keys.index(f.id) for f in self.document.objects.values()
                          if isinstance(f, ConstructionFeature) and f.body_id == body.id
                          and any(source.sketch_id == sketch.id for source, _ in f.profile_sections)), default=len(keys))
            candidates = [*reversed(keys[:cutoff])]
            if not isinstance(body, BodyObject) or body.source_kind is not None:
                candidates.append(None)
            for producer in candidates:
                ref = BodyStateRef(body.id, producer)
                if result_at(self.document, ref).status is ResultStatus.READY:
                    self.available[ref] = body.name+" · "+(self.document.objects[producer].name if producer else "base statique")

    def create_editor(self, parent, changed):
        self.changed = changed
        self.editor = QWidget(parent)
        layout, form = QVBoxLayout(self.editor), QFormLayout()
        form.addRow("Esquisse", QLabel(self.sketch.name))
        self.producer = QComboBox()
        for ref, label in self.available.items():
            self.producer.addItem(label, ref)
        self.producer.currentIndexChanged.connect(self.source_changed)
        form.addRow("État producteur à afficher", self.producer)
        self.selected = QLabel("Aucun nouveau support")
        self.selected.setWordWrap(True)
        form.addRow("Nouveau support", self.selected)
        self.preserve = QCheckBox("Conserver l’orientation et projeter l’origine sur le support")
        self.preserve.setChecked(True)
        self.preserve.toggled.connect(changed)
        form.addRow(self.preserve)
        layout.addLayout(form)
        hint = QLabel("Choisissez l’état du corps puis cliquez sur une face plane dans la vue. "
                      "Un plan peut aussi être choisi dans l’arbre. Les coordonnées et UUID des entités sont conservés. "
                      "L’aperçu recalcule les fonctions consommatrices avant validation.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        self.document.selection_changed.connect(self.selection_changed)
        self._connected = True
        return self.editor

    def source_changed(self, *_):
        self.support = None
        self.selected.setText("Cliquez sur une face de cet état producteur")
        self.changed()

    def ref_for(self, body_id):
        selected = self.producer.currentData()
        if selected is not None and selected.body_id == body_id:
            return selected
        return next((ref for ref in self.available if ref.body_id == body_id), None)

    def pick_shape(self, body_id):
        ref = self.ref_for(body_id)
        return result_at(self.document, ref).shape if ref else None

    def attach(self, surface):
        self.surface = surface

    def set_support(self, support):
        self.support = support
        self.selected.setText(support.frame.name)
        self.changed()

    def clicked(self, x, y):
        try:
            item = self.surface.pick_face(x, y)
            if item is not None:
                ref = self.ref_for(item.object_id)
                if ref is None:
                    raise ValueError("Aucun état valide de ce corps ne précède l’esquisse.")
                self.set_support(from_face_state(self.document, item.object_id, item.shape, ref.producer_id))
        except (ValueError, RuntimeError) as exc:
            self.support = None
            self.selected.setText(str(exc))
            self.changed()
        return True

    def selection_changed(self, *_):
        items = self.services.selection_items
        if len(items) != 1:
            return
        item = items[0]
        target = self.document.objects[item.object_id]
        if target.kind is ObjectType.REFERENCE_PLANE:
            try:
                frame = resolve_plane(self.document, target)
                self.set_support(PlaneSupport(target.id, frame, target.revision, attachment=frame))
            except ValueError as exc:
                self.support = None
                self.selected.setText(str(exc))
                self.changed()

    def preview(self):
        if self.services.active_document is not self.document or dict(self.document.objects) != self.snapshot:
            raise ValueError("Le document source a changé.")
        self.preview_parts, self.preview_hidden_ids = None, ()
        if self.support is None:
            self.commit_error = "Choisissez un nouveau plan ou une nouvelle face."
            ref = self.producer.currentData()
            if ref is None:
                return None
            state = result_at(self.document, ref)
            body = self.document.objects[ref.body_id]
            self.preview_hidden_ids = (body.id,)
            return SceneObject("État producteur à sélectionner", state.shape, body.color)
        key = (self.support, self.preserve.isChecked())
        if key != self._key:
            objects = reassociation_objects(self.document, self.sketch.id, self.support, preserve_frame=key[1])
            candidate = {**self.document.objects, **{o.id: o for o in objects}}
            affected = affected_bodies(candidate, (o.id for o in objects))
            if affected:
                extrusions = self.services.service("cadastrophe.modeling.extrusions")
                self._results, self._prefixes = preview_construction(self.document, candidate, affected, extrusions.evaluator)
            else:
                self._results, self._prefixes = {}, {}
            self._objects, self._key = objects, key
        self.commit_error = ""
        self.preview_hidden_ids = tuple(self._results)
        self.preview_parts = tuple((result.shape, self.document.objects[key].color, (), self.document.objects[key].transparency)
                                   for key, result in self._results.items() if self.document.objects[key].visible)
        if self.preview_parts:
            return SceneObject("Aperçu du support réparé", self.preview_parts[0][0], self.preview_parts[0][1])
        self.preview_parts = None
        frame = self._objects[0].sketch.support.frame
        return SceneObject("Nouveau support d’esquisse", plane_shape(frame, 25), self.sketch.color, plane=frame)

    def commit(self, document, preview):
        if self.commit_error or dict(document.objects) != self.snapshot:
            raise ValueError(self.commit_error or "Le document source a changé.")
        document.update_construction(self._objects, title=self.title, results=self._results, prefixes=self._prefixes)
        document.select_objects((self.sketch.id,))
        self.committed = True

    def dispose(self):
        if self._connected:
            self.document.selection_changed.disconnect(self.selection_changed)
        if not self.committed:
            self.document.select_objects(self.original_selection)
        self.snapshot = {}
        self._objects = self._results = self._prefixes = self._key = None
        self.surface = self.editor = self.changed = self.preview_parts = None
