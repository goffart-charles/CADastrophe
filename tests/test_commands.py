"""Registry contracts and actual Qt shortcut dispatch (no mocked key handling)."""
import pytest
from PySide6.QtCore import Qt, QMimeData
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QMenu,
    QToolBar, QVBoxLayout, QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox)

from cadastrophe.core.commands import CommandRegistry, ShortcutConflictError
from cadastrophe.ui.themes import apply_theme


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    return app


@pytest.fixture
def surface(qt_app):
    window = QMainWindow()
    body = QWidget()
    body.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    window.setCentralWidget(body)
    QVBoxLayout(body)
    registry = CommandRegistry(window)
    window.resize(600, 400)
    window.show()
    try:
        window.raise_()
        window.activateWindow()
        qt_app.processEvents()
        assert QTest.qWaitForWindowActive(window, 1000)
        body.setFocus()
        yield window, registry
    finally:
        window.close()
        window.deleteLater()
        qt_app.processEvents()


@pytest.fixture
def preserved_clipboard(qt_app):
    clipboard = qt_app.clipboard()
    snapshot = QMimeData()
    original = clipboard.mimeData()
    if original is not None:
        for mime in original.formats():
            snapshot.setData(mime, original.data(mime))
    # A locked/restricted Windows clipboard cannot validate copy/paste. Probe
    # the OS independently of shortcuts rather than misdiagnosing a UI change.
    marker = "cadastrophe-clipboard-test-probe"
    clipboard.setText(marker)
    if clipboard.text() != marker:
        clipboard.setMimeData(snapshot)
        pytest.skip("System clipboard round-trip unavailable (also fails without the theme)")
    # Windows can deliver dataChanged on the next Qt turn, after text is readable.
    # Let the real notification refresh action availability before asserting it.
    qt_app.processEvents()
    yield
    clipboard.setMimeData(snapshot)


def test_metadata_single_execution_and_enabled_state(surface):
    window, registry = surface
    calls = []
    action = registry.register("test.run", "Exécuter", "Test", lambda: calls.append(True), shortcuts=("F",))
    command = registry.command("test.run")
    assert (command.id, command.label, command.category, command.shortcuts) == (
        "test.run", "Exécuter", "Test", ("F",))
    assert action is registry.action("test.run") and action in window.actions()
    assert registry.execute("test.run") and calls == [True]
    registry.set_enabled("test.run", False)
    assert not command.enabled and not registry.execute("test.run")
    action.trigger()
    assert calls == [True]


def test_missing_implementation_stays_disabled(surface):
    _, registry = surface
    action = registry.register("future", "À venir", "Test", shortcuts=("Ctrl+S",), reason="Absent")
    assert not action.isEnabled() and action.toolTip() == "Absent"
    assert not registry.execute("future")
    with pytest.raises(ValueError, match="not implemented"):
        registry.set_enabled("future", True)
    with pytest.raises(ShortcutConflictError, match="future"):
        registry.register("steal", "Collision", "Test", lambda: None, shortcuts=("Ctrl+S",))


@pytest.mark.parametrize("command_id", ["", "existing"])
def test_duplicate_or_empty_ids_are_rejected(surface, command_id):
    _, registry = surface
    registry.register("existing", "Test", "Test", lambda: None)
    with pytest.raises(ValueError):
        registry.register(command_id, "Test", "Test", lambda: None)
    assert len(list(registry)) == 1


@pytest.mark.parametrize("first,second", [
    ("Ctrl+K", "Ctrl+K"), ("Ctrl+K", "Ctrl+K, Ctrl+C"), ("Ctrl+K, Ctrl+C", "Ctrl+K")])
def test_conflicts_and_prefixes_are_rejected_atomically(surface, first, second):
    _, registry = surface
    registry.register("first", "Premier", "Test", lambda: None, shortcuts=(first,))
    with pytest.raises(ShortcutConflictError, match="first"):
        registry.register("second", "Second", "Test", lambda: None, shortcuts=("F", second))
    assert [command.id for command in registry] == ["first"]
    # The non-conflicting first candidate was not accidentally reserved.
    registry.register("free", "Libre", "Test", lambda: None, shortcuts=("F",))


def test_rebinding_preserves_old_keys_on_error_and_releases_on_success(surface):
    _, registry = surface
    registry.register("first", "Premier", "Test", lambda: None, shortcuts=("F",))
    registry.register("second", "Second", "Test", lambda: None, shortcuts=("Z",))
    with pytest.raises(ShortcutConflictError):
        registry.set_shortcuts("first", ("X", "Z"))
    assert registry.command("first").shortcuts == ("F",)
    registry.set_shortcuts("first", ("X", "X"))
    assert registry.command("first").shortcuts == ("X",)
    registry.set_shortcuts("second", ("F",))
    with pytest.raises(ShortcutConflictError):
        registry.set_shortcuts("second", ("Ctrl+L", "Ctrl+L, Ctrl+C"))
    assert registry.command("second").shortcuts == ("F",)


@pytest.mark.parametrize("keys", [("",), ("not-a-valid-key",), "Ctrl+S"])
def test_invalid_shortcuts_rejected_without_partial_registration(surface, keys):
    _, registry = surface
    with pytest.raises((ValueError, TypeError)):
        registry.register("invalid", "Invalide", "Test", lambda: None, shortcuts=keys)
    assert list(registry) == []


def test_adopt_preserves_native_toggle_and_rejects_duplicate_action(surface):
    window, registry = surface
    native = QAction("Panneau", window)
    native.setCheckable(True)
    assert registry.adopt("panel", native, "Fenêtre") is native
    assert registry.execute("panel") and native.isChecked()
    with pytest.raises(ValueError, match="already registered"):
        registry.adopt("alias", native, "Fenêtre")


def test_menu_toolbar_and_shortcut_execute_same_action_once(surface, qt_app):
    window, registry = surface
    calls = []
    action = registry.register("run", "Exécuter", "Test", lambda: calls.append(True), shortcuts=("Ctrl+L",))
    menu = QMenu(window)
    menu.addAction(action)
    bar = QToolBar(window)
    window.addToolBar(bar)
    bar.addAction(action)
    menu.popup(window.mapToGlobal(window.rect().center()))
    qt_app.processEvents()
    QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center())
    assert calls == [True]
    bar.widgetForAction(action).click()
    assert calls == [True, True]
    window.activateWindow()
    window.centralWidget().setFocus()
    qt_app.processEvents()
    QTest.keyClick(window.centralWidget(), Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
    assert calls == [True, True, True]


@pytest.mark.parametrize("editor_type", [QLineEdit, QPlainTextEdit, QTextEdit])
def test_native_text_editing_wins_even_over_enabled_cad_actions(surface, qt_app, editor_type, preserved_clipboard):
    window, registry = surface
    calls = []
    for index, key in enumerate(("F", "Z", "Space", "Delete", "Escape", "Ctrl+A", "Ctrl+C",
                                 "Ctrl+X", "Ctrl+V", "Ctrl+Z", "Ctrl+Y", "Ctrl+Shift+Z")):
        registry.register(f"cad.{index}", key, "Test", lambda: calls.append(True), shortcuts=(key,))
    editor = editor_type()
    window.centralWidget().layout().addWidget(editor)
    editor.setFocus()
    qt_app.processEvents()
    text = editor.text if isinstance(editor, QLineEdit) else editor.toPlainText
    QTest.keyClicks(editor, "fz a")
    assert text() == "fz a"
    ctrl = Qt.KeyboardModifier.ControlModifier
    QTest.keyClick(editor, Qt.Key.Key_A, ctrl)
    selection = editor.selectedText() if isinstance(editor, QLineEdit) else editor.textCursor().selectedText()
    assert selection == "fz a" and calls == []
    QTest.keyClick(editor, Qt.Key.Key_C, ctrl)
    assert QApplication.clipboard().text() == "fz a"
    QTest.keyClick(editor, Qt.Key.Key_X, ctrl)
    assert text() == ""
    QTest.keyClick(editor, Qt.Key.Key_V, ctrl)
    assert text() == "fz a"
    QTest.keyClick(editor, Qt.Key.Key_Z, ctrl)
    assert text() == ""
    QTest.keyClick(editor, Qt.Key.Key_Y, ctrl)
    assert text() == "fz a"
    QTest.keyClick(editor, Qt.Key.Key_Z, ctrl | Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClick(editor, Qt.Key.Key_A, ctrl)
    QTest.keyClick(editor, Qt.Key.Key_Delete)
    assert text() == ""
    QTest.keyClick(editor, Qt.Key.Key_Escape)
    assert calls == []
    window.centralWidget().setFocus()
    qt_app.processEvents()
    QTest.keyClick(window.centralWidget(), Qt.Key.Key_C, ctrl)
    assert calls == [True]  # CAD shortcut resumes outside the editor


@pytest.mark.parametrize("kind", ["spin", "combo", "readonly"])
def test_nested_and_readonly_text_contexts_are_protected(surface, qt_app, kind, preserved_clipboard):
    window, registry = surface
    calls = []
    registry.register("copy", "Copier objets", "Test", lambda: calls.append(True), shortcuts=("Ctrl+C",))
    registry.register("select", "Sélection objets", "Test", lambda: calls.append(True), shortcuts=("Ctrl+A",))
    if kind == "spin":
        editor = QSpinBox()
        editor.setValue(42)
        target = editor.lineEdit()
    elif kind == "combo":
        editor = QComboBox()
        editor.setEditable(True)
        editor.setEditText("42")
        target = editor.lineEdit()
    else:
        editor = target = QLineEdit("42")
        editor.setReadOnly(True)
    window.centralWidget().layout().addWidget(editor)
    target.setFocus()
    qt_app.processEvents()
    QTest.keyClick(target, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert target.selectedText() == "42" and calls == []
    QTest.keyClick(target, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == "42" and calls == []


@pytest.fixture
def cad_window(qt_app, discard_document_changes):
    from cadastrophe.sample_scene import demo_document
    from cadastrophe.app import MainWindow
    window = MainWindow()
    window.documents.replace(window.viewport.document.id, demo_document())
    window.show()
    window.activateWindow()
    qt_app.processEvents()
    assert QTest.qWaitForWindowActive(window, 1000)
    yield window
    window.close()
    window.deleteLater()
    qt_app.processEvents()


def test_existing_actions_context_menu_and_availability(cad_window):
    window = cad_window
    registry = window.command_registry
    assert registry.command("view.fit").enabled
    assert not registry.command("object.delete").enabled
    assert not registry.command("object.frame").enabled
    context = window.object_context_menu()
    for command_id in ("view.fit", "object.show_all"):
        assert registry.action(command_id) in context.actions()
    body = next(obj for obj in window.viewport.document.objects.values() if not obj.plane)
    window.tree.setCurrentItem(window.tree_items[body.id])
    assert registry.command("object.delete").enabled and registry.command("object.frame").enabled
    plane = next(obj for obj in window.viewport.document.objects.values() if obj.plane)
    window.tree.clearSelection()
    window.tree.setCurrentItem(window.tree_items[plane.id])
    assert not registry.command("object.delete").enabled
    assert registry.command("view.normal_to_plane").enabled
    assert len({id(command.action) for command in registry}) == len(list(registry))
    context.deleteLater()


def test_document_shortcuts_remain_available_inside_text_fields(surface, qt_app):
    window, registry = surface
    calls = []
    registry.register("save", "Enregistrer", "Test", lambda: calls.append(True), shortcuts=("Ctrl+S",))
    editor = QLineEdit()
    window.centralWidget().layout().addWidget(editor)
    editor.setFocus()
    qt_app.processEvents()
    QTest.keyClick(editor, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert calls == [True]


def test_registry_respects_window_scope(surface, qt_app):
    window, registry = surface
    first_calls, second_calls = [], []
    registry.register("run", "Premier", "Test", lambda: first_calls.append(True), shortcuts=("F",))
    second = QMainWindow()
    body = QWidget()
    body.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    second.setCentralWidget(body)
    other = CommandRegistry(second)
    other.register("run", "Second", "Test", lambda: second_calls.append(True), shortcuts=("F",))
    try:
        second.show()
        second.activateWindow()
        qt_app.processEvents()
        assert QTest.qWaitForWindowActive(second, 1000)
        body.setFocus()
        QTest.keyClick(body, Qt.Key.Key_F)
        assert first_calls == [] and second_calls == [True]
        window.activateWindow()
        qt_app.processEvents()
        window.centralWidget().setFocus()
        QTest.keyClick(window.centralWidget(), Qt.Key.Key_F)
        assert first_calls == [True] and second_calls == [True]
    finally:
        second.close()
        second.deleteLater()


def test_initial_cad_actions_disabled_until_native_view_is_ready(qt_app):
    from cadastrophe.app import MainWindow
    window = MainWindow()
    try:
        for command_id in ("view.fit", "view.zoom_window", "view.standard.front",
                           "object.delete", "view.normal_to_plane", "view.render.edges"):
            assert not window.command_registry.command(command_id).enabled
        assert window.command_registry.command("view.cancel").enabled
    finally:
        window.close()
        window.deleteLater()


def test_document_commands_keep_shortcuts_and_require_content(cad_window, preserved_clipboard):
    registry = cad_window.command_registry
    expected = {"edit.undo": ("Ctrl+Z",),
                "edit.redo": ("Ctrl+Y", "Ctrl+Shift+Z"), "edit.copy": ("Ctrl+C",),
                "edit.cut": ("Ctrl+X",), "edit.paste": ("Ctrl+V",), "edit.select_all": ("Ctrl+A",)}
    for command_id, keys in expected.items():
        assert registry.command(command_id).shortcuts == keys
        assert not registry.execute(command_id)
    for command_id, keys in (("file.open", ("Ctrl+O",)), ("file.save", ("Ctrl+S",)),
                             ("file.save_as", ("Ctrl+Shift+S",))):
        assert registry.command(command_id).shortcuts == keys
        assert registry.command(command_id).implemented and registry.command(command_id).enabled


def test_isometric_aliases_and_escape_cancel_from_tree(cad_window, qt_app):
    window = cad_window
    registry = window.command_registry
    action = registry.action("view.standard.isometric")
    assert registry.command("view.standard.isometric").shortcuts == ("Ctrl+7", "0")
    calls = []
    action.triggered.connect(lambda: calls.append(True))
    window.viewport.setFocus()
    qt_app.processEvents()
    QTest.keyClick(window.viewport, Qt.Key.Key_7, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(window.viewport, Qt.Key.Key_0)
    assert calls == [True, True]
    assert registry.execute("view.zoom_window") and window.viewport.window_zoom
    window.tree.setFocus()
    qt_app.processEvents()
    QTest.keyClick(window.tree, Qt.Key.Key_Escape)
    assert not window.viewport.window_zoom
    assert window.viewport.state.button == ""
    plane = next(obj for obj in window.viewport.document.objects.values() if obj.plane)
    window.tree.setCurrentItem(window.tree_items[plane.id])
    assert registry.execute("view.normal_to_plane")
    assert window.viewport.workplane is plane.plane
    QTest.keyClick(window.tree, Qt.Key.Key_Escape)
    assert window.viewport.workplane is None


def test_render_controls_use_registry_without_selection_modes(cad_window):
    window = cad_window
    registry = window.command_registry
    assert registry.execute("view.render.wireframe")
    assert window.viewport.render_mode == "Filaire"
    next(action for action in window.render_menu.actions() if action.text() == "Ombré").trigger()
    assert registry.action("view.render.shaded").isChecked()
    assert not registry.action("view.render.wireframe").isChecked()
    assert not any(command.id.startswith("selection.filter.") for command in registry)
    assert not hasattr(window, "filter_menu")
