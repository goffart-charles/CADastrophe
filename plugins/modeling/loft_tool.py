"""Ordered section collection and detached loft previews in the host session."""
from dataclasses import replace
from uuid import uuid4

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QHBoxLayout,
    QListWidget, QPushButton, QComboBox, QCheckBox, QLabel)

from cadastrophe.document import SceneObject, ObjectType
from cadastrophe.core.feature_objects import BodyObject, LoftFeature, SketchSourceObject
from cadastrophe.core.feature_model import Operation
from cadastrophe.core.operation_model import LoftSection, LoftParameters
from cadastrophe.core.body_history import preview_construction
from cadastrophe.core.boss import fuse_boss
from .recipe_session import RecipePreview, adopted, unique_name
from .revolution_tool import sketch_input
from .selection_field import SelectionField


class LoftSession(RecipePreview):
    def __init__(self, services, extrusions, profiles, *, cut=False, feature=None):
        self.initialize(services, extrusions, feature)
        self.profiles = profiles
        self.body_id = feature.body_id if feature else str(uuid4())
        self.is_cut = cut or feature is not None and feature.operation is Operation.CUT
        self.title = "Modifier "+feature.name if feature else "Lissage soustractif" if self.is_cut else "Lissage additif"
        self.sections = feature.parameters.sections if feature else ()
        self.editor = None
        self.target = "section"
        self._updating = False

    def create_editor(self, parent, changed):
        self.changed = changed
        self.editor = QWidget(parent)
        self.editor.setObjectName("loftFeatureEditor")
        layout = QVBoxLayout(self.editor)
        layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.source = SelectionField("Cliquer sur une esquisse pour ajouter une section")
        self.source.setObjectName("loftSectionPicker")
        self.source.activated.connect(lambda: self.activate("section"))
        self.source.cleared.connect(self.remove_section)
        form.addRow("Sections", self.source)
        self.section_sketch = QComboBox()
        self.section_sketch.addItem("Ou choisir une esquisse…", None)
        for obj in self.document.objects.values():
            if isinstance(obj, SketchSourceObject):
                self.section_sketch.addItem(obj.name, obj.id)
        self.section_sketch.currentIndexChanged.connect(self.choose_combo)
        form.addRow(self.section_sketch)
        self.replace_section = QCheckBox("Remplacer la section sélectionnée")
        form.addRow(self.replace_section)
        self.section_list = QListWidget()
        self.section_list.setObjectName("loftOrderedSections")
        self.section_list.setMaximumHeight(110)
        self.section_list.currentRowChanged.connect(self.refresh_section)
        form.addRow(self.section_list)
        controls = QWidget()
        row = QHBoxLayout(controls)
        row.setContentsMargins(0, 0, 0, 0)
        for label, callback in (("Monter", lambda: self.move_section(-1)), ("Descendre", lambda: self.move_section(1)), ("Retirer", self.remove_section)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        form.addRow(controls)
        self.region = QComboBox()
        self.region.setObjectName("loftSectionRegion")
        self.region.currentIndexChanged.connect(self.change_region)
        form.addRow("Région de la section", self.region)
        self.boundaries = QWidget()
        self.boundary_form = QFormLayout(self.boundaries)
        self.boundary_form.setContentsMargins(0, 0, 0, 0)
        form.addRow(self.boundaries)
        self.mode = QComboBox()
        self.mode.addItem("Lisse", "smooth")
        self.mode.addItem("Réglé · tronçons droits", "ruled")
        self.matching = QComboBox()
        self.matching.addItem("Automatique", "automatic")
        self.matching.addItem("Explicite · ancres et sens", "explicit")
        p = self.feature.parameters if self.feature else None
        self.mode.setCurrentIndex(self.mode.findData(p.mode if p else "smooth"))
        self.matching.setCurrentIndex(self.matching.findData(p.matching if p else "automatic"))
        self.mode.currentIndexChanged.connect(changed)
        self.matching.currentIndexChanged.connect(self.matching_changed)
        form.addRow("Interpolation", self.mode)
        form.addRow("Correspondance", self.matching)
        self.merge = QCheckBox("Fusionner le résultat")
        self.merge.setChecked(True)
        self.merge.toggled.connect(self.merge_changed)
        self.body = SelectionField("Automatique · cliquer pour choisir un corps")
        self.body.activated.connect(lambda: self.activate("body"))
        self.body.cleared.connect(lambda: self.choose_body(None))
        if not self.feature:
            if not self.is_cut:
                form.addRow(self.merge)
            form.addRow("Corps cible", self.body)
        else:
            self.merge.hide()
            self.body.hide()
        layout.addLayout(form)
        hint = QLabel("Ajoutez les sections dans leur ordre. Chaque section contient une région fermée, "
                      "avec le même nombre de trous. Les ancres associent les trous d’une section à l’autre. "
                      "En mode explicite, elles fixent aussi la première arête et le sens du contour. "
                      "Milieu : rotation · Ctrl+milieu : déplacement · Molette : zoom.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        # An empty contour selection is a valid recipe for a unique region.
        # Materialise that region in the editor while preserving any lost
        # anchor, so a damaged recipe remains repairable rather than replaced.
        prepared = []
        for section in self.sections:
            try:
                regions = self.profiles.analyze_regions(self.document, section.source.sketch_id).regions
                selected = next((r for r in regions if section.contours == (r[0].id,)), None)
                if not section.contours and len(regions) == 1:
                    selected = regions[0]
                if selected is not None:
                    default = self.with_region(section, selected)
                    section = replace(section, contours=default.contours,
                                      anchors=section.anchors or default.anchors,
                                      reversed=section.reversed or default.reversed)
            except (ValueError, RuntimeError, KeyError):
                pass  # Keep the stored section for explicit replacement.
            prepared.append(section)
        self.sections = tuple(prepared)
        self.refresh_sections(0)
        if not self.feature:
            for key in self.original_selection:
                if isinstance(self.document.objects.get(key), SketchSourceObject):
                    self.choose_section(key)
        self.document.selection_changed.connect(self.selection_changed)
        self._connected = True
        self.activate("section")
        self.editor.setFocusProxy(self.source)
        return self.editor

    def activate(self, target):
        self.target = target
        self.source.set_active(target == "section")
        self.body.set_active(target == "body")
        self._pick_error = ""
        self.changed()

    def choose_combo(self, *_):
        key = self.section_sketch.currentData()
        if key is not None:
            try:
                self.choose_section(key)
            except (ValueError, RuntimeError) as exc:
                self._pick_error = str(exc)
            self.section_sketch.blockSignals(True)
            self.section_sketch.setCurrentIndex(0)
            self.section_sketch.blockSignals(False)
            self.changed()

    def choose_section(self, key):
        obj = self.document.objects.get(key)
        if not isinstance(obj, SketchSourceObject):
            raise ValueError("Choisissez une esquisse de ce document.")
        report = self.profiles.analyze_regions(self.document, key)
        if not report.valid or not report.regions:
            raise ValueError("La section doit contenir une région fermée valide.")
        index = self.section_list.currentRow() if self.replace_section.isChecked() else -1
        if any(s.source.sketch_id == key for i, s in enumerate(self.sections) if i != index):
            raise ValueError("Cette esquisse figure déjà parmi les sections.")
        contours = (report.regions[0][0].id,) if len(report.regions) == 1 else ()
        new = LoftSection(sketch_input(self.document, obj), contours)
        if contours:
            new = self.with_region(new, report.regions[0])
        sections = list(self.sections)
        if 0 <= index < len(sections):
            sections[index] = new
        else:
            if len(sections) >= 64:
                raise ValueError("Le lissage accepte au maximum 64 sections.")
            index = len(sections)
            sections.append(new)
        self.sections = tuple(sections)
        self._pick_error = ""
        self.refresh_sections(index)
        self.activate("section")

    @staticmethod
    def with_region(section, region):
        outer, holes = region
        anchors = tuple(contour.edges[0].entity_id for contour in (outer, *holes))
        return replace(section, contours=(outer.id,), anchors=anchors, reversed=(False,)*len(anchors))

    def refresh_sections(self, index):
        self._updating = True
        self.section_list.clear()
        for i, section in enumerate(self.sections, 1):
            obj = self.document.objects.get(section.source.sketch_id)
            self.section_list.addItem(f"{i}. {obj.name if obj else 'Esquisse absente'}")
        self.section_list.setCurrentRow(min(index, len(self.sections)-1))
        self.source.set_value(tuple(s.source.sketch_id for s in self.sections) or None, f"{len(self.sections)} section(s) ordonnée(s)")
        self._updating = False
        self.refresh_section()

    def refresh_section(self, *_):
        if self._updating:
            return
        self._updating = True
        self.region.clear()
        while self.boundary_form.rowCount():
            self.boundary_form.removeRow(0)
        self.anchor_fields, self.reverse_fields = [], []
        self._regions = ()
        index = self.section_list.currentRow()
        if 0 <= index < len(self.sections):
            section = self.sections[index]
            try:
                self._regions = self.profiles.analyze_regions(self.document, section.source.sketch_id).regions
                self.region.addItem("Choisir une région…", None)
                for i, (outer, holes) in enumerate(self._regions, 1):
                    self.region.addItem(f"Région {i} · {len(holes)} trou(s)", outer.id)
                region_id = section.contours[0] if section.contours else None
                if region_id is not None and self.region.findData(region_id) < 0:
                    self.region.addItem("Région introuvable · réassocier", region_id)
                self.region.setCurrentIndex(max(0, self.region.findData(region_id)))
                selected = next((r for r in self._regions if r[0].id == region_id), None)
                if selected is not None:
                    outer, holes = selected
                    for boundary, anchor in enumerate(section.anchors):
                        choices = (outer,) if boundary == 0 else holes
                        combo, reverse = QComboBox(), QCheckBox("Inverser")
                        sketch = self.document.objects[section.source.sketch_id].sketch
                        labels = {e.id: f"Courbe {i} · "+{"segment": "segment", "circle": "cercle", "arc": "arc",
                                  "ellipse": "ellipse", "spline": "spline"}.get(e.kind, e.kind)
                                  for i, e in enumerate(sketch.entities, 1)}
                        for contour in choices:
                            for edge in contour.edges:
                                if combo.findData(edge.entity_id) < 0:
                                    combo.addItem(labels.get(edge.entity_id, "Courbe du contour"), edge.entity_id)
                        if combo.findData(anchor) < 0:
                            combo.addItem("Ancre introuvable · réassocier", anchor)
                        combo.setCurrentIndex(combo.findData(anchor))
                        reverse.setChecked(section.reversed[boundary])
                        reverse.setEnabled(self.matching.currentData() == "explicit")
                        combo.currentIndexChanged.connect(self.change_boundaries)
                        reverse.toggled.connect(self.change_boundaries)
                        container = QWidget()
                        row = QHBoxLayout(container)
                        row.setContentsMargins(0, 0, 0, 0)
                        row.addWidget(combo)
                        row.addWidget(reverse)
                        self.boundary_form.addRow("Extérieur" if boundary == 0 else f"Trou {boundary}", container)
                        self.anchor_fields.append(combo)
                        self.reverse_fields.append(reverse)
            except (ValueError, RuntimeError, KeyError):
                self.region.addItem("Section indisponible · remplacer l’esquisse", None)
        self._updating = False
        self.changed()

    def change_region(self, *_):
        if self._updating:
            return
        index = self.section_list.currentRow()
        selected = next((r for r in self._regions if r[0].id == self.region.currentData()), None)
        if selected is not None and index >= 0:
            sections = list(self.sections)
            sections[index] = self.with_region(sections[index], selected)
            self.sections = tuple(sections)
            self._pick_error = ""
            self.refresh_section()

    def change_boundaries(self, *_):
        if self._updating:
            return
        index = self.section_list.currentRow()
        if index >= 0:
            try:
                sections = list(self.sections)
                sections[index] = replace(sections[index], anchors=tuple(c.currentData() for c in self.anchor_fields),
                                          reversed=tuple(c.isChecked() for c in self.reverse_fields))
                self.sections = tuple(sections)
                self._pick_error = ""
            except ValueError as exc:
                self._pick_error = str(exc)
            self.changed()

    def matching_changed(self, *_):
        self.refresh_section()

    def move_section(self, delta):
        index = self.section_list.currentRow()
        other = index+delta
        if 0 <= index < len(self.sections) and 0 <= other < len(self.sections):
            sections = list(self.sections)
            sections[index], sections[other] = sections[other], sections[index]
            self.sections = tuple(sections)
            self._pick_error = ""
            self.refresh_sections(other)

    def remove_section(self):
        index = self.section_list.currentRow()
        if index >= 0:
            self.sections = self.sections[:index]+self.sections[index+1:]
            self._pick_error = ""
            self.refresh_sections(max(0, index-1))

    def choose_body(self, key):
        obj = self.document.objects.get(key)
        if key is not None and (obj is None or obj.kind is not ObjectType.BODY or obj.shape is None):
            raise ValueError("Choisissez un corps solide disponible.")
        self.body.set_value(key, obj.name if obj else "")
        self.activate("section")

    def merge_changed(self, checked):
        self.body.setEnabled(checked)
        self.activate("section")

    def selection_changed(self, *_):
        selected = self.document.selected_ids
        if len(selected) != 1:
            return
        obj = self.document.objects.get(selected[0])
        try:
            if self.target == "body" and obj is not None and obj.kind is ObjectType.BODY:
                self.choose_body(obj.id)
            elif self.target == "section" and isinstance(obj, SketchSourceObject):
                self.choose_section(obj.id)
        except (ValueError, RuntimeError) as exc:
            self._pick_error = str(exc)
            self.changed()

    def clicked(self, x, y):
        key = self.surface.detect_object(x, y)
        obj = self.document.objects.get(key)
        try:
            if self.target == "body" and obj is not None and obj.kind is ObjectType.BODY:
                self.choose_body(key)
            elif self.target == "section" and isinstance(obj, SketchSourceObject):
                self.choose_section(key)
        except (ValueError, RuntimeError) as exc:
            self._pick_error = str(exc)
            self.changed()
        return True

    def preview(self):
        self.check_snapshot()
        if any(not s.contours for s in self.sections):
            raise ValueError("Choisissez une région pour chaque section.")
        parameters = LoftParameters(self.sections, self.mode.currentData(), self.matching.currentData())
        key = (parameters, self.merge.isChecked(), self.body.value)
        if key != self._key:
            if self.feature:
                candidate = replace(self.feature, parameters=parameters)
                self.calculate((candidate,), candidate)
            else:
                feature = LoftFeature(unique_name(self.document, "Enlèvement par lissage" if self.is_cut else "Lissage"),
                    None, (.72, .75, .8), kind=ObjectType.FEATURE, id=self.feature_id,
                    body_id=self.body_id, operation=Operation.NEW_BODY, parameters=parameters)
                body, feature = self.plan_body(feature)
                self.calculate((body, feature), feature)
            self._key = key
        return SceneObject("Aperçu du lissage", self._current.shape, (.72, .75, .8))

    def plan_body(self, feature):
        from cadastrophe.core.history_position import context_object
        candidates = [o for original in self.document.objects.values() if (o := context_object(self.document, original))
                      and o.kind is ObjectType.BODY and o.shape is not None]
        if self.body.value is not None:
            candidates = [o for o in candidates if o.id == self.body.value]
            if not candidates:
                raise ValueError("Le corps cible n’est plus disponible.")
        target = None
        if self.is_cut:
            if len(candidates) != 1:
                raise ValueError("Choisissez un corps cible unique pour le lissage soustractif.")
            target = candidates[0]
        elif self.merge.isChecked() and candidates:
            temporary = BodyObject("Aperçu", None, feature.color, id=self.body_id, features=(feature.id,))
            graph = {**self.document.objects, temporary.id: temporary, feature.id: feature}
            results, _ = preview_construction(self.document, graph, (temporary.id,), self.extrusions.evaluator)
            generated = results[temporary.id].shape
            from OCC.Core.TopAbs import TopAbs_COMPOUND
            from cadastrophe.core.operation_effects import checked_solids
            touching = []
            for body in candidates:
                collection = body.shape.ShapeType() == TopAbs_COMPOUND
                union = fuse_boss(body.shape, generated, allow_disconnected=collection)
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
            return BodyObject(unique_name(self.document, "Corps"), None, feature.color, id=self.body_id, features=(feature.id,)), feature
        body = adopted(target)
        feature = replace(feature, body_id=body.id, operation=Operation.CUT if self.is_cut else Operation.BOSS)
        return replace(body, features=(*body.features, feature.id)), feature

    def commit(self, document, preview):
        RecipePreview.commit(self, document, preview)
        for section in self.sections:
            document.set_visible(section.source.sketch_id, False)
