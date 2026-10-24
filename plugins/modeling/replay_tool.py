"""One panel/session for mirror, linear and circular function effects."""
from dataclasses import replace

from PySide6.QtWidgets import QWidget,QVBoxLayout,QFormLayout,QSpinBox,QDoubleSpinBox,QCheckBox,QLabel

from cadastrophe.plugin_api import LengthSpinBox
from cadastrophe.document import SceneObject,ObjectType
from cadastrophe.core.feature_model import Operation
from cadastrophe.core.feature_objects import ConstructionFeature,ReplayFeature
from cadastrophe.core.operation_model import GeometryReference,ReplayParameters
from .selection_field import SelectionField
from .reference_picker import ReferencePicker
from .recipe_session import RecipePreview,adopted,unique_name


TITLES = {Operation.MIRROR:"Symétrie",Operation.LINEAR_PATTERN:"Répétition linéaire",Operation.CIRCULAR_PATTERN:"Répétition circulaire"}


class ReplaySession(RecipePreview):
    def __init__(self,services,extrusions,operation,feature=None):
        self.initialize(services,extrusions,feature)
        self.operation = operation
        self.title = "Modifier "+feature.name if feature else TITLES[operation]
        self.source_ids = feature.parameters.sources if feature else tuple(k for k in self.original_selection
            if isinstance(self.document.objects.get(k),ConstructionFeature) and self.document.objects[k].replayable)
        self._selecting_sources = not self.source_ids
        self._hover_source = None

    def create_editor(self,parent,changed):
        self.changed = changed
        self.editor = QWidget(parent)
        layout,form = QVBoxLayout(self.editor),QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.sources = SelectionField("Cliquer ici, puis sur une face de la fonction")
        self.sources.setMinimumWidth(190)
        self.sources.activated.connect(self.activate_sources)
        self.sources.cleared.connect(self.clear_sources)
        form.addRow("Fonctions sources",self.sources)
        p = self.feature.parameters if self.feature else None
        kind = "plane" if self.operation is Operation.MIRROR else "axis"
        default = p.reference if p else next((GeometryReference("plane",o.id) for o in self.document.objects.values() if o.plane is not None),None) if kind == "plane" else GeometryReference("axis",values=(0,0,0,0,0,1))
        self.reference = ReferencePicker(self,kind,default,collected=True)
        form.addRow("Plan" if kind == "plane" else "Direction" if self.operation is Operation.LINEAR_PATTERN else "Axe",self.reference)
        from cadastrophe.plugin_api import EquationSpinBox, EquationIntSpinBox
        self.count = EquationIntSpinBox()
        self.count.setRange(2,256)
        self.count.setMinimumWidth(80)
        self.count.setValue(p.count if p else 2)
        self.spacing = LengthSpinBox(self.services.display_units,1e-7,1e9,p.spacing if p else 20.)
        self.angle = EquationSpinBox()
        self.angle.setRange(.000001,360.)
        self.angle.setDecimals(6)
        self.angle.setSuffix("°")
        self.angle.setValue(p.angle if p else 360.)
        self.full = QCheckBox("Répartition uniforme sur 360°")
        angle_driven = self.feature is not None and any(e.target == "angle" for e in self.feature.expressions)
        self.full.setChecked(p.angle == 360 and not angle_driven if p else True)
        self.reverse = QCheckBox("Inverser le sens")
        self.reverse.setChecked(p.reversed if p else False)
        self.merge = QCheckBox("Fusionner le résultat")
        self.merge.setChecked(p.merge_result if p else True)
        self.merge.setToolTip("Décochez pour autoriser plusieurs solides fermés dans le corps de construction.")
        form.addRow(self.merge)
        if self.operation is not Operation.MIRROR:
            form.addRow("Nombre total (original inclus)",self.count)
            if self.operation is Operation.LINEAR_PATTERN:
                form.addRow("Espacement",self.spacing)
            else:
                form.addRow(self.full)
                form.addRow("Angle total",self.angle)
            form.addRow(self.reverse)
        layout.addLayout(form)
        hint = QLabel("Cliquez une face de la fonction dans la vue 3D, puis choisissez l’axe, la direction ou le plan. Ctrl+clic permet de choisir plusieurs fonctions ; un second clic retire une fonction. L’arbre reste disponible en complément.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        self.full.toggled.connect(lambda state:self.angle.setEnabled(not state))
        self.angle.setEnabled(not self.full.isChecked())
        for signal in (self.count.valueChanged,self.spacing.valueChanged,self.angle.valueChanged,self.full.toggled,self.reverse.toggled,self.merge.toggled):
            signal.connect(self.parameters_changed)
        for field in (self.count,self.spacing,self.angle):
            field.lineEdit().textEdited.connect(self.parameters_changed)
        self.document.selection_changed.connect(self.selection_changed)
        self._connected = True
        self.set_sources(self.source_ids)
        self.sources.set_active(self._selecting_sources)
        from cadastrophe.plugin_api import bind_fields
        bind_fields(self, {"count": self.count, "spacing": self.spacing, "angle": self.angle})
        return self.editor

    def activate_reference(self,picker):
        self._selecting_sources = False
        if self.surface is not None:
            self.surface.highlight_regions()
        super().activate_reference(picker)

    def parameters_changed(self,*_):
        self._selecting_sources = False
        self.sources.set_active(False)
        if self.surface is not None:
            self.surface.highlight_regions()
        self.changed()

    def activate_sources(self):
        if self.active_reference:
            self.active_reference.pick.setChecked(False)
        self.active_reference = None
        self.refresh_reference_guides()
        self._selecting_sources = True
        self.sources.set_active(True)
        self.changed()

    def clear_sources(self):
        self.set_sources(())
        self.activate_sources()

    def clicked(self,x,y):
        if self.active_reference is not None:
            return self.pick_reference_at(x,y)
        if not self._selecting_sources:
            self.activate_sources()
            return True
        from .feature_selection import face_sources
        try:
            item = self.surface.pick_face(x,y)
            if item is None:
                return True
            key = face_sources(self.document,item,self.surface.face_point(item,x,y),before=self.feature)
            keys = tuple(k for k in self.source_ids if k != key) if key in self.source_ids else (*self.source_ids,key)
            self.set_sources(keys)
            self._feedback()
            if keys and not self.surface.ctrl:
                self.activate_reference(self.reference)
        except (ValueError,RuntimeError) as exc:
            self._pick_error = str(exc)
            self.changed()
        return True

    def _feedback(self):
        if self.surface is not None:
            from .feature_selection import selected_faces
            selected = selected_faces(self.document,self.source_ids)
            hovered = selected_faces(self.document,(self._hover_source,)) if self._hover_source else ()
            self.surface.highlight_regions(selected,hovered[0] if hovered else None)

    def hovered(self,x,y):
        if not self._selecting_sources or self.surface is None:
            return
        from .feature_selection import face_sources
        self._hover_source = None
        if x is not None:
            try:
                item = self.surface.pick_face(x,y)
                if item is not None:
                    self._hover_source = face_sources(self.document,item,self.surface.face_point(item,x,y),before=self.feature)
            except (ValueError,RuntimeError):
                pass
        self._feedback()

    def set_sources(self,keys):
        self.source_ids = tuple(dict.fromkeys(keys))
        self.sources.set_value(self.source_ids or None,", ".join(self.document.objects[k].name for k in self.source_ids))
        self._pick_error = ""
        self._hover_source = None
        self.changed()

    def selection_changed(self,*_):
        if self.selection_reference():
            return
        if any(not isinstance(self.document.objects[k],ConstructionFeature) or not self.document.objects[k].replayable for k in self.document.selected_ids):
            self._pick_error = "Cette fonction n’est pas rejouable : choisissez des extrusions, révolutions, répétitions ou symétries."
            self.changed()
            return
        selected = tuple(k for k in self.document.selected_ids if isinstance(self.document.objects[k],ConstructionFeature) and self.document.objects[k].replayable)
        if selected:
            self._selecting_sources = False
            self.sources.set_active(False)
            self.set_sources(selected)

    def preview_steps(self):
        self.check_snapshot()
        if self.active_reference is not None or self._selecting_sources:
            return None
        if not self.source_ids:
            raise ValueError("Cliquez une face de la fonction à reproduire dans la vue 3D.")
        sources = [self.document.objects[k] for k in self.source_ids]
        if len({f.body_id for f in sources}) != 1:
            raise ValueError("Toutes les fonctions sources doivent appartenir au même corps.")
        body = self.document.objects[sources[0].body_id]
        keys = tuple(k for k in body.features if k in self.source_ids)
        if self.operation is not Operation.MIRROR:
            fields = (self.count,self.spacing) if self.operation is Operation.LINEAR_PATTERN else (self.count,) if self.full.isChecked() else (self.count,self.angle)
            if any(not field.hasAcceptableInput() for field in fields):
                raise ValueError("Saisissez un nombre d’occurrences, un espacement ou un angle valide.")
            self.count.interpretText()
        self.spacing.interpretText()
        self.angle.interpretText()
        params = ReplayParameters(keys,self.reference.reference(),self.count.value(),self.spacing.internal_value,
                                  360. if self.full.isChecked() else self.angle.value(),self.reverse.isChecked(),self.merge.isChecked())
        if params != self._key:
            if self.feature:
                if body.id != self.feature.body_id:
                    raise ValueError("La fonction doit conserver son corps propriétaire.")
                feature = replace(self.feature,parameters=params)
                objects = (feature,)
            else:
                feature = ReplayFeature(unique_name(self.document,TITLES[self.operation]),None,body.color,
                    id=self.feature_id,kind=ObjectType.FEATURE,body_id=body.id,operation=self.operation,parameters=params)
                body = adopted(body)
                objects = (replace(body,features=(*body.features,feature.id)),feature)
            yield from self.calculate_steps(objects,feature)
            self._key = params
        return SceneObject("Aperçu du motif",self._current.shape,body.color)
