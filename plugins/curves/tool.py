"""Detached native previews, atomic publication and common undo/redo."""
from dataclasses import replace

from PySide6.QtWidgets import QWidget, QFormLayout, QComboBox, QLineEdit, QDoubleSpinBox, QCheckBox, QPlainTextEdit, QLabel
from cadastrophe.plugin_api import CurveObject, CurveParameters, ObjectType, LengthSpinBox, SceneObject
from cadastrophe.geometry import Vec3
from cadastrophe.core.curve_geometry import curve_edge
from cadastrophe.core.body_history import affected_bodies, preview_construction


TITLES = {'helix': 'Hélice', 'spiral': 'Spirale', 'points': 'Courbe 3D par points'}


class CurveSession:
    preview_opaque = True

    def __init__(self, services, kind='helix', feature=None):
        self.services = services
        self.document = services.active_document
        self.feature = feature
        self.kind = feature.parameters.kind if feature is not None else kind
        self.title = ('Modifier : ' if feature is not None else 'Créer : ')+TITLES[self.kind]
        planes = [obj for obj in self.document.objects.values() if obj.plane is not None]
        plane_id = planes[0].id if planes and self.kind != 'points' else None
        self.initial = feature.parameters if feature is not None else CurveParameters(kind=self.kind, plane_id=plane_id,
            points=(Vec3(0., 0., 0.), Vec3(20., 0., 10.), Vec3(30., 20., 20.)) if self.kind == 'points' else ())
        self.candidate = feature or CurveObject(TITLES[self.kind], None, (.25, .55, .85), kind=ObjectType.FEATURE, parameters=self.initial)
        self.preview_hidden_ids = (feature.id,) if feature is not None else ()
        self.preview_parts = None
        self.results = self.prefixes = None

    def create_editor(self, parent, changed):
        editor = QWidget(parent)
        editor.setObjectName('curveParameters')
        layout = QFormLayout(editor)
        self.name = QLineEdit(self.candidate.name)
        self.name.textChanged.connect(changed)
        layout.addRow('Nom', self.name)
        self.plane = QComboBox()
        for obj in self.document.objects.values():
            if obj.plane is not None:
                self.plane.addItem(obj.name, obj.id)
        self.plane.setCurrentIndex(self.plane.findData(self.initial.plane_id))
        self.plane.currentIndexChanged.connect(changed)
        if self.kind != 'points':
            layout.addRow('Plan de départ', self.plane)
        self.fields = {}
        descriptions = [('radius', 'Rayon initial'), ('center_u', 'Centre U'), ('center_v', 'Centre V')]
        descriptions += [('pitch', 'Pas par tour')] if self.kind == 'helix' else [('radial_pitch', 'Écartement par tour')]
        if self.kind != 'points':
            for key, label in descriptions:
                field = LengthSpinBox(self.services.display_units, -1e7, 1e7, getattr(self.initial, key))
                field.setObjectName('curve_'+key)
                field.valueChanged.connect(changed)
                field.lineEdit().textEdited.connect(changed)
                self.fields[key] = field
                layout.addRow(label, field)
        self.turns = self.number(.001, 100., self.initial.turns, changed)
        self.start_angle = self.number(-36000., 36000., self.initial.start_angle, changed, ' °')
        self.conical_angle = self.number(-79.9, 79.9, self.initial.conical_angle, changed, ' °')
        self.clockwise = QCheckBox('Sens horaire')
        self.clockwise.setChecked(self.initial.clockwise)
        self.clockwise.toggled.connect(changed)
        if self.kind != 'points':
            layout.addRow('Nombre de tours', self.turns)
            layout.addRow('Angle de départ', self.start_angle)
            if self.kind == 'helix':
                layout.addRow('Angle conique', self.conical_angle)
            layout.addRow(self.clockwise)
        self.points = QPlainTextEdit()
        scale = self.services.display_units.to_display
        self.points.setPlainText('\n'.join('; '.join(str(scale(v)) for v in point.tuple()) for point in self.initial.points))
        self.points.textChanged.connect(changed)
        self.closed = QCheckBox('Fermer la courbe')
        self.closed.setChecked(self.initial.closed)
        self.closed.toggled.connect(changed)
        if self.kind == 'points':
            layout.addRow(QLabel('Un point X ; Y ; Z par ligne, en '+self.services.display_units.unit+'.'))
            layout.addRow(self.points)
            layout.addRow(self.closed)
        self.editor = editor
        return editor

    @staticmethod
    def number(low, high, value, changed, suffix=''):
        field = QDoubleSpinBox()
        field.setRange(low, high)
        field.setDecimals(4)
        field.setValue(value)
        field.setSuffix(suffix)
        field.valueChanged.connect(changed)
        field.lineEdit().textEdited.connect(changed)
        return field

    def preview(self):
        changes = {key: field.internal_value for key, field in self.fields.items()}
        if self.kind == 'points':
            points = []
            for line in self.points.toPlainText().splitlines():
                if not line.strip():
                    continue
                values = line.replace(',', '.').split(';')
                if len(values) != 3:
                    raise ValueError('Chaque ligne doit contenir X ; Y ; Z.')
                points.append(Vec3(*(self.services.display_units.to_internal(float(v.strip())) for v in values)))
            changes.update(points=tuple(points), closed=self.closed.isChecked())
        else:
            changes.update(plane_id=self.plane.currentData(), turns=self.turns.value(), start_angle=self.start_angle.value(),
                           conical_angle=self.conical_angle.value(), clockwise=self.clockwise.isChecked())
        parameters = replace(self.initial, **changes)
        self.candidate = replace(self.candidate, name=self.name.text(), parameters=parameters)
        edge = curve_edge(self.document, self.candidate)
        self.preview_parts = ((edge, self.candidate.color, (), 0.),)
        candidate = {**self.document.objects, self.candidate.id: self.candidate}
        affected = affected_bodies(candidate, {self.candidate.id})
        self.results = self.prefixes = None
        if affected:
            extrusions = self.services.service('cadastrophe.modeling.extrusions')
            self.results, self.prefixes = preview_construction(self.document, candidate, affected, extrusions.evaluator)
            self.preview_parts += tuple((result.shape, candidate[key].color, (), candidate[key].transparency)
                                        for key, result in self.results.items() if result.shape is not None and candidate[key].visible)
        self.preview_hidden_ids = (*affected, *((self.feature.id,) if self.feature is not None else ()))
        return SceneObject('Aperçu de la courbe', edge, self.candidate.color)

    def commit(self, document, preview):
        document.update_construction((self.candidate,), title=self.title, results=self.results, prefixes=self.prefixes)
        document.select_objects([self.candidate.id])

    def dispose(self):
        self.editor = self.preview_parts = self.results = self.prefixes = None
