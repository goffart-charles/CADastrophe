"""Face collection, neutral reference and historical draft editing."""
from dataclasses import replace

from PySide6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QLabel, QCheckBox, QDoubleSpinBox

from cadastrophe.document import SceneObject, ObjectType
from cadastrophe.core.body_history import result_at, rollback_hidden_ids
from cadastrophe.core.feature_model import BodyStateRef, Operation, ResultStatus
from cadastrophe.core.feature_objects import DraftFeature
from cadastrophe.core.operation_model import DraftParameters, GeometryReference
from cadastrophe.core.native_naming import capture, resolve, subshapes, static_topology
from .recipe_session import RecipePreview, adopted, unique_name
from .selection_field import SelectionField
from .reference_picker import ReferencePicker


class DraftSession(RecipePreview):
    def __init__(self, services, extrusions, feature=None):
        self.initialize(services, extrusions, feature)
        self.title = "Modifier "+feature.name if feature else "Dépouille"
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
            raise ValueError("Sélectionnez les faces à dépouiller dans la vue 3D.")
        state = result_at(self.document, self.source_ref())
        if state.status is not ResultStatus.READY:
            raise ValueError("Le corps source doit être recalculé avant de créer ou modifier la dépouille.")
        return state

    def pick_shape(self, object_id):
        from cadastrophe.core.history_position import context_object
        return self.source_state().shape if object_id == self.body_id else context_object(self.document, self.document.objects[object_id]).shape

    def reference_from_pick(self, item):
        from cadastrophe.core.reference_geometry import capture_reference
        if item.object_id != self.body_id:
            return capture_reference(self.document, item)
        body = self.document.objects[self.body_id]
        if item.revision != body.revision:
            raise ValueError("La sélection géométrique est périmée.")
        state = self.source_state()
        try:
            topology = capture(state.shape, item.shape, item.topology, state.topology or static_topology(state.shape))
        except ValueError:
            # Native detection may still provide the final AIS's edge. Its
            # named history must prove a unique upstream counterpart.
            return self.reference_for_edit(capture_reference(self.document, item))
        return GeometryReference(item.topology.lower(), self.body_id, self.source_ref().producer_id, topology)

    def selection_reference(self):
        try:
            if len(self.services.selection_items) == 1:
                self.active_reference.set_reference(self.reference_from_pick(self.services.selection_items[0]))
                self._pick_error = ""
        except (ValueError, RuntimeError) as exc:
            self._pick_error = str(exc)
        self.changed()
        return True

    def add_face(self, body_id, face):
        body = self.document.objects.get(body_id)
        if body is None or body.kind is not ObjectType.BODY:
            raise ValueError("Choisissez les faces d’un corps solide.")
        adopted(body)
        if self.body_id is not None and self.body_id != body_id and (self.feature or self.face_references):
            raise ValueError("Les faces à dépouiller doivent appartenir au même corps.")
        previous = self.body_id
        self.body_id = body_id
        try:
            state, source = self.source_state(), self.source_ref()
            ref = GeometryReference("face", body_id, source.producer_id,
                capture(state.shape, face, "Face", state.topology or static_topology(state.shape)))
        except (ValueError, RuntimeError):
            self.body_id = previous
            raise
        self.face_references = (tuple(r for r in self.face_references if r != ref) if ref in self.face_references
                                else (*self.face_references, ref))
        self._pick_error = ""

    def clear_faces(self):
        self.face_references = ()
        self.activate_faces()

    def activate_faces(self):
        if self.active_reference is not None:
            self.active_reference.pick.setChecked(False)
        self.active_reference = None
        self.refresh_reference_guides()
        self._pick_error = ""
        self.update_faces()

    def activate_reference(self, picker):
        self.faces.set_active(False)
        super().activate_reference(picker)

    def update_faces(self):
        label = f"{len(self.face_references)} face(s)"
        if self.body_id:
            label += " · "+self.document.objects[self.body_id].name
        self.faces.set_value(self.face_references or None, label)
        self.faces.set_active(self.active_reference is None)
        self.changed()

    def create_editor(self, parent, changed):
        self.changed = changed
        self.editor = QWidget(parent)
        self.editor.setObjectName("draftFeatureEditor")
        layout, form = QVBoxLayout(self.editor), QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.faces = SelectionField("Cliquer sur les faces à dépouiller dans la vue")
        self.faces.activated.connect(self.activate_faces)
        self.faces.cleared.connect(self.clear_faces)
        form.addRow("Faces à dépouiller", self.faces)
        p = self.feature.parameters if self.feature else None
        xy = next((o for o in self.document.objects.values() if o.plane is not None and o.plane.name == "XY"), None)
        initial = p.neutral if p else GeometryReference("plane", xy.id) if xy else None
        self.neutral = ReferencePicker(self, "plane", initial, collected=True)
        self.neutral.setObjectName("draftNeutral")
        self.direction = ReferencePicker(self, "axis", p.direction if p else GeometryReference("axis", values=(0, 0, 0, 0, 0, 1)), collected=True)
        self.direction.setObjectName("draftDirection")
        form.addRow("Plan neutre", self.neutral)
        form.addRow("Direction", self.direction)
        from cadastrophe.plugin_api import EquationSpinBox
        self.angle = EquationSpinBox()
        self.angle.setObjectName("draftAngle")
        self.angle.setRange(0., 89.999999)
        self.angle.setDecimals(6)
        self.angle.setSuffix("°")
        self.angle.setValue(p.angle if p else 3.)
        form.addRow("Angle", self.angle)
        self.reverse = QCheckBox("Inverser l’angle")
        self.reverse.setChecked(p.reversed if p else False)
        form.addRow(self.reverse)
        layout.addLayout(form)
        hint = QLabel("Les intersections avec le plan neutre sont conservées. "
                      "L’inversion change le côté d’inclinaison ; l’aperçu montre la matière conservée. "
                      "Les faces tangentes compatibles sont propagées. Échap annule.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        self.angle.valueChanged.connect(changed)
        self.angle.lineEdit().textEdited.connect(changed)
        self.reverse.toggled.connect(changed)
        self.document.selection_changed.connect(self.selection_changed)
        self._connected = True
        self.update_faces()
        from cadastrophe.plugin_api import bind_fields
        bind_fields(self, {"angle": self.angle})
        return self.editor

    def selection_changed(self, *_):
        if self.active_reference is not None:
            self.selection_reference()
            self.update_faces()
            return
        for item in self.services.selection_items:
            if item.topology == "Face" and item.shape is not None:
                try:
                    self.add_face(item.object_id, item.shape)
                except (ValueError, RuntimeError) as exc:
                    self._pick_error = str(exc)
        self.update_faces()

    def clicked(self, x, y):
        if self.active_reference is not None:
            self.pick_reference_at(x, y)
        else:
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
            item = self.surface.pick_face(x, y) if x is not None and self.active_reference is None else None
            state = self.source_state()
            selected = tuple(resolve(state.shape, r.topology, state.topology or static_topology(state.shape)) for r in self.face_references)
            self.surface.highlight_regions(selected, item.shape if item else None)
        except (ValueError, RuntimeError):
            self.surface.highlight_regions()

    def preview_steps(self):
        self.check_snapshot()
        try:
            if self.active_reference is not None:
                raise ValueError("Choisissez la référence dans la vue ou les options du champ.")
            if not self.angle.hasAcceptableInput():
                raise ValueError("Saisissez un angle valide.")
            self.angle.interpretText()
            params = DraftParameters(self.face_references, self.neutral.reference(), self.direction.reference(),
                                     self.angle.value(), self.reverse.isChecked())
            if params != self._key:
                body = adopted(self.document.objects[self.body_id])
                if self.feature:
                    feature = replace(self.feature, parameters=params)
                    objects = (feature,)
                else:
                    feature = DraftFeature(unique_name(self.document, "Dépouille"), None, body.color,
                        id=self.feature_id, kind=ObjectType.FEATURE, body_id=body.id, operation=Operation.DRAFT,
                        input_state=self.source_ref(), parameters=params)
                    objects = (replace(body, features=(*body.features, feature.id)), feature)
                yield from self.calculate_steps(objects, feature)
                self._key = params
            self.commit_error = ""
            return SceneObject("Aperçu de la dépouille", self._current.shape, self.document.objects[self.body_id].color)
        except (ValueError, RuntimeError) as exc:
            if self.body_id is None:
                raise
            state = self.source_state()
            body = self.document.objects[self.body_id]
            self.commit_error = str(exc)
            self._key = None
            self.preview_hidden_ids = rollback_hidden_ids(self.document.objects, self.feature) if self.feature else (self.body_id,)
            self.preview_parts = ((state.shape, body.color, subshapes(state.shape, "Face"), body.transparency),)
            return SceneObject("État source — dépouille à corriger", state.shape, body.color)

    def commit(self, document, preview):
        if self.commit_error or self._objects is None:
            raise ValueError(self.commit_error or "La dépouille n’est pas calculée.")
        super().commit(document, preview)
