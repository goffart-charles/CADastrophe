"""E2 real Qt/OCCT lifecycle and rendering; no E3 drawing operations."""
from dataclasses import replace
import importlib
from pathlib import Path
import shutil

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB

from cadastrophe.sample_scene import demo_document
from cadastrophe.app import MainWindow
from cadastrophe.geometry import Vec3
from cadastrophe.interaction import Gesture
from cadastrophe.ui.themes import apply_theme, DARK, LIGHT


@pytest.fixture
def system(tmp_path):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "plugins"
    for name in ("sketch", "primitives"):
        shutil.copytree(Path(__file__).resolve().parents[1] / "plugins" / name, root / name,
                        ignore=shutil.ignore_patterns("__pycache__"))
    window = MainWindow(plugin_directory=root, preferences_path=tmp_path / "prefs.json",
                        recovery_directory=tmp_path / "recovery")
    window.documents.replace(window.viewport.document.id, demo_document())
    window.show()
    window.activateWindow()
    app.processEvents()
    window.viewport.camera_animation_duration = 0  # Drawing tests use a settled camera; animation has dedicated tests.
    record = window.plugins._record("cadastrophe.sketch")
    commands = importlib.import_module(record.namespace + ".commands")
    model = importlib.import_module(record.namespace + ".model")
    editor = importlib.import_module(record.namespace + ".editor")
    try:
        yield window, commands, model, editor
    finally:
        window.confirm_discard = lambda doc: True
        window.close()
        window.deleteLater()
        app.processEvents()


def camera_state(viewport):
    camera = viewport.view.Camera()
    return (*camera.Eye().Coord(), *camera.Center().Coord(), *camera.Up().Coord(), camera.Scale(),
            camera.ProjectionType())


def plane(document, name="XY"):
    return next(obj for obj in document.objects.values() if obj.plane and obj.plane.name == name)


def create(system, name="XY", populated=False):
    window, commands, model, editor = system
    document = window.viewport.document
    obj = commands.create_sketch(document, plane(document, name).id)
    if populated:
        for entity in (model.Point(model.Vec2(0, 0)), model.Segment(model.Vec2(-30, -20), model.Vec2(30, -20)),
                       model.Circle(model.Vec2(15, 15), 10), model.Arc(model.Vec2(-20, 15), 12, 0, -2.)):
            commands.add_entity(document, obj.id, entity)
    return document.objects[obj.id]


def enter(system, obj):
    window = system[0]
    window.tree_selection_changed([obj.id])
    assert window.command_registry.execute("cadastrophe.sketch.edit")
    assert window.tool_sessions.editing


@pytest.mark.parametrize("name", ["XY", "XZ", "YZ"])
def test_create_from_plane_normal_camera_and_explicit_exit_restores_exact_view(system, name):
    window, commands, model, editor = system
    vp = window.viewport
    doc = vp.document
    vp.camera.set_pivot(Vec3(10, -20, 30))
    previous, navigation = camera_state(vp), vp.camera.capture_navigation()
    window.tree_selection_changed([plane(doc, name).id])
    assert window.command_registry.execute("cadastrophe.sketch.new")
    session = window.tool_sessions.editor
    obj = doc.objects[session.object_id]
    assert obj.sketch.support.frame.name == name and doc.history.count == 1
    assert vp.editing_id == obj.id and vp.selection.suspended and vp.local_axes is not None
    assert Vec3(*vp.view.Camera().Direction().Coord()).dot(obj.sketch.support.frame.normal) == pytest.approx(-1)
    assert Vec3(*vp.view.Camera().Up().Coord()) == obj.sketch.support.frame.v
    assert not doc.history.blocked and window.command_registry.action("file.save").isEnabled()
    assert window.tool_host.panel is not None and window.manager_pane.currentWidget() is window.manager_pane.properties_stack
    assert not window.tool_host.panel.buttons.button(QDialogButtonBox.StandardButton.Cancel).isVisible()
    previous = camera_state(vp)
    assert window.command_registry.execute("cadastrophe.sketch.finish")
    assert not window.tool_sessions.active and not vp.selection.suspended and vp.editing_id is None
    assert camera_state(vp) == previous and vp.camera.capture_navigation() == navigation
    assert doc.history.count == 2 and obj.id not in doc.objects and doc.selected_ids == (plane(doc, name).id,)


def test_edit_blocks_model_mutations_but_keeps_camera_commands_and_navigation(system):
    window = system[0]
    # E4 legitimately enables Paste for an entity MIME payload. This E2 test
    # exercises an empty clipboard, independent of other tests or user content.
    from types import SimpleNamespace
    window.object_clipboard.clipboard = SimpleNamespace(mimeData=lambda: None)
    obj = create(system)
    enter(system, obj)
    vp = window.viewport
    for command in ("object.delete", "object.transparency", "cadastrophe.primitives.box", "edit.copy", "edit.paste"):
        assert not window.command_registry.execute(command), command
    window.update_action_states()
    assert window.command_registry.action("view.standard.isometric").isEnabled()
    assert not window.tree.isEnabled()
    previous = camera_state(vp)
    window.standard_view("Face")
    assert camera_state(vp) != previous
    assert window.command_registry.execute("view.normal_to_plane")
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=QPoint(80, 80))
    assert not vp.selection.items() and not vp.context.HasDetected()
    direction, up = vp.view.Camera().Direction().Coord(), vp.view.Camera().Up().Coord()
    for modifiers, gesture in ((Qt.KeyboardModifier.NoModifier, Gesture.ROTATE),
                               (Qt.KeyboardModifier.AltModifier, Gesture.ROLL),
                               (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier, Gesture.PAN)):
        QTest.mousePress(vp, Qt.MouseButton.MiddleButton, modifiers, QPoint(320, 280))
        assert vp.state.gesture is gesture
        QTest.mouseRelease(vp, Qt.MouseButton.MiddleButton, modifiers, QPoint(320, 280))
    assert vp.view.Camera().Direction().Coord() == direction and vp.view.Camera().Up().Coord() == up
    scale = vp.view.Camera().Scale()
    vp.camera.zoom(*vp.pixel(QPoint(350, 250)), 1)
    assert vp.view.Camera().Scale() < scale
    QTest.keyClick(vp, Qt.Key.Key_Escape)
    QTest.keyClick(vp, Qt.Key.Key_Return)
    assert window.tool_sessions.editing and vp.editing_id == obj.id


def test_native_renderer_updates_only_changed_entities_and_has_no_selection_owners(system):
    window, commands, model, editor = system
    obj = create(system, populated=True)
    vp, doc = window.viewport, window.viewport.document
    presenter = vp.document_adapter.features[obj.id][1]
    assert len(presenter.entities) == 4 and obj.shape is None
    original = {key: ais for key, (_, ais) in presenter.entities.items()}
    assert all(vp.context.IsDisplayed(ais) for ais in original.values())
    point = obj.sketch.entities[0]
    commands.replace_entity(doc, obj.id, replace(point, position=model.Vec2(2, 3)))
    assert presenter.entities[point.id][1] != original[point.id]
    assert all(presenter.entities[key][1] == ais for key, ais in original.items() if key != point.id)
    window.tree_selection_changed([obj.id])
    enter(system, doc.objects[obj.id])
    assert all(vp.context.IsDisplayed(ais) for _, ais in presenter.entities.values())
    commands.remove_entity(doc, obj.id, point.id)
    assert len(presenter.entities) == 3
    assert window.command_registry.execute("edit.undo") and len(presenter.entities) == 4
    assert window.tool_sessions.editing
    window.command_registry.execute("cadastrophe.sketch.finish")
    doc.set_visible(obj.id, False)
    assert all(not vp.context.IsDisplayed(ais) for _, ais in presenter.entities.values())


def test_save_close_reopen_and_reedit_from_tree_context_and_double_click(system, tmp_path):
    window = system[0]
    obj = create(system, "XZ", populated=True)
    enter(system, obj)
    path = tmp_path / "sketch.cada"
    assert window.save_document(path=path) and window.tool_sessions.editing
    assert window.close_document() and not window.tool_sessions.editing
    doc = window.open_document(path)
    QApplication.processEvents()
    assert obj.id in window.viewport.document_adapter.features
    assert len(window.viewport.document_adapter.features[obj.id][1].entities) == 4
    window.tree_selection_changed([obj.id])
    menu = window.object_context_menu()
    assert window.command_registry.action("cadastrophe.sketch.edit") in menu.actions()
    menu.deleteLater()
    window.activate_tree_object(window.tree_items[obj.id])
    assert window.tool_sessions.editing and not doc.is_modified
    window.tool_host.panel.buttons.button(QDialogButtonBox.StandardButton.Ok).click()
    assert not window.tool_sessions.editing


def test_document_switch_and_undo_creation_end_editor_without_dirty_camera_state(system):
    window = system[0]
    vp = window.viewport
    previous = camera_state(vp)
    window.tree_selection_changed([plane(vp.document).id])
    window.command_registry.execute("cadastrophe.sketch.new")
    key = window.tool_sessions.editor.object_id
    previous = camera_state(vp)
    assert window.command_registry.execute("edit.undo")
    assert key not in vp.document.objects and not window.tool_sessions.editing
    assert camera_state(vp) == previous
    assert window.command_registry.execute("edit.redo")
    enter(system, vp.document.objects[key])
    old = vp.document
    window.create_document()
    QApplication.processEvents()
    assert not window.tool_sessions.active and camera_state(vp) == previous
    window.documents.activate(old.id)
    assert key in window.tree_items and not vp.selection.suspended


def test_transient_topological_selection_restores_only_for_unchanged_revision(system):
    window, commands, model, editor = system
    obj = create(system)
    vp = window.viewport
    x, y = vp.view.Convert(-55., -55., 20.)
    vp.selection.click(x, y)
    selected = vp.selection.items()
    assert selected and selected[0].topology == "Face"
    context = window.plugins._record("cadastrophe.sketch").context
    assert context.start_edit(editor.SketchEditSession(obj))
    context.finish_edit()
    restored = vp.selection.items()
    assert len(restored) == len(selected) and restored[0].shape.IsSame(selected[0].shape)
    assert context.start_edit(editor.SketchEditSession(obj))
    parent = vp.document.objects[selected[0].object_id]
    vp.document.replace_shape(parent.id, parent.shape)
    context.finish_edit()
    assert not vp.selection.items()


def test_existing_plane_demo_perspective_and_resize_are_restored_after_edit(system):
    window = system[0]
    obj = create(system, "XZ")
    vp = window.viewport
    original_3d = camera_state(vp)
    vp.enter_plane(plane(vp.document, "XY").plane)
    vp.view.Camera().SetProjectionType(vp.view.Camera().Projection_Perspective)
    previous = camera_state(vp)
    enter(system, obj)
    assert not vp.view.Camera().IsOrthographic()
    window.resize(1050, 680)
    QApplication.processEvents()
    previous = camera_state(vp)
    assert window.command_registry.execute("cadastrophe.sketch.finish")
    assert camera_state(vp) == previous
    assert vp.workplane is None and vp.saved_camera is None
    width, height = vp.view.Window().Size()
    assert vp.view.Camera().Aspect() == pytest.approx(width / height)
    vp.exit_plane()
    assert camera_state(vp) == previous


def test_native_start_error_restores_context_and_rolls_back_new_sketch(system, monkeypatch):
    window, commands, model, editor = system
    doc, vp = window.viewport.document, window.viewport
    previous = camera_state(vp)
    window.tree_selection_changed([plane(doc).id])
    before = tuple(doc.objects)
    def fail(*_):
        raise RuntimeError("Panne du panneau simulée")
    monkeypatch.setattr(editor.SketchEditSession, "create_editor", fail)
    window.command_registry.execute("cadastrophe.sketch.new")
    assert tuple(doc.objects) == before and doc.history.count == 0 and not doc.is_modified
    assert not window.tool_sessions.active and not vp.selection.suspended and vp.editing_id is None
    assert camera_state(vp) == previous and not vp.document_adapter.features


def test_renderer_release_on_native_close_and_repeated_edit_has_no_drift(system):
    window = system[0]
    obj = create(system, populated=True)
    vp = window.viewport
    presenter = vp.document_adapter.features[obj.id][1]
    previous = camera_state(vp)
    for _ in range(3):
        enter(system, obj)
        previous = camera_state(vp)
        window.command_registry.execute("cadastrophe.sketch.finish")
        assert camera_state(vp) == previous
    window.confirm_discard = lambda doc: True
    assert window.close_document()
    assert not presenter.entities and not vp.document_adapter.features


@pytest.mark.parametrize("theme,size", [(LIGHT, (1400, 850)), (DARK, (1050, 680))])
def test_rendered_sketch_and_property_manager_capture_for_visual_review(system, theme, size):
    window = system[0]
    app = QApplication.instance()
    apply_theme(app, theme)
    obj = create(system, populated=True)
    enter(system, obj)
    window.resize(*size)
    QApplication.processEvents()
    window.viewport.camera.fit()
    window.viewport.view.Redraw()
    root = Path(__file__).resolve().parents[1] / "artifacts"
    suffix = "light" if theme is LIGHT else "dark"
    assert window.grab().save(str(root / f"e2-shell-{suffix}.png"))
    assert window.viewport.view.Dump(str(root / f"e2-viewport-{suffix}.png"))
    assert window.tool_host.panel.width() > 100 and window.tool_host.panel.height() > 100
    apply_theme(app, LIGHT)
