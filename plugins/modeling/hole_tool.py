"""Guided holes use the common detached recipe and publication lifecycle."""
from dataclasses import replace

from PySide6.QtWidgets import QWidget, QFormLayout, QComboBox, QCheckBox, QListWidget, QListWidgetItem, QLabel
from PySide6.QtCore import Qt, QSignalBlocker
from cadastrophe.plugin_api import LengthSpinBox, EquationSpinBox, bind_fields
from .selection_field import SelectionField
from cadastrophe.document import ObjectType, SceneObject
from cadastrophe.core.feature_objects import HoleFeature, SketchSourceObject
from cadastrophe.core.feature_model import Operation
from cadastrophe.core.hole_model import HoleParameters
from cadastrophe.core.fastener_catalog import FAMILIES, hole_choices, hole_form
from cadastrophe.core.operation_model import GeometryReference
from .recipe_session import RecipePreview, adopted, unique_name
from .revolution_tool import sketch_input


class HoleSession(RecipePreview):
    def __init__(self, services, extrusions, profiles, feature=None):
        self.initialize(services, extrusions, feature)
        self.profiles = profiles
        self.title = "Modifier " + feature.name if feature else "Perçage guidé"

    def create_editor(self, parent, changed):
        self.changed = changed
        self.editor = QWidget(parent)
        self.editor.setObjectName("holeFeatureEditor")
        form = QFormLayout(self.editor)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        p = self.feature.parameters if self.feature else HoleParameters()
        self.catalog_choice = p.catalog
        self.source, self.body = QComboBox(), QComboBox()
        self.source.setObjectName("holeSketch")
        self.body.setObjectName("holeTarget")
        self.sketch_pick = SelectionField("Cliquer une esquisse de points dans la vue 3D")
        self.sketch_pick.setObjectName("holeSketchSelection")
        self.body_pick = SelectionField("Cliquer le corps à percer dans la vue 3D")
        self.body_pick.setObjectName("holeBodySelection")
        self.body_pick.setEnabled(not self.feature)
        self.pick_target = "sketch"
        self.sketch_pick.activated.connect(lambda: self.activate_pick("sketch"))
        self.body_pick.activated.connect(lambda: self.activate_pick("body"))
        self.sketch_pick.cleared.connect(lambda: self.source.setCurrentIndex(0))
        self.body_pick.cleared.connect(lambda: self.body.setCurrentIndex(0))
        self.source.addItem("Choisir une esquisse de points…", None)
        self.body.addItem("Choisir le corps à percer…", None)
        for obj in self.document.objects.values():
            if isinstance(obj, SketchSourceObject):
                self.source.addItem(obj.name, obj.id)
            if obj.kind is ObjectType.BODY:
                self.body.addItem(obj.name, obj.id)
        selected = self.feature.sketch_id if self.feature else next((key for key in self.original_selection
                    if isinstance(self.document.objects.get(key), SketchSourceObject)), None)
        self.source.setCurrentIndex(self.source.findData(selected))
        if self.feature:
            self.body.setCurrentIndex(self.body.findData(self.feature.body_id))
            self.body.setEnabled(False)
        form.addRow("Esquisse de positionnement", self.sketch_pick)
        form.addRow("Ou choisir dans la liste", self.source)
        self.all_points = QCheckBox("Tous les points, y compris ceux des motifs")
        self.all_points.setChecked(not p.positions)
        form.addRow(self.all_points)
        self.positions = QListWidget()
        self.positions.setObjectName("holePositions")
        self.positions.setMaximumHeight(120)
        form.addRow("Points", self.positions)
        self.catalog_family, self.catalog_size = QComboBox(), QComboBox()
        self.catalog_family.setObjectName("holeCatalogFamily")
        self.catalog_size.setObjectName("holeCatalogSize")
        self.catalog_family.addItem("Dimensions personnalisées", None)
        for key, label in FAMILIES:
            self.catalog_family.addItem(label, key)
        form.addRow("Usage", self.catalog_family)
        form.addRow("Taille", self.catalog_size)
        self.catalog_info = QLabel()
        self.catalog_info.setWordWrap(True)
        form.addRow(self.catalog_info)
        self.form = QComboBox()
        for label, value in (("Simple", "simple"), ("Lamé", "counterbore"), ("Fraisé", "countersink")):
            self.form.addItem(label, value)
        self.form.setCurrentIndex(self.form.findData(p.form))
        self.condition = QComboBox()
        self.condition.addItem("Borgne", "blind")
        self.condition.addItem("À travers tout", "through_all")
        self.condition.setCurrentIndex(self.condition.findData(p.end_condition))
        self.reverse = QCheckBox("Inverser la direction")
        self.reverse.setChecked(p.reversed)
        form.addRow("Forme", self.form)
        form.addRow("Condition de fin", self.condition)
        self.fields = {}
        for key, label in (("diameter", "Diamètre"), ("depth", "Profondeur"),
                           ("head_diameter", "Diamètre d’entrée"), ("head_depth", "Profondeur du lamage")):
            field = LengthSpinBox(self.services.display_units, .000001, 1e6, getattr(p, key))
            field.setObjectName("hole_" + key)
            self.fields[key] = field
            form.addRow(label, field)
        angle = EquationSpinBox()
        angle.setRange(.001, 179.999)
        angle.setDecimals(4)
        angle.setSuffix("°")
        angle.setValue(p.angle)
        self.fields["angle"] = angle
        form.addRow("Angle de fraisure", angle)
        form.addRow(self.reverse)
        form.addRow("Corps cible", self.body_pick)
        form.addRow("Ou choisir dans la liste", self.body)
        hint = QLabel("Créez d’abord une esquisse sur un plan ou une face plane avec l’outil Esquisse, "
                      "puis placez ses points et leurs motifs. Sélectionnez ici cette esquisse et le corps à percer.")
        hint.setWordWrap(True)
        form.addRow(hint)
        self.source.currentIndexChanged.connect(self.refresh_points)
        for widget in (self.form, self.condition):
            widget.currentIndexChanged.connect(self.refresh_fields)
        self.all_points.toggled.connect(self.refresh_fields)
        self.positions.itemChanged.connect(changed)
        self.body.currentIndexChanged.connect(self.target_changed)
        self.reverse.toggled.connect(changed)
        for field in self.fields.values():
            field.valueChanged.connect(changed)
            field.lineEdit().textEdited.connect(changed)
        bind_fields(self, self.fields)
        self.catalog_family.currentIndexChanged.connect(self.refresh_catalog)
        self.catalog_size.currentIndexChanged.connect(self.select_catalog)
        if p.catalog is not None:
            with QSignalBlocker(self.catalog_family):
                self.catalog_family.setCurrentIndex(self.catalog_family.findData(p.catalog.family))
            self.refresh_catalog(saved=p.catalog)
        else:
            self.refresh_catalog()
        self.refresh_points()
        return self.editor

    def refresh_catalog(self, *_args, saved=None):
        family = self.catalog_family.currentData()
        with QSignalBlocker(self.catalog_size):
            self.catalog_size.clear()
            if family:
                for choice in hole_choices(family):
                    self.catalog_size.addItem(choice.designation, choice)
                if saved is not None:
                    # A saved snapshot remains authoritative even after data updates.
                    index = next((i for i in range(self.catalog_size.count())
                                  if self.catalog_size.itemData(i) == saved), -1)
                    if index < 0:
                        self.catalog_size.addItem(saved.designation + " · enregistré", saved)
                        index = self.catalog_size.count() - 1
                    self.catalog_size.setCurrentIndex(index)
            self.catalog_size.setEnabled(bool(family))
        if saved is not None:
            self.catalog_choice = saved
            self.refresh_fields()
        else:
            self.select_catalog()

    def select_catalog(self, *_):
        self.catalog_choice = self.catalog_size.currentData()
        if self.catalog_choice is not None:
            with QSignalBlocker(self.form):
                self.form.setCurrentIndex(self.form.findData(hole_form(self.catalog_choice)))
            for key, value in self.catalog_choice.dimensions:
                widget = self.fields[key]
                with QSignalBlocker(widget):
                    widget.clear_equation()
                    if key == "angle":
                        widget.setValue(value)
                    else:
                        widget.set_internal_value(value)
        self.refresh_fields()

    def refresh_points(self, *_):
        self._pick_error = ""
        self.positions.clear()
        obj = self.document.objects.get(self.source.currentData())
        self.sketch_pick.set_value(obj.id if obj else None, obj.name if obj else "")
        if not self.feature:
            available = [self.body.itemData(i) for i in range(self.body.count()) if self.body.itemData(i)]
            support = self.document.objects.get(obj.sketch.support.plane_id) if obj else None
            # The sketch's attachment has priority over unrelated projected geometry.
            owners, visited, pending = set(), set(), [support] if support else []
            from cadastrophe.core.reference_objects import DatumPlaneObject
            while pending:
                current = pending.pop()
                if current.id in visited:
                    continue
                visited.add(current.id)
                if current.kind is ObjectType.BODY:
                    owners.add(current.id)
                elif isinstance(current, DatumPlaneObject):
                    pending.extend(self.document.objects[ref.object_id] for ref in current.geometry_references
                                   if ref.object_id in self.document.objects)
            if not owners and obj:
                owners = set(obj.body_dependencies) | {ref.body_id for ref in obj.producer_refs}
            target = next(iter(owners)) if len(owners) == 1 else available[0] if len(available) == 1 else None
            self.body.setCurrentIndex(max(0, self.body.findData(target)))
        if obj is not None:
            selected = self.feature.parameters.positions if self.feature else ()
            keys = {r.entity_id for r in selected}
            # Keep missing selections visible and invalid until explicitly repaired.
            points = [e for e in obj.sketch.all_entities if e.kind == "point"]
            for key, label in [(e.id, f"Point {i}") for i, e in enumerate(points, 1)] + [
                    (key, "Point absent") for key in keys - {e.id for e in points}]:
                row = QListWidgetItem(label, self.positions)
                row.setData(Qt.ItemDataRole.UserRole, key)
                row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                row.setCheckState(Qt.CheckState.Checked if key in keys else Qt.CheckState.Unchecked)
        self.suggest_direction()
        key = self.body.currentData()
        self.body_pick.set_value(key, self.body.currentText() if key else "")
        if obj is not None and key is None and not self.feature:
            self.activate_pick("body")
        self.sketch_pick.set_active(self.pick_target == "sketch")
        self.body_pick.set_active(self.pick_target == "body")
        self.refresh_fields()

    def target_changed(self, *_):
        self._pick_error = ""
        key = self.body.currentData()
        self.body_pick.set_value(key, self.body.currentText() if key else "")
        self.suggest_direction()
        self.changed()

    def activate_pick(self, target):
        if target == "body" and self.feature:
            return
        self.pick_target = target
        self.sketch_pick.set_active(target == "sketch")
        self.body_pick.set_active(target == "body")
        if self.surface is not None:
            self.surface.highlight_object(None)
            self.surface.highlight_regions()
            self.surface.focus()

    def pick_shape(self, object_id):
        if self.feature and object_id == self.feature.body_id:
            from cadastrophe.core.body_history import result_at
            return result_at(self.document, self.feature.input_state).shape
        from cadastrophe.core.history_position import context_object
        return context_object(self.document, self.document.objects[object_id]).shape

    def clicked(self, x, y):
        try:
            self._pick_error = ""
            if self.pick_target == "sketch":
                key = self.surface.detect_object(x, y)
                if isinstance(self.document.objects.get(key), SketchSourceObject):
                    self.source.setCurrentIndex(self.source.findData(key))
                    self.changed()
                    return True
                raise ValueError("Cliquez une esquisse visible ou choisissez-la dans la liste.")
            item = self.surface.pick_face(x, y)
            if item is not None:
                self.body.setCurrentIndex(self.body.findData(item.object_id))
                self.changed()
                return True
            raise ValueError("Cliquez un corps solide ou choisissez-le dans la liste.")
        except (ValueError, RuntimeError) as error:
            self._pick_error = str(error)
            self.changed()
        return True

    def hovered(self, x, y):
        if self.surface is None:
            return
        key, face = None, None
        try:
            if x is not None and y is not None:
                if self.pick_target == "sketch":
                    detected = self.surface.detect_object(x, y)
                    if isinstance(self.document.objects.get(detected), SketchSourceObject):
                        key = detected
                else:
                    item = self.surface.pick_face(x, y)
                    face = item.shape if item is not None else None
        except (ValueError, RuntimeError):
            pass
        self.surface.highlight_object(key)
        self.surface.highlight_regions(hovered=face)

    def suggest_direction(self):
        if self.feature:
            return
        sketch = self.document.objects.get(self.source.currentData())
        target = self.document.objects.get(self.body.currentData())
        if sketch is None or target is None or target.shape is None:
            return
        stamp = sketch.id, sketch.revision, target.id, target.revision
        if stamp == getattr(self, '_direction_stamp', None):
            return
        self._direction_stamp = stamp
        from .hole_geometry import suggested_hole_direction
        from cadastrophe.core.history_position import context_object
        try:
            frame, points = self.profiles.position_points(self.document, sketch.id, ())
            shape = context_object(self.document, target).shape
            if shape is None:
                return
            suggested = suggested_hole_direction(shape, frame, points)
            if suggested is not None:
                with QSignalBlocker(self.reverse):
                    self.reverse.setChecked(suggested)
        except (ValueError, RuntimeError):
            pass

    def refresh_fields(self, *_):
        form = self.form.currentData()
        self.fields["depth"].setEnabled(self.condition.currentData() == "blind")
        self.fields["head_diameter"].setEnabled(form != "simple")
        self.fields["head_depth"].setEnabled(form == "counterbore")
        self.fields["angle"].setEnabled(form == "countersink")
        self.positions.setEnabled(not self.all_points.isChecked())
        self.refresh_catalog_info()
        self.changed()

    def refresh_catalog_info(self):
        choice = self.catalog_choice
        if choice is None:
            self.catalog_info.setText("Dimensions libres.")
            return
        values = {key: field.value() if key == "angle" else field.internal_value for key, field in self.fields.items()}
        matching = self.form.currentData() == hole_form(choice) and all(abs(values[k] - v) < 1e-9 for k, v in choice.dimensions)
        text = choice.designation if matching else "Dimensions personnalisées · origine " + choice.designation
        self.catalog_info.setText(text + " · données " + choice.version +
            (" · taraudage par coupe" if choice.family == "tap_drill" else "") +
            (" · profondeur de lamage à choisir" if choice.family == "counterbore" else ""))
        self.catalog_info.setToolTip(choice.source)

    def preview_steps(self):
        self.check_snapshot()
        sketch = self.document.objects.get(self.source.currentData())
        target = self.document.objects.get(self.body.currentData())
        if sketch is None or target is None:
            raise ValueError("Choisissez une esquisse de positionnement et un corps cible.")
        values = {}
        initial = self.feature.parameters if self.feature else HoleParameters()
        for key, field in self.fields.items():
            if field.isEnabled():
                if not field.hasAcceptableInput():
                    raise ValueError(field.equation_error or "Dimension de perçage invalide.")
                field.interpretText()
                values[key] = field.value() if key == "angle" else field.internal_value
        positions = ()
        if not self.all_points.isChecked():
            positions = tuple(GeometryReference("sketch_point", sketch.id,
                entity_id=self.positions.item(i).data(Qt.ItemDataRole.UserRole), part="position")
                for i in range(self.positions.count()) if self.positions.item(i).checkState() == Qt.CheckState.Checked)
            if not positions:
                raise ValueError("Choisissez au moins un point de positionnement.")
        parameters = replace(initial, **values, form=self.form.currentData(), end_condition=self.condition.currentData(),
                             reversed=self.reverse.isChecked(), positions=positions, catalog=self.catalog_choice)
        self.refresh_catalog_info()
        source = sketch_input(self.document, sketch)
        key = source, target.id, parameters
        if key != self._key:
            if self.feature:
                feature = replace(self.feature, source=source, parameters=parameters)
                objects = (feature,)
            else:
                body = adopted(target)
                feature = HoleFeature(unique_name(self.document, "Perçage"), None, body.color, kind=ObjectType.FEATURE,
                    id=self.feature_id, body_id=body.id, operation=Operation.CUT, source=source, parameters=parameters)
                objects = (replace(body, features=(*body.features, feature.id)), feature)
            yield from self.calculate_steps(objects, feature)
            self._key = key
        return SceneObject("Aperçu du perçage", self._current.shape, target.color)

    def commit(self, document, preview):
        super().commit(document, preview)
        document.set_visible(self.source.currentData(), False)
