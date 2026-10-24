"""Face collection and historical editing for a constant-thickness shell."""
from dataclasses import replace

from PySide6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QLabel, QCheckBox

from cadastrophe.document import SceneObject, ObjectType
from cadastrophe.plugin_api import LengthSpinBox
from cadastrophe.core.body_history import result_at, rollback_hidden_ids
from cadastrophe.core.feature_model import BodyStateRef, Operation, ResultStatus
from cadastrophe.core.feature_objects import ShellFeature
from cadastrophe.core.operation_model import ShellParameters, GeometryReference
from cadastrophe.core.native_naming import capture, resolve, subshapes, static_topology
from .recipe_session import RecipePreview, adopted, unique_name
from .selection_field import SelectionField


class ShellSession(RecipePreview):
    def __init__(self, services, extrusions, feature=None):
        self.initialize(services, extrusions, feature)
        self.title = "Modifier "+feature.name if feature else "Coque"
        self.body_id = feature.body_id if feature else None
        self.face_references = feature.parameters.faces if feature else ()
        self.commit_error = ""
        if feature is None:
            for item in services.selection_items:
                if item.topology == "Face" and item.shape is not None:
                    try:
                        if self.document.objects[item.object_id].revision != item.revision:
                            raise ValueError("La face sélectionnée a changé ; sélectionnez-la à nouveau.")
                        self.add_face(item.object_id, item.shape)
                    except ValueError as exc:
                        self._pick_error = str(exc)

    def source_ref(self):
        if self.feature:
            return self.feature.input_state
        body = self.document.objects[self.body_id]
        from cadastrophe.core.history_position import current_state_ref
        return current_state_ref(self.document, body.id)

    def source_state(self):
        if self.body_id is None:
            raise ValueError("Sélectionnez les faces à ouvrir dans la vue 3D.")
        state = result_at(self.document, self.source_ref())
        if state.status is not ResultStatus.READY:
            raise ValueError("Le corps source doit être recalculé avant de créer ou modifier la coque.")
        return state

    def pick_shape(self, object_id):
        from cadastrophe.core.history_position import context_object
        return self.source_state().shape if object_id == self.body_id else context_object(self.document, self.document.objects[object_id]).shape

    def add_face(self, body_id, face):
        body = self.document.objects.get(body_id)
        if body is None or body.kind is not ObjectType.BODY:
            raise ValueError("Choisissez les faces d’un corps solide.")
        adopted(body)  # Respect ownership when adopting a primitive or an import.
        if self.body_id is not None and self.body_id != body_id and (self.feature or self.face_references):
            raise ValueError("Les faces à ouvrir doivent appartenir au même corps.")
        previous_body_id = self.body_id
        self.body_id = body_id
        try:
            state, source = self.source_state(), self.source_ref()
            ref = GeometryReference("face", body_id, source.producer_id,
                                    capture(state.shape, face, "Face", state.topology or static_topology(state.shape)))
        except (ValueError, RuntimeError):
            self.body_id = previous_body_id
            raise
        if ref in self.face_references:
            self.face_references = tuple(r for r in self.face_references if r != ref)
        else:
            self.face_references = (*self.face_references, ref)
        self._pick_error = ""

    def clear_faces(self):
        self.face_references = ()
        self._pick_error = ""
        self.update_faces()

    def update_faces(self):
        label = f"{len(self.face_references)} face(s)"
        if self.body_id:
            label += " · "+self.document.objects[self.body_id].name
        self.faces.set_value(self.face_references or None, label)
        self.faces.set_active(True)
        self.changed()

    def create_editor(self, parent, changed):
        self.changed = changed
        self.editor = QWidget(parent)
        self.editor.setObjectName("shellFeatureEditor")
        layout, form = QVBoxLayout(self.editor), QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.faces = SelectionField("Cliquer sur les faces à ouvrir dans la vue")
        self.faces.activated.connect(self.update_faces)
        self.faces.cleared.connect(self.clear_faces)
        form.addRow("Faces à ouvrir", self.faces)
        p = self.feature.parameters if self.feature else None
        self.thickness = LengthSpinBox(self.services.display_units, 0., 1e6, p.thickness if p else 2.)
        self.thickness.setObjectName("shellThickness")
        form.addRow("Épaisseur", self.thickness)
        self.outward = QCheckBox("Épaissir vers l’extérieur")
        self.outward.setChecked(p.outward if p else False)
        form.addRow(self.outward)
        layout.addLayout(form)
        hint = QLabel("Cliquez sur une face pour l’ajouter ou la retirer. L’épaisseur est constante ; "
                      "par défaut la matière est conservée à l’intérieur du volume source. Échap annule.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        self.thickness.valueChanged.connect(changed)
        self.thickness.lineEdit().textEdited.connect(changed)
        self.outward.toggled.connect(changed)
        self.document.selection_changed.connect(self.selection_changed)
        self._connected = True
        self.update_faces()
        from cadastrophe.plugin_api import bind_fields
        bind_fields(self, {"thickness": self.thickness})
        return self.editor

    def selection_changed(self, *_):
        for item in self.services.selection_items:
            if item.topology == "Face" and item.shape is not None:
                try:
                    self.add_face(item.object_id, item.shape)
                except (ValueError, RuntimeError) as exc:
                    self._pick_error = str(exc)
        self.update_faces()

    def clicked(self, x, y):
        try:
            self._pick_error = ""
            item = self.surface.pick_face(x, y)
            if item is not None:
                self.add_face(item.object_id, item.shape)
        except (ValueError, RuntimeError) as exc:
            self._pick_error = str(exc)
        self.update_faces()
        return True

    def hovered(self, x, y):
        if self.surface is None:
            return
        try:
            item = self.surface.pick_face(x, y) if x is not None else None
            state = self.source_state()
            selected = tuple(resolve(state.shape, r.topology, state.topology or static_topology(state.shape)) for r in self.face_references)
            self.surface.highlight_regions(selected, item.shape if item else None)
        except (ValueError, RuntimeError):
            self.surface.highlight_regions()

    def preview_steps(self):
        self.check_snapshot()
        try:
            if not self.thickness.hasAcceptableInput():
                raise ValueError("Saisissez une épaisseur positive valide.")
            self.thickness.interpretText()
            params = ShellParameters(self.face_references, self.thickness.internal_value, self.outward.isChecked())
            if params != self._key:
                body = adopted(self.document.objects[self.body_id])
                if self.feature:
                    feature = replace(self.feature, parameters=params)
                    objects = (feature,)
                else:
                    feature = ShellFeature(unique_name(self.document, "Coque"), None, body.color,
                        id=self.feature_id, kind=ObjectType.FEATURE, body_id=body.id, operation=Operation.SHELL,
                        input_state=self.source_ref(), parameters=params)
                    objects = (replace(body, features=(*body.features, feature.id)), feature)
                yield from self.calculate_steps(objects, feature)
                self._key = params
            self.commit_error = ""
            return SceneObject("Aperçu de la coque", self._current.shape, self.document.objects[self.body_id].color)
        except (ValueError, RuntimeError) as exc:
            if self.body_id is None:
                raise
            state = self.source_state()
            body = self.document.objects[self.body_id]
            self.commit_error = str(exc)
            self._key = None
            self.preview_hidden_ids = rollback_hidden_ids(self.document.objects, self.feature) if self.feature else (self.body_id,)
            self.preview_parts = ((state.shape, body.color, subshapes(state.shape, "Face"), body.transparency),)
            return SceneObject("État source — coque à corriger", state.shape, body.color)

    def commit(self, document, preview):
        if self.commit_error or self._objects is None:
            raise ValueError(self.commit_error or "La coque n’est pas calculée.")
        super().commit(document, preview)
