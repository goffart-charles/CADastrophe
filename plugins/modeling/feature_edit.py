"""Reserved parameter panel session for editing an existing construction recipe."""
from dataclasses import replace

from PySide6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QLabel, QCheckBox, QComboBox

from cadastrophe.document import SceneObject
from cadastrophe.plugin_api import LengthSpinBox
from cadastrophe.core.body_history import preview_feature_steps, rollback_hidden_ids
from cadastrophe.core.feature_model import Operation, BodyStateRef, ResultStatus
from cadastrophe.core.computation import complete, cooperative_required
from .recipe_session import inherited_faces


class FeatureEditSession:
    preview_opaque = True

    @property
    def cooperative_preview(self):
        return cooperative_required(self.document.objects)

    def invalidate_preview(self):
        self._key = None

    def __init__(self, services, extrusions, feature):
        self.services, self.extrusions = services, extrusions
        self.document = services.active_document
        self.feature = feature
        self.title = (f"Ajout par extrusion · {feature.name}" if feature.operation in (Operation.NEW_BODY, Operation.BOSS)
                      else f"Modifier {feature.name}")
        self.original_selection = self.document.selected_ids
        self.snapshot = dict(self.document.objects)
        self.preview_hidden_ids, self.preview_parts = (), None
        self._key = self._candidate = self._results = self._prefixes = None
        self.committed = False
        self.commit_error = ""
        self._preview_cache = {}

    def create_editor(self, parent, changed):
        self.editor = QWidget(parent)
        self.editor.setObjectName("extrusionFeatureEditor")
        layout, form = QVBoxLayout(self.editor), QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.addRow("Esquisse", QLabel(self.document.objects[self.feature.source.sketch_id].name))
        form.addRow("Corps", QLabel(self.document.objects[self.feature.body_id].name))
        labels = {Operation.NEW_BODY: "Nouveau corps", Operation.BOSS: "Ajout par extrusion", Operation.CUT: "Enlèvement de matière"}
        form.addRow("Opération", QLabel(labels[self.feature.operation]))
        params = self.feature.parameters
        form.addRow("Contours", QLabel(f"{len(params.contours)} contour(s) conservé(s)" if params.contours else "Profil complet"))
        self.condition = QComboBox()
        self.condition.addItem("Borgne", "blind")
        if self.feature.operation is Operation.CUT:
            self.condition.addItem("À travers tout", "through_all")
        self.condition.setCurrentIndex(self.condition.findData(params.end_condition))
        form.addRow("Condition de fin", self.condition)
        depth = params.depth if params.depth is not None else 5.
        self.depth = LengthSpinBox(self.services.display_units, 0., max(1e6, depth), depth)
        self.depth.setObjectName("extrusionDepth")
        form.addRow("Profondeur", self.depth)
        self.reverse = QCheckBox("Inverser la direction")
        self.reverse.setChecked(params.reversed)
        form.addRow(self.reverse)
        layout.addLayout(form)
        hint = QLabel("L’objet est affiché à cette étape de son historique. Les fonctions suivantes sont réappliquées à la validation. Entrée : valider · Échap : annuler.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        def condition_changed():
            self.depth.setEnabled(self.condition.currentData() == "blind")
            changed()
        self.condition.currentIndexChanged.connect(condition_changed)
        self.depth.valueChanged.connect(changed)
        self.depth.lineEdit().textEdited.connect(changed)
        self.reverse.toggled.connect(changed)
        condition_changed()
        self.editor.setFocusProxy(self.depth if self.depth.isEnabled() else self.condition)
        from cadastrophe.plugin_api import bind_fields
        bind_fields(self, {"depth": self.depth})
        return self.editor

    def preview(self):
        return complete(self.preview_steps())

    def preview_steps(self):
        self.preview_hidden_ids, self.preview_parts = (), None
        if self.services.active_document is not self.document or self.document not in self.services.documents:
            raise ValueError("Le document source n’est plus actif.")
        if set(self.document.objects) != set(self.snapshot) or any(self.document.objects[k] is not obj for k, obj in self.snapshot.items()):
            raise ValueError("Le document a changé depuis l’ouverture de la fonction.")
        condition = self.condition.currentData()
        if condition == "blind":
            if not self.depth.hasAcceptableInput():
                raise ValueError(self.depth.equation_error or "Saisissez une profondeur positive valide.")
            self.depth.interpretText()
        params = replace(self.feature.parameters, depth=self.depth.internal_value if condition == "blind" else None,
                         reversed=self.reverse.isChecked(), end_condition=condition)
        if params != self._key:
            candidate = replace(self.feature, parameters=params)
            results, prefixes = yield from preview_feature_steps(self.document, candidate,
                self.extrusions.evaluator, strict=False, cache=self._preview_cache)
            current = prefixes[candidate.body_id][BodyStateRef(candidate.body_id, candidate.id)]
            if current.status is not ResultStatus.READY:
                raise ValueError(current.message)
            self._candidate = candidate
            self._results, self._prefixes = {candidate.body_id: current}, prefixes
            self.commit_error = ""
            self._final_results, self._final_prefixes = results, prefixes
            self.rebuild_warning = next((r.message for r in self._final_results.values() if r.shape is None), "")
            self._key = params
        visible = [key for key in self._results if self.document.objects[key].visible]
        self.preview_hidden_ids = tuple(key for key in rollback_hidden_ids(self.document.objects, self.feature)
                                        if self.document.objects[key].visible)
        self.preview_parts = tuple((self._results[key].shape, self.document.objects[key].color,
                                    inherited_faces(self._results[key], self.feature.id),
                                    self.document.objects[key].transparency) for key in visible)
        shape = self._results[self.feature.body_id].shape
        return SceneObject("Aperçu de la fonction", shape, self.document.objects[self.feature.body_id].color)

    def commit(self, document, preview):
        if document is not self.document or document.objects.get(self.feature.id) is not self.feature:
            raise ValueError("La fonction source a changé.")
        if self.commit_error:
            raise ValueError(self.commit_error)
        from cadastrophe.plugin_api import equation_recipe
        document.update_construction((equation_recipe(self, self._candidate),), title=self.title,
                                     results=self._final_results, prefixes=self._final_prefixes)
        document.select_objects((self.feature.id,))
        self.committed = True

    def dispose(self):
        if not self.committed:
            self.document.select_objects(key for key in self.original_selection if key in self.document.objects)
        self._key = self._candidate = self._results = self._prefixes = None
        self._final_results = self._final_prefixes = None
        self.snapshot = {}
        self._preview_cache.clear()
        self.preview_parts = None
        self.editor = None
