"""Guided holes use the common detached recipe and publication lifecycle."""
from dataclasses import replace

from PySide6.QtWidgets import QWidget, QFormLayout, QComboBox, QCheckBox, QListWidget, QListWidgetItem, QLabel
from PySide6.QtCore import Qt, QSignalBlocker
from cadastrophe.plugin_api import LengthSpinBox, EquationSpinBox, bind_fields
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
        form.addRow("Esquisse de positionnement", self.source)
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
        form.addRow("Corps cible", self.body)
        hint = QLabel("Créez d’abord une esquisse sur un plan ou une face plane avec l’outil Esquisse, "
                      "puis placez ses points et leurs motifs. Sélectionnez ici cette esquisse et le corps à percer.")
        hint.setWordWrap(True)
        form.addRow(hint)
        self.source.currentIndexChanged.connect(self.refresh_points)
        for widget in (self.form, self.condition):
            widget.currentIndexChanged.connect(self.refresh_fields)
        self.all_points.toggled.connect(self.refresh_fields)
        self.positions.itemChanged.connect(changed)
        self.body.currentIndexChanged.connect(changed)
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
        self.positions.clear()
        obj = self.document.objects.get(self.source.currentData())
        if obj is not None:
            if not self.feature:
                supports = set(obj.body_dependencies)
                available = [self.body.itemData(i) for i in range(self.body.count()) if self.body.itemData(i)]
                target = next(iter(supports)) if len(supports) == 1 else available[0] if len(available) == 1 else None
                if target in available:
                    self.body.setCurrentIndex(self.body.findData(target))
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
        self.refresh_fields()

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
