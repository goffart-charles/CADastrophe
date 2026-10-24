"""Reserved edge fillet/chamfer tool, common preview, history and persistence."""
from dataclasses import fields, replace
from uuid import uuid4

from PySide6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QLabel, QComboBox, QCheckBox, QDoubleSpinBox, QPushButton

from cadastrophe.document import SceneObject, ObjectType
from cadastrophe.plugin_api import LengthSpinBox
from cadastrophe.core.feature_objects import BodyObject, EdgeFeature
from cadastrophe.core.feature_model import Operation, EdgeParameters, BodyStateRef, EvaluationResult, ResultStatus
from cadastrophe.core.body_history import prefix_results, result_at, preview_feature_steps, affected_bodies, rollback_hidden_ids
from cadastrophe.core.computation import complete, cooperative_required
from .edge_geometry import apply_edges, edge_reference, edges_of, resolve_edges


class EdgeSession:
    @property
    def cooperative_preview(self):
        return cooperative_required(self.document.objects)

    def invalidate_preview(self):
        self._key = None

    preview_opaque = True

    def __init__(self, services, extrusions, operation, feature=None):
        self.services, self.extrusions, self.operation, self.feature = services, extrusions, operation, feature
        self.document = services.active_document
        self.title = ("Modifier " + feature.name) if feature else "Congé" if operation is Operation.FILLET else "Chanfrein"
        self.snapshot = dict(self.document.objects)
        self.original_selection = self.document.selected_ids
        self.body_id = feature.body_id if feature else None
        self.references = feature.parameters.edges if feature else ()
        self.topology_references = feature.parameters.references if feature else ()
        self.surface = None
        self._key = None
        self._pick_error = ""
        self.preview_hidden_ids, self.preview_parts = (), None
        self.committed = False
        self.commit_error = ""
        self.feature_id = feature.id if feature else str(uuid4())
        self._preview_cache = {}
        if feature is not None and self.topology_references:
            try:
                from cadastrophe.core.native_naming import resolve
                base, topology = self.base_shape(), self.base_state().topology
                self.references = tuple(edge_reference(base, resolve(base, r, topology)) for r in self.topology_references)
            except ValueError:
                pass  # Keep the broken reference available for explicit repair.
        if feature is None:
            for item in services.selection_items:
                if item.topology == "Edge" and item.shape is not None:
                    self.add_edge(item.object_id, item.shape)

    def base_shape(self):
        body = self.document.objects.get(self.body_id)
        if body is None:
            raise ValueError("Sélectionnez une ou plusieurs arêtes d’un même corps dans la vue 3D.")
        if self.feature is None:
            return self.base_state().shape
        index = body.features.index(self.feature.id)
        ref = BodyStateRef(body.id, body.features[index-1] if index else None)
        state = result_at(self.document, ref)
        if state.status is not ResultStatus.READY:
            raise ValueError("L’état précédant cette fonction est indisponible.")
        return state.shape

    def add_edge(self, body_id, edge):
        obj = self.document.objects.get(body_id)
        if obj is not None:
            from cadastrophe.core.history_position import context_object
            obj = context_object(self.document, obj)
        if (obj is None or obj.kind is not ObjectType.BODY
                or (obj.shape is None and (self.feature is None or body_id != self.body_id))):
            raise ValueError("L’arête doit appartenir à un corps solide disponible.")
        if self.body_id is not None and body_id != self.body_id and self.references:
            raise ValueError("Toutes les arêtes d’une fonction doivent appartenir au même corps.")
        self.body_id = body_id
        base = self.base_shape()
        if not any(edge.IsSame(candidate) for candidate in edges_of(base)):
            raise ValueError("Choisissez une arête de l’état source de cette fonction.")
        ref = edge_reference(base, edge)
        from cadastrophe.core.native_naming import capture
        if not self.topology_references and self.references:
            self.topology_references = tuple(capture(base, e, "Edge", self.base_state().topology)
                                             for e in resolve_edges(base, self.references))
        if ref in self.references:
            index = self.references.index(ref)
            self.references = self.references[:index]+self.references[index+1:]
            self.topology_references = self.topology_references[:index]+self.topology_references[index+1:]
        else:
            self.references = (*self.references, ref)
            self.topology_references = (*self.topology_references, capture(base, edge, "Edge", self.base_state().topology))

    def base_state(self):
        body = self.document.objects[self.body_id]
        if self.feature is None:
            from cadastrophe.core.history_position import current_state_ref
            return result_at(self.document, current_state_ref(self.document, body.id))
        index = body.features.index(self.feature.id) if self.feature else len(getattr(body, "features", ()))
        ref = BodyStateRef(body.id, body.features[index-1] if index else None)
        return result_at(self.document, ref)

    def pick_shape(self, object_id):
        if object_id == self.body_id:
            return self.base_shape()
        from cadastrophe.core.history_position import context_object
        return context_object(self.document, self.document.objects[object_id]).shape

    def clear_selection(self):
        self.references = self.topology_references = ()
        self._pick_error = ""
        self._update()

    def create_editor(self, parent, changed):
        self.changed = changed
        self.editor = QWidget(parent)
        self.editor.setObjectName("edgeFeatureEditor")
        layout, form = QVBoxLayout(self.editor), QFormLayout()
        params = self.feature.parameters if self.feature else None
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        form.addRow("Arêtes", self.summary)
        self.clear_button = QPushButton("Effacer la sélection")
        self.clear_button.clicked.connect(self.clear_selection)
        form.addRow(self.clear_button)
        self.mode = QComboBox()
        modes = (("Rayon constant", "symmetric"), ("Rayon variable (début/fin)", "variable")) if self.operation is Operation.FILLET else (
            ("Distance égale", "symmetric"), ("Deux distances", "two_distances"), ("Distance et angle", "distance_angle"))
        for label, value in modes:
            self.mode.addItem(label, value)
        if params:
            self.mode.setCurrentIndex(self.mode.findData(params.mode))
        form.addRow("Méthode", self.mode)
        self.size = LengthSpinBox(self.services.display_units, 1e-7, 1e6, params.size if params else 2.)
        self.size2 = LengthSpinBox(self.services.display_units, 1e-7, 1e6, params.size2 if params else 2.)
        self.size.setObjectName("edgeSize")
        self.size2.setObjectName("edgeSize2")
        form.addRow("Rayon / Distance 1", self.size)
        form.addRow("Rayon fin / Distance 2", self.size2)
        from cadastrophe.plugin_api import EquationSpinBox
        self.angle = EquationSpinBox()
        self.angle.setRange(.01, 89.99)
        self.angle.setSuffix("°")
        self.angle.setValue(params.angle if params else 45.)
        form.addRow("Angle", self.angle)
        self.tangent = QCheckBox("Propagation tangente")
        self.tangent.setChecked(params.tangent if params else True)
        self.flip = QCheckBox("Inverser les côtés / le sens")
        self.flip.setChecked(params.flip if params else False)
        form.addRow(self.tangent)
        form.addRow(self.flip)
        layout.addLayout(form)
        hint = QLabel("Cliquez sur les arêtes dans la vue pour les ajouter ou les retirer. Modifiez les paramètres, puis validez. Échap annule.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        def update():
            self.size2.setEnabled(self.mode.currentData() in ("two_distances", "variable"))
            self.angle.setEnabled(self.mode.currentData() == "distance_angle")
            self.summary.setText(f"{len(self.references)} arête(s)" + (f" · {self.document.objects[self.body_id].name}" if self.body_id else ""))
            self._key = None
            changed()
        self._update = update
        self.mode.currentIndexChanged.connect(update)
        for control in (self.size, self.size2, self.angle):
            control.valueChanged.connect(update)
            control.lineEdit().textEdited.connect(update)
        self.tangent.toggled.connect(update)
        self.flip.toggled.connect(update)
        update()
        from cadastrophe.plugin_api import bind_fields
        bind_fields(self, {"size": self.size, "size2": self.size2, "angle": self.angle})
        return self.editor

    def attach(self, surface):
        self.surface = surface

    def clicked(self, x, y):
        try:
            self._pick_error = ""
            item = self.surface.pick_edge(x, y)
            if item:
                self.add_edge(item.object_id, item.shape)
                self._update()
        except (ValueError, RuntimeError) as exc:
            self._pick_error = str(exc)
            self.changed()
        return True

    def hovered(self, x, y):
        if self.surface is None or x is None:
            return
        try:
            item = self.surface.pick_edge(x, y)
            if self.topology_references:
                from cadastrophe.core.native_naming import resolve
                selected = tuple(resolve(self.base_shape(), ref, self.base_state().topology) for ref in self.topology_references)
            else:
                selected = resolve_edges(self.base_shape(), self.references) if self.references else ()
            self.surface.highlight_regions(selected, item.shape if item else None, outline=True)
        except (ValueError, RuntimeError):
            self.surface.highlight_regions()

    def parameters(self):
        for field in (self.size, self.size2, self.angle):
            if field.isEnabled() and not field.hasAcceptableInput():
                raise ValueError(field.equation_error or "Saisissez des paramètres valides.")
            field.interpretText()
        return EdgeParameters(self.references, self.size.internal_value, self.size2.internal_value,
                              self.mode.currentData(), self.angle.value(), self.tangent.isChecked(), self.flip.isChecked(),
                              self.topology_references)

    def preview(self):
        return complete(self.preview_steps())

    def preview_steps(self):
        try:
            return (yield from self._preview_steps())
        except (ValueError, RuntimeError) as exc:
            if self.body_id is None:
                raise
            # The valid preceding prefix remains selectable to repair a lost
            # edge, even when this body's final result is currently an error.
            from cadastrophe.core.native_naming import subshapes
            base, body = self.base_shape(), self.document.objects[self.body_id]
            self.commit_error = str(exc)
            self._key = None
            self.preview_hidden_ids = rollback_hidden_ids(self.document.objects, self.feature) if self.feature else (self.body_id,)
            self.preview_parts = ((base, body.color, subshapes(base, "Face"), body.transparency),)
            return SceneObject("État source — référence à corriger", base, body.color)

    def _preview_steps(self):
        self.preview_hidden_ids, self.preview_parts = (), None
        if self._pick_error:
            raise ValueError(self._pick_error)
        if self.services.active_document is not self.document or dict(self.document.objects) != self.snapshot:
            raise ValueError("Le document source a changé.")
        base, params = self.base_shape(), self.parameters()
        if params != self._key:
            self._shape, self._base_faces = apply_edges(base, self.operation, params, with_base_faces=True,
                                                       topology=self.base_state().topology)
            self.commit_error = ""
            if self.feature:
                self._final_results, self._final_prefixes = yield from preview_feature_steps(
                    self.document, replace(self.feature, parameters=params), self.extrusions.evaluator,
                    strict=False, cache=self._preview_cache)
                if len(self.document.configurations) > 1 or self.document.configuration_state.common_values:
                    state = self._final_prefixes[self.body_id][BodyStateRef(self.body_id, self.feature_id)]
                    if state.status is not ResultStatus.READY:
                        raise ValueError(state.message)
                    from .recipe_session import inherited_faces
                    self._shape, self._base_faces = state.shape, inherited_faces(state, self.feature_id)
                self.rebuild_warning = next((r.message for r in self._final_results.values() if r.shape is None), "")
            elif self.document.history_context.position is not None or self.cooperative_preview:
                from cadastrophe.core.body_history import preview_construction_steps
                body = self.document.objects[self.body_id]
                from .recipe_session import adopted, unique_name
                feature = EdgeFeature(unique_name(self.document, "Congé" if self.operation is Operation.FILLET else "Chanfrein"),
                    None, body.color, id=self.feature_id, kind=ObjectType.FEATURE, body_id=body.id,
                    operation=self.operation, parameters=params)
                body = replace(adopted(body), features=(*getattr(body, "features", ()), feature.id))
                graph = {**self.document.objects, body.id: body, feature.id: feature}
                self._final_results, self._final_prefixes = yield from preview_construction_steps(self.document, graph,
                    affected_bodies(graph, (feature.id,)), self.extrusions.evaluator, strict=False, cache=self._preview_cache)
                state = self._final_prefixes[body.id][BodyStateRef(body.id, feature.id)]
                if state.status is not ResultStatus.READY:
                    raise ValueError(state.message)
                self.rebuild_warning = next((r.message for r in self._final_results.values() if r.shape is None), "")
            self._key = params
        self.preview_hidden_ids = (self.body_id,) if self.feature is None else rollback_hidden_ids(self.document.objects, self.feature)
        body = self.document.objects[self.body_id]
        self.preview_parts = ((self._shape, body.color, self._base_faces, body.transparency),)
        return SceneObject("Aperçu du traitement d’arêtes", self._shape, self.document.objects[self.body_id].color)

    def commit(self, document, preview):
        if document is not self.document or dict(document.objects) != self.snapshot:
            raise ValueError("Le document source a changé.")
        body, params = document.objects[self.body_id], self.parameters()
        if self.feature:
            candidate = replace(self.feature, parameters=params)
            if self.commit_error:
                raise ValueError(self.commit_error)
            results, prefixes = self._final_results, self._final_prefixes
            objects = (candidate,)
        else:
            if not isinstance(body, BodyObject):
                body = BodyObject(**{f.name: getattr(body, f.name) for f in fields(SceneObject)}, source_shape=body.shape, source_kind="static")
            name = "Congé" if self.operation is Operation.FILLET else "Chanfrein"
            index = 1
            while f"{name}{index}" in {o.name for o in document.objects.values()}:
                index += 1
            feature = EdgeFeature(f"{name}{index}", None, body.color, kind=ObjectType.FEATURE,
                                  id=self.feature_id, body_id=body.id, operation=self.operation, parameters=params)
            previous = BodyStateRef(body.id, body.features[-1] if body.features else None)
            states = dict(prefix_results(document, body.id)) if isinstance(document.objects[body.id], BodyObject) else {}
            states[previous] = EvaluationResult(ResultStatus.READY, self.base_shape())
            body = replace(body, features=(*body.features, feature.id))
            objects, results, prefixes = (body, feature), {body.id: EvaluationResult(ResultStatus.READY, preview.shape)}, {body.id: states}
        if not self.feature:
            if self.document.history_context.position is not None or self.cooperative_preview:
                results, prefixes = self._final_results, self._final_prefixes
            else:
                from cadastrophe.core.body_history import preview_construction, affected_bodies
                candidate = {**document.objects, **{o.id: o for o in objects}}
                results, prefixes = preview_construction(document, candidate, affected_bodies(candidate, (self.feature_id,)),
                                                         self.extrusions.evaluator, strict=False)
        from cadastrophe.plugin_api import equation_recipe
        objects = tuple(equation_recipe(self, o) if hasattr(o, "parameters") else o for o in objects)
        document.update_construction(objects, title=self.title, results=results, prefixes=prefixes)
        document.select_objects((self.feature_id,))
        self.committed = True

    def dispose(self):
        if not self.committed:
            self.document.select_objects(self.original_selection)
        # Release preview topology while its viewer/context is still alive.
        # The editor's update closure otherwise keeps this whole session in a
        # cycle until cyclic GC, potentially after destruction of the viewer.
        self._update = self.changed = None
        self._shape = None
        self._base_faces = ()
        self.preview_parts = None
        self._final_results = self._final_prefixes = None
        self.snapshot = {}
        self._preview_cache.clear()
        self.surface = self.editor = None
