"""Primitive parameters implement the public interactive-session contract."""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QLineEdit, QLabel
from cadastrophe.plugin_api import DisplayUnits, LengthSpinBox

from .geometry import PRIMITIVES, MIN_SIZE, MAX_SIZE, build_object


class PrimitiveSession:
    def __init__(self, kind, name, units=None):
        self.kind, self.name = kind, name
        self.units = units or DisplayUnits()
        self.title = f"Créer une {PRIMITIVES[kind][0].lower()}" if kind in ("box", "sphere") else f"Créer un {PRIMITIVES[kind][0].lower()}"
        self.editor = None

    def create_editor(self, parent, changed):
        self.editor = QWidget(parent)
        self.editor.setObjectName("primitiveParameters")
        layout = QVBoxLayout(self.editor)
        layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.name_edit = QLineEdit(self.name)
        self.name_edit.setObjectName("primitiveName")
        self.name_edit.textChanged.connect(changed)
        self.name_edit.selectAll()
        self.editor.setFocusProxy(self.name_edit)
        form.addRow("Nom", self.name_edit)
        self.dimensions = {}
        for key, label, default in PRIMITIVES[self.kind][1]:
            field = self._field(key, MIN_SIZE, MAX_SIZE, default, changed)
            self.dimensions[key] = field
            form.addRow(label, field)
        layout.addLayout(form)
        label = QLabel(PRIMITIVES[self.kind][2])
        label.setWordWrap(True)
        layout.addWidget(label)
        position = QFormLayout()
        position.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.origin = []
        for axis in "XYZ":
            field = self._field(axis.lower(), -MAX_SIZE, MAX_SIZE, 0., changed)
            self.origin.append(field)
            position.addRow(axis, field)
        layout.addLayout(position)
        return self.editor

    def _field(self, key, lower, upper, value, changed):
        field = LengthSpinBox(self.units, lower, upper, value)
        field.setObjectName(f"primitive_{key}")
        field.valueChanged.connect(changed)
        field.lineEdit().textEdited.connect(changed)
        return field

    def preview(self):
        for field in (*self.dimensions.values(), *self.origin):
            if not field.hasAcceptableInput():
                raise ValueError(f"Saisissez des dimensions et coordonnées valides en {self.units.unit}.")
            field.interpretText()
        return build_object(self.kind, self.name_edit.text(), tuple(field.internal_value for field in self.origin),
                            {key: field.internal_value for key, field in self.dimensions.items()})

    def commit(self, document, preview):
        document.add(preview)
        document.select_objects([preview.id])

    def dispose(self):
        self.editor = None
