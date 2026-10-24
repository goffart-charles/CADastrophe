"""Production plugin: real OCCT solids, interactive tools and independent native files."""
import importlib.util
import json
from math import pi
from pathlib import Path
import shutil
from zipfile import ZipFile

import pytest
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.GProp import GProp_GProps
from OCC.Core.TopAbs import TopAbs_SOLID
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QToolButton

from cadastrophe.sample_scene import demo_document
from cadastrophe.app import MainWindow
from cadastrophe.document import SceneObject
from cadastrophe.ui.themes import apply_theme

PLUGIN = Path(__file__).resolve().parents[1] / "plugins/primitives"
PREFIX = "cadastrophe.primitives."


@pytest.fixture(scope="module")
def geometry():
    spec = importlib.util.spec_from_file_location("primitive_geometry_test", PLUGIN / "geometry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    root = tmp_path / "plugins"
    shutil.copytree(PLUGIN, root / "primitives")
    window = MainWindow(plugin_directory=root)
    window.documents.replace(window.viewport.document.id, demo_document())
    window.show()
    app.processEvents()
    try:
        yield window
    finally:
        window.confirm_discard = lambda document: True
        window.close()
        window.deleteLater()
        app.processEvents()


def volume(shape):
    props = GProp_GProps()
    brepgprop.VolumeProperties(shape, props)
    return props.Mass()


def pose(viewport):
    camera = viewport.view.Camera()
    return (*camera.Eye().Coord(), *camera.Center().Coord(), *camera.Up().Coord(), camera.Scale())


def invoke(window, kind, *, edit=None, cancel=False, button=False):
    """Exercise the non-modal panel and always clean up failed test sessions."""
    QApplication.processEvents()
    if window.viewport.camera is None:
        QTest.qWait(100)
    action = window.command_registry.action(PREFIX + kind)
    if button:
        next(tool for tool in window.command_manager.findChildren(QToolButton)
             if tool.defaultAction() is action).click()
    else:
        assert window.command_registry.execute(PREFIX + kind)
    sessions = window.tool_sessions
    assert sessions.active and QApplication.activeModalWidget() is None
    assert window.documents.blocked and not action.isEnabled()
    tool = sessions.tool
    try:
        if edit:
            edit(tool)
        sessions.update_preview()
        if cancel:
            QTest.keyClick(tool.editor, Qt.Key.Key_Escape)
        else:
            assert sessions.can_commit, sessions.message
            QTest.mouseClick(window.tool_host.panel.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
        assert not sessions.active
    finally:
        sessions.cancel()
    assert action.isEnabled()


@pytest.mark.parametrize("kind,dimensions,expected_volume,offsets", [
    ("box", {"length": 10., "width": 20., "height": 30.}, 6000., (0, 0, 0, 10, 20, 30)),
    ("cylinder", {"radius": 7., "height": 30.}, pi * 49 * 30, (-7, -7, 0, 7, 7, 30)),
    ("sphere", {"radius": 7.}, 4 / 3 * pi * 343, (-7, -7, -7, 7, 7, 7)),
    ("cone", {"radius": 7., "height": 30.}, pi * 49 * 10, (-7, -7, 0, 7, 7, 30)),
])
def test_native_solids_have_correct_volume_bounds_and_world_position(geometry, kind, dimensions, expected_volume, offsets):
    origin = (13., -17., 23.)
    obj = geometry.build_object(kind, " Solide ", origin, dimensions)
    assert type(obj) is SceneObject and obj.name == "Solide" and obj.visible
    assert obj.shape.ShapeType() == TopAbs_SOLID and BRepCheck_Analyzer(obj.shape).IsValid()
    assert volume(obj.shape) == pytest.approx(expected_volume)
    bounds = Bnd_Box()
    brepbndlib.AddOptimal(obj.shape, bounds, False, False)
    assert bounds.Get() == pytest.approx(tuple(origin[i % 3] + value for i, value in enumerate(offsets)), abs=1e-6)


@pytest.mark.parametrize("value", [0, -1, 0.00001, float("nan"), float("inf"), True, "20", 1_000_001])
def test_invalid_dimensions_rejected_before_occt(geometry, value):
    with pytest.raises(ValueError):
        geometry.build_object("sphere", "Sphère", (0, 0, 0), {"radius": value})


@pytest.mark.parametrize("kind,name,origin,dimensions", [
    ("sphere", "", (0, 0, 0), {"radius": 20}),
    ("sphere", "S", (0, 0), {"radius": 20}),
    ("sphere", "S", (0, float("inf"), 0), {"radius": 20}),
    ("sphere", "S", (0, 0, 0), {}),
    ("sphere", "S", (0, 0, 0), {"radius": 20, "height": 40}),
    ("unknown", "S", (0, 0, 0), {"radius": 20}),
])
def test_invalid_creation_requests_rejected(geometry, kind, name, origin, dimensions):
    with pytest.raises(ValueError):
        geometry.build_object(kind, name, origin, dimensions)


@pytest.mark.parametrize("kind", ["box", "cylinder", "sphere", "cone"])
def test_actions_create_in_tree_and_native_view_with_one_undo_and_stable_camera(window, kind):
    action = window.command_registry.action(PREFIX + kind)
    assert action not in window.plugins_menu.actions() and not action.icon().isNull()
    doc = window.create_document()
    QApplication.processEvents()
    original_pose = pose(window.viewport)
    before = set(doc.objects)

    def parameters(dialog):
        dialog.name_edit.setText("Mon solide")
        dialog.origin[0].setValue(35.)
        for field in dialog.dimensions.values():
            field.setValue(12.)

    invoke(window, kind, edit=parameters, button=True)
    object_id, = set(doc.objects) - before
    obj = doc.objects[object_id]
    assert obj.name == "Mon solide" and doc.selected_ids == (object_id,)
    assert object_id in window.tree_items and object_id in window.viewport.presentations
    assert window.viewport.context.IsDisplayed(window.viewport.presentations[object_id])
    assert doc.history.count == 1 and doc.is_modified
    assert window.command_registry.execute("edit.undo")
    assert object_id not in doc.objects and object_id not in window.tree_items
    assert object_id not in window.viewport.presentations and not doc.is_modified
    assert window.command_registry.execute("edit.redo")
    assert doc.objects[object_id] is obj and object_id in window.tree_items
    assert window.viewport.context.IsDisplayed(window.viewport.presentations[object_id])
    assert pose(window.viewport) == original_pose


def test_cancel_preserves_redo_and_invalid_name_can_be_corrected_without_disabling_tool(window):
    doc = window.create_document()
    invoke(window, "box")
    assert next(obj.name for obj in doc.objects.values() if not obj.plane) == "Boîte1"
    invoke(window, "box")
    assert {obj.name for obj in doc.objects.values() if not obj.plane} == {"Boîte1", "Boîte2"}
    doc.history.undo()
    before = tuple(doc.objects)
    selected = doc.selected_ids
    invoke(window, "sphere", cancel=True)
    assert tuple(doc.objects) == before and doc.selected_ids == selected
    assert doc.history.count == 2 and doc.history.index == 1 and doc.history.can_redo

    def correct_name(dialog):
        dialog.name_edit.setText("   ")
        assert not window.tool_sessions.commit()
        assert dialog.editor.isVisible() and "nom" in window.tool_sessions.message
        assert tuple(doc.objects) == before
        dialog.name_edit.setText("Corrigé")

    invoke(window, "sphere", edit=correct_name)
    assert doc.history.count == 2 and not doc.history.can_redo
    assert any(obj.name == "Corrigé" for obj in doc.objects.values())


def test_plugin_unload_keeps_brep_undo_and_native_files_usable_without_plugin(window, tmp_path):
    doc = window.create_document()
    QApplication.processEvents()
    for kind in ("box", "cylinder", "sphere", "cone"):
        invoke(window, kind)
    solids = {key: obj for key, obj in doc.objects.items() if not obj.plane}
    assert doc.history.count == 4
    assert window.plugins.deactivate("cadastrophe.primitives")
    assert not any(command.id.startswith(PREFIX) for command in window.command_registry)
    assert all(key in window.tree_items and key in window.viewport.presentations for key in solids)
    doc.history.undo()
    assert len(doc.objects) == 6
    doc.history.redo()
    assert all(doc.objects[key] is obj for key, obj in solids.items())
    hidden_id = next(iter(solids))
    doc.rename(hidden_id, "Corps conservé sans extension")
    doc.set_visible(hidden_id, False)
    path = tmp_path / "primitives.cada"
    assert window.save_document(path=path)
    with ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["schema_version"] == 9 and not manifest.get("required_plugins")
    assert window.close_document()
    reopened = window.open_document(path)
    QTest.qWait(100)  # Deliver the new native widget's first paint after tab replacement.
    assert reopened is not None and not reopened.is_modified and reopened.history.count == 0
    for key, obj in solids.items():
        restored = reopened.objects[key]
        assert type(restored) is SceneObject and restored.color == obj.color
        assert volume(restored.shape) == pytest.approx(volume(obj.shape))
        assert key in window.tree_items and key in window.viewport.presentations
        assert window.viewport.context.IsDisplayed(window.viewport.presentations[key]) == (key != hidden_id)
    assert reopened.objects[hidden_id].name == "Corps conservé sans extension"
    assert window.plugins.activate("cadastrophe.primitives")
    invoke(window, "box")
    assert any(obj.name == "Boîte1" for obj in reopened.objects.values())


def test_document_isolation_action_availability_and_startup_disabled(window, tmp_path):
    first = window.viewport.document
    first_pose = pose(window.viewport)
    first_ids = tuple(first.objects)
    second = window.create_document()
    invoke(window, "cylinder")
    assert tuple(first.objects) == first_ids and first.history.count == 0
    window.documents.activate(first.id)
    assert pose(window.viewport) == first_pose
    assert first.selected_ids == () and second.history.count == 1
    window.confirm_discard = lambda document: True
    assert window.close_document() and window.close_document()
    assert not window.command_registry.action(PREFIX + "box").isEnabled()
    window.create_document()
    QTest.qWait(100)
    assert window.command_registry.action(PREFIX + "box").isEnabled()
    root = tmp_path / "disabled"
    shutil.copytree(PLUGIN, root / "primitives")
    (root / "settings.json").write_text(json.dumps({"schema_version": 1, "enabled": {"cadastrophe.primitives": False}}))
    other = MainWindow(plugin_directory=root)
    try:
        assert other.plugins.records[0].state == "disabled"
        assert not any(command.id.startswith(PREFIX) for command in other.command_registry)
    finally:
        other.close()
        other.deleteLater()


def start_tool(window, kind="box"):
    QApplication.processEvents()
    if window.viewport.camera is None:
        QTest.qWait(100)
    assert window.command_registry.execute(PREFIX + kind)
    assert window.tool_sessions.active and window.tool_sessions.can_commit
    return window.tool_sessions.tool


def test_preview_is_transient_unpickable_and_excluded_from_fit_and_serialization(window, tmp_path):
    from OCC.Core.TColStd import TColStd_ListOfInteger
    from cadastrophe.core.persistence import save_document
    from cadastrophe.core.history import HistoryBusyError

    doc = window.viewport.document
    selected = next(iter(doc.objects))
    doc.select_objects([selected])
    original_ids, original_pose = tuple(doc.objects), pose(window.viewport)
    original_presentations = dict(window.viewport.presentations)
    previous_page = window.manager_pane.currentWidget()
    tool = start_tool(window)
    overlay = window.tool_host.overlay
    initial = overlay.presentation
    assert window.viewport.context.IsDisplayed(initial) and initial.Presentation().IsInfinite()
    assert not initial.IsInfinite()  # AIS-level infinity would force wireframe.
    groups = initial.Presentation().Groups()
    assert any(groups.Value(index).IsClosed() for index in range(1, groups.Size()+1))
    assert initial.DisplayMode() == 1 and initial.Transparency() == pytest.approx(.3)
    modes = TColStd_ListOfInteger()
    window.viewport.context.ActivatedModes(initial, modes)
    assert modes.Size() == 0
    assert initial not in original_presentations.values()
    assert tuple(doc.objects) == original_ids and doc.history.count == 0 and not doc.is_modified
    assert doc.selected_ids == (selected,) and pose(window.viewport) == original_pose
    tool.dimensions["length"].setValue(65.)
    QTest.qWait(120)
    assert not window.viewport.context.IsDisplayed(initial)
    assert volume(window.tool_sessions.preview_object.shape) == pytest.approx(65 * 30 * 25)
    assert tuple(doc.objects) == original_ids and dict(window.viewport.presentations) == original_presentations
    assert not window.save_document(path=tmp_path / "blocked.cada")
    with pytest.raises(HistoryBusyError):
        save_document(doc, tmp_path / "blocked-direct.cada")
    assert not (tmp_path / "blocked.cada").exists() and not (tmp_path / "blocked-direct.cada").exists()
    tool.origin[0].setValue(10000.)
    window.tool_sessions.update_preview()
    assert window.command_registry.execute("view.fit")
    assert pose(window.viewport) == pytest.approx(original_pose)
    tool.origin[0].setValue(0.)
    window.tool_sessions.update_preview()
    output = Path("artifacts")
    output.mkdir(exist_ok=True)
    window.viewport.view.Redraw()
    assert window.viewport.view.Dump(str(output / "t09-preview.png"))
    assert window.grab().save(str(output / "t09-property-manager.png"))
    latest = overlay.presentation
    assert window.tool_sessions.cancel()
    assert not window.viewport.context.IsDisplayed(latest)
    assert doc.selected_ids == (selected,) and tuple(doc.objects) == original_ids
    assert window.manager_pane.currentWidget() is previous_page
    assert window.tool_host.panel is None and not doc.is_modified


@pytest.mark.parametrize("focus", ["name", "dimension", "viewport", "tree"])
def test_escape_cancels_tool_before_leaving_plane_from_any_editor_or_view(window, focus):
    from cadastrophe.interaction import WorkMode

    doc = window.viewport.document
    plane = next(obj for obj in doc.objects.values() if obj.plane)
    window.viewport.enter_plane(plane.plane)
    original_pose = pose(window.viewport)
    tool = start_tool(window)
    target = {"name": tool.name_edit, "dimension": tool.dimensions["length"],
              "viewport": window.viewport, "tree": window.tree}[focus]
    target.setFocus()
    QTest.keyClick(target, Qt.Key.Key_Escape)
    assert not window.tool_sessions.active and window.viewport.mode == WorkMode.PLANE
    assert pose(window.viewport) == original_pose and not doc.is_modified and doc.history.count == 0
    assert window.command_registry.execute("view.cancel")
    assert window.viewport.mode == WorkMode.MODEL


def test_enter_rejects_invalid_current_text_then_commits_actual_spinbox_text(window):
    doc = window.viewport.document
    tool = start_tool(window)
    field = tool.dimensions["length"]
    field.setFocus()
    field.lineEdit().setText("0 mm")
    QTest.keyClick(field, Qt.Key.Key_Return)
    assert window.tool_sessions.active and not window.tool_sessions.can_commit
    assert window.tool_host.overlay.presentation is None
    assert doc.history.count == 0 and not doc.is_modified
    field.lineEdit().setText("55 mm")
    QTest.keyClick(field, Qt.Key.Key_Enter)
    assert not window.tool_sessions.active and doc.history.count == 1
    obj = next(obj for obj in doc.objects.values() if obj.name == "Boîte1")
    assert volume(obj.shape) == pytest.approx(55 * 30 * 25)


def test_exclusive_tool_blocks_mutations_and_tabs_but_tree_selection_still_works(window):
    from cadastrophe.core.history import HistoryBusyError

    first = window.viewport.document
    other = window.create_document()
    window.documents.activate(first.id)
    tool = start_tool(window)
    assert not window.document_tabs.isEnabled() and window.tree.isEnabled()
    for command in ("file.new", "file.open", "file.save", "file.close", "edit.undo", PREFIX + "sphere"):
        assert not window.command_registry.execute(command)
    assert not window.plugins.deactivate("cadastrophe.primitives")
    assert not window.close_document() and window.create_document() is None
    with pytest.raises(HistoryBusyError):
        window.documents.activate(other.id)
    plane_id = next(key for key, obj in first.objects.items() if obj.plane)
    item = window.tree_items[plane_id]
    assert not item.flags() & Qt.ItemFlag.ItemIsUserCheckable
    window.tree.setCurrentItem(item)
    assert first.selected_ids == (plane_id,)
    item.setCheckState(0, Qt.CheckState.Checked)  # Even an API-driven check is rejected cleanly.
    assert not first.objects[plane_id].visible and item.checkState(0) == Qt.CheckState.Unchecked
    assert window.tool_sessions.tool is tool
    assert window.command_registry.execute("view.cancel")
    assert window.document_tabs.isEnabled() and item.flags() & Qt.ItemFlag.ItemIsUserCheckable
    window.documents.activate(other.id)
    assert other.history.count == 0


def test_navigation_and_native_picking_remain_available_while_preview_is_active(window):
    from PySide6.QtCore import QPoint, QPointF, QEvent
    from PySide6.QtGui import QMouseEvent

    viewport = window.viewport
    doc = viewport.document
    original_pose = pose(viewport)
    tool = start_tool(window)
    # Pick a point on the demo cube, away from the primitive preview at the origin.
    px, py = viewport.view.Convert(-55., -55., 20.)
    scale = viewport.devicePixelRatioF()
    point = QPoint(round(px / scale), round(py / scale))
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=point)
    assert len(viewport.selection.items()) == 1
    selected_face = viewport.selection.items()[0]
    assert selected_face.topology == "Face"
    assert doc.objects[doc.selected_ids[0]].name == "Cube"
    start = QPointF(viewport.width() / 2, viewport.height() / 2)
    end = start + QPointF(80, 30)
    for kind, point, button, buttons in (
        (QEvent.Type.MouseButtonPress, start, Qt.MouseButton.MiddleButton, Qt.MouseButton.MiddleButton),
        (QEvent.Type.MouseMove, end, Qt.MouseButton.NoButton, Qt.MouseButton.MiddleButton),
        (QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.MiddleButton, Qt.MouseButton.NoButton)):
        QApplication.sendEvent(viewport, QMouseEvent(kind, point, QPointF(viewport.mapToGlobal(point.toPoint())),
                                                    button, buttons, Qt.KeyboardModifier.NoModifier))
    navigated_pose = pose(viewport)
    assert navigated_pose != original_pose and window.tool_sessions.tool is tool
    selected = doc.selected_ids
    assert window.tool_sessions.cancel()
    assert pose(viewport) == navigated_pose and doc.selected_ids == selected and doc.history.count == 0
    assert viewport.selection.items()[0].shape.IsSame(selected_face.shape)


def test_context_cleanup_cancels_its_session_and_releases_preview_and_controls(window):
    start_tool(window)
    presentation = window.tool_host.overlay.presentation
    record = next(record for record in window.plugins.records if record.manifest.id == "cadastrophe.primitives")
    record.context.close()
    assert not window.tool_sessions.active and not window.documents.blocked
    assert not window.viewport.context.IsDisplayed(presentation)
    assert not any(command.id.startswith(PREFIX) for command in window.command_registry)
    assert window.viewport.document.history.count == 0


def test_window_close_cancels_pending_preview_and_no_timer_runs_after_native_close(window):
    tool = start_tool(window)
    tool.dimensions["length"].setValue(80.)
    viewport = window.viewport
    assert window.close()
    QTest.qWait(100)
    assert not window.tool_sessions.active and not window.documents.documents
    assert viewport._native_closed and window.tool_host.panel is None


def test_modal_escape_is_not_stolen_by_the_underlying_tool(window):
    from PySide6.QtWidgets import QDialog

    tool = start_tool(window)
    dialog = QDialog(window)
    dialog.setModal(True)
    dialog.show()
    QApplication.processEvents()
    try:
        assert QApplication.activeModalWidget() is dialog
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert not dialog.isVisible() and window.tool_sessions.tool is tool
        QTest.keyClick(window.viewport, Qt.Key.Key_Escape)
        assert not window.tool_sessions.active
    finally:
        dialog.close()
        dialog.deleteLater()
