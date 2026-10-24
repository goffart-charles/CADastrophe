"""Real tree/native picking menus share actions, undo and safe selection semantics."""
from pathlib import Path
import shutil

import pytest
from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QLineEdit

from cadastrophe.sample_scene import demo_document
from cadastrophe.app import MainWindow
from cadastrophe.ui.themes import apply_theme


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    root = tmp_path / "plugins"
    shutil.copytree(Path(__file__).resolve().parents[1] / "plugins/primitives", root / "primitives")
    window = MainWindow(plugin_directory=root)
    window.documents.replace(window.viewport.document.id, demo_document())
    window.show()
    window.activateWindow()
    app.processEvents()
    try:
        yield window
    finally:
        window.tool_sessions.cancel()
        window.confirm_discard = lambda document: True
        window.close()
        window.deleteLater()
        app.processEvents()


def bodies(window):
    return [obj for obj in window.viewport.document.objects.values() if not obj.plane]


def point(window, world):
    x, y = window.viewport.view.Convert(*world)
    scale = window.viewport.devicePixelRatioF()
    return QPoint(round(x / scale), round(y / scale))


def pose(window):
    camera = window.viewport.view.Camera()
    return (*camera.Eye().Coord(), *camera.Center().Coord(), *camera.Up().Coord(), camera.Scale())


def request(widget, position):
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, position, widget.mapToGlobal(position))
    QApplication.sendEvent(widget, event)


def capture(window, monkeypatch):
    menus = []
    monkeypatch.setattr(window, "_show_context_menu", lambda menu, position: menus.append(menu))
    return menus


def execute_popup(window, position, command_id):
    errors, observed = [], []

    def choose():
        menu = QApplication.activePopupWidget()
        try:
            assert isinstance(menu, QMenu)
            action = window.command_registry.action(command_id)
            assert action in menu.actions()
            observed.append(True)
            QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center())
        except BaseException as exc:
            errors.append(exc)
        finally:
            if isinstance(menu, QMenu):
                menu.close()

    QTimer.singleShot(0, choose)
    request(window.viewport, position)
    if errors:
        raise errors[0]
    assert observed == [True]


def test_context_actions_adapt_to_empty_body_plane_and_mixed_selection(window):
    registry = window.command_registry
    doc = window.viewport.document
    body = bodies(window)[0]
    plane = next(obj for obj in doc.objects.values() if obj.plane)
    menu = window.object_context_menu()
    assert [action.objectName() for action in menu.actions()] == ["view.fit", "object.show_all", "edit.paste"]
    doc.select_objects([body.id])
    menu = window.object_context_menu()
    for key in ("object.frame", "object.show", "object.hide", "object.isolate", "object.transparency", "object.rename", "object.properties", "object.delete"):
        assert registry.action(key) in menu.actions()
    assert registry.action("view.normal_to_plane") not in menu.actions()
    assert not registry.action("object.show").isEnabled() and registry.action("object.hide").isEnabled()
    doc.select_objects([plane.id])
    menu = window.object_context_menu()
    assert registry.action("view.normal_to_plane") in menu.actions()
    assert registry.action("object.show").isEnabled() and not registry.action("object.hide").isEnabled()
    for key in ("object.delete", "object.isolate", "object.transparency"):
        assert registry.action(key) not in menu.actions() and not registry.action(key).isEnabled()
    doc.select_objects([body.id, plane.id])
    menu = window.object_context_menu()
    assert registry.action("object.rename") not in menu.actions()
    assert registry.action("object.delete") in menu.actions()


def test_tree_right_click_preserves_multiple_selection_then_targets_a_hidden_plane(window, monkeypatch):
    menus = capture(window, monkeypatch)
    doc = window.viewport.document
    first, second, *_ = bodies(window)
    doc.select_objects([first.id, second.id])
    window.tree_adapter.bodies.setExpanded(True)
    item = window.tree_items[first.id]
    position = window.tree.visualItemRect(item).center()
    QTest.mouseClick(window.tree.viewport(), Qt.MouseButton.RightButton, pos=position)
    request(window.tree.viewport(), position)
    assert set(doc.selected_ids) == {first.id, second.id}
    assert window.command_registry.action("object.rename") not in menus[-1].actions()
    plane = next(obj for obj in doc.objects.values() if obj.plane)
    window.tree_adapter.planes.setExpanded(True)
    item = window.tree_items[plane.id]
    request(window.tree.viewport(), window.tree.visualItemRect(item).center())
    assert doc.selected_ids == (plane.id,)
    assert window.command_registry.action("view.normal_to_plane") in menus[-1].actions()
    assert window.command_registry.execute("object.show")
    assert doc.objects[plane.id].visible and window.viewport.context.IsDisplayed(window.viewport.presentations[plane.id])
    assert doc.history.count == 1
    doc.history.undo()
    assert not doc.objects[plane.id].visible


def test_viewport_context_targets_new_owner_and_background_keeps_face_and_multiple_selection(window, monkeypatch):
    menus = capture(window, monkeypatch)
    viewport, doc = window.viewport, window.viewport.document
    first, second, *_ = bodies(window)
    cube_point = point(window, (-55., -55., 20.))
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=cube_point)
    original = viewport.selection.items()[0]
    assert original.object_id == first.id and original.topology == "Face"
    request(viewport, cube_point)
    assert viewport.selection.items()[0].shape.IsSame(original.shape)
    assert window.command_registry.action("object.transparency") in menus[-1].actions()
    request(viewport, QPoint(5, viewport.height() - 5))
    assert viewport.selection.items()[0].shape.IsSame(original.shape)
    assert [action.objectName() for action in menus[-1].actions()] == ["view.fit", "object.show_all", "edit.paste"]
    viewport.selection.from_tree([first.id, second.id])
    request(viewport, cube_point)
    assert set(doc.selected_ids) == {first.id, second.id}
    doc.select_objects([])
    request(viewport, cube_point)
    assert doc.selected_ids == (first.id,) and doc.history.count == 0


def test_transparency_undo_keeps_ais_owner_face_selection_camera_and_render_mode(window):
    viewport, doc = window.viewport, window.viewport.document
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=point(window, (-55., -55., 20.)))
    item = viewport.selection.items()[0]
    ais = viewport.presentations[item.object_id]
    original_pose = pose(window)
    for mode in ("Ombré avec arêtes", "Filaire", "Ombré"):
        viewport.set_render_mode(mode)
        count = doc.history.count
        assert window.command_registry.execute("object.transparency")
        assert doc.objects[item.object_id].transparency == 0.5 and ais.Transparency() == pytest.approx(0.5)
        assert viewport.presentations[item.object_id] is ais
        assert viewport.selection.items()[0].shape.IsSame(item.shape)
        assert window.command_registry.action("object.transparency").isChecked()
        assert doc.history.count == count + 1
        doc.history.undo()
        assert ais.Transparency() == 0 and doc.objects[item.object_id].revision == item.revision
        assert viewport.selection.items()[0].shape.IsSame(item.shape) and pose(window) == original_pose
        # Redo branch will be replaced by the next actual command.
        doc.history.clear()


def test_isolate_and_show_all_are_single_commands_and_do_not_move_camera(window):
    viewport, doc = window.viewport, window.viewport.document
    first, second, *_ = bodies(window)
    doc.set_visible(first.id, False)
    before = {key: obj.visible for key, obj in doc.objects.items()}
    doc.select_objects([first.id, second.id])
    original_pose, index = pose(window), doc.history.index
    assert window.command_registry.execute("object.isolate")
    assert {key for key, obj in doc.objects.items() if obj.visible} == {first.id, second.id}
    assert doc.history.index == index + 1
    assert all(viewport.context.IsDisplayed(ais) == doc.objects[key].visible for key, ais in viewport.presentations.items())
    assert not window.command_registry.action("object.isolate").isEnabled()
    doc.history.undo()
    assert {key: obj.visible for key, obj in doc.objects.items()} == before
    doc.history.redo()
    assert window.command_registry.execute("object.show_all")
    assert all(obj.visible for obj in bodies(window)) and all(not obj.visible for obj in doc.objects.values() if obj.plane)
    assert doc.history.index == index + 2 and pose(window) == original_pose


def test_real_popup_uses_shared_action_to_hide_and_undo_restores_native_presentation(window):
    doc = window.viewport.document
    obj = bodies(window)[0]
    doc.select_objects([obj.id])
    execute_popup(window, point(window, (-55., -55., 20.)), "object.hide")
    assert not doc.objects[obj.id].visible
    assert not window.viewport.context.IsDisplayed(window.viewport.presentations[obj.id]) and not doc.selected_ids
    assert window.command_registry.execute("edit.undo")
    assert doc.objects[obj.id].visible and window.viewport.context.IsDisplayed(window.viewport.presentations[obj.id])


def test_properties_and_rename_reveal_the_manager_without_mutating_face_selection(window):
    viewport, doc = window.viewport, window.viewport.document
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=point(window, (-55., -55., 20.)))
    selected = viewport.selection.items()[0]
    window.command_registry.action("window.tree").setChecked(False)
    assert window.command_registry.execute("object.properties")
    assert window.manager_pane.isVisible() and window.manager_pane.currentWidget() is window.manager_pane.properties_stack
    assert selected.object_id in window.manager_pane.selection_detail.text()
    assert "Face" in window.manager_pane.selection_detail.text()
    assert doc.history.count == 0
    execute_popup(window, point(window, (-55., -55., 20.)), "object.rename")
    QApplication.processEvents()
    editor = window.tree.findChild(QLineEdit)
    assert editor is not None
    assert editor.text() == "Cube"
    assert QApplication.focusWidget() is editor
    editor.selectAll()
    QTest.keyClicks(editor, "Corps depuis le menu")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    QApplication.processEvents()
    assert doc.objects[selected.object_id].name == "Corps depuis le menu" and doc.history.count == 1
    assert viewport.selection.items()[0].shape.IsSame(selected.shape)


def test_right_click_and_transparency_keep_multiple_faces_on_the_same_parent(window, monkeypatch):
    capture(window, monkeypatch)
    viewport = window.viewport
    front, top = point(window, (-55., -55., 20.)), point(window, (-55., -35., 40.))
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=front)
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, top)
    QTest.keyRelease(viewport, Qt.Key.Key_Control)
    faces = viewport.selection.items()
    assert len(faces) == 2 and len(window.selected_ids) == 1
    request(viewport, front)
    assert window.command_registry.execute("object.transparency")
    viewport.document.history.undo()
    restored = viewport.selection.items()
    assert len(restored) == 2 and all(any(item.shape.IsSame(other.shape) for other in restored) for item in faces)


def test_tree_background_context_keeps_current_multiple_selection(window, monkeypatch):
    menus = capture(window, monkeypatch)
    doc = window.viewport.document
    first, second, *_ = bodies(window)
    doc.select_objects([first.id, second.id])
    blank = QPoint(5, window.tree.viewport().height() - 5)
    assert window.tree.itemAt(blank) is None
    QTest.mouseClick(window.tree.viewport(), Qt.MouseButton.RightButton, pos=blank)
    request(window.tree.viewport(), blank)
    assert set(doc.selected_ids) == {first.id, second.id}
    assert [action.objectName() for action in menus[-1].actions()] == ["view.fit", "object.show_all", "edit.paste"]


def test_new_commands_are_isolated_by_document_and_blocked_during_interactive_tool(window):
    first = window.viewport.document
    obj = bodies(window)[0]
    first.select_objects([obj.id])
    assert window.command_registry.execute("object.transparency")
    second = window.create_document()
    QApplication.processEvents()
    window.documents.activate(first.id)
    assert window.command_registry.execute("cadastrophe.primitives.box")
    for key in ("object.show", "object.hide", "object.isolate", "object.show_all", "object.transparency", "object.rename", "object.delete", "object.properties"):
        assert not window.command_registry.execute(key)
    assert window.command_registry.execute("view.cancel")
    assert first.history.count == 1 and first.objects[obj.id].transparency == 0.5
    window.documents.activate(second.id)
    assert second.history.count == 0 and all(obj.transparency == 0.82 for obj in second.objects.values())


def test_transparent_body_saves_and_reopens_in_native_view(window, tmp_path):
    doc = window.viewport.document
    obj = bodies(window)[0]
    doc.select_objects([obj.id])
    assert window.command_registry.execute("object.transparency")
    path = tmp_path / "transparent.cada"
    assert window.save_document(path=path) and window.close_document()
    loaded = window.open_document(path)
    QTest.qWait(100)
    assert loaded.objects[obj.id].transparency == 0.5
    assert window.viewport.presentations[obj.id].Transparency() == pytest.approx(0.5)
    assert not loaded.is_modified
    output = Path("artifacts")
    output.mkdir(exist_ok=True)
    window.viewport.view.Redraw()
    assert window.viewport.view.Dump(str(output / "t10-transparency.png"))
