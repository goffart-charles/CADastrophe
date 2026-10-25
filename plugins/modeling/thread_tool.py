"""One editable thread function, backed by generated profile and helix steps."""
from dataclasses import replace, fields

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QWidget, QFormLayout, QComboBox, QLabel, QCheckBox, QGroupBox

from cadastrophe.plugin_api import LengthSpinBox, EquationSpinBox, EquationIntSpinBox, bind_fields
from cadastrophe.document import ObjectType, SceneObject
from cadastrophe.core.feature_objects import ThreadFeature
from cadastrophe.core.feature_model import BodyStateRef, Operation
from cadastrophe.core.body_history import result_at
from cadastrophe.core.native_naming import capture, resolve, static_topology, subshapes
from cadastrophe.core.operation_model import GeometryReference
from cadastrophe.core.thread_model import ThreadParameters, thread_choices, apply_thread_choice
from cadastrophe.core.thread_geometry import cylindrical_support
from .recipe_session import RecipePreview, adopted, unique_name
from .selection_field import SelectionField


class ThreadSession(RecipePreview):
    cooperative_preview = True

    def __init__(self, services, extrusions, feature=None):
        self.initialize(services, extrusions, feature)
        self.title = "Modifier " + feature.name if feature else "Filetage / taraudage"
        self.body_id = feature.body_id if feature else None
        self.face_ref = feature.parameters.face if feature else None
        self.catalog_choice = feature.parameters.catalog if feature else None
        self.internal = feature.parameters.internal if feature else False

    def source_ref(self):
        if self.feature:
            return self.feature.input_state
        from cadastrophe.core.history_position import current_state_ref
        return current_state_ref(self.document, self.body_id)

    def pick_shape(self, object_id):
        if self.feature and object_id == self.body_id:
            return result_at(self.document, self.feature.input_state).shape
        from cadastrophe.core.history_position import context_object
        return context_object(self.document, self.document.objects[object_id]).shape

    def create_editor(self, parent, changed):
        self.changed = changed
        self.editor = QWidget(parent)
        self.editor.setObjectName("threadFeatureEditor")
        form = QFormLayout(self.editor)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.support = QComboBox()
        self.support.setObjectName("threadSupport")
        self.support.addItem("Choisir une face cylindrique dans la vue…", None)
        self._supports = {}
        for body in self.document.objects.values():
            if body.kind is not ObjectType.BODY or self.feature and body.id != self.body_id:
                continue
            try:
                adopted(body)
                from cadastrophe.core.history_position import current_state_ref
                ref = self.feature.input_state if self.feature else current_state_ref(self.document, body.id)
                state = result_at(self.document, ref)
                if state.shape is None:
                    continue
                topology = state.topology or static_topology(state.shape)
                for i, face in enumerate(subshapes(state.shape, "Face"), 1):
                    try:
                        cylinder, v0, v1, internal = cylindrical_support(state.shape, face)
                    except ValueError:
                        continue
                    selection = GeometryReference("face", body.id, ref.producer_id, capture(state.shape, face, "Face", topology))
                    self.support.addItem(f"{body.name} · {'Trou' if internal else 'Tige'} Ø{2*cylinder.Radius():g} · face {i}", selection)
                    self._supports[selection] = (cylinder, v0, v1, internal)
            except (ValueError, RuntimeError):
                continue
        self.face_field = SelectionField("Choisir la paroi du trou ou la tige dans la vue 3D")
        self.face_field.setObjectName("threadFaceSelection")
        self.face_field.activated.connect(lambda: self.surface.focus() if self.surface else None)
        self.face_field.cleared.connect(lambda: self.support.setCurrentIndex(0))
        form.addRow("Face cylindrique", self.face_field)
        form.addRow("Ou choisir dans la liste", self.support)
        self.kind_label = QLabel()
        form.addRow(self.kind_label)
        self.catalog_size = QComboBox()
        self.catalog_size.setObjectName("threadCatalogSize")
        form.addRow("Préréglage", self.catalog_size)
        self.catalog_info = QLabel()
        self.catalog_info.setWordWrap(True)
        form.addRow(self.catalog_info)
        self.profile_info = QLabel()
        self.profile_info.setWordWrap(True)
        form.addRow(self.profile_info)
        p = self.feature.parameters if self.feature else None
        defaults = {f.name: f.default for f in fields(ThreadParameters)}
        self.fields = {}
        advanced = QGroupBox("Profil, départs et extrémités")
        show_advanced = QCheckBox("Afficher les paramètres avancés")
        advanced_form = QFormLayout(advanced)
        content = QWidget()
        details = QFormLayout(content)
        advanced_form.addRow(content)
        show_advanced.toggled.connect(advanced.setVisible)
        for key, label in (("diameter", "Diamètre nominal"), ("pitch", "Pas"), ("length", "Longueur filetée"),
                           ("offset", "Décalage du départ"), ("clearance", "Jeu radial"),
                           ("angle", "Angle des flancs"), ("depth", "Profondeur du profil"),
                           ("root_flat", "Largeur du fond plat"), ("root_radius", "Rayon du fond arrondi"),
                           ("lead_in", "Entrée progressive"), ("lead_out", "Sortie progressive"),
                           ("phase", "Position angulaire"), ("starts", "Nombre de départs")):
            value = getattr(p, key) if p else defaults[key]
            if key == "starts":
                control = EquationIntSpinBox()
                control.setRange(1, 16)
                control.setValue(value)
            elif key in {"angle", "phase"}:
                control = EquationSpinBox()
                control.setDecimals(6)
                control.setRange(10. if key == "angle" else -36000., 150. if key == "angle" else 36000.)
                control.setSuffix("°")
                control.setValue(value)
            else:
                control = LengthSpinBox(self.services.display_units, 0. if key in {"offset", "clearance", "root_flat", "root_radius", "lead_in", "lead_out"} else 1e-6, 1e6, value)
            control.setObjectName("thread_" + key)
            self.fields[key] = control
            (form if key in {"diameter", "pitch", "length", "offset", "clearance"} else details).addRow(label, control)
        self.left = QCheckBox("Pas à gauche")
        self.reverse = QCheckBox("Partir de l’autre extrémité")
        self.left.setChecked(p.left_handed if p else False)
        self.reverse.setChecked(p.reversed if p else False)
        form.addRow(self.left)
        form.addRow(self.reverse)
        form.addRow(show_advanced)
        form.addRow(advanced)
        advanced.hide()
        hint = QLabel("Le profil et l’hélice sont générés par cette fonction. Les filets font partie du solide. "
                      "Un fond plat et un fond arrondi sont exclusifs. Entrées nulles : limites droites. "
                      "Le jeu est un décalage radial personnalisé, sans classe de tolérance implicite.")
        hint.setWordWrap(True)
        form.addRow(hint)
        bind_fields(self, self.fields)
        for control in self.fields.values():
            control.valueChanged.connect(self.parameters_changed)
            control.lineEdit().textEdited.connect(self.parameters_changed)
        self.left.toggled.connect(changed)
        self.reverse.toggled.connect(changed)
        self.support.currentIndexChanged.connect(self.select_support)
        self.catalog_size.currentIndexChanged.connect(self.select_catalog)
        index = self.support.findData(self.face_ref) if self.face_ref else -1
        if index < 0 and not self.feature:
            selected = self.services.selection_items
            for item in selected if len(selected) == 1 else ():
                owner = self.document.objects.get(item.object_id)
                if item.topology == "Face" and item.shape is not None and owner and owner.revision == item.revision:
                    for i in range(1, self.support.count()):
                        ref = self.support.itemData(i)
                        if ref.object_id == item.object_id:
                            state = result_at(self.document, BodyStateRef(ref.object_id, ref.producer_id))
                            try:
                                if resolve(state.shape, ref.topology, state.topology or static_topology(state.shape)).IsSame(item.shape):
                                    index = i
                                    break
                            except ValueError:
                                pass
        if self.feature and index < 0:
            self.support.addItem("Support enregistré · à réparer", self.face_ref)
            index = self.support.count() - 1
        with QSignalBlocker(self.support):
            self.support.setCurrentIndex(max(0, index))
        self.select_support(initial=True)
        return self.editor

    def select_support(self, *_args, initial=False):
        self._pick_error = ""
        self.face_ref = self.support.currentData()
        self.face_field.set_value(self.face_ref, self.support.currentText() if self.face_ref else "")
        self.face_field.set_active(self.face_ref is None)
        if self.face_ref is None:
            self.kind_label.setText("Choisissez un trou ou une tige cylindrique.")
            self.changed()
            return
        if self.feature and self.face_ref.object_id != self.feature.body_id:
            raise ValueError("Le support doit appartenir au même corps.")
        self.body_id = self.face_ref.object_id
        description = self._supports.get(self.face_ref)
        if description is not None:
            cylinder, low, high, self.internal = description
            if not self.feature or not initial:
                self.fields["length"].set_internal_value(min(6., high - low))
        self.kind_label.setText("Taraudage interne" if self.internal else "Filetage externe")
        saved = self.catalog_choice if initial and self.feature else None
        with QSignalBlocker(self.catalog_size):
            self.catalog_size.clear()
            self.catalog_size.addItem("Dimensions personnalisées", None)
            for choice in thread_choices(self.internal):
                self.catalog_size.addItem(choice.designation, choice)
            if saved is not None:
                index = self.catalog_size.findData(saved)
                if index < 0:
                    self.catalog_size.addItem(saved.designation + " · enregistré", saved)
                    index = self.catalog_size.count() - 1
                self.catalog_size.setCurrentIndex(index)
            elif description is not None:
                radius = cylinder.Radius()
                from cadastrophe.core.fastener_catalog import metric_threads
                match = next(((d, pitch) for d, pitch, drill, _ in metric_threads()
                              if abs((drill if self.internal else d) - 2 * radius) < 1e-5), None)
                index = next((i for i in range(1, self.catalog_size.count())
                              if (self.catalog_size.itemData(i).nominal, self.catalog_size.itemData(i).pitch) == match), 0)
                self.catalog_size.setCurrentIndex(index)
        if self.feature and initial:
            self.parameters_changed()
        else:
            if description is not None and not self.catalog_size.currentData():
                self.fields["diameter"].set_internal_value(2 * cylinder.Radius() + (1.1 if self.internal else 0.))
            self.select_catalog()

    def select_catalog(self, *_):
        self.catalog_choice = self.catalog_size.currentData()
        if self.face_ref is not None and self.catalog_choice is not None:
            base = ThreadParameters(self.face_ref, internal=self.internal)
            p = apply_thread_choice(base, self.catalog_choice)
            for key in ("diameter", "pitch", "angle", "depth", "root_flat", "root_radius"):
                control = self.fields[key]
                with QSignalBlocker(control):
                    control.clear_equation()
                    if key == "angle":
                        control.setValue(getattr(p, key))
                    else:
                        control.set_internal_value(getattr(p, key))
        self.parameters_changed()

    def parameters_changed(self, *_):
        self.catalog_info.setText("Dimensions personnalisées" if self.catalog_choice is None else
            self.catalog_choice.designation + " · données " + self.catalog_choice.version)
        if self.catalog_choice:
            self.catalog_info.setToolTip(self.catalog_choice.source)
        self.changed()

    def clicked(self, x, y):
        try:
            item = self.surface.pick_face(x, y)
            if item is not None:
                for i in range(1, self.support.count()):
                    ref = self.support.itemData(i)
                    if ref.object_id != item.object_id:
                        continue
                    state = result_at(self.document, BodyStateRef(ref.object_id, ref.producer_id))
                    face = resolve(state.shape, ref.topology, state.topology or static_topology(state.shape))
                    if face.IsSame(item.shape):
                        self.support.setCurrentIndex(i)
                        self._pick_error = ""
                        self.changed()
                        return True
                raise ValueError("Choisissez une face cylindrique du corps source.")
        except (ValueError, RuntimeError) as exc:
            self._pick_error = str(exc)
            self.changed()
        return True

    def preview_steps(self):
        self.check_snapshot()
        if self._pick_error:
            raise ValueError(self._pick_error)
        if self.face_ref is None:
            return None
        values = {}
        for key, control in self.fields.items():
            if not control.hasAcceptableInput():
                raise ValueError(control.equation_error or "Paramètre de filetage invalide.")
            control.interpretText()
            values[key] = control.value() if key in {"angle", "phase", "starts"} else control.internal_value
        p = ThreadParameters(self.face_ref, internal=self.internal, left_handed=self.left.isChecked(),
                             reversed=self.reverse.isChecked(), catalog=self.catalog_choice, **values)
        units = self.services.display_units
        self.profile_info.setText("Avance par tour : " + units.format_length(p.lead) +
                                  " · largeur de crête : " + units.format_length(p.crest))
        if self.catalog_choice and not p.catalog_active:
            self.catalog_info.setText("Dimensions personnalisées · origine " + self.catalog_choice.designation)
        if p != self._key:
            body = adopted(self.document.objects[self.body_id])
            feature = replace(self.feature, parameters=p) if self.feature else ThreadFeature(
                unique_name(self.document, "Taraudage" if p.internal else "Filetage"), None, body.color,
                kind=ObjectType.FEATURE, id=self.feature_id, body_id=body.id, operation=Operation.CUT,
                input_state=self.source_ref(), parameters=p)
            objects = (feature,) if self.feature else (replace(body, features=(*body.features, feature.id)), feature)
            yield from self.calculate_steps(objects, feature)
            self._key = p
        return SceneObject("Aperçu du filetage", self._current.shape, self.document.objects[self.body_id].color)
