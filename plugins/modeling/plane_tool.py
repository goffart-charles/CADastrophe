"""Offset, hinged and three-point planes through the common tool/undo lifecycle."""
from dataclasses import replace

from PySide6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QComboBox, QCheckBox, QDoubleSpinBox, QLabel

from cadastrophe.plugin_api import LengthSpinBox
from cadastrophe.document import SceneObject
from cadastrophe.geometry import REFERENCE_PLANES
from cadastrophe.core.reference_objects import DatumPlaneObject
from cadastrophe.core.operation_model import PlaneParameters
from cadastrophe.core.reference_geometry import resolve_plane, plane_shape, capture_reference
from cadastrophe.core.body_history import affected_bodies, validate_graph
from .reference_picker import ReferencePicker
from .recipe_session import RecipePreview, unique_name


class PlaneSession(RecipePreview):
    def __init__(self, services, extrusions, feature=None):
        self.initialize(services,extrusions,feature)
        self.title = "Modifier "+feature.name if feature else "Plan de référence"

    def create_editor(self,parent,changed):
        self.changed = changed
        self.editor = QWidget(parent)
        layout, self.form = QVBoxLayout(self.editor), QFormLayout()
        self.form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.method = QComboBox()
        for label,key in (("Plan décalé","offset"), ("Plan incliné","inclined"), ("Par trois points","three_points")):
            self.method.addItem(label,key)
        p = self.feature.parameters if self.feature else None
        if p:
            self.method.setCurrentIndex(self.method.findData(p.method))
        self.form.addRow("Méthode",self.method)
        initial = p.references[0] if p and p.method != "three_points" else None
        if initial is None and len(self.services.selection_items) == 1:
            try:
                selected = capture_reference(self.document,self.services.selection_items[0])
                if selected.kind in {"plane","face"}:
                    initial = selected
            except ValueError:
                pass
        self.base = ReferencePicker(self,"plane",initial)
        self.hinge = ReferencePicker(self,"axis",p.references[1] if p and p.method == "inclined" else None)
        self.points = [ReferencePicker(self,"point",p.references[i] if p and p.method == "three_points" else None,
                                      point=v) for i,v in enumerate(((0.,0.,0.),(10.,0.,0.),(0.,10.,0.)))]
        self.distance = LengthSpinBox(self.services.display_units,-1e9,1e9,p.distance if p else 10.)
        from cadastrophe.plugin_api import EquationSpinBox
        self.angle = EquationSpinBox()
        self.angle.setRange(-360,360)
        self.angle.setDecimals(6)
        self.angle.setSuffix("°")
        self.angle.setValue(p.angle if p else 45.)
        self.reverse = QCheckBox("Inverser le sens")
        self.reverse.setChecked(p.reversed if p else False)
        for label,widget in (("Plan / face",self.base), ("Charnière",self.hinge), ("Décalage",self.distance), ("Angle",self.angle)):
            self.form.addRow(label,widget)
        for i,widget in enumerate(self.points,1):
            self.form.addRow(f"Point {i}",widget)
        self.form.addRow(self.reverse)
        layout.addLayout(self.form)
        hint = QLabel("Activez un champ de référence, puis cliquez dans la vue ou dans l’arbre. Les coordonnées des points peuvent aussi être saisies.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        self.method.currentIndexChanged.connect(self.update_method)
        for signal in (self.distance.valueChanged,self.angle.valueChanged,self.reverse.toggled):
            signal.connect(changed)
        for field in (self.distance,self.angle):
            field.lineEdit().textEdited.connect(changed)
        self.document.selection_changed.connect(self.selection_changed)
        self._connected = True
        self.update_method()
        from cadastrophe.plugin_api import bind_fields
        bind_fields(self, {"distance": self.distance, "angle": self.angle})
        return self.editor

    def update_method(self):
        method = self.method.currentData()
        for widget,visible in ((self.base,method!="three_points"), (self.hinge,method=="inclined"),
                               (self.distance,method=="offset"), (self.angle,method=="inclined"),
                               (self.reverse,method!="three_points")):
            self.form.setRowVisible(widget,visible)
        for widget in self.points:
            self.form.setRowVisible(widget,method=="three_points")
        if self.active_reference is not None and not self.active_reference.isVisible():
            self.active_reference.pick.setChecked(False)
            self.active_reference = None
        self.changed()

    def selection_changed(self,*_):
        self.selection_reference()

    def parameters(self):
        method = self.method.currentData()
        if method == "offset" and not self.distance.hasAcceptableInput() or method == "inclined" and not self.angle.hasAcceptableInput():
            raise ValueError("Saisissez un décalage ou un angle valide.")
        self.distance.interpretText()
        self.angle.interpretText()
        refs = tuple(p.reference() for p in self.points) if method == "three_points" else (
            (self.base.reference(),self.hinge.reference()) if method == "inclined" else (self.base.reference(),))
        return PlaneParameters(method,refs,self.distance.internal_value,self.angle.value(),self.reverse.isChecked())

    def preview_steps(self):
        self.check_snapshot()
        if self.active_reference is not None:
            return None
        params = self.parameters()
        if params != self._key:
            obj = replace(self.feature, parameters=params) if self.feature else DatumPlaneObject(
                unique_name(self.document,"Plan"), None, (.35,.65,.7), plane=REFERENCE_PLANES[0],
                id=self.feature_id, parameters=params)
            graph = {**self.document.objects,obj.id:obj}
            validate_graph(graph)
            frame = resolve_plane(self.document,obj)
            obj = replace(obj,plane=frame,shape=plane_shape(frame),reference_error_message="")
            graph[obj.id] = obj
            affected = affected_bodies(graph,(obj.id,))
            resolved = {}
            from cadastrophe.core.body_history import preview_construction_steps
            self._results,self._prefixes = yield from preview_construction_steps(self.document,graph,affected,
                self.extrusions.evaluator, strict=False,resolved_objects=resolved, cache=self._preview_cache)
            self._objects = (obj,)
            obj = resolved.get(obj.id, obj)
            self._preview_plane = obj.plane
            self.rebuild_warning = next((r.message for r in self._results.values() if r.shape is None),"")
            self.preview_hidden_ids = (*affected,*((obj.id,) if self.feature else ()))
            self.preview_parts = ((obj.shape,obj.color,(),obj.transparency),) + tuple(
                (result.shape,graph[key].color,(),graph[key].transparency) for key,result in self._results.items()
                if result.shape is not None and graph[key].visible)
            self._key = params
        obj, = self._objects
        return SceneObject("Aperçu du plan",self.preview_parts[0][0],obj.color,plane=self._preview_plane)
