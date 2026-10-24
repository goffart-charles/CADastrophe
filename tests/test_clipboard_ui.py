"""Real Windows MIME clipboard, Qt shortcuts and native AIS/document isolation."""
from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest
from PySide6.QtCore import QMimeData, Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QTextEdit, QDoubleSpinBox, QComboBox

from cadastrophe.sample_scene import demo_document
from cadastrophe.app import MainWindow
from cadastrophe.core.clipboard import MIME_TYPE
from cadastrophe.ui.themes import apply_theme


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    clipboard = app.clipboard()
    snapshot = QMimeData()
    original = clipboard.mimeData()
    if original is not None:
        for mime in original.formats():
            snapshot.setData(mime, original.data(mime))
    clipboard.clear()
    root = tmp_path / "plugins"
    shutil.copytree(Path(__file__).resolve().parents[1] / "plugins/primitives", root / "primitives")
    window = MainWindow(plugin_directory=root)
    window.documents.replace(window.viewport.document.id, demo_document())
    window.show()
    window.activateWindow()
    app.processEvents()
    assert QTest.qWaitForWindowActive(window, 1000)
    try:
        yield window
    finally:
        window.tool_sessions.cancel()
        window.confirm_discard = lambda document: True
        window.close()
        window.deleteLater()
        app.processEvents()
        clipboard.setMimeData(snapshot)
        apply_theme(app)


def bodies(document):
    return [obj for obj in document.objects.values() if not obj.plane]


def pose(viewport):
    camera = viewport.view.Camera()
    return (*camera.Eye().Coord(), *camera.Center().Coord(), *camera.Up().Coord(), camera.Scale())


def ctrl(widget, key):
    widget.setFocus()
    QApplication.processEvents()
    QTest.keyClick(widget, key, Qt.KeyboardModifier.ControlModifier)
    QTest.keyRelease(widget, Qt.Key.Key_Control)


def test_real_keyboard_copy_paste_cut_across_documents_and_independent_undo(window):
    source, first_view = window.viewport.document, window.viewport
    first, second, *_ = bodies(source)
    source.select_objects([first.id, second.id])
    source_pose = pose(first_view)
    ctrl(window.viewport, Qt.Key.Key_C)
    mime = QApplication.clipboard().mimeData()
    assert mime.hasFormat(MIME_TYPE) and not mime.hasText()
    assert source.history.count == 0 and not source.is_modified
    target = window.create_document()
    QApplication.processEvents()
    target_pose = pose(window.viewport)
    before = tuple(target.objects)
    ctrl(window.viewport, Qt.Key.Key_V)
    pasted = target.selected_ids
    assert len(pasted) == 2 and target.history.count == 1
    assert not set(pasted) & set(source.objects)
    for source_obj, object_id in zip((first, second), pasted):
        obj = target.objects[object_id]
        assert not obj.shape.IsPartner(source_obj.shape) and obj.color == source_obj.color
        assert object_id in window.tree_items
        assert window.viewport.context.IsDisplayed(window.viewport.presentations[object_id])
    ctrl(window.viewport, Qt.Key.Key_Z)
    assert tuple(target.objects) == before and not target.is_modified
    ctrl(window.viewport, Qt.Key.Key_Y)
    assert target.selected_ids == pasted
    assert pose(window.viewport) == target_pose and pose(first_view) == source_pose
    window.documents.activate(source.id)
    ctrl(window.tree, Qt.Key.Key_X)
    assert first.id not in source.objects and second.id not in source.objects
    assert source.history.count == 1
    ctrl(window.tree, Qt.Key.Key_Z)
    assert first.id in source.objects and source.selected_ids == (first.id, second.id)
    assert target.selected_ids == pasted and target.history.count == 1
    assert pose(first_view) == source_pose


def test_snapshot_survives_source_close_and_primitive_plugin_deactivation(window):
    source = window.viewport.document
    assert window.command_registry.execute("cadastrophe.primitives.box")
    assert window.command_registry.execute("tool.commit")
    original = source.objects[source.selected_ids[0]]
    assert window.command_registry.execute("edit.copy")
    window.confirm_discard = lambda document: True
    assert window.close_document()
    assert window.viewport is None and not window.command_registry.action("edit.paste").isEnabled()
    assert window.plugins.deactivate("cadastrophe.primitives")
    target = window.create_document()
    QApplication.processEvents()
    assert window.command_registry.execute("edit.paste")
    pasted = target.objects[target.selected_ids[0]]
    assert pasted.id != original.id and pasted.name == original.name
    assert not pasted.shape.IsPartner(original.shape)


def test_context_menu_uses_registry_actions_and_external_text_disables_object_paste(window):
    document = window.viewport.document
    body = bodies(document)[0]
    document.select_objects([body.id])
    menu = window.object_context_menu()
    for key in ("edit.copy", "edit.cut", "edit.paste"):
        assert window.command_registry.action(key) in menu.actions()
    menu.deleteLater()
    assert window.command_registry.execute("edit.copy")
    assert window.command_registry.action("edit.paste").isEnabled()
    QApplication.clipboard().setText("texte externe")
    QApplication.processEvents()
    assert not window.command_registry.action("edit.paste").isEnabled()
    assert not window.command_registry.execute("edit.paste")
    assert not document.is_modified


def test_face_plane_mixed_selection_and_tool_session_reject_clipboard_operations(window):
    viewport, document = window.viewport, window.viewport.document
    x, y = viewport.view.Convert(-55., -55., 20.)
    scale = viewport.devicePixelRatioF()
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=QPoint(round(x / scale), round(y / scale)))
    face = viewport.selection.items()[0]
    assert face.topology == "Face"
    for key in ("edit.copy", "edit.cut"):
        assert not window.command_registry.action(key).isEnabled()
    window.copy_selected(cut=True)
    assert "sous-formes" in window.hint.text()
    assert viewport.selection.items()[0].shape.IsSame(face.shape) and document.history.count == 0
    plane = next(obj for obj in document.objects.values() if obj.plane)
    body = bodies(document)[0]
    for ids in ([plane.id], [plane.id, body.id]):
        document.select_objects(ids)
        assert not window.command_registry.action("edit.copy").isEnabled()
        assert not window.command_registry.action("edit.cut").isEnabled()
    document.select_objects([body.id])
    assert window.command_registry.execute("edit.copy")
    raw = bytes(QApplication.clipboard().mimeData().data(MIME_TYPE))
    assert window.command_registry.execute("cadastrophe.primitives.box")
    for key in ("edit.copy", "edit.cut", "edit.paste"):
        assert not window.command_registry.execute(key)
    window.copy_selected(cut=True)
    window.paste_objects()
    assert document.history.count == 0 and body.id in document.objects
    assert bytes(QApplication.clipboard().mimeData().data(MIME_TYPE)) == raw
    window.tool_sessions.cancel()
    assert window.command_registry.action("edit.copy").isEnabled()
    assert window.command_registry.action("edit.paste").isEnabled()


@pytest.mark.parametrize("editor_type", [QLineEdit, QPlainTextEdit, QTextEdit, QDoubleSpinBox, QComboBox])
def test_text_shortcuts_have_priority_even_when_object_copy_cut_paste_are_available(window, editor_type):
    document = window.viewport.document
    obj = bodies(document)[0]
    document.select_objects([obj.id])
    assert window.command_registry.execute("edit.copy")
    editor = editor_type()
    window.workspace_layout.addWidget(editor)
    if isinstance(editor, QComboBox):
        editor.setEditable(True)
        editor.setEditText("texte")
        field = editor.lineEdit()
    elif isinstance(editor, QDoubleSpinBox):
        editor.setValue(12.34)
        field = editor.lineEdit()
    else:
        field = editor
        if isinstance(editor, QLineEdit): editor.setText("texte")
        else: editor.setPlainText("texte")
    field.selectAll()
    ctrl(field, Qt.Key.Key_C)
    assert QApplication.clipboard().mimeData().hasText()
    assert not QApplication.clipboard().mimeData().hasFormat(MIME_TYPE)
    ctrl(field, Qt.Key.Key_X)
    assert obj.id in document.objects and document.history.count == 0
    ctrl(field, Qt.Key.Key_V)
    assert document.history.count == 0 and not document.is_modified
    assert document.selected_ids == (obj.id,)


def test_invalid_mime_reports_error_without_a_dialog_or_partial_insertion(window):
    document = window.viewport.document
    obj = bodies(document)[0]
    document.select_objects([obj.id])
    assert window.command_registry.execute("edit.copy")
    data = json.loads(bytes(QApplication.clipboard().mimeData().data(MIME_TYPE)))
    data["schema_version"] = 99
    mime = QMimeData()
    mime.setData(MIME_TYPE, json.dumps(data).encode("utf-8"))
    QApplication.clipboard().setMimeData(mime)
    QApplication.processEvents()
    before = tuple(document.objects), pose(window.viewport)
    assert window.command_registry.execute("edit.paste")
    assert "Version de presse-papiers non prise en charge" in window.hint.text()
    assert (tuple(document.objects), pose(window.viewport)) == before
    assert not document.is_modified and QApplication.activeModalWidget() is None


def test_display_units_do_not_scale_pasted_shapes_or_change_camera(window):
    source = window.viewport.document
    obj = bodies(source)[0]
    source.select_objects([obj.id])
    assert window.command_registry.execute("edit.copy")
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps
    before = GProp_GProps()
    brepgprop.VolumeProperties(obj.shape, before)
    window.apply_preferences(replace(window.preferences, length_unit="in"))
    target = window.create_document()
    QApplication.processEvents()
    camera = pose(window.viewport)
    assert window.command_registry.execute("edit.paste")
    after = GProp_GProps()
    brepgprop.VolumeProperties(target.objects[target.selected_ids[0]].shape, after)
    assert after.Mass() == pytest.approx(before.Mass()) and target.length_unit == "mm"
    assert pose(window.viewport) == camera
