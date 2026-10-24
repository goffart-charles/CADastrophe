"""E1 model compatibility retained as the host gains E2 presentations."""
import importlib
from pathlib import Path
import shutil

import pytest
from PySide6.QtCore import QMimeData, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cadastrophe.sample_scene import demo_document
from cadastrophe.app import MainWindow
from cadastrophe.document import ObjectType


@pytest.fixture
def host(tmp_path):
    app = QApplication.instance() or QApplication([])
    clipboard = app.clipboard()
    snapshot = QMimeData()
    mime = clipboard.mimeData()
    if mime is not None:
        for fmt in mime.formats():
            snapshot.setData(fmt, mime.data(fmt))
    root = tmp_path / "plugins"
    shutil.copytree(Path(__file__).resolve().parents[1] / "plugins/sketch", root / "sketch",
                    ignore=shutil.ignore_patterns("__pycache__"))
    window = MainWindow(plugin_directory=root, preferences_path=tmp_path / "preferences.json",
                        recovery_directory=tmp_path / "recovery")
    window.documents.replace(window.viewport.document.id, demo_document())
    window.show()
    window.activateWindow()
    app.processEvents()
    assert QTest.qWaitForWindowActive(window, 1000)
    record = window.plugins._record("cadastrophe.sketch")
    commands = importlib.import_module(record.namespace + ".commands")
    model = importlib.import_module(record.namespace + ".model")
    try:
        yield window, commands, model
    finally:
        window.confirm_discard = lambda doc: True
        window.close()
        window.deleteLater()
        app.processEvents()
        clipboard.setMimeData(snapshot)


def pose(viewport):
    camera = viewport.view.Camera()
    return (*camera.Eye().Coord(), *camera.Center().Coord(), *camera.Up().Coord(), camera.Scale())


def make_sketch(window, commands):
    doc = window.viewport.document
    plane = next(obj for obj in doc.objects.values() if obj.kind is ObjectType.REFERENCE_PLANE)
    return commands.create_sketch(doc, plane.id)


def test_native_plugin_is_registered_with_contextual_drawing_actions(host):
    window, commands, model = host
    assert window.plugins._record("cadastrophe.sketch").state == "active"
    assert window.object_types.binding("cadastrophe.sketch.sketch").codec.version == 8
    assert window.command_registry.command("cadastrophe.sketch.new").implemented
    # E3 tools exist but require the plugin's persistent editing context.
    for tool in ("line", "rectangle", "circle", "arc"):
        assert window.command_registry.command("cadastrophe.sketch." + tool).implemented
        assert not window.command_registry.action("cadastrophe.sketch." + tool).isEnabled()


def test_logical_object_tree_selection_history_and_camera_remain_consistent(host):
    window, commands, model = host
    viewport = window.viewport
    previous_pose, pivot = pose(viewport), viewport.camera.pivot
    obj = make_sketch(window, commands)
    doc = viewport.document
    assert obj.id in window.tree_items and obj.id not in viewport.presentations
    assert window.tree_items[obj.id].parent() is window.tree.topLevelItem(0)
    window.tree_selection_changed([obj.id])
    assert window.selected_ids == [obj.id]
    items = window.services.selection_items
    assert len(items) == 1 and items[0].shape is None and items[0].object_id == obj.id
    assert not window.command_registry.action("object.frame").isEnabled()
    assert not window.command_registry.action("object.transparency").isEnabled()
    assert not window.command_registry.action("edit.copy").isEnabled()
    # Recolor, rebuild, fit-ignore and surface picking must tolerate no shape.
    viewport.selection.set_pixel_ratio(viewport.devicePixelRatioF())
    viewport.frame_objects([obj.id])
    viewport.selection.pick_surface(viewport.camera.screen_ray(100, 100))
    window.rebuild_tree()
    point = model.Point(model.Vec2(3, 4))
    commands.add_entity(doc, obj.id, point)
    assert window.command_registry.execute("edit.undo")
    assert not doc.objects[obj.id].sketch.entities
    assert window.command_registry.execute("edit.redo")
    assert doc.objects[obj.id].sketch.entities == (point,)
    window.tree_selection_changed([obj.id])
    window.command_registry.execute("object.hide")
    window.tree_selection_changed([obj.id])
    window.command_registry.execute("object.show")
    window.tree_selection_changed([obj.id])
    assert window.command_registry.execute("object.delete")
    assert obj.id not in doc.objects and obj.id not in window.tree_items
    assert window.command_registry.execute("edit.undo")
    assert obj.id in doc.objects and obj.id in window.tree_items
    assert pose(viewport) == previous_pose and viewport.camera.pivot == pivot
    viewport.update()
    QApplication.processEvents()
    assert not viewport._failed


def test_native_save_reopen_other_document_and_plugin_lifetime(host, tmp_path):
    window, commands, model = host
    obj = make_sketch(window, commands)
    doc = window.viewport.document
    point = model.Point(model.Vec2(5, 6))
    commands.add_entity(doc, obj.id, point)
    path = tmp_path / "native-sketch.cada"
    assert window.save_document(path=path)
    assert not window.plugins.deactivate("cadastrophe.sketch")
    other = window.create_document()
    QApplication.processEvents()
    assert obj.id not in other.objects and obj.id not in window.tree_items
    window.documents.activate(doc.id)
    assert window.close_document()
    assert window.plugins.deactivate("cadastrophe.sketch")
    assert window.plugins.activate("cadastrophe.sketch")
    restored = window.open_document(path)
    QApplication.processEvents()
    assert restored.objects[obj.id].sketch.entities[0].position.u == 5
    assert obj.id in window.tree_items and obj.id not in window.viewport.presentations
    assert not restored.is_modified
    window.tree_selection_changed([obj.id])
    window.viewport.setFocus()
    QTest.keyClick(window.viewport, Qt.Key.Key_Delete)
    assert obj.id not in restored.objects
    QTest.keyClick(window.viewport, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert obj.id in restored.objects


def test_undo_retains_codec_until_all_snapshots_are_released(host):
    window, commands, model = host
    obj = make_sketch(window, commands)
    doc = window.viewport.document
    commands.add_entity(doc, obj.id, model.Circle(model.Vec2(0, 0), 10))
    doc.history.undo()
    doc.history.undo()
    assert obj.id not in doc.objects
    assert not window.plugins.deactivate("cadastrophe.sketch")
    doc.history.clear()
    assert window.plugins.deactivate("cadastrophe.sketch")


def test_brep_clipboard_still_works_in_a_document_with_a_logical_object(host):
    from cadastrophe.core.clipboard import MIME_TYPE
    window, commands, model = host
    clipboard = QApplication.clipboard()
    marker = "cadastrophe-sketch-clipboard-probe"
    clipboard.setText(marker)
    if clipboard.text() != marker:
        pytest.skip("Presse-papiers Windows indisponible : aller-retour Qt texte seul impossible.")
    make_sketch(window, commands)
    doc = window.viewport.document
    body = next(obj for obj in doc.objects.values() if obj.kind is ObjectType.BODY)
    doc.select_objects([body.id])
    window.viewport.setFocus()
    QApplication.processEvents()
    assert window.command_registry.execute("edit.copy"), window.clipboard_selection_error()
    assert QApplication.clipboard().mimeData().hasFormat(MIME_TYPE), (
        window.hint.text(), clipboard.ownsClipboard(), clipboard.mimeData().formats())
