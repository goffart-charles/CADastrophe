"""Compact entity list and buffered, explicit parameter apply; no live undo spam."""
from cadastrophe.plugin_api import ErrorLabel, set_feedback
from dataclasses import replace
from math import degrees, radians, isfinite

from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QListWidget, QListWidgetItem,
                               QAbstractItemView, QGroupBox, QPushButton, QDoubleSpinBox, QLabel, QToolButton, QCheckBox)
from PySide6.QtWidgets import QComboBox
from cadastrophe.plugin_api import LengthSpinBox
from cadastrophe.plugin_api import EquationSpinBox
from .model import Arc, Circle, Point, Segment, Vec2, Ellipse, Spline


class EntityPanel(QWidget):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.snapshot = None
        self._expression_snapshot = None
        self._pattern_snapshot = None
        self.fields = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.browse = QToolButton()
        self.browse.setText("Entités de l’esquisse")
        self.browse.setCheckable(True)
        self.browse.setArrowType(Qt.ArrowType.RightArrow)
        self.browse.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout.addWidget(self.browse)
        self.list = QListWidget()
        self.list.setAccessibleName("Entités de l’esquisse")
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setMaximumHeight(self.fontMetrics().height() * 7)
        self.list.itemSelectionChanged.connect(self._selected)
        layout.addWidget(self.list)
        self.list.hide()
        self.browse.toggled.connect(self.list.setVisible)
        self.browse.toggled.connect(lambda opened: self.browse.setArrowType(Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow))
        self.pattern_group = QGroupBox("Motifs associatifs")
        pattern_layout = QVBoxLayout(self.pattern_group)
        self.patterns = QComboBox()
        self.patterns.setAccessibleName("Motifs associatifs de l’esquisse")
        pattern_layout.addWidget(self.patterns)
        self.edit_pattern = QPushButton("Modifier le motif")
        self.edit_pattern.clicked.connect(self._edit_pattern)
        pattern_layout.addWidget(self.edit_pattern)
        self.detach_pattern = QPushButton("Dissocier le motif")
        self.detach_pattern.setToolTip("Rendre les occurrences indépendantes en conservant leurs identifiants et les références existantes.")
        self.detach_pattern.clicked.connect(lambda: self._change_pattern(detach=True))
        pattern_layout.addWidget(self.detach_pattern)
        self.remove_pattern = QPushButton("Supprimer le motif")
        self.remove_pattern.clicked.connect(lambda: self._change_pattern(detach=False))
        pattern_layout.addWidget(self.remove_pattern)
        layout.addWidget(self.pattern_group)
        self.pattern_group.hide()
        self.construction = QCheckBox("Ligne de construction")
        self.construction.setToolTip("Une référence discontinue qui ne participe pas aux profils solides.")
        self.construction.toggled.connect(self._set_construction)
        self.construction.hide()
        layout.addWidget(self.construction)
        self.group = QGroupBox("Paramètres de l’entité")
        self.form = QFormLayout(self.group)
        layout.addWidget(self.group)
        self.apply = QPushButton("Appliquer les paramètres")
        self.apply.clicked.connect(self.commit)
        layout.addWidget(self.apply)
        self.group.hide()
        self.apply.hide()
        self.message = ErrorLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.message.hide()

    def _selected(self):
        self.session.interaction.set_selected(item.data(Qt.ItemDataRole.UserRole) for item in self.list.selectedItems())

    def refresh(self):
        session = self.session
        interaction = session.interaction
        if interaction is None or session.document is None:
            return
        sketch = session.document.objects[session.object_id].sketch
        entities = sketch.entities
        blocker = QSignalBlocker(self.patterns)
        chosen = self.patterns.currentData()
        occurrence = sketch.pattern_for(interaction.selected[0]) if len(interaction.selected) == 1 else None
        if occurrence is not None:
            chosen = occurrence.id
        self.patterns.clear()
        for index, pattern in enumerate(sketch.patterns, 1):
            self.patterns.addItem(f"{index} · {'Linéaire' if pattern.mode == 'linear_pattern' else 'Circulaire'} · {pattern.count} occurrences", pattern.id)
        self.patterns.setCurrentIndex(max(0, self.patterns.findData(chosen)))
        self.pattern_group.setVisible(bool(sketch.patterns))
        self.pattern_group.setEnabled(session.corner_panel is None)
        del blocker
        blocker = QSignalBlocker(self.list)
        existing = [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]
        if existing != [entity.id for entity in entities]:
            self.list.clear()
            names = {Point: "Point", Segment: "Segment", Circle: "Cercle", Arc: "Arc", Ellipse: "Ellipse", Spline: "Spline"}
            for index, entity in enumerate(entities, 1):
                item = QListWidgetItem(f"{names[type(entity)]} {index} · {entity.id[:8]}")
                item.setData(Qt.ItemDataRole.UserRole, entity.id)
                self.list.addItem(item)
        for i in range(self.list.count()):
            entity = entities[i]
            text = self.list.item(i).text().split(" · construction")[0]
            self.list.item(i).setText(text + (" · construction" if entity.construction else ""))
            self.list.item(i).setSelected(self.list.item(i).data(Qt.ItemDataRole.UserRole) in interaction.selected)
        del blocker
        active = session.draft.tool is None and interaction.pending is None
        self.list.setEnabled(active)
        target = next((entity for entity in entities if entity.id == interaction.selected[0]), None) if len(interaction.selected) == 1 else None
        expressions = session.document.objects[session.object_id].expressions
        if target != self.snapshot or self._expression_snapshot != expressions or self._pattern_snapshot != sketch.patterns:
            self.snapshot = target
            self._expression_snapshot = expressions
            self._pattern_snapshot = sketch.patterns
            self._fields(target)
        blocker = QSignalBlocker(self.construction)
        self.construction.setVisible(type(target) is Segment)
        self.construction.setChecked(bool(target is not None and target.construction))
        generated = target is not None and sketch.pattern_for(target.id) is not None
        self.construction.setEnabled(active and type(target) is Segment and not generated)
        del blocker
        self.group.setEnabled(active and not generated)
        self.apply.setEnabled(active and target is not None and bool(self.fields) and not generated)
        self.message.setVisible(generated or bool(self.message.text()))
        if generated:
            set_feedback(self.message, "Occurrence pilotée par un motif. Ses paramètres sont des mesures ; utilisez Modifier le motif ou Dissocier le motif.", "info")

    def _edit_pattern(self):
        key = self.patterns.currentData()
        sketch = self.session.document.objects[self.session.object_id].sketch
        pattern = next((p for p in sketch.patterns if p.id == key), None)
        if pattern is not None:
            from .reshape_panel import open_reshape
            open_reshape(self.session, pattern.mode, pattern.id)

    def _change_pattern(self, *, detach):
        from .patterns import detach as bake, remove
        from .commands import apply_edit
        try:
            session = self.session
            sketch = session.document.objects[session.object_id].sketch
            candidate = (bake if detach else remove)(sketch, self.patterns.currentData())
            session.mutate(lambda: apply_edit(session.document, session.object_id, candidate,
                title="Dissocier le motif d’esquisse" if detach else "Supprimer le motif d’esquisse",
                validate=session.validate_native, validate_sketch=session.validate_sketch))
        except (ValueError, RuntimeError) as exc:
            self.session.report_error(str(exc))

    def _set_construction(self, enabled):
        entity = self.snapshot
        if type(entity) is not Segment or not self.construction.isEnabled() or entity.construction == enabled:
            return
        try:
            self.session.commit_entities((replace(entity, construction=enabled),),
                title="Convertir en ligne de construction" if enabled else "Convertir en ligne de contour")
        except (ValueError, RuntimeError) as exc:
            self.session.report_error(str(exc))
            self.refresh()

    def _fields(self, entity):
        while self.form.rowCount():
            self.form.removeRow(0)
        self.fields = {}
        self.message.clear()
        self.group.setVisible(entity is not None)
        self.apply.setVisible(entity is not None)
        if entity is None:
            return
        def length(key, label, value):
            control = LengthSpinBox(self.session.surface.units, -1e12 if key != "radius" else 1e-9, 1e12, value)
            self.fields[key] = (control, value, control.value())
            self.form.addRow(label, control)
        def vector(prefix, label, point):
            length(prefix + "u", label + " U", point.u)
            length(prefix + "v", label + " V", point.v)
        try:
            if type(entity) is Point:
                vector("p", "Position", entity.position)
            elif type(entity) is Segment:
                vector("a", "Départ", entity.start)
                vector("b", "Fin", entity.end)
            elif type(entity) is Spline:
                for i, point in enumerate(entity.points):
                    vector(f"p{i}", f"Pôle {i+1}", point)
            elif type(entity) is Ellipse:
                vector("c", "Centre", entity.center)
                length("major_radius", "Grand demi-axe", entity.major_radius)
                length("minor_radius", "Petit demi-axe", entity.minor_radius)
                rotation = EquationSpinBox()
                rotation.setRange(-36000, 36000)
                rotation.setDecimals(6)
                rotation.setValue(degrees(entity.rotation))
                self.fields["rotation"] = (rotation, entity.rotation, rotation.value())
                self.form.addRow("Orientation (°)", rotation)
            else:
                vector("c", "Centre", entity.center)
                length("radius", "Rayon", entity.radius)
                if type(entity) is Arc:
                    for key, label in (("start_angle", "Départ (°)"), ("sweep", "Balayage signé (°)")):
                        original = getattr(entity, key)
                        value = degrees(original)
                        if not isfinite(value) or abs(value) > 1e12:
                            raise ValueError("Angle hors de la plage de l’éditeur de paramètres.")
                        control = EquationSpinBox()
                        control.setRange(-1e12, 1e12)
                        control.setDecimals(6)
                        control.setKeyboardTracking(False)
                        control.setValue(value)
                        self.fields[key] = (control, original, control.value())
                        self.form.addRow(label, control)
            from .equation_parameters import ANGLES
            for key, (control, _, _) in self.fields.items():
                if self.session.document.objects[self.session.object_id].sketch.pattern_for(entity.id) is not None:
                    control.setReadOnly(True)
                    continue
                control.bind_equations(self.session.document, entity.id+"."+key,
                                       owner=self.session.object_id,
                                       unit="angle" if key in ANGLES else "length",
                                       scale=1. if key in ANGLES else self.session.surface.units.to_internal(1.))
            self.message.clear()
        except ValueError as exc:
            self.fields = {}
            self.message.setText(str(exc))

    def commit(self):
        if not self.apply.isEnabled() or self.snapshot is None:
            return False
        def value(key):
            control, original, displayed = self.fields[key]
            control.interpretText()
            if isinstance(control, LengthSpinBox):
                return control.internal_value
            return original if control.value() == displayed else radians(control.value())
        def vector(prefix):
            return Vec2(value(prefix + "u"), value(prefix + "v"))
        try:
            entity = self.snapshot
            if type(entity) is Point:
                result = replace(entity, position=vector("p"))
            elif type(entity) is Segment:
                result = replace(entity, start=vector("a"), end=vector("b"))
            elif type(entity) is Spline:
                result = replace(entity, points=tuple(vector(f"p{i}") for i in range(len(entity.points))))
            elif type(entity) is Ellipse:
                result = replace(entity, center=vector("c"), major_radius=value("major_radius"),
                                 minor_radius=value("minor_radius"), rotation=value("rotation"))
            else:
                changes = dict(center=vector("c"), radius=value("radius"))
                if type(entity) is Arc:
                    changes.update(start_angle=value("start_angle"), sweep=value("sweep"))
                result = replace(entity, **changes)
            expressions = {entity.id+"."+key: control.equation_expression()
                           for key, (control, _, _) in self.fields.items()}
            self.session.commit_entities((result,), title="Modifier les paramètres d’esquisse", expressions=expressions)
            self.message.clear()
            return True
        except (ValueError, RuntimeError) as exc:
            self.message.setText(str(exc))
            self.session.report_error(str(exc))
            return False
