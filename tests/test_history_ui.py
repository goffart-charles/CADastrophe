"""Native shortcuts and tree/AIS projections while replaying document history."""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox

from cadastrophe.app import MainWindow
from cadastrophe.core.history import HistoryBusyError
from cadastrophe.document import ObjectType, SceneObject
from cadastrophe.sample_scene import demo_document
from cadastrophe.ui.themes import apply_theme


@pytest.fixture
def window(discard_document_changes):
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    window = MainWindow()
    window.documents.replace(window.viewport.document.id, demo_document())
    window.show()
    window.activateWindow()
    app.processEvents()
    assert window.viewport.camera and not window.viewport._failed
    try:
        yield window
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def bodies(window):
    return [obj for obj in window.viewport.document.objects.values() if obj.kind is ObjectType.BODY]


def pose(window):
    camera = window.viewport.view.Camera()
    return (*camera.Eye().Coord(), *camera.Center().Coord(), *camera.Up().Coord(), camera.Scale())


def key(widget, key, modifiers=Qt.KeyboardModifier.NoModifier):
    widget.setFocus()
    QApplication.processEvents()
    QTest.keyClick(widget, key, modifiers)
    QApplication.processEvents()


def test_shortcuts_use_active_history_and_share_quick_access_actions(window):
    document = window.viewport.document
    obj = bodies(window)[0]
    assert document.history.count == 0
    undo = window.command_registry.action("edit.undo")
    redo = window.command_registry.action("edit.redo")
    assert not undo.isEnabled() and not redo.isEnabled()
    original_pose = pose(window)
    document.rename(obj.id, "Renommé")
    assert undo.isEnabled() and "Renommer" in undo.text()
    key(window.tree, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert document.objects[obj.id].name == obj.name
    assert window.tree_items[obj.id].text(0) == obj.name
    assert redo.isEnabled() and not undo.isEnabled()
    key(window.viewport, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert document.objects[obj.id].name == "Renommé"
    key(window.viewport, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    key(window.tree, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert document.objects[obj.id].name == "Renommé" and pose(window) == original_pose


def test_f2_rename_preserves_native_text_undo_and_rejects_blank_name(window):
    document = window.viewport.document
    obj = bodies(window)[0]
    window.tree.setCurrentItem(window.tree_items[obj.id])
    key(window.tree, Qt.Key.Key_F2)
    editor = window.tree.findChild(QLineEdit)
    assert editor is not None and editor.hasFocus()
    editor.selectAll()
    QTest.keyClicks(editor, "Nouveau")
    QTest.keyClick(editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert document.history.count == 0 and document.objects[obj.id].name == obj.name
    editor.selectAll()
    QTest.keyClicks(editor, "Nom final")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    QApplication.processEvents()
    assert document.objects[obj.id].name == "Nom final" and document.history.count == 1
    key(window.tree, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert document.objects[obj.id].name == obj.name
    key(window.tree, Qt.Key.Key_F2)
    editor = QApplication.focusWidget()
    assert isinstance(editor, QLineEdit)
    editor.selectAll()
    QTest.keyClicks(editor, "   ")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    QApplication.processEvents()
    assert window.tree_items[obj.id].text(0) == obj.name
    assert document.history.index == 0 and document.history.can_redo


def test_checkbox_and_multi_visibility_round_trip_updates_real_ais(window):
    document = window.viewport.document
    first, second = bodies(window)[:2]
    original_pose = pose(window)
    window.tree_items[first.id].setCheckState(0, Qt.CheckState.Unchecked)
    assert document.history.count == 1
    window.command_registry.execute("edit.undo")
    assert window.viewport.context.IsDisplayed(window.viewport.presentations[first.id])
    document.select_objects([first.id, second.id])
    key(window.tree, Qt.Key.Key_Space)
    assert document.history.count == 1  # new command discards the undone checkbox edit
    assert not document.objects[first.id].visible and not document.objects[second.id].visible
    window.command_registry.execute("edit.undo")
    for obj in (first, second):
        assert document.objects[obj.id].visible
        assert window.tree_items[obj.id].checkState(0) == Qt.CheckState.Checked
        assert window.viewport.context.IsDisplayed(window.viewport.presentations[obj.id])
    assert pose(window) == original_pose


def test_multi_delete_restores_original_tree_order_and_shapes_without_moving_camera(window):
    document = window.viewport.document
    objects = bodies(window)
    ids = [obj.id for obj in objects]
    original_order = list(document.objects)
    original_pose = pose(window)
    document.select_objects([ids[2], ids[0]])
    key(window.tree, Qt.Key.Key_Delete)
    assert document.history.count == 1
    assert ids[0] not in window.tree_items and ids[2] not in window.viewport.presentations
    key(window.viewport, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert list(document.objects) == original_order
    parent = window.tree_items[ids[0]].parent()
    assert [parent.child(i).text(0) for i in range(parent.childCount())] == [obj.name for obj in objects]
    for obj in objects:
        assert document.objects[obj.id].shape.IsSame(obj.shape)
        assert window.viewport.context.IsDisplayed(window.viewport.presentations[obj.id])
    assert pose(window) == original_pose
    key(window.tree, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert ids[0] not in document.objects and ids[2] not in document.objects


def test_created_object_round_trip_projects_into_tree_and_viewport(window):
    document = window.viewport.document
    original_pose = pose(window)
    obj = SceneObject("Création", BRepPrimAPI_MakeBox(10, 20, 30).Shape(), (0.4, 0.6, 0.8))
    document.add(obj)
    assert obj.id in window.tree_items and obj.id in window.viewport.presentations
    window.command_registry.execute("edit.undo")
    assert obj.id not in window.tree_items and obj.id not in window.viewport.presentations
    window.command_registry.execute("edit.redo")
    assert document.objects[obj.id].shape.IsSame(obj.shape)
    assert window.viewport.context.IsDisplayed(window.viewport.presentations[obj.id])
    assert pose(window) == original_pose


def test_native_face_survives_rename_undo_but_shape_undo_invalidates_it(window):
    document = window.viewport.document
    obj = bodies(window)[0]
    px, py = window.viewport.view.Convert(-55., -35., 40.)
    scale = window.viewport.devicePixelRatioF()
    QTest.mouseClick(window.viewport, Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier, QPoint(round(px / scale), round(py / scale)))
    selected = window.viewport.selection.items()[0]
    assert selected.topology == "Face" and selected.object_id == obj.id
    original_pose = pose(window)
    document.rename(obj.id, "Renommé")
    document.history.undo()
    assert window.viewport.selection.items()[0].shape.IsSame(selected.shape)
    replacement = BRepPrimAPI_MakeBox(42, 42, 42).Shape()
    document.replace_shape(obj.id, replacement)
    assert not document.selected_ids and not window.viewport.selection.items()
    document.select_objects([obj.id])
    old_ais = window.viewport.presentations[obj.id]
    document.history.undo()
    assert not document.selected_ids and not window.viewport.selection.items()
    assert not window.viewport.context.IsDisplayed(old_ais)
    assert document.objects[obj.id].shape.IsSame(obj.shape) and document.objects[obj.id].revision == 2
    document.history.redo()
    assert document.objects[obj.id].shape.IsSame(replacement) and document.objects[obj.id].revision == 3
    assert pose(window) == original_pose


def test_document_switch_uses_independent_history_and_disconnects_previous_notifications(window):
    first = window.viewport.document
    obj = bodies(window)[0]
    first.rename(obj.id, "Premier")
    second = demo_document()
    other = next(obj for obj in second.objects.values() if obj.kind is ObjectType.BODY)
    window.viewport.load_document(second)
    assert not window.command_registry.action("edit.undo").isEnabled()
    first.history.undo()
    assert not window.command_registry.action("edit.redo").isEnabled()
    second.rename(other.id, "Second")
    window.viewport.load_document(first)
    assert window.command_registry.action("edit.redo").isEnabled()
    key(window.viewport, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert first.objects[obj.id].name == "Premier" and second.objects[other.id].name == "Second"
    window.viewport.load_document(second)
    window.command_registry.execute("edit.undo")
    assert second.objects[other.id].name == other.name and first.objects[obj.id].name == "Premier"


def test_transaction_blocks_ui_mutations_switch_and_close_but_recovers(window):
    document = window.viewport.document
    obj = bodies(window)[0]
    document.select_objects([obj.id])
    original_pose = pose(window)
    with document.history.transaction("Lot"):
        document.rename(obj.id, "Lot renommé")
        for command_id in ("edit.undo", "edit.redo", "object.rename", "object.visibility",
                           "object.delete"):
            assert not window.command_registry.action(command_id).isEnabled()
            assert not window.command_registry.execute(command_id)
        assert not window.tree.isEnabled()
        with pytest.raises(HistoryBusyError):
            window.documents.replace(window.viewport.document.id, demo_document())
        assert not window.close() and window.isVisible()
        assert not window.viewport._native_closed and pose(window) == original_pose
    assert window.tree.isEnabled() and window.command_registry.action("edit.undo").isEnabled()
    window.command_registry.execute("edit.undo")
    assert document.objects[obj.id].name == obj.name and pose(window) == original_pose
