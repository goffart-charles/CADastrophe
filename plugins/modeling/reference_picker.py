"""Shared reference fields for datum, revolution and transformed-operation tools."""
from cadastrophe.plugin_api import DecimalSpinBox
from PySide6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QComboBox, QPushButton, QDoubleSpinBox, QSizePolicy, QCheckBox
from PySide6.QtCore import QSignalBlocker

from cadastrophe.plugin_api import LengthSpinBox
from cadastrophe.core.operation_model import GeometryReference
from cadastrophe.core.feature_objects import SketchSourceObject
from cadastrophe.core.reference_geometry import resolve_reference, resolve_axis_reference
from cadastrophe.geometry import WorkPlane, Vec3


class ReferencePicker(QWidget):
    def __init__(self, session, kind, value=None, *, point=(0.,0.,0.), collected=False):
        super().__init__()
        self.setMinimumWidth(190)
        self.session, self.kind = session, kind
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        self.field = None
        if collected:
            from .selection_field import SelectionField
            self.field = SelectionField({"axis": "Cliquer ici, puis choisir l’axe dans la vue 3D",
                                         "plane": "Cliquer ici, puis choisir un plan ou une face",
                                         "point": "Cliquer ici, puis choisir un point"}[kind])
            self.field.activated.connect(lambda: session.activate_reference(self))
            self.field.cleared.connect(lambda: self.combo.setCurrentIndex(0))
            layout.addWidget(self.field)
        self.combo = QComboBox()
        self.combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setMinimumContentsLength(12)
        self.combo.addItem("Choisir une référence…", None)
        document = session.document
        if kind == "axis":
            for name, direction in (("X",(1,0,0)), ("Y",(0,1,0)), ("Z",(0,0,1))):
                self.combo.addItem(f"Axe {name}", GeometryReference("axis", values=(0,0,0,*direction)))
        for obj in document.objects.values():
            if obj.plane is not None and kind in {"plane", "axis"}:
                self.combo.addItem(obj.name + (" · normale" if kind == "axis" else ""), GeometryReference("plane", obj.id))
            if not isinstance(obj, SketchSourceObject):
                continue
            for index, entity in enumerate(obj.sketch.entities, 1):
                kind_label = {"segment": "Ligne", "circle": "Cercle", "arc": "Arc", "point": "Point"}.get(entity.kind, entity.kind)
                label = f"{obj.name} · {kind_label} {index}"
                if kind == "axis" and entity.kind == "segment":
                    self.combo.addItem(label + (" · construction" if entity.construction else ""), GeometryReference("sketch_line", obj.id, entity_id=entity.id))
                if kind == "point":
                    for part, caption in (("start","départ"), ("end","fin"), ("center","centre"), ("position","point")):
                        if getattr(entity, part, None) is not None:
                            self.combo.addItem(label+" · "+caption, GeometryReference("sketch_point", obj.id, entity_id=entity.id, part=part))
        if kind in {"axis", "point"}:
            self.combo.addItem("Coordonnées…", "manual")
        layout.addWidget(self.combo)
        self.pick = QPushButton("Choisir dans la vue / l’arbre")
        self.pick.setCheckable(True)
        self.pick.clicked.connect(lambda: session.activate_reference(self))
        layout.addWidget(self.pick)
        if collected:
            self.pick.hide()
            self.pick.toggled.connect(self.field.set_active)
            self.options = QCheckBox("Plans de référence…" if kind == "plane" else "Axes principaux et coordonnées…")
            layout.addWidget(self.options)
            self.combo.hide()
            self.options.toggled.connect(self.combo.setVisible)
        self.coordinates = QWidget()
        form = QFormLayout(self.coordinates)
        form.setContentsMargins(0,0,0,0)
        self.values = []
        for i, label in enumerate(("X", "Y", "Z", "Direction X", "Direction Y", "Direction Z")[:6 if kind == "axis" else 3]):
            control = LengthSpinBox(session.services.display_units, -1e9, 1e9, point[i] if i < 3 else (1. if i == 5 else 0.)) if i < 3 else DecimalSpinBox()
            if i >= 3:
                control.setRange(-1e6,1e6)
                control.setDecimals(6)
                control.setValue(1. if i == 5 else 0.)
            control.valueChanged.connect(session.changed)
            control.setMinimumWidth(70)
            control.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
            control.lineEdit().textEdited.connect(session.changed)
            form.addRow(label, control)
            self.values.append(control)
        layout.addWidget(self.coordinates)
        self.coordinates.hide()
        self.combo.currentIndexChanged.connect(self._changed)
        if value is not None:
            self.set_reference(value, validate=False)
        elif kind == "point":
            self.combo.setCurrentIndex(self.combo.findData("manual"))
        self._changed()

    def _changed(self):
        self.combo.setToolTip(self.combo.currentText())
        if self.field is not None:
            value = self.combo.currentData()
            self.field.set_value(value, self.combo.currentText() if value is not None else "")
        self.coordinates.setVisible(self.combo.currentData() == "manual")
        if self.session.active_reference is self:
            self.pick.setChecked(False)
            self.session.active_reference = None
        self.session.refresh_reference_guides()
        self.session._pick_error = ""
        self.session.changed()

    def reference(self):
        value = self.combo.currentData()
        if value == "manual":
            for control in self.values:
                if not control.hasAcceptableInput():
                    raise ValueError("Saisissez des coordonnées valides.")
                control.interpretText()
            return GeometryReference(self.kind, values=tuple(c.internal_value if hasattr(c,"internal_value") else c.value() for c in self.values))
        if not isinstance(value, GeometryReference):
            raise ValueError("Choisissez une référence géométrique.")
        return value

    def set_reference(self, value, *, validate=True):
        if validate:
            value = self.session.reference_for_edit(value)
            resolved = resolve_axis_reference(self.session.document, value) if self.kind == "axis" else resolve_reference(self.session.document, value)
            if self.kind == "plane" and not isinstance(resolved, WorkPlane):
                raise ValueError("Sélectionnez un plan ou une face plane.")
            if self.kind == "point" and not isinstance(resolved, Vec3):
                raise ValueError("Sélectionnez un point ou un sommet.")
        index = next((i for i in range(self.combo.count()) if self.combo.itemData(i) == value), -1)
        if index < 0 and value.kind in {"axis","point"}:
            for control,number in zip(self.values,value.values):
                blocker = QSignalBlocker(control)
                control.set_internal_value(number) if hasattr(control,"set_internal_value") else control.setValue(number)
                del blocker
            index = self.combo.findData("manual")
        if index < 0:
            obj = self.session.document.objects.get(value.object_id)
            label = obj.name if obj is not None else "Coordonnées enregistrées"
            label = {"face":"Face · ","edge":"Arête · ","vertex":"Sommet · "}.get(value.kind,"")+label
            self.combo.addItem(label+" · référence sélectionnée", value)
            index = self.combo.count()-1
        self.combo.setCurrentIndex(index)
        self._changed()
        self.session.changed()

    def dispose(self):
        self.combo.currentIndexChanged.disconnect(self._changed)
        self.pick.clicked.disconnect()
        if self.field is not None:
            self.field.activated.disconnect()
            self.field.cleared.disconnect()
        self.session = None
