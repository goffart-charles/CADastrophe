"""Revolution tools reuse the existing sketch/region picking and session contract."""
from dataclasses import replace

from cadastrophe.plugin_api import EquationSpinBox as QDoubleSpinBox

from cadastrophe.document import ObjectType, SceneObject
from cadastrophe.core.feature_model import Operation, SketchInput, BodyStateRef
from cadastrophe.core.feature_objects import BodyObject, ConstructionFeature, RevolutionFeature
from cadastrophe.core.operation_model import RevolutionParameters
from cadastrophe.core.body_history import preview_construction
from cadastrophe.core.boss import fuse_boss
from .extrusion_tool import ExtrusionSession
from .reference_picker import ReferencePicker
from .recipe_session import RecipePreview, adopted, unique_name


def sketch_input(document, sketch):
    contexts = {source.supports for f in document.objects.values() if isinstance(f, ConstructionFeature)
                for source, _ in f.profile_sections if source.sketch_id == sketch.id}
    if len(contexts) > 1:
        raise ValueError("L’esquisse possède plusieurs états producteurs incompatibles.")
    if contexts:
        return SketchInput(sketch.id, next(iter(contexts)))
    refs = []
    for key in sketch.body_dependencies:
        body = document.objects[key]
        captured = next((ref for ref in sketch.producer_refs if ref.body_id == key), None)
        refs.append(captured or BodyStateRef(key, body.features[-1] if isinstance(body, BodyObject) and body.features else None))
    return SketchInput(sketch.id, tuple(refs))


class RevolutionSession(RecipePreview, ExtrusionSession):
    equation_extent = "angle"
    uses_end_condition = False
    extent_label = "Angle"
    operation_name = "révolution"

    def __init__(self, services, extrusions, profiles, *, cut=False, feature=None):
        ExtrusionSession.__init__(self, services, extrusions, profiles)
        self.initialize(services, extrusions, feature)
        self.is_cut = cut or feature is not None and feature.operation is Operation.CUT
        self.title = "Modifier "+feature.name if feature else "Révolution soustractive" if self.is_cut else "Révolution additive"

    def create_extent_editor(self):
        field = QDoubleSpinBox()
        field.setRange(.000001,360.)
        field.setDecimals(6)
        field.setSuffix("°")
        field.setValue(self.feature.parameters.angle if self.feature else 360.)
        return field

    def add_geometry_fields(self, form, changed):
        self.axis = ReferencePicker(self, "axis", self.feature.parameters.axis if self.feature else None, collected=True)
        form.addRow("Axe", self.axis)

    def create_editor(self, parent, changed):
        editor = ExtrusionSession.create_editor(self, parent, changed)
        self.angle = self.depth
        self.angle.setObjectName("revolutionAngle")
        if self.feature:
            self.choose_source(self.feature.sketch_id)
            if self.feature.parameters.contours:
                self._set_regions(self.feature.parameters.contours)
            self.reverse.setChecked(self.feature.parameters.reversed)
            self.body.hide()
            self.merge.hide()
        if self.source.value is not None:
            self.activate_reference(self.axis)
        return editor

    def activate(self, target):
        if self.active_reference is not None:
            self.active_reference.pick.setChecked(False)
            self.active_reference = None
        self.refresh_reference_guides()
        ExtrusionSession.activate(self, target)

    def clicked(self,x,y):
        return self.pick_reference_at(x,y) if self.active_reference else ExtrusionSession.clicked(self,x,y)

    def _selection_changed(self, *_):
        if not self.selection_reference():
            ExtrusionSession._selection_changed(self)

    def geometry_parameters(self):
        if not self.depth.hasAcceptableInput():
            raise ValueError("Saisissez un angle entre 0° exclu et 360°.")
        self.depth.interpretText()
        return RevolutionParameters(self.axis.reference(), self.depth.value(), self.reverse.isChecked(), self.selected_regions)

    def new_feature(self, source, color, parameters):
        return RevolutionFeature(unique_name(self.document, "Enlèvement par révolution" if self.is_cut else "Révolution"),
            None, color, kind=ObjectType.FEATURE, id=self.feature_id, operation=Operation.NEW_BODY,
            body_id=self.body_id, source=source, parameters=parameters)

    def preview(self):
        self.check_snapshot()
        if self.active_reference is not None:
            return None
        if self._profile_error:
            raise ValueError(self._profile_error)
        if len(self.selected_regions) != 1:
            raise ValueError("Choisissez une seule région fermée dans la vue 3D.")
        params = self.geometry_parameters()
        key = (params, self.source.value, self.merge.isChecked(), self.body.value)
        if key != self._key:
            if self.feature:
                candidate = replace(self.feature, parameters=params,
                                    source=sketch_input(self.document, self.document.objects[self.source.value]))
                self.calculate((candidate,), candidate)
            else:
                sketch = self.document.objects[self.source.value]
                source = sketch_input(self.document, sketch)
                color = (.72,.75,.8)
                feature = self.new_feature(source, color, params)
                from cadastrophe.core.history_position import context_object
                candidates = [obj for original in self.document.objects.values() if (obj := context_object(self.document, original))
                              and obj.kind is ObjectType.BODY and obj.shape is not None]
                if self.body.value is not None:
                    candidates = [obj for obj in candidates if obj.id == self.body.value]
                    if not candidates:
                        raise ValueError("Le corps cible n’est plus disponible.")
                target = None
                if self.is_cut:
                    if len(candidates) != 1:
                        raise ValueError("Choisissez un corps cible unique pour l’enlèvement par "+self.operation_name+".")
                    target = candidates[0]
                elif self.merge.isChecked() and candidates:
                    temporary = BodyObject("Aperçu", None, color, id=self.body_id, features=(feature.id,))
                    graph = {**self.document.objects, temporary.id:temporary, feature.id:feature}
                    result, _ = preview_construction(self.document, graph, (temporary.id,), self.extrusions.evaluator)
                    volume = result[temporary.id].shape
                    touching = []
                    for body in candidates:
                        from OCC.Core.TopAbs import TopAbs_COMPOUND
                        from cadastrophe.core.operation_effects import checked_solids
                        collection = body.shape.ShapeType() == TopAbs_COMPOUND
                        union = fuse_boss(body.shape, volume, allow_disconnected=collection)
                        if collection and union.success and len(checked_solids(union.shape)) > len(checked_solids(body.shape)):
                            continue
                        if union.success or union.diagnostics[0].code == "no_added_material":
                            touching.append(body)
                        elif self.body.value is not None:
                            raise ValueError(union.message)
                    if len(touching) > 1:
                        raise ValueError("Plusieurs corps peuvent fusionner : choisissez la cible.")
                    target = touching[0] if touching else None
                if target is None:
                    body = BodyObject(unique_name(self.document,"Corps"), None, color, id=self.body_id, features=(feature.id,))
                else:
                    body = adopted(target)
                    feature = replace(feature, body_id=body.id, operation=Operation.CUT if self.is_cut else Operation.BOSS)
                    body = replace(body, features=(*body.features, feature.id))
                self.calculate((body,feature), feature)
            self._key = key
        self._result = self._current
        self._feedback()
        return SceneObject("Aperçu de "+self.operation_name, self._current.shape, (.72,.75,.8))

    def commit(self, document, preview):
        RecipePreview.commit(self, document, preview)
        document.set_visible(self.source.value, False)

    def dispose(self):
        if self._connected:
            self.document.selection_changed.disconnect(self._selection_changed)
            self._connected = False
        RecipePreview.dispose(self)
