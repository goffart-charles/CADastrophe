"""Sweeps use the common profile, target-body and detached-preview lifecycle."""
from PySide6.QtWidgets import QComboBox, QListWidget, QWidget, QHBoxLayout, QPushButton, QLabel
from cadastrophe.plugin_api import EquationSpinBox as QDoubleSpinBox

from cadastrophe.document import ObjectType
from cadastrophe.core.feature_objects import SweepFeature, SketchSourceObject
from cadastrophe.core.feature_model import Operation
from cadastrophe.core.operation_model import SweepParameters, GeometryReference
from .extrusion_tool import ExtrusionSession
from .revolution_tool import RevolutionSession
from .recipe_session import unique_name
from .selection_field import SelectionField


class SweepSession(RevolutionSession):
    cooperative_preview = True
    equation_extent = "roll"
    extent_label = "Rotation du profil"
    operation_name = "balayage"

    def __init__(self, services, extrusions, profiles, *, cut=False, feature=None):
        super().__init__(services, extrusions, profiles, cut=cut, feature=feature)
        self.title = "Modifier "+feature.name if feature else "Balayage soustractif" if self.is_cut else "Balayage additif"
        self.path_references = feature.parameters.path if feature else ()
        self._choosing_source = False

    def create_extent_editor(self):
        field = QDoubleSpinBox()
        field.setRange(-360., 360.)
        field.setDecimals(6)
        field.setSuffix("°")
        field.setValue(self.feature.parameters.roll if self.feature else 0.)
        return field

    def add_geometry_fields(self, form, changed):
        self.path_field = SelectionField("Cliquer sur les courbes du parcours dans leur ordre")
        self.path_field.setObjectName("sweepPath")
        self.path_field.activated.connect(lambda: self.activate("path"))
        self.path_field.cleared.connect(lambda: self.set_path(()))
        form.addRow("Parcours", self.path_field)
        self.path_sketch = QComboBox()
        self.path_sketch.setObjectName("sweepPathSketch")
        self.path_sketch.addItem("Ou choisir une esquisse ou une courbe…", None)
        from cadastrophe.core.curve_model import CurveObject
        for obj in self.document.objects.values():
            if isinstance(obj, SketchSourceObject) and any(e.kind != "point" for e in obj.sketch.entities):
                self.path_sketch.addItem(obj.name, obj.id)
            elif isinstance(obj, CurveObject):
                self.path_sketch.addItem(obj.name, obj.id)
        self.path_sketch.currentIndexChanged.connect(self.choose_path_sketch)
        form.addRow(self.path_sketch)
        self.path_list = QListWidget()
        self.path_list.setObjectName("sweepOrderedPath")
        self.path_list.setMaximumHeight(95)
        form.addRow(self.path_list)
        controls = QWidget()
        row = QHBoxLayout(controls)
        row.setContentsMargins(0, 0, 0, 0)
        for label, callback in (("Monter", lambda: self.move_path(-1)), ("Descendre", lambda: self.move_path(1)), ("Retirer", self.remove_path)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        form.addRow(controls)
        p = self.feature.parameters if self.feature else None
        self.transport = QComboBox()
        for label, value in (("Frenet corrigé", "corrected"), ("Frenet", "frenet"), ("Repère fixe · sections parallèles", "fixed")):
            self.transport.addItem(label, value)
        self.orientation = QComboBox()
        self.orientation.addItem("Normale au parcours", "normal")
        self.orientation.addItem("Conserver l’inclinaison du profil", "keep")
        self.transition = QComboBox()
        self.transition.addItem("Intersection des tronçons", "right")
        self.transition.addItem("Rotation autour du coude", "round")
        for label, widget, value in (("Transport", self.transport, p.transport if p else "corrected"),
                                    ("Orientation", self.orientation, p.orientation if p else "normal"),
                                    ("Raccords", self.transition, p.transition if p else "right")):
            widget.setCurrentIndex(widget.findData(value))
            widget.currentIndexChanged.connect(changed)
            form.addRow(label, widget)

    def create_editor(self, parent, changed):
        editor = ExtrusionSession.create_editor(self, parent, changed)
        editor.setObjectName("sweepFeatureEditor")
        self.roll = self.depth
        self.roll.setObjectName("sweepRoll")
        self.reverse.setText("Inverser le parcours")
        if self.feature:
            self.choose_source(self.feature.sketch_id)
            if self.feature.parameters.contours:
                self._set_regions(self.feature.parameters.contours)
            self.reverse.setChecked(self.feature.parameters.reversed)
            self.body.hide()
            self.merge.hide()
        else:
            self.reverse.setChecked(False)
        for label in editor.findChildren(QLabel):
            if label.text().startswith("Cliquez à l’intérieur des zones"):
                label.setText("Choisissez un profil fermé, puis les courbes du parcours dans leur ordre. "
                              "Son début doit être dans le plan du profil. Les boutons réordonnent les courbes. "
                              "Inverser déplace la section à l’autre extrémité du même parcours. "
                              "Milieu : rotation · Ctrl+milieu : déplacement · Molette : zoom.")
        self.refresh_path()
        editor.setFocusProxy(self.roll if self.source.value is not None else self.source)
        if self.source.value is not None:
            self.activate("path")
        return editor

    def choose_source(self, key):
        self._choosing_source = True
        try:
            super().choose_source(key)
        finally:
            self._choosing_source = False

    def activate(self, target):
        super().activate(target)
        if hasattr(self, "path_field"):
            self.path_field.set_active(target == "path")

    def refresh_path(self):
        self.path_field.set_value(self.path_references or None, f"{len(self.path_references)} courbe(s)")
        selected = self.path_list.currentRow()
        self.path_list.clear()
        for ref in self.path_references:
            obj = self.document.objects.get(ref.object_id)
            label = obj.name if obj is not None else "Référence absente"
            if isinstance(obj, SketchSourceObject):
                entity = next((e for e in obj.sketch.all_entities if e.id == ref.entity_id), None)
                caption = {"segment": "Segment", "arc": "Arc", "circle": "Cercle", "ellipse": "Ellipse", "spline": "Spline"}.get(getattr(entity, "kind", None), "Courbe absente")
                label += " · "+caption
            elif ref.kind == 'curve':
                label += " · Courbe de construction"
            else:
                label += " · Arête nommée"
            self.path_list.addItem(label)
        if self.path_list.count():
            self.path_list.setCurrentRow(max(0, min(selected, self.path_list.count()-1)))
        self.changed()

    def set_path(self, references):
        self.path_references = tuple(references)
        self._pick_error = ""
        self.activate("path")
        self.refresh_path()

    def choose_path_sketch(self):
        obj = self.document.objects.get(self.path_sketch.currentData())
        from cadastrophe.core.curve_model import CurveObject
        if isinstance(obj, CurveObject):
            self.set_path((GeometryReference('curve', obj.id),))
            return
        if isinstance(obj, SketchSourceObject):
            self.set_path(GeometryReference("sketch_curve", obj.id, entity_id=e.id) for e in obj.sketch.entities if e.kind != "point")

    def add_path(self, ref):
        ref = self.reference_for_edit(ref)
        if ref.kind not in {"edge", "sketch_curve", "curve"}:
            raise ValueError("Choisissez une courbe de construction, une courbe d’esquisse ou une arête nommée.")
        self.set_path(tuple(r for r in self.path_references if r != ref) if ref in self.path_references else (*self.path_references, ref))

    def move_path(self, delta):
        index = self.path_list.currentRow()
        if 0 <= index < len(self.path_references) and 0 <= index+delta < len(self.path_references):
            refs = list(self.path_references)
            refs[index], refs[index+delta] = refs[index+delta], refs[index]
            self.set_path(refs)
            self.path_list.setCurrentRow(index+delta)

    def remove_path(self):
        index = self.path_list.currentRow()
        if 0 <= index < len(self.path_references):
            self.set_path(self.path_references[:index]+self.path_references[index+1:])

    def _selection_changed(self, *_):
        if self._choosing_source:
            return
        if self.target != "path":
            return super()._selection_changed()
        try:
            items = self.services.selection_items
            if len(items) == 1:
                obj = self.document.objects.get(items[0].object_id)
                from cadastrophe.core.curve_model import CurveObject
                if isinstance(obj, SketchSourceObject):
                    self.set_path(GeometryReference("sketch_curve", obj.id, entity_id=e.id) for e in obj.sketch.entities if e.kind != "point")
                elif isinstance(obj, CurveObject):
                    self.add_path(GeometryReference('curve', obj.id))
                elif items[0].topology == "Edge":
                    from cadastrophe.core.reference_geometry import capture_reference
                    self.add_path(capture_reference(self.document, items[0]))
        except (ValueError, RuntimeError) as exc:
            self._pick_error = str(exc)
            self.changed()

    def clicked(self, x, y):
        if self.target != "path":
            return super().clicked(x, y)
        try:
            self._pick_error = ""
            ref = self.surface.pick_curve(x, y)
            if ref is not None:
                self.add_path(ref)
        except (ValueError, RuntimeError) as exc:
            self._pick_error = str(exc)
        self.changed()
        return True

    def geometry_parameters(self):
        if not self.roll.hasAcceptableInput():
            raise ValueError("Saisissez une rotation de profil valide.")
        self.roll.interpretText()
        return SweepParameters(self.path_references, self.transport.currentData(), self.orientation.currentData(),
                               self.transition.currentData(), self.roll.value(), self.reverse.isChecked(), self.selected_regions)

    def new_feature(self, source, color, parameters):
        return SweepFeature(unique_name(self.document, "Enlèvement par balayage" if self.is_cut else "Balayage"),
            None, color, kind=ObjectType.FEATURE, id=self.feature_id, operation=Operation.NEW_BODY,
            body_id=self.body_id, source=source, parameters=parameters)

    def commit(self, document, preview):
        super().commit(document, preview)
        for key in {ref.object_id for ref in self.path_references if ref.kind in {"sketch_curve", 'curve'}}:
            document.set_visible(key, False)
