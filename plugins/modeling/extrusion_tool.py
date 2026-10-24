"""X5 new-body or additive extrusion through the common reserved session."""
from dataclasses import fields, replace
from uuid import uuid4

from PySide6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QCheckBox, QLabel, QComboBox
from .selection_field import SelectionField

from cadastrophe.plugin_api import LengthSpinBox
from cadastrophe.document import SceneObject, ObjectType
from cadastrophe.core.feature_objects import BodyObject, ExtrusionFeature, SketchSourceObject, ConstructionFeature
from cadastrophe.core.body_history import prefix_results
from cadastrophe.core.feature_model import (
    BodyStateRef, SketchInput, ExtrusionParameters, Operation, EvaluationResult, ResultStatus,
)


class ExtrusionSession:
    title = "Ajout par extrusion"
    preview_added_only = False
    preview_opaque = True
    is_cut = False
    uses_end_condition = True
    extent_label = "Profondeur"

    def invalidate_preview(self):
        self._cache_key = None

    def create_extent_editor(self):
        return LengthSpinBox(self.services.display_units, 0., 1e6, 5. if self.is_cut else 20.)

    def add_geometry_fields(self, form, changed):
        pass

    def __init__(self, services, extrusions, profiles):
        self.services, self.extrusions, self.profiles = services, extrusions, profiles
        self.document = services.active_document
        self.original_selection = self.document.selected_ids
        self.body_id, self.feature_id = str(uuid4()), str(uuid4())
        self.editor = None
        self.committed = False
        self._cache_key = self._result = None
        self._connected = False
        self.surface = None
        self.target = "sketch"
        self.regions = ()
        self._faces = None
        self.preview_hidden_ids = ()
        self.preview_parts = None
        self.selected_regions = ()
        self._hover_region = None
        self.condition = None

    def create_editor(self, parent, changed):
        self.changed = changed
        self.editor = QWidget(parent)
        self.editor.setObjectName("extrusionParameters")
        layout = QVBoxLayout(self.editor)
        layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.source = SelectionField("Cliquer puis sélectionner une esquisse")
        self.source.setObjectName("extrusionSketch")
        self.region = SelectionField("Cliquer dans les régions à extruder")
        self.region.setObjectName("extrusionContour")
        self.depth = self.create_extent_editor()
        self.depth.setObjectName("extrusionDepth")
        self.reverse = QCheckBox("Inverser la direction")
        self.reverse.setObjectName("extrusionReverse")
        self.merge = QCheckBox("Fusionner le résultat", self.editor)
        self.merge.setObjectName("extrusionMerge")
        self.body = SelectionField("Automatique · cliquer pour choisir une cible")
        self.body.setObjectName("extrusionBody")
        form.addRow("Esquisse", self.source)
        form.addRow("Contour", self.region)
        self.add_geometry_fields(form, changed)
        if self.is_cut and self.uses_end_condition:
            self.condition = QComboBox()
            self.condition.setObjectName("cutEndCondition")
            self.condition.addItem("Borgne", "blind")
            self.condition.addItem("À travers tout", "through_all")
            form.addRow("Condition de fin", self.condition)
            self.condition.currentIndexChanged.connect(self._condition_changed)
        elif self.uses_end_condition:
            form.addRow("Condition de fin", QLabel("Borgne"))
        form.addRow(self.extent_label, self.depth)
        form.addRow(self.reverse)
        if self.is_cut:
            self.merge.hide()
        else:
            form.addRow(self.merge)
        form.addRow("Corps cible", self.body)
        self.operation = QLabel("Enlèvement de matière" if self.is_cut else "Fusion automatique")
        form.addRow("Opération", self.operation)
        self.merge.setChecked(True)
        layout.addLayout(form)
        hint = QLabel("Cliquez à l’intérieur des zones dans la vue 3D pour les ajouter à l’extrusion. "
                      "Cliquez à nouveau pour retirer une zone. "
                      "Milieu : rotation · Ctrl+milieu : déplacement · Molette : zoom.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        self.source.activated.connect(lambda: self.activate("sketch"))
        self.region.activated.connect(lambda: self.activate("contour"))
        self.source.cleared.connect(lambda: self.choose_source(None))
        self.region.cleared.connect(lambda: self.choose_region(None))
        self.body.activated.connect(lambda: self.activate("body"))
        self.body.cleared.connect(lambda: self.choose_body(None))
        self.merge.toggled.connect(self._merge_changed)
        self.depth.valueChanged.connect(changed)
        self.depth.lineEdit().textEdited.connect(changed)
        self.reverse.toggled.connect(changed)
        selected = [key for key in self.original_selection
                    if isinstance(self.document.objects.get(key), SketchSourceObject)]
        if len(selected) == 1:
            self.choose_source(selected[0])
        else:
            self.choose_source(None)
        self.document.selection_changed.connect(self._selection_changed)
        self._connected = True
        self.editor.setFocusProxy(self.source if self.source.value is None else self.depth)
        from cadastrophe.plugin_api import bind_fields
        bind_fields(self, {getattr(self, "equation_extent", "depth"): self.depth})
        return self.editor

    def attach(self, surface):
        self.surface = surface
        self._feedback()

    def activate(self, target):
        previous = self.target
        self.target = target
        self.source.set_active(target == "sketch")
        self.region.set_active(target == "contour")
        self.body.set_active(target == "body")
        self.hovered(None, None)
        if previous != target:
            self.changed()

    def clicked(self, x, y):
        """Only completed left clicks are routed here; native navigation is intact."""
        try:
            self._pick_error = ""
            return self._clicked(x, y)
        except (ValueError, RuntimeError) as exc:
            self._pick_error = str(exc)
            self.changed()
            return True

    def _clicked(self, x, y):
        if self.target == "body":
            key = self.surface.detect_object(x, y)
            if (obj := self.document.objects.get(key)) is not None and obj.kind is ObjectType.BODY:
                self.choose_body(key)
            return True
        if self.target == "sketch":
            key = self.surface.detect_object(x, y)
            if isinstance(self.document.objects.get(key), SketchSourceObject):
                self.choose_source(key)
            return True  # Never reinterpret this click as another selection.
        key = self._region_at(x, y)
        if key is not None:
            keys = tuple(item for item in self.selected_regions if item != key) if key in self.selected_regions else (*self.selected_regions, key)
            self._set_regions(keys)
        return True

    def _region_at(self, x, y):
        if self.source.value is None or self._profile_error or self.surface is None:
            return None
        sketch = self.document.objects[self.source.value].sketch
        hit = sketch.support.frame.intersect(self.surface.screen_ray(x, y))
        if hit is None or not self.surface.visible_points((hit,))[0]:
            return None
        if self._faces is None:
            self._faces = self.profiles.to_region_faces(self.document, self.source.value)
        from OCC.Core.BRepClass import BRepClass_FaceClassifier
        from OCC.Core.TopAbs import TopAbs_IN, TopAbs_ON
        from OCC.Core.gp import gp_Pnt
        boundaries = []
        for region in self._faces.regions:
            state = BRepClass_FaceClassifier(region.face, gp_Pnt(*hit.tuple()), self._faces.tolerance).State()
            if state == TopAbs_IN:
                return region.id
            if state == TopAbs_ON:
                boundaries.append(region.id)
        return boundaries[0] if len(boundaries) == 1 else None

    def hovered(self, x, y):
        if self.surface is None:
            return
        key = None
        self._hover_region = None
        try:
            if x is not None:
                if self.target == "sketch":
                    detected = self.surface.detect_object(x, y)
                    if isinstance(self.document.objects.get(detected), SketchSourceObject):
                        key = detected
                elif self.target == "contour":
                    self._hover_region = self._region_at(x, y)
                elif self.target == "body":
                    self.surface.detect_object(x, y, redraw=True)
        except (ValueError, RuntimeError):
            pass  # Hovering an invalid profile is harmless; preview reports why.
        self.surface.highlight_object(key)
        self._feedback()

    def _feedback(self):
        if self.surface is None:
            return
        selected, hovered = (), None
        if self.target == "contour" and self._faces is not None:
            selected = tuple(region.face for region in self._faces.regions if region.id in self.selected_regions)
            hovered = next((region.face for region in self._faces.regions if region.id == self._hover_region), None)
        self.surface.highlight_regions(selected, hovered, outline=self.is_cut and self._result is not None)

    def _selection_changed(self, *_):
        selected = self.document.selected_ids
        if self.target == "body":
            if len(selected) == 1 and self.document.objects[selected[0]].kind is ObjectType.BODY:
                self.choose_body(selected[0])
            return
        if len(selected) == 1 and selected[0] != self.source.value and isinstance(self.document.objects.get(selected[0]), SketchSourceObject):
            self.choose_source(selected[0])

    def choose_source(self, key):
        previous = self.source.value
        obj = self.document.objects.get(key)
        if key is not None and not isinstance(obj, SketchSourceObject):
            raise ValueError("Sélectionnez une esquisse de ce document.")
        self.source.set_value(key, obj.name if obj is not None else "")
        self.region.set_value(None)
        self.selected_regions = ()
        self.regions, self._faces = (), None
        self._profile_error = "Sélectionnez une esquisse fermée."
        self._pick_error = ""
        # A planar face provides an unambiguous target, even in a multi-body file.
        # A sketch on a reference plane never guesses the first/nearest body.
        support = getattr(getattr(obj, "sketch", None), "support", None)
        face_body = support.plane_id if support is not None and support.face_index is not None else None
        self.body.set_value(face_body, self.document.objects[face_body].name if face_body in self.document.objects else "")
        if self.is_cut:
            self.reverse.setChecked(face_body is not None)
        if key is not None:
            try:
                report = self.profiles.analyze_regions(self.document, key)
                if report.valid:
                    self._profile_error = ""
                    self.regions = report.regions
                    if len(self.regions) == 1:
                        self.choose_region(self.regions[0][0].id)
                else:
                    self._profile_error = next((d.message for d in report.diagnostics if d.severity == "error"),
                                               "L’esquisse ne définit aucune région fermée.")
            except (ValueError, RuntimeError) as exc:
                self._profile_error = str(exc)
        if key is not None:
            self.document.select_objects((key,))
        elif previous is not None and self.document.selected_ids == (previous,):
            self.document.select_objects(())
        self.activate("contour" if key is not None else "sketch")
        self.changed()

    def _condition_changed(self, *_):
        self.depth.setEnabled(self.condition.currentData() == "blind")
        self.changed()

    def choose_region(self, key):
        self._set_regions(() if key is None else (key,))

    def _set_regions(self, keys):
        self._pick_error = ""
        labels = []
        for key in keys:
            found = next(((i, holes) for i, (outer, holes) in enumerate(self.regions, 1) if outer.id == key), None)
            if found is None:
                raise ValueError("Cette région n’appartient pas à l’esquisse sélectionnée.")
            index, holes = found
            labels.append(f"Région {index}" + (f" · {len(holes)} ouverture(s)" if holes else ""))
        self.selected_regions = tuple(keys)
        value = keys[0] if len(keys) == 1 else tuple(keys) if keys else None
        self.region.set_value(value, ", ".join(labels))
        self.region.edit.setCursorPosition(0)
        self._feedback()
        self.changed()

    def _merge_changed(self, checked):
        self.body.setEnabled(checked)
        self.operation.setText("Fusion automatique" if checked else "Nouveau corps")
        self._pick_error = ""
        if self.target == "body":
            self.activate("contour")
        self.changed()

    def choose_body(self, key):
        obj = self.document.objects.get(key)
        if obj is not None:
            from cadastrophe.core.history_position import context_object
            obj = context_object(self.document, obj)
        if key is not None and (obj is None or obj.kind is not ObjectType.BODY or obj.shape is None):
            raise ValueError("Sélectionnez un corps solide disponible.")
        self.body.set_value(key, obj.name if obj is not None else "")
        self._pick_error = ""
        self.activate("contour")
        self.changed()

    def preview(self):
        self.preview_hidden_ids = ()
        self.preview_parts = None
        if self.merge.isChecked() and self.target == "body":
            return None
        if self._pick_error:
            raise ValueError(self._pick_error)
        if self._profile_error:
            raise ValueError(self._profile_error)
        if not self.selected_regions:
            raise ValueError("Choisissez une ou plusieurs régions à extruder ; leurs ouvertures seront conservées.")
        end_condition = self.condition.currentData() if self.is_cut else "blind"
        if end_condition == "blind" and not self.depth.hasAcceptableInput():
            raise ValueError(self.depth.equation_error or "Saisissez une profondeur positive valide.")
        if end_condition == "blind":
            self.depth.interpretText()
        depth = self.depth.internal_value if end_condition == "blind" else None
        reverse = self.reverse.isChecked()
        merge, target_id = self.merge.isChecked(), self.body.value
        key = (tuple((key, id(obj)) for key, obj in self.document.objects.items()),
               self.source.value, self.selected_regions, depth, reverse, merge, target_id, end_condition)
        if key != self._cache_key:
            self._insertion_prepared = None
            if self.is_cut:
                self._result = self.extrusions.cut_plan(self.document, self.source.value, depth,
                    target_id=target_id, reversed=reverse, contour_ids=self.selected_regions, end_condition=end_condition)
            else:
                self._result = self.extrusions.plan(self.document, self.source.value, depth,
                    merge=merge, target_id=target_id, reversed=reverse, contour_ids=self.selected_regions)
            self._cache_key = key
        self.preview_hidden_ids = tuple(part.target_id for part in self._result.parts if part.target_id is not None)
        self.preview_parts = tuple((part.shape,
            self.document.objects[part.target_id].color if part.target_id is not None else (.72, .75, .8),
            part.base_faces, self.document.objects[part.target_id].transparency if part.target_id is not None else 0.)
            for part in self._result.parts)
        count = sum(part.target_id is None for part in self._result.parts)
        self.operation.setText("Enlèvement de matière" if self.is_cut else ("Ajout de matière" if self.preview_hidden_ids else "")
            + (" + " if self.preview_hidden_ids and count else "")
            + (f"{count} nouveau(x) corps" if count else ""))
        if self.is_cut:
            self._feedback()  # Filled profile sheets would visually cap a previewed hole.
        preview = SceneObject("Aperçu de l’enlèvement" if self.is_cut else "Aperçu du bossage",
                              self._result.shape, (.72, .75, .8), id=self.body_id)
        if self.document.history_context.position is not None:
            if self._insertion_prepared is None:
                self._insertion_prepared = self.construction(preview)
            self.rebuild_warning = next((r.message for r in self._insertion_prepared[1].values() if r.shape is None), "")
        return preview

    def construction(self, preview):
        document = self.document
        sketch = document.objects[self.source.value]
        supports = []
        for key in sketch.body_dependencies:
            captured = next((ref for ref in sketch.producer_refs if ref.body_id == key), None)
            from cadastrophe.core.history_position import current_state_ref
            producer = captured.producer_id if captured else current_state_ref(document, key).producer_id
            supports.append(BodyStateRef(key, producer))
        contexts = {source.supports for obj in document.objects.values() if isinstance(obj, ConstructionFeature)
                    for source, _ in obj.profile_sections if source.sketch_id == sketch.id}
        if len(contexts) > 1:
            raise ValueError("Support ambigu : plusieurs états producteurs pour cette esquisse.")
        if contexts:
            supports = next(iter(contexts))  # Reusing a sketch retains its original support, never a later face.
        index = 1 + sum(isinstance(obj, ExtrusionFeature) for obj in document.objects.values())
        prefix = "Enlèvement-Extrusion" if self.is_cut else "Bossage-Extrusion"
        end_condition = self.condition.currentData() if self.is_cut else "blind"
        names = {obj.name for obj in document.objects.values()}
        objects, results, prefixes = [], {}, {}
        for ordinal, part in enumerate(self._result.parts):
            target = document.objects[part.target_id] if part.target_id is not None else None
            merge = target is not None
            body_id = target.id if merge else self.body_id if ordinal == 0 else str(uuid4())
            while f"{prefix}{index}" in names or f"Corps{index}" in names:
                index += 1
            feature = ExtrusionFeature(f"{prefix}{index}", None, preview.color,
                id=self.feature_id if ordinal == 0 else str(uuid4()), kind=ObjectType.FEATURE,
                body_id=body_id, operation=Operation.CUT if self.is_cut else Operation.BOSS if merge else Operation.NEW_BODY,
                source=SketchInput(sketch.id, tuple(supports)),
                parameters=ExtrusionParameters(depth=self.depth.internal_value if end_condition == "blind" else None,
                    end_condition=end_condition, reversed=self.reverse.isChecked(),
                    merge_result=merge and not self.is_cut, contours=part.contours))
            states = {}
            if merge:
                if isinstance(target, BodyObject):
                    body = replace(target, features=(*target.features, feature.id))
                    states = dict(prefix_results(document, target.id))
                else:
                    body = BodyObject(**{f.name: getattr(target, f.name) for f in fields(SceneObject)},
                        source_shape=target.shape, source_kind="static", features=(feature.id,))
                before = BodyStateRef(target.id, target.features[-1] if isinstance(target, BodyObject) and target.features else None)
                if before not in states:
                    states[before] = EvaluationResult(ResultStatus.READY, target.shape)
            else:
                body = BodyObject(f"Corps{index}", None, preview.color, id=body_id, features=(feature.id,))
            objects.extend((body, feature))
            results[body.id] = EvaluationResult(ResultStatus.READY, part.shape)
            prefixes[body.id] = states
            names.update((body.name, feature.name))
        from cadastrophe.core.body_history import preview_construction, affected_bodies
        candidate = {**document.objects, **{o.id: o for o in objects}}
        results, prefixes = preview_construction(document, candidate, affected_bodies(candidate, (o.id for o in objects)),
                                                 self.extrusions.evaluator, strict=False)
        if document.history_context.position is not None:
            for obj in objects:
                if isinstance(obj, ConstructionFeature):
                    state = prefixes[obj.body_id][BodyStateRef(obj.body_id, obj.id)]
                    if state.status is not ResultStatus.READY:
                        raise ValueError(state.message)
        from cadastrophe.plugin_api import equation_recipe
        objects = tuple(equation_recipe(self, o) if hasattr(o, "parameters") else o for o in objects)
        return objects, results, prefixes

    def commit(self, document, preview):
        if document is not self.document:
            raise ValueError("Le document source a changé.")
        objects, results, prefixes = self.construction(preview)
        document.update_construction(objects, title=self.title, results=results, prefixes=prefixes)
        document.set_visible(self.source.value, False)
        document.select_objects(results)
        self.committed = True

    def dispose(self):
        if self._connected:
            self.document.selection_changed.disconnect(self._selection_changed)
            self._connected = False
        if not self.committed:
            self.document.select_objects(key for key in self.original_selection if key in self.document.objects)
        self._result = self._cache_key = None
        self._insertion_prepared = None
        self.surface = self._faces = None
        self.editor = None


class CutSession(ExtrusionSession):
    title = "Découpe extrudée"
    is_cut = True
    preview_added_only = False
    preview_opaque = True
