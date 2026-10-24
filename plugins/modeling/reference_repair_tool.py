"""One explicit repair session over existing recipes and native prefix previews."""
from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem, QComboBox, QPushButton, QCheckBox

from cadastrophe.document import Document, SceneObject, ObjectType
from cadastrophe.core.feature_model import BodyStateRef, ResultStatus
from cadastrophe.core.feature_objects import BodyObject, ConstructionFeature, SketchSourceObject, EdgeFeature, HoleFeature
from cadastrophe.core.operation_model import GeometryReference
from cadastrophe.core.body_history import result_at, body_result, last_valid_result, validate_graph, preview_construction, affected_bodies
from cadastrophe.core.reference_repair import reference_entries, entry_issue, replacement_objects, related_objects, error_sources
from cadastrophe.core.native_naming import capture, subshapes


class ReferenceRepairSession:
    preview_opaque = True

    def __init__(self, services, extrusions, profiles, selected):
        self.services, self.extrusions, self.profiles = services, extrusions, profiles
        self.document = services.active_document
        self.object_id = selected.id
        self.title = "Réparer les références · " + selected.name
        self.snapshot = dict(self.document.objects)
        self.original_selection = self.document.selected_ids
        self.staged, self.changed_keys = {}, set()
        self.surface = self.editor = None
        self.preview_parts, self.preview_hidden_ids = None, ()
        self.commit_error = "Choisissez explicitement une référence de remplacement."
        self.rebuild_warning = ""
        self._results = self._prefixes = self._key = None
        self._pick_error = ""
        self._shapes = []
        self.committed = self._connected = False
        self.view = Document(self.document.name, document_id=self.document.id)
        self.view._objects.update(self.snapshot)
        self.view._body_prefixes = {key: dict(values) for key, values in self.document._body_prefixes.items()}
        self.view._body_results = dict(self.document._body_results)
        self._entries = reference_entries(self.view, self.object_id)

    def create_editor(self, parent, changed):
        self.changed = changed
        self.editor = QWidget(parent)
        self.editor.setObjectName("referenceRepairEditor")
        layout = QVBoxLayout(self.editor)
        parents, consumers = related_objects(self.document, self.object_id)
        names = lambda keys: ", ".join(self.document.objects[k].name for k in keys) or "Aucun"
        self.summary = QLabel("Parents : " + names(parents) + "\nConsommateurs : " + names(consumers) +
                              "\nCauses initiales : " + names(error_sources(self.document, self.object_id)))
        bodies = {entry.slot.state.body_id for entry in self._entries if entry.slot.state is not None}
        bodies.update(owner.body_id for entry in self._entries
                      if isinstance(owner := self.document.objects[entry.slot.owner_id], ConstructionFeature))
        previous = tuple(key for key in self.document.objects if key in bodies
                         and isinstance(self.document.objects[key], BodyObject)
                         and body_result(self.document, key).status is not ResultStatus.READY
                         and last_valid_result(self.document, key) is not None)
        if previous:
            self.summary.setText(self.summary.text() + "\nDernier résultat valide conservé pour diagnostic : " +
                                 names(previous) + ". Les choix utilisent uniquement les étapes actuellement valides.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.references = QTreeWidget()
        self.references.setHeaderLabels(("Référence", "Cible", "État"))
        self.references.setRootIsDecorated(False)
        self.references.currentItemChanged.connect(self.entry_changed)
        layout.addWidget(self.references)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.producer = QComboBox()
        self.producer.currentIndexChanged.connect(self.producer_changed)
        layout.addWidget(self.producer)
        self.replacement = QComboBox()
        self.replacement.currentIndexChanged.connect(self.highlight_replacement)
        layout.addWidget(self.replacement)
        self.choose = QPushButton("Choisir ce remplacement")
        self.choose.clicked.connect(self.choose_replacement)
        layout.addWidget(self.choose)
        self.preserve = QCheckBox("Conserver l’orientation du repère d’esquisse")
        self.preserve.setChecked(True)
        layout.addWidget(self.preserve)
        hint = QLabel("Sélectionnez une ligne, puis son remplacement. Vous pouvez aussi cliquer dans la vue "
                      "sur l’état producteur affiché. Chaque choix reste temporaire jusqu’à la validation. "
                      "Les références absentes ou ambiguës ne sont jamais remplacées automatiquement.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.document.selection_changed.connect(self.selection_changed)
        self._connected = True
        self.refresh_rows()
        return self.editor

    def current_entry(self):
        item = self.references.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None

    def refresh_statuses(self):
        # Recalculation can resolve dependent rows without changing their
        # recipes. Refresh labels without restarting selection or the preview.
        for i in range(self.references.topLevelItemCount()):
            item = self.references.topLevelItem(i)
            error = entry_issue(self.view, item.data(0, Qt.ItemDataRole.UserRole), self.profiles)
            item.setText(2, "Bloquée" if error.startswith("Dépendance") else "À réparer" if error else "Résolue")
            item.setToolTip(2, error or "Référence résolue dans son état producteur.")
        entry = self.current_entry()
        if entry is not None:
            self.status.setText(entry_issue(self.view, entry, self.profiles) or
                                "Cette référence est résolue ; vous pouvez la remplacer explicitement.")

    def refresh_rows(self, selected_key=None):
        blocker = QSignalBlocker(self.references)
        self.references.clear()
        self._entries = reference_entries(self.view, self.object_id)
        select, first_error = None, None
        for entry in self._entries:
            error = entry_issue(self.view, entry, self.profiles)
            owner = self.view.objects[entry.slot.owner_id]
            target = self.view.objects.get(entry.slot.target_id)
            item = QTreeWidgetItem(self.references, (owner.name + " · " + entry.label,
                target.name if target else "Objet absent", "Bloquée" if error.startswith("Dépendance") else "À réparer" if error else "Résolue"))
            item.setToolTip(2, error or "Référence résolue dans son état producteur.")
            item.setData(0, Qt.ItemDataRole.UserRole, entry)
            if entry.key == selected_key:
                select = item
            if error and first_error is None:
                first_error = item
        self.references.setCurrentItem(select or first_error or self.references.topLevelItem(0))
        del blocker
        self.entry_changed()

    def entry_changed(self, *_):
        entry = self.current_entry()
        if entry is None:
            self.choose.setEnabled(False)
            return
        self.status.setText(entry_issue(self.view, entry, self.profiles) or "Cette référence est résolue ; vous pouvez la remplacer explicitement.")
        self.preserve.setVisible(entry.role == "support")
        blocker = QSignalBlocker(self.producer)
        self.producer.clear()
        self.producer.addItem("Plans et références d’esquisse", None)
        owner = self.document.objects[entry.slot.owner_id]
        fixed = entry.slot.state if isinstance(owner, EdgeFeature) or entry.role == "face" else None
        for body in self.document.objects.values():
            if body.kind is not ObjectType.BODY:
                continue
            refs = ([BodyStateRef(body.id)] if not isinstance(body, BodyObject) or body.source_shape is not None else [])
            refs += [BodyStateRef(body.id, key) for key in getattr(body, "features", ())]
            for ref in refs:
                if fixed is not None and ref != fixed:
                    continue
                if isinstance(owner, ConstructionFeature) and owner.body_id == body.id and ref.producer_id is not None:
                    if body.features.index(ref.producer_id) >= body.features.index(owner.id):
                        continue
                if result_at(self.document, ref).status is ResultStatus.READY:
                    name = self.document.objects[ref.producer_id].name if ref.producer_id else "base statique"
                    self.producer.addItem(body.name + " · " + name, ref)
        if entry.slot.state is not None:
            index = next((i for i in range(self.producer.count()) if self.producer.itemData(i) == entry.slot.state), -1)
            if index >= 0:
                self.producer.setCurrentIndex(index)
        del blocker
        self.producer_changed()

    def producer_changed(self, *_):
        entry = self.current_entry()
        if entry is None:
            return
        self.replacement.clear()
        self.replacement.addItem("Choisir explicitement un remplacement…", None)
        role = entry.role
        for obj in self.view.objects.values():
            if role == "sketch" and isinstance(obj, SketchSourceObject):
                if isinstance(self.view.objects[entry.slot.owner_id], HoleFeature):
                    self.replacement.addItem(obj.name + " · tous les points", (obj.id, ()))
                    continue
                self.replacement.addItem(obj.name + " · profil complet", (obj.id, ()))
                for i, key in enumerate(self.profiles.selectable_contours(self.view, obj.id), 1):
                    self.replacement.addItem(f"{obj.name} · contour {i}", (obj.id, (key,)))
            elif role in {"contour", "anchor"} and obj.id == entry.sketch_id:
                keys = obj.profile_source_ids if role == "anchor" else self.profiles.selectable_contours(self.view, obj.id)
                for i, key in enumerate(keys, 1):
                    self.replacement.addItem(f"{obj.name} · entité {i}", key)
            elif role == "function" and isinstance(obj, ConstructionFeature) and obj.replayable and obj.enabled:
                owner = self.view.objects[entry.slot.owner_id]
                if obj.body_id == owner.body_id and self.view.objects[obj.body_id].features.index(obj.id) < self.view.objects[obj.body_id].features.index(owner.id):
                    self.replacement.addItem(obj.name, obj.id)
            elif role in {"plane", "axis", "support"} and obj.plane is not None:
                self.replacement.addItem(obj.name, GeometryReference("plane", obj.id))
            elif role in {"axis", "point", "curve"} and isinstance(obj, SketchSourceObject):
                for i, entity in enumerate(obj.sketch.all_entities, 1):
                    if role == "axis" and entity.kind == "segment":
                        self.replacement.addItem(f"{obj.name} · ligne {i}", GeometryReference("sketch_line", obj.id, entity_id=entity.id))
                    if role == "curve" and entity.kind != "point":
                        self.replacement.addItem(f"{obj.name} · courbe {i}", GeometryReference("sketch_curve", obj.id, entity_id=entity.id))
                    if role == "point":
                        for part in ("start", "end", "center", "position"):
                            if getattr(entity, part, None) is not None:
                                caption = {"start": "départ", "end": "fin", "center": "centre", "position": "point"}[part]
                                self.replacement.addItem(f"{obj.name} · point {i} ({caption})", GeometryReference("sketch_point", obj.id, entity_id=entity.id, part=part))
        if role == "axis":
            for name, direction in (("X", (1, 0, 0)), ("Y", (0, 1, 0)), ("Z", (0, 0, 1))):
                self.replacement.addItem("Axe " + name, GeometryReference("axis", values=(0, 0, 0, *direction)))
        if role == "point":
            self.replacement.addItem("Origine", GeometryReference("point", values=(0, 0, 0)))
        ref = self.producer.currentData()
        self._shapes = []
        if ref is not None and role not in {"sketch", "contour", "anchor", "function"}:
            state = result_at(self.document, ref)
            kinds = ("Edge",) if role in {"edge", "curve"} else (entry.value.topology,) if role == "external" else ("Vertex",) if role == "point" else ("Edge", "Face") if role == "axis" else ("Face",)
            for kind in kinds:
                for i, shape in enumerate(subshapes(state.shape, kind), 1):
                    try:
                        value = GeometryReference(kind.lower(), ref.body_id, ref.producer_id, capture(state.shape, shape, kind, state.topology))
                        # The final detached preview validates role compatibility and cycles.
                        self.replacement.addItem({"Face": "Face", "Edge": "Arête", "Vertex": "Sommet"}[kind] + f" {i}", value)
                        self._shapes.append((shape, value))
                    except ValueError:
                        continue
        self.choose.setEnabled(self.replacement.count() > 1)
        self._pick_error = ""
        self.changed()

    def set_replacement(self, entry, value):
        updates = replacement_objects(self.view, entry, value, self.profiles, preserve_frame=self.preserve.isChecked())
        self.staged.update({obj.id: obj for obj in updates})
        self.view._objects.update(self.staged)
        self.changed_keys.add(entry.key)
        self._key = None
        self.refresh_rows(entry.key)
        self.changed()

    def highlight_replacement(self, *_):
        if self.surface is None:
            return
        entry, value = self.current_entry(), self.replacement.currentData()
        shapes = ()
        try:
            if isinstance(value, GeometryReference):
                shapes = tuple(shape for shape, ref in self._shapes if ref == value)
            elif entry is not None and value is not None and entry.role in {"contour", "anchor", "sketch"}:
                if entry.role == "anchor":
                    shapes = self.profiles.path_edges(self.view, entry.sketch_id, (value,))
                else:
                    sketch_id, contours = value if entry.role == "sketch" else (entry.sketch_id, (value,))
                    faces = self.profiles.to_region_faces(self.view, sketch_id, contour_ids=contours)
                    shapes = tuple(region.face for region in faces.regions)
        except (ValueError, RuntimeError):
            shapes = ()
        self.surface.highlight_regions(shapes, outline=True)

    def choose_replacement(self):
        try:
            value = self.replacement.currentData()
            if value is None:
                raise ValueError("Choisissez un remplacement dans la liste ou dans la vue.")
            self.set_replacement(self.current_entry(), value)
        except (ValueError, RuntimeError, KeyError) as exc:
            self._pick_error = str(exc)
            self.status.setText(str(exc))
            self.changed()

    def pick_shape(self, body_id):
        ref = self.producer.currentData()
        return result_at(self.document, ref).shape if ref and ref.body_id == body_id else None

    def clicked(self, x, y):
        entry = self.current_entry()
        if entry is None:
            return True
        try:
            item = self.surface.pick_edge(x, y) if entry.role in {"edge", "curve", "external", "axis"} else self.surface.pick_face(x, y)
            if item is not None:
                value = next((value for shape, value in self._shapes if shape.IsSame(item.shape)), None)
                if value is None:
                    raise ValueError("Cette géométrie n'appartient pas à l'état producteur affiché ; choisissez une référence dans la liste.")
                self.set_replacement(entry, value)
        except (ValueError, RuntimeError) as exc:
            self._pick_error = str(exc)
            self.status.setText(str(exc))
            self.changed()
        return True

    def selection_changed(self, *_):
        entry = self.current_entry()
        if entry is None or len(self.document.selected_ids) != 1:
            return
        key = self.document.selected_ids[0]
        obj = self.document.objects[key]
        try:
            if entry.role == "sketch" and isinstance(obj, SketchSourceObject):
                # Tree picking selects the source; contour choice remains explicit.
                self.status.setText("Choisissez le profil complet ou un contour dans la liste, puis cliquez sur Choisir.")
                return
            if entry.role in {"support", "plane", "axis"} and obj.plane is not None:
                self.set_replacement(entry, GeometryReference("plane", key))
            elif entry.role == "function" and isinstance(obj, ConstructionFeature):
                self.set_replacement(entry, key)
        except (ValueError, RuntimeError) as exc:
            self._pick_error = str(exc)
            self.changed()

    def attach(self, surface):
        self.surface = surface

    def preview(self):
        if self.services.active_document is not self.document or any(self.document.objects.get(k) is not v for k, v in self.snapshot.items()) or set(self.document.objects) != set(self.snapshot):
            raise ValueError("Le document source a changé depuis l'ouverture de la réparation.")
        self.preview_parts, self.preview_hidden_ids = None, ()
        if self._pick_error:
            raise ValueError(self._pick_error)
        if not self.staged:
            self.commit_error = "Choisissez explicitement une référence de remplacement."
        elif self._key is None:
            candidate = {**self.snapshot, **self.staged}
            from cadastrophe.core.parameters import prepare
            candidate, _, _ = prepare(candidate, self.document.variables)
            validate_graph(candidate)
            affected = affected_bodies(candidate, self.staged)
            resolved = {}
            self._results, self._prefixes = preview_construction(self.document, candidate, affected,
                self.extrusions.evaluator, strict=False, resolved_objects=resolved)
            candidate.update(resolved)
            self.view._objects.update(candidate)
            self.view._body_prefixes.update(self._prefixes)
            self.view._body_results.update(self._results)
            self.refresh_statuses()
            issues = [entry_issue(self.view, entry, self.profiles) for entry in reference_entries(self.view, self.object_id)
                      if entry.key in self.changed_keys]
            self.commit_error = next((error for error in issues if error), "")
            self.staged = {key: obj for key, obj in candidate.items() if obj != self.snapshot[key]}
            self.rebuild_warning = "\n".join(self.document.objects[key].name + " : " + result.message
                for key, result in self._results.items() if result.status is not ResultStatus.READY)
            self._key = True
        if self.staged and self._results is not None:
            parts = tuple((r.shape, self.document.objects[key].color, (), self.document.objects[key].transparency)
                          for key, r in self._results.items() if r.status is ResultStatus.READY and self.document.objects[key].visible)
            if parts:
                self.preview_hidden_ids = tuple(self._results)
                self.preview_parts = parts
                return SceneObject("Aperçu de la réparation", parts[0][0], parts[0][1])
        ref = self.producer.currentData()
        if ref is not None:
            state = result_at(self.document, ref)
            body = self.document.objects[ref.body_id]
            self.preview_hidden_ids = (body.id,)
            return SceneObject("État producteur à sélectionner", state.shape, body.color)
        owner = self.view.objects.get(self.object_id)
        if isinstance(owner, SketchSourceObject):
            from cadastrophe.core.reference_geometry import plane_shape
            plane = owner.sketch.support.frame
            return SceneObject("Support d'esquisse", plane_shape(plane, 25), owner.color, plane=plane)
        if getattr(owner, "plane", None) is not None:
            from cadastrophe.core.reference_geometry import plane_shape
            return SceneObject("Plan de référence", plane_shape(owner.plane), owner.color, plane=owner.plane)
        return None

    def commit(self, document, preview):
        if document is not self.document or self.commit_error or not self.staged:
            raise ValueError(self.commit_error or "Aucune réparation prête à valider.")
        document.update_construction(tuple(self.staged.values()), title=self.title,
                                     results=self._results, prefixes=self._prefixes, allow_errors=True)
        document.select_objects((self.object_id,))
        self.committed = True

    def dispose(self):
        if self._connected:
            self.document.selection_changed.disconnect(self.selection_changed)
        if not self.committed:
            self.document.select_objects(key for key in self.original_selection if key in self.document.objects)
        self.references.currentItemChanged.disconnect(self.entry_changed)
        self.producer.currentIndexChanged.disconnect(self.producer_changed)
        self.replacement.currentIndexChanged.disconnect(self.highlight_replacement)
        self.choose.clicked.disconnect(self.choose_replacement)
        # Drop native handles while the owning tool/viewport still exists.
        # A deferred QObject deletion alone retains this detached graph through
        # Python callback cycles until a later native window is destroyed.
        self._shapes = []
        self._entries = ()
        self.view._objects.clear()
        self.view._body_prefixes.clear()
        self.view._body_results.clear()
        self.view.deleteLater()
        self.view = None
        self.staged.clear()
        self.snapshot.clear()
        self._results = self._prefixes = self.preview_parts = None
        self.surface = self.editor = self.changed = None
