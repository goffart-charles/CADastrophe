"""A tree projection of Document; it owns no CAD objects or selection state."""
from weakref import ref
from PySide6.QtCore import QObject, Qt, QSignalBlocker, Signal, QEvent
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import QTreeWidgetItem, QStyle

from ..document import ObjectType
from ..core.feature_objects import BodyObject, ConstructionFeature, SketchSourceObject
from ..core.curve_model import CurveObject
from .icons import icon
from .themes import theme_tokens

ROLE = Qt.ItemDataRole.UserRole


class DocumentTreeAdapter(QObject):
    selection_requested = Signal(list)
    visibility_requested = Signal(str, bool)
    rename_requested = Signal(str, str)
    state_changed = Signal()

    def __init__(self, tree, parent=None, *, presentation_registry=None):
        super().__init__(parent)
        self._tree_ref = ref(tree)
        self.document = None
        self._projection_document_id = None
        self.items = {}
        self.rows = {}
        self._history_marker = None
        self._mutable = True
        self.presentation_registry = presentation_registry
        if presentation_registry is not None:
            presentation_registry.changed.connect(self.rebuild)
        tree.itemSelectionChanged.connect(self._selection_requested)
        # A parent row is already highlighted after picking one of its faces.
        # Clicking that same row must still request the WHOLE object.
        tree.itemClicked.connect(lambda item, column: self._selection_requested()
                                 if item.data(0, ROLE) in self.items else None)
        tree.itemChanged.connect(self._visibility_requested)
        tree.viewport().installEventFilter(self)

    def eventFilter(self, watched, event):
        # Resolve right-click selection in the context-menu handler, preserving
        # native face owners and multiple selection even over an empty row.
        if (event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.RightButton):
            event.accept()
            return True
        return False

    @property
    def tree(self):
        return self._tree_ref()

    def bind(self, document):
        self.detach()
        self.document = document
        document.object_added.connect(self._added)
        document.object_changed.connect(self._changed)
        document.object_removed.connect(self._removed)
        document.cleared.connect(self.rebuild)
        document.selection_changed.connect(self._select_rows)
        document.metadata_changed.connect(self._metadata_changed)
        document.body_result_changed.connect(self._body_state_changed)
        document.history_context.changed.connect(self.rebuild)
        self.rebuild()

    def detach(self):
        if self.document is not None:
            self.document.object_added.disconnect(self._added)
            self.document.object_changed.disconnect(self._changed)
            self.document.object_removed.disconnect(self._removed)
            self.document.cleared.disconnect(self.rebuild)
            self.document.selection_changed.disconnect(self._select_rows)
            self.document.metadata_changed.disconnect(self._metadata_changed)
            self.document.body_result_changed.disconnect(self._body_state_changed)
            self.document.history_context.changed.disconnect(self.rebuild)
            self.document = None

    def rebuild(self):
        blocker = QSignalBlocker(self.tree)
        same_document = self.document is not None and self._projection_document_id == self.document.id
        expanded = {key: item.isExpanded() for key, item in self.items.items()} if same_document else {}
        old_root = self.tree.topLevelItem(0)
        folders = (True, False, True)
        if same_document and old_root is not None:
            folders = (old_root.isExpanded(), self.planes.isExpanded(),
                       self.bodies.isExpanded() if self.bodies is not None else True)
        self._history_marker = None
        self.tree.clear()
        self.items.clear()
        self.rows.clear()
        if self.document is None:
            self._projection_document_id = None
            del blocker
            self.state_changed.emit()
            return
        root = QTreeWidgetItem(self.tree, [f"{self.document.display_name} · {self.document.length_unit}"])
        root.setToolTip(0, str(self.document.file_path or self.document.name))
        root.setIcon(0, icon("document"))
        self.planes = QTreeWidgetItem(root, ["Plans de référence"])
        single_body = sum(obj.kind is ObjectType.BODY for obj in self.document.objects.values()) == 1
        self.bodies = None if single_body else QTreeWidgetItem(root, ["Corps"])
        for branch in (self.planes, self.bodies):
            if branch is not None:
                branch.setIcon(0, icon("folder"))
        for object_id in self.document.objects:
            self._insert(object_id)
        self._organize()
        root.setExpanded(folders[0])
        self.planes.setExpanded(folders[1])
        if self.bodies is not None:
            self.bodies.setExpanded(folders[2])
        for key, item in self.items.items():
            item.setExpanded(expanded.get(key, single_body and self.document.objects[key].kind is ObjectType.BODY))
        self._projection_document_id = self.document.id
        del blocker
        self._select_rows(self.document.selected_ids)
        self.state_changed.emit()

    def _metadata_changed(self):
        blocker = QSignalBlocker(self.tree)
        root = self.tree.topLevelItem(0)
        if root is not None:
            root.setText(0, f"{self.document.display_name} · {self.document.length_unit}")
            root.setToolTip(0, str(self.document.file_path or self.document.name))
        del blocker
        self.state_changed.emit()

    def _insert(self, object_id):
        obj = self.document.objects[object_id]
        root = self.tree.topLevelItem(0)
        branch = {ObjectType.REFERENCE_PLANE: self.planes,
                  ObjectType.BODY: self.bodies if self.bodies is not None else root,
                  ObjectType.FEATURE: root}[obj.kind]
        item = QTreeWidgetItem(branch)
        self.items[object_id] = item
        self.rows[object_id] = [item]
        item.setData(0, ROLE, object_id)
        self._set_item_mutable(item)
        self._decorate(object_id)
        # Reinserting an undone deletion must restore its original tree order.
        siblings = [key for key, peer in self.document.objects.items() if peer.kind is obj.kind
                    and key in self.items and self.items[key].parent() == branch]
        position = siblings.index(object_id)
        if branch is root:
            # Structural folders precede document objects, including the single
            # body displayed directly under the document instead of a folder.
            position += 1 + (self.bodies is not None)
        if branch.indexOfChild(item) != position:
            branch.takeChild(branch.indexOfChild(item))
            branch.insertChild(position, item)

    def _decorate(self, object_id):
        for item in self.rows[object_id]:
            self._decorate_row(object_id, item)

    def _decorate_row(self, object_id, item):
        obj = self.document.objects[object_id]
        item.setText(0, obj.name)
        item.setCheckState(0, Qt.CheckState.Checked if obj.visible else Qt.CheckState.Unchecked)
        item.setIcon(0, icon({ObjectType.REFERENCE_PLANE: "plane", ObjectType.BODY: "body",
                             ObjectType.FEATURE: "properties"}[obj.kind]))
        binding = self.presentation_registry.binding(obj) if self.presentation_registry is not None else None
        if binding is not None:
            item.setIcon(0, icon(binding.icon))
        if isinstance(obj, ConstructionFeature) and obj.operation.value == "cut":
            item.setIcon(0, icon("cut-extrude"))
        from ..core.feature_objects import RevolutionFeature, ReplayFeature, SweepFeature, LoftFeature
        if isinstance(obj,RevolutionFeature):
            item.setIcon(0,icon("cut-revolve" if obj.operation.value == "cut" else "revolve"))
        elif isinstance(obj, SweepFeature):
            item.setIcon(0, icon("cut-sweep" if obj.operation.value == "cut" else "sweep"))
        elif isinstance(obj, LoftFeature):
            item.setIcon(0, icon("cut-loft" if obj.operation.value == "cut" else "loft"))
        elif isinstance(obj,ReplayFeature):
            item.setIcon(0,icon(obj.operation.value.replace("_","-")))
        elif isinstance(obj, ConstructionFeature) and obj.operation.value in {"fillet", "chamfer"}:
            item.setIcon(0, icon(obj.operation.value))
        item.setForeground(0, QBrush(QColor(theme_tokens().text if obj.visible else theme_tokens().muted)))
        font = self.tree.font()
        font.setItalic(not obj.visible)
        item.setFont(0, font)
        text = f"{binding.label if binding is not None else obj.kind.label} · {self.document.length_unit}"
        if obj.kind is ObjectType.BODY:
            from .workspace import material_text
            text += material_text(obj)
        if obj.plane is not None:
            text += f"\nNormale {obj.plane.normal.tuple()} · U {obj.plane.u.tuple()} · V {obj.plane.v.tuple()}"
        if isinstance(obj, (BodyObject, ConstructionFeature)):
            from ..core.body_history import body_result, result_at
            from ..core.feature_model import BodyStateRef, ResultStatus
            state = (body_result(self.document, obj.id) if isinstance(obj, BodyObject) else
                     result_at(self.document, BodyStateRef(obj.body_id, obj.id)))
            labels = {ResultStatus.DIRTY: "À reconstruire", ResultStatus.ERROR: "Erreur",
                      ResultStatus.BLOCKED: "Dépendance bloquée", ResultStatus.UNAVAILABLE: "Résultat indisponible"}
            if state.status is not ResultStatus.READY:
                label = labels[state.status]
                item.setIcon(0, self.tree.style().standardIcon(
                    QStyle.StandardPixmap.SP_BrowserReload if state.status is ResultStatus.DIRTY
                    else QStyle.StandardPixmap.SP_MessageBoxWarning if state.status is ResultStatus.BLOCKED
                    else QStyle.StandardPixmap.SP_MessageBoxCritical))
                text += f"\n{label} : {state.message}"
                if state.status is not ResultStatus.DIRTY:
                    item.setForeground(0, QBrush(QColor(theme_tokens().warning if state.status is ResultStatus.BLOCKED else theme_tokens().negative)))
                    font.setBold(True)
                    item.setFont(0, font)
        if isinstance(obj, ConstructionFeature) and not obj.enabled:
            text += "\nDésactivée : effet retiré, paramètres conservés."
            text += "\nRéactivez pour ouvrir l'éditeur. Les paramètres restent accessibles dans Variables et équations."
            font.setStrikeOut(True)
            item.setFont(0, font)
            item.setForeground(0, QBrush(QColor(theme_tokens().muted)))
            item.setIcon(0, self.tree.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        item.setToolTip(0, text)
        if isinstance(obj, (SketchSourceObject, CurveObject)):
            error = obj.reference_error(self.document)
            if error:
                item.setIcon(0, self.tree.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxCritical))
                item.setForeground(0, QBrush(QColor(theme_tokens().negative)))
                item.setToolTip(0, text+"\n"+error)
        from ..core.reference_objects import DatumPlaneObject
        if isinstance(obj, DatumPlaneObject) and obj.reference_error_message:
            item.setIcon(0, self.tree.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxCritical))
            item.setForeground(0, QBrush(QColor(theme_tokens().negative)))
            item.setToolTip(0, text+"\n"+obj.reference_error_message)
        if object_id in self.document.history_context.display()[1]:
            font.setItalic(True)
            item.setFont(0, font)
            item.setForeground(0, QBrush(QColor(theme_tokens().muted)))
            item.setToolTip(0, item.toolTip(0)+"\nAprès le repère : géométrie indisponible à cette étape.")

    def _body_state_changed(self, body_id):
        body = self.document.objects.get(body_id)
        if isinstance(body, BodyObject):
            for key in (body_id, *body.features):
                if key in self.items:
                    self.refresh(key)
        for obj in self.document.objects.values():
            if isinstance(obj, SketchSourceObject) and body_id in obj.body_dependencies and obj.id in self.items:
                self.refresh(obj.id)

    def _added(self, object_id):
        if object_id in self.items:
            return
        if self.document.objects[object_id].kind is ObjectType.BODY:
            self.rebuild()
            return
        blocker = QSignalBlocker(self.tree)
        self._insert(object_id)
        self._organize()
        del blocker
        self.state_changed.emit()

    def _changed(self, object_id, fields):
        if "features" in fields or "source" in fields or "parameters" in fields:
            self.rebuild()
            return
        self.refresh(object_id)

    def _organize(self):
        """Project existing ownership/references, including shared-sketch aliases."""
        if self._history_marker is not None:
            marker, self._history_marker = self._history_marker, None
            parent = marker.parent()
            parent.takeChild(parent.indexOfChild(marker))
        for key, rows in self.rows.items():
            for alias in rows[1:]:
                alias.parent().takeChild(alias.parent().indexOfChild(alias))
            self.rows[key] = rows[:1]
        logical = [key for key in self.items if self.document.objects[key].kind is ObjectType.FEATURE]
        for key in logical:
            item = self.items[key]
            item.parent().takeChild(item.parent().indexOfChild(item))
            self.tree.topLevelItem(0).addChild(item)
        used = set()
        for body in self.document.objects.values():
            if not isinstance(body, BodyObject) or body.id not in self.items:
                continue
            for feature_id in body.features:
                feature = self.document.objects.get(feature_id)
                if feature_id not in self.items or not isinstance(feature, ConstructionFeature):
                    continue
                row = self.items[feature_id]
                row.parent().takeChild(row.parent().indexOfChild(row))
                self.items[body.id].addChild(row)
                sketches = dict.fromkeys((*(source.sketch_id for source, _ in feature.profile_sections), *(ref.object_id for ref in
                    getattr(feature, "geometry_references", ()) if ref.kind.startswith("sketch_"))))
                for key in sketches:
                    if key not in self.items:
                        continue
                    if key not in used:
                        child = self.items[key]
                        child.parent().takeChild(child.parent().indexOfChild(child))
                        row.addChild(child)
                    else:
                        child = QTreeWidgetItem(row)
                        child.setData(0, ROLE, key)
                        self._set_item_mutable(child)
                        self.rows[key].append(child)
                        self._decorate_row(key, child)
                    used.add(key)
        for key in logical:
            obj = self.document.objects[key]
            if isinstance(obj, SketchSourceObject) and key not in used and len(obj.body_dependencies) == 1:
                parent = self.items.get(obj.body_dependencies[0])
                if parent is not None:
                    row = self.items[key]
                    row.parent().takeChild(row.parent().indexOfChild(row))
                    parent.addChild(row)

        position = self.document.history_context.position
        if position is not None and position.feature_id in self.items:
            anchor = self.items[position.feature_id]
            parent = anchor.parent()
            marker = QTreeWidgetItem(["── Repère d’insertion ──"])
            marker.setFlags(Qt.ItemFlag.ItemIsEnabled)
            marker.setToolTip(0, "Les nouvelles fonctions sont insérées ici. Revenir à la fin pour terminer la consultation.")
            parent.insertChild(parent.indexOfChild(anchor) + int(position.after), marker)
            self._history_marker = marker

    def refresh(self, object_id):
        blocker = QSignalBlocker(self.tree)
        self._decorate(object_id)
        del blocker
        self.state_changed.emit()

    def refresh_style(self):
        blocker = QSignalBlocker(self.tree)
        for object_id in self.items:
            self._decorate(object_id)
        del blocker
        self.state_changed.emit()

    def _removed(self, object_id):
        # A whole construction graph is already replaced before removal signals.
        # Rebuild once, so deleting a parent never destroys still-indexed rows.
        if object_id in self.items:
            self.rebuild()

    def _select_rows(self, object_ids):
        blocker = QSignalBlocker(self.tree)
        self.tree.clearSelection()
        for object_id in object_ids:
            if object_id in self.items:
                for item in self.rows[object_id]:
                    item.setSelected(True)
        del blocker
        self.state_changed.emit()

    def _selection_requested(self):
        if self.document is not None:
            self.selection_requested.emit(list(dict.fromkeys(item.data(0, ROLE) for item in self.tree.selectedItems()
                                           if item.data(0, ROLE) in self.items)))

    def _visibility_requested(self, item, column):
        object_id = item.data(0, ROLE)
        if self.document is not None and object_id in self.document.objects:
            if not self._mutable:
                self.refresh(object_id)
                return
            if item.text(0) != self.document.objects[object_id].name:
                self.rename_requested.emit(object_id, item.text(0))
            visible = item.checkState(0) == Qt.CheckState.Checked
            if visible != self.document.objects[object_id].visible:
                self.visibility_requested.emit(object_id, visible)

    def _set_item_mutable(self, item):
        flags = Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable
        item.setFlags(item.flags() | flags if self._mutable else item.flags() & ~flags)

    def set_mutable(self, mutable):
        if self._mutable == mutable:
            return
        self._mutable = mutable
        blocker = QSignalBlocker(self.tree)
        for rows in self.rows.values():
            for item in rows:
                self._set_item_mutable(item)
        del blocker
