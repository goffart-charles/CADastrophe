"""One interactive tool per application window; previews never enter the document."""
from typing import Protocol

from PySide6.QtCore import QObject, QTimer, Signal

from ..document import SceneObject
from ..geometry import WorkPlane


class ToolSession(Protocol):
    title: str

    def create_editor(self, parent, changed):
        """Return a child QWidget; call changed() whenever parameters change."""
        ...

    def preview(self) -> SceneObject | None:
        """Read/validate current inputs and build a detached snapshot, or raise."""
        ...

    def commit(self, document, preview: SceneObject) -> None:
        """Mutate through the document API inside the host's transaction."""
        ...

    def dispose(self) -> None:
        """Release any resources not owned by the parameter widget."""
        ...


class EditSession(Protocol):
    """Persistent editor: validated gestures use normal document undo/save."""
    title: str
    object_id: str
    workplane: WorkPlane

    def create_editor(self, parent, changed): ...
    def is_valid(self, document) -> bool: ...
    def refresh(self, document) -> None: ...
    def dispose(self) -> None: ...


class ToolSessionManager(QObject):
    changed = Signal()
    error = Signal(str)

    def __init__(self, documents, host, parent=None):
        super().__init__(parent)
        self.documents, self.host = documents, host
        self.tool = self.document = self.owner = self.preview_object = None
        self.editor = None
        self.can_commit = False
        self.message = ""
        self._working = False
        self._committing = False
        self._preview_revision = 0
        self._preview_task = None
        self._commit_requested = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(75)
        self._timer.timeout.connect(lambda: self.update_preview(interactive=True))
        documents.about_to_change.connect(self.end_edit)

    @property
    def active(self):
        return self.tool is not None or self.editor is not None

    @property
    def editing(self):
        return self.editor is not None

    @property
    def available(self):
        return (not self.active and not self._working and not self._committing and self.documents.active_document is not None
                and not self.documents.blocked and self.host.ready)

    @property
    def can_start_from_sketch(self):
        from .feature_objects import SketchSourceObject
        return bool(self.editing and not self._working and not self._committing
                    and not self.documents.blocked and self.host.ready
                    and isinstance(self.document.objects.get(self.editor.object_id), SketchSourceObject))

    def start_from_sketch(self, owner, factory):
        """Finish the current sketch before constructing the consuming tool."""
        if not self.can_start_from_sketch or not self.editor.is_valid(self.document):
            return False
        document, key = self.document, self.editor.object_id
        self.host.cancel_edit_gesture()
        if not self.end_edit() or key not in document.objects:
            return False
        document.select_objects((key,))
        return self.start(owner, factory())

    def start(self, owner, tool: ToolSession):
        if not self.available:
            return False
        if not isinstance(tool.title, str) or not tool.title.strip() or not all(
                callable(getattr(tool, name, None)) for name in ("create_editor", "preview", "commit", "dispose")):
            raise ValueError("Contrat de session interactive invalide.")
        self.owner, self.tool, self.document = owner, tool, self.documents.active_document
        self.document.parameter_scope_changed.connect(self.configuration_scope_changed)
        self._working = True
        self.document.history.reserve(self)
        try:
            self.host.open(tool.title, tool.create_editor, self.request_preview)
            if callable(getattr(tool, "attach", None)):
                self.host.attach_tool(tool)
        except (Exception, SystemExit) as exc:
            self._finish()
            self.error.emit(f"Démarrage de l’outil impossible : {exc}")
            return False
        finally:
            self._working = False
            self.changed.emit()
        self.update_preview(interactive=True)
        return self.active

    def configuration_scope_changed(self, *_):
        invalidate = getattr(self.tool, "invalidate_preview", None)
        if callable(invalidate):
            invalidate()
        self.request_preview()

    def request_preview(self):
        if self.editing:
            self.refresh_edit()
            return
        if self.tool is not None:
            # Input signals may arrive during a native calculation/presentation.
            # Remember them even while reentrant actions are otherwise blocked.
            self._preview_revision += 1
            self._commit_requested = False
            self._cancel_preview()
            self.can_commit = False
            self.preview_object = None
            self.message = "Mise à jour de l’aperçu…"
            if not self._working:
                self.changed.emit()
                self._timer.start()

    def _cancel_preview(self):
        task, self._preview_task = self._preview_task, None
        if task is not None:
            task.cancel()

    def _show_preview(self, obj, revision):
        if obj is not None and (not isinstance(obj, SceneObject) or obj.shape is None or obj.shape.IsNull()):
            raise ValueError("L’aperçu doit être un objet B-Rep autonome.")
        if self.documents.active_document is not self.document:
            raise ValueError("Le document source n’est plus actif.")
        if revision != self._preview_revision:
            self.host.clear_preview()
            return
        self.host.preview(obj)
        if self.documents.active_document is not self.document:
            raise ValueError("Le document source n’est plus actif.")
        if revision != self._preview_revision:
            self.host.clear_preview()
            return
        self.preview_object = obj
        commit_error = getattr(self.tool, "commit_error", "")
        self.can_commit = obj is not None and not commit_error
        self.message = commit_error or getattr(self.tool, "rebuild_warning", "") or ("Entrée : valider · Échap : annuler" if obj is not None else "Renseignez les paramètres de l’outil.")

    def _start_preview(self, revision):
        tool = self.tool
        def current():
            return self.tool is tool and revision == self._preview_revision
        def completed(obj):
            if not current():
                return
            self._preview_task = None
            self._working = True
            try:
                self._show_preview(obj, revision)
            except (Exception, SystemExit) as exc:
                self._preview_error(exc)
            finally:
                self._working = False
                if self.tool is tool and revision != self._preview_revision:
                    self.can_commit = False
                    self.preview_object = None
                    self.message = "Mise à jour de l’aperçu…"
                    self._timer.start()
                self.changed.emit()
            if self._commit_requested and self.can_commit:
                self._commit_requested = False
                QTimer.singleShot(0, lambda: self.commit() if current() else None)
        def failed(error):
            if current():
                self._preview_task = None
                self._commit_requested = False
                self._preview_error(error)
                self.changed.emit()
        def cancelled():
            if current():
                self._preview_task = None
                self._commit_requested = False
                self.message = "Calcul de l’aperçu annulé. Modifiez les paramètres pour réessayer."
                self.changed.emit()
        self.message = "Calcul de l’aperçu…"
        self._preview_task = self.host.run_preview(tool.preview_steps(), tool.title,
            completed=completed, failed=failed, cancelled=cancelled)

    def _preview_error(self, error):
        self.can_commit = False
        self.preview_object = None
        self.message = f"Aperçu impossible : {error}"
        try:
            self.host.clear_preview()
        except (Exception, SystemExit) as cleanup_error:
            self._finish()
            self.error.emit(f"Aperçu interrompu : {cleanup_error}")

    def update_preview(self, *, interactive=False):
        if self.editing or not self.active or self._working:
            return
        self._timer.stop()
        self._working = True
        self._cancel_preview()
        self.can_commit = False
        self.preview_object = None
        revision = self._preview_revision
        try:
            prepare = getattr(self.host, "prepare_preview", None)
            if callable(prepare):
                prepare()
            revision = self._preview_revision
            if (interactive and getattr(self.tool, "cooperative_preview", False)
                    and callable(getattr(self.tool, "preview_steps", None))
                    and callable(getattr(self.host, "run_preview", None))):
                self.host.clear_preview()
                self._start_preview(revision)
                return
            obj = self.tool.preview()
            self._show_preview(obj, revision)
        except (Exception, SystemExit) as exc:
            self._preview_error(exc)
        finally:
            self._working = False
            if self.tool is not None and revision != self._preview_revision:
                self.can_commit = False
                self.preview_object = None
                self.message = "Mise à jour de l’aperçu…"
                self._timer.start()
            self.changed.emit()

    def commit_interactive(self):
        """Queue validation of the current candidate without draining native work."""
        if self.editing:
            return self.end_edit()
        if not self.active or self._working or self._committing:
            return False
        prepare = getattr(self.host, "prepare_preview", None)
        if callable(prepare):
            prepare()
        if not getattr(self.tool, "cooperative_preview", False):
            return self.commit()
        if self.can_commit:
            return self.commit()
        if self._preview_task is None:
            self.update_preview(interactive=True)
        self._commit_requested = self._preview_task is not None
        return self._commit_requested

    def commit(self):
        if self.editing:
            return self.end_edit()
        if not self.active or self._working or self._committing:
            return False
        self._committing = True
        try:
            return self._commit_current()
        finally:
            self._committing = False
            self.changed.emit()

    def _commit_current(self):
        # Also interprets pending editor text: never commit an old valid preview.
        tool = self.tool
        self.update_preview()
        if self.tool is not tool or not self.can_commit:
            return False
        document, tool, obj = self.document, self.tool, self.preview_object
        insertion_position = document.history_context.position
        original_ids = set(document.objects)
        selected = document.selected_ids
        self._working = True
        success = False
        try:
            self.host.clear_preview()
            with document.history.commit_reserved(self, tool.title):
                tool.commit(document, obj)
            success = True
        except (Exception, SystemExit) as exc:
            document.select_objects([key for key in selected if key in document.objects])
            self.error.emit(f"Validation de l’outil annulée : {exc}")
        finally:
            self._finish()
            self._working = False
            self.changed.emit()
        if success and insertion_position is not None:
            from .feature_objects import ConstructionFeature
            inserted = [key for key in document.objects if key not in original_ids
                        and isinstance(document.objects[key], ConstructionFeature)]
            if inserted:
                document.history_context.set_position(inserted[-1])
        return success

    def cancel(self):
        if self.editing:
            # Escape abandons only the current gesture, never the whole editor.
            self.host.cancel_edit_gesture()
            return True
        if not self.active or self._working or self._committing:
            return False
        self._working = True
        try:
            self._finish()
        finally:
            self._working = False
            self.changed.emit()
        return True

    def cancel_owner(self, owner):
        if self.owner == owner:
            self.end_edit() if self.editing else self.cancel()

    def editor_command_enabled(self, command_id):
        predicate = getattr(self.editor, "command_enabled", None)
        return bool(self.editing and callable(predicate) and predicate(command_id))

    def execute_editor_command(self, command_id):
        if not self.editor_command_enabled(command_id):
            return False
        try:
            return self.editor.execute_command(command_id)
        except (Exception, SystemExit) as exc:
            self.error.emit(f"Commande d’édition impossible : {exc}")
            return False

    def start_edit(self, owner, editor: EditSession, *, new_object=None):
        if not self.available:
            return False
        if (not isinstance(editor.title, str) or not editor.title.strip()
                or not isinstance(editor.workplane, WorkPlane) or not all(
                    callable(getattr(editor, name, None)) for name in
                    ("create_editor", "is_valid", "refresh", "dispose"))):
            raise ValueError("Contrat d’édition invalide.")
        document = self.documents.active_document
        if new_object is not None and (not isinstance(new_object, SceneObject) or new_object.id != editor.object_id):
            raise ValueError("L’objet créé doit correspondre à l’éditeur.")
        self.owner, self.editor, self.document = owner, editor, document
        document.configuration_editing = True
        self._working = True
        connected = False
        try:
            # Roll back creation if native mode/panel startup fails. Do not keep
            # this transaction or a history reservation during actual editing.
            with document.history.transaction(editor.title):
                if new_object is not None:
                    document.add(new_object)
                if editor.object_id not in document.objects or not editor.is_valid(document):
                    raise ValueError("Support ou objet d’édition indisponible.")
                self.host.open_edit(editor)
                editor.refresh(document)
            document.object_changed.connect(self.refresh_edit)
            document.object_removed.connect(self.refresh_edit)
            document.cleared.connect(self.refresh_edit)
            connected = True
            self.can_commit = True
            self.message = "Milieu : rotation · Ctrl+milieu : pan · Alt+milieu : roulis · Ctrl+8 : vue normale"
        except (Exception, SystemExit) as exc:
            self._finish_edit(connected=connected)
            self.error.emit(f"Édition impossible : {exc}")
            return False
        finally:
            self._working = False
            self.changed.emit()
        return self.editing

    def refresh_edit(self, *_):
        if not self.editing or self._working:
            return
        try:
            if not self.editor.is_valid(self.document):
                self.end_edit()
                return
            self.editor.refresh(self.document)
        except (Exception, SystemExit) as exc:
            self.end_edit()
            self.error.emit(f"Édition interrompue : {exc}")

    def end_edit(self):
        if not self.editing or self._working:
            return False
        self._working = True
        try:
            self._finish_edit()
        finally:
            self._working = False
            self.changed.emit()
        return True

    def _finish_edit(self, *, connected=True):
        document, editor = self.document, self.editor
        document.configuration_editing = False
        self.editor = self.document = self.owner = None
        self.can_commit = False
        self.message = ""
        if connected:
            document.object_changed.disconnect(self.refresh_edit)
            document.object_removed.disconnect(self.refresh_edit)
            document.cleared.disconnect(self.refresh_edit)
        finish = getattr(editor, "finish_edit", None)
        for cleanup in (lambda: self.host.close_edit(restore_camera=not connected),
                        lambda: finish(document) if connected and callable(finish) else None, editor.dispose):
            try:
                cleanup()
            except (Exception, SystemExit) as exc:
                self.error.emit(f"Nettoyage de l’édition : {exc}")

    def _finish(self):
        self._timer.stop()
        self._commit_requested = False
        self._cancel_preview()
        document, tool = self.document, self.tool
        document.parameter_scope_changed.disconnect(self.configuration_scope_changed)
        # Drop the session before notifying UI/lifecycle listeners on release.
        self.tool = self.document = self.owner = self.preview_object = None
        self.can_commit = False
        self.message = ""
        try:
            for cleanup in (self.host.clear_preview, self.host.close, tool.dispose):
                try:
                    cleanup()
                except (Exception, SystemExit) as exc:
                    self.error.emit(f"Nettoyage de l’outil : {exc}")
        finally:
            document.history.release(self)
            self.changed.emit()
