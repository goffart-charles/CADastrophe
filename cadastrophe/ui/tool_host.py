"""Contextual parameter panels and window-scoped keyboard routing for tools."""
from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtWidgets import QApplication, QWidget, QTextEdit, QPlainTextEdit, QDialogButtonBox, QAbstractSpinBox

from ..preview import ViewportPreview
from ..presentation import PlaneEditSurface, ToolInteractionSurface
from .property_panel import PropertyPanel


class ToolUiHost(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.panel = self.overlay = self.previous_page = None
        self._consumed = set()
        self.edit_viewport = None
        self.tool_surface = None
        self.previous_command_tab = None
        self.edit_surface = self.edit_session = None
        QApplication.instance().installEventFilter(self)

    @property
    def ready(self):
        viewport = self.window.viewport
        return viewport is not None and viewport.camera is not None and not viewport._failed

    def open(self, title, factory, changed):
        pane = self.window.manager_pane
        self.previous_page = pane.currentWidget()
        self.panel = PropertyPanel(title, pane.properties_stack)
        editor = factory(self.panel, changed)
        if not isinstance(editor, QWidget) or editor is self.panel:
            raise ValueError("L’éditeur de paramètres doit être un QWidget enfant.")
        self.panel.set_editor(editor)
        document = self.window.services.active_document
        if document is not None and (len(document.configurations) > 1 or document.configuration_state.common_values):
            from .configurations import ParameterScope
            scope = ParameterScope(document)
            self.panel.sections.insertWidget(0, scope)
        self.panel.accepted.connect(lambda: self.window.command_registry.execute("tool.commit"))
        self.panel.rejected.connect(lambda: self.window.command_registry.execute("view.cancel"))
        pane.properties_stack.addWidget(self.panel)
        pane.properties_stack.setCurrentWidget(self.panel)
        pane.setCurrentWidget(pane.properties_stack)
        self.window.command_registry.action("window.tree").setChecked(True)
        self.overlay = ViewportPreview(self.window.viewport)
        self.window.viewport.cancel_gesture()
        editor.setFocus(Qt.FocusReason.OtherFocusReason)

    def prepare_preview(self):
        # Accept pending valid text before capturing the calculation revision.
        # Leave invalid text untouched so the tool can report it faithfully.
        for field in self.panel.findChildren(QAbstractSpinBox):
            if field.isEnabled() and field.hasAcceptableInput():
                field.interpretText()

    def run_preview(self, steps, title, **callbacks):
        return self.window.services.run_computation(steps, "Aperçu · " + title, preserve_focus=True, **callbacks)

    def preview(self, obj):
        tool = self.window.tool_sessions.tool
        self.overlay.show(obj, hidden_ids=getattr(tool, "preview_hidden_ids", ()),
                          parts=getattr(tool, "preview_parts", None),
                          added_only=getattr(tool, "preview_added_only", False),
                          opaque=getattr(tool, "preview_opaque", False))

    def open_edit(self, session):
        self.previous_command_tab = self.window.command_manager.currentIndex()
        self.edit_viewport = self.window.viewport
        history_display = getattr(session, "history_display", None)
        if callable(history_display):
            self.edit_viewport.document_adapter.set_history_display(*history_display(self.edit_viewport.document))
        self.edit_viewport.begin_plane_edit(session.object_id, session.workplane)
        self.open(session.title, session.create_editor, self.window.tool_sessions.refresh_edit)
        self.panel.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Quitter l’esquisse")
        self.panel.buttons.button(QDialogButtonBox.StandardButton.Cancel).hide()
        self.panel.layout().removeWidget(self.panel.buttons)
        self.panel.layout().insertWidget(1,self.panel.buttons,0,Qt.AlignmentFlag.AlignLeft)
        self.panel.status.hide()  # Context instructions belong to the editor.
        self.edit_session = session
        if callable(getattr(session, "attach", None)):
            if not callable(getattr(session, "detach", None)):
                raise ValueError("Un éditeur interactif doit fournir attach et detach.")
            self.edit_surface = PlaneEditSurface(self.edit_viewport, clipboard=self.window.services.clipboard,
                                                 state_changed=self.window.tool_sessions.changed.emit)
            session.attach(self.edit_surface)
        self.window.command_manager.setCurrentIndex(1)
        self.edit_viewport.setFocus(Qt.FocusReason.OtherFocusReason)
        self.edit_viewport.animate_plane_entry()

    def close_edit(self, *, restore_camera=False):
        viewport, self.edit_viewport = self.edit_viewport, None
        try:
            if self.edit_surface is not None:
                self.edit_surface.close()
                self.edit_surface = None
            if self.edit_session is not None:
                session, self.edit_session = self.edit_session, None
                if callable(getattr(session, "detach", None)):
                    session.detach()
        finally:
            if viewport is not None:
                viewport.document_adapter.set_history_display()
                viewport.end_plane_edit(restore_camera=restore_camera)
            self.close()
            if self.previous_command_tab is not None:
                self.window.command_manager.setCurrentIndex(self.previous_command_tab)
                self.previous_command_tab = None

    def cancel_edit_gesture(self):
        if self.edit_session is not None and callable(getattr(self.edit_session, "cancel_operation", None)):
            self.edit_session.cancel_operation()
        if self.edit_viewport is not None:
            self.edit_viewport.cancel_gesture()

    def clear_preview(self):
        if self.overlay is not None:
            self.overlay.clear()

    def attach_tool(self, tool):
        if callable(getattr(tool, "attach", None)):
            self.tool_surface = ToolInteractionSurface(self.window.viewport, tool.clicked, getattr(tool, "hovered", None))
            tool.attach(self.tool_surface)

    def close(self):
        if self.tool_surface is not None:
            self.tool_surface.close()
            self.tool_surface = None
        pane = self.window.manager_pane
        if self.panel is not None:
            pane.properties_stack.removeWidget(self.panel)
            self.panel.hide()
            self.panel.deleteLater()
            self.panel = None
        pane.properties_stack.setCurrentIndex(0)
        if self.previous_page is not None and pane.indexOf(self.previous_page) >= 0:
            pane.setCurrentWidget(self.previous_page)
        self.overlay = self.previous_page = None
        if self.window.viewport is not None:
            self.window.viewport.cancel_gesture()
            self.window.viewport.setFocus(Qt.FocusReason.OtherFocusReason)

    def refresh(self):
        sessions = self.window.tool_sessions
        if self.panel is not None:
            self.panel.set_validation(sessions.can_commit, sessions.message, sessions.message_level)

    def eventFilter(self, watched, event):
        kind = event.type()
        if watched is self.window and kind == QEvent.Type.WindowDeactivate:
            self._consumed.clear()
        if kind not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return False
        if not isinstance(watched, QWidget) or not (watched is self.window or self.window.isAncestorOf(watched)):
            return False
        key = event.key()
        if kind == QEvent.Type.KeyRelease and key in self._consumed:
            self._consumed.discard(key)
            return True
        sessions = getattr(self.window, "tool_sessions", None)
        if (sessions is None or not sessions.active or QApplication.activeModalWidget() is not None
                or QApplication.activePopupWidget() is not None):
            return False
        if key not in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter) or event.modifiers() != Qt.KeyboardModifier.NoModifier:
            return False
        # Multiline text keeps its newline; other parameter editors use Enter to commit.
        if key != Qt.Key.Key_Escape and isinstance(QApplication.focusWidget(), (QTextEdit, QPlainTextEdit)):
            return False
        event.accept()
        if kind == QEvent.Type.KeyPress:
            if event.isAutoRepeat() or key in self._consumed:
                return True
            self._consumed.add(key)
            if key == Qt.Key.Key_Escape:
                self.window.command_registry.execute("view.cancel")
            elif not sessions.editing:
                # Commit validates current input, including uncommitted spin-box text.
                self.window.command_registry.execute("tool.commit")
            elif callable(getattr(sessions.editor, "confirm_operation", None)):
                sessions.editor.confirm_operation()
        return True
