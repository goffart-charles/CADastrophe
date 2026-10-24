"""Real plugin UI and AIS subshape picking, with document/camera invariants."""
from dataclasses import replace
from pathlib import Path
import shutil

import pytest
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QPalette
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton, QLineEdit

from cadastrophe.sample_scene import demo_document
from cadastrophe.app import MainWindow
from cadastrophe.document import SelectionItem
from cadastrophe.ui.themes import LIGHT, DARK, apply_theme

PREFIX = "cadastrophe.measurements."
PLUGIN_ID = "cadastrophe.measurements"
MODES = ("distance", "length", "radius", "angle", "area", 'volume', 'mass')


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "plugins"
    for name in ("primitives", "measurements"):
        shutil.copytree(Path(__file__).resolve().parents[1] / "plugins" / name, root / name)
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
        apply_theme(app, LIGHT)


def panel(window):
    record = next(record for record in window.plugins.records if record.manifest.id == PLUGIN_ID)
    assert record.state == "active", record.error
    return record.instance.panel


def pose(viewport):
    camera = viewport.view.Camera()
    return (*camera.Eye().Coord(), *camera.Center().Coord(), *camera.Up().Coord(), camera.Scale())


def pick(window, world, *, add=False):
    viewport = window.viewport
    x, y = viewport.view.Convert(*world)
    scale = viewport.devicePixelRatioF()
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.ControlModifier if add else Qt.KeyboardModifier.NoModifier,
                     pos=QPoint(round(x / scale), round(y / scale)))
    if add:
        QTest.keyRelease(viewport, Qt.Key.Key_Control)


def settle(window):
    # Preferences can queue a later state notification and restart the 60 ms
    # timers. Wait for their completion instead of assuming a 90 ms deadline.
    instance = window.plugins._record(PLUGIN_ID).instance
    timers = (instance.status_timer, instance.panel.timer)
    for _ in range(100):
        QTest.qWait(10)
        if not any(timer.isActive() for timer in timers):
            return
    pytest.fail("Les mises à jour de mesure ne se terminent pas dans la seconde.")


def test_measurement_actions_are_same_in_ribbon_menu_and_palette_and_no_selection_is_explained(window):
    registry = window.command_registry
    assert not any(command.id.startswith("measure.") for command in registry)
    assert window.manager_pane.count() == 5
    for mode in MODES:
        action = registry.action(PREFIX + mode)
        assert action not in window.plugins_menu.actions() and action.isEnabled()
        assert len([button for button in window.command_manager.findChildren(QToolButton) if button.defaultAction() is action]) == 1
        assert action in [command.action for command in registry.search(PREFIX + mode)]
    window.command_registry.action("window.tree").setChecked(False)
    action = registry.action(PREFIX + "distance")
    button = next(button for button in window.command_manager.findChildren(QToolButton) if button.defaultAction() is action)
    button.click()
    widget = panel(window)
    assert widget.isVisible() and window.command_registry.action("window.tree").isChecked()
    assert widget.mode.currentData() == "distance" and widget.result.text() == ""
    assert "deux éléments" in widget.detail.text()
    assert not window.viewport.document.is_modified


def test_material_assignment_updates_native_mass_and_undo_preserves_shape(window):
    document = window.viewport.document
    obj = next(obj for obj in document.objects.values() if obj.name == 'Cube')
    document.select_objects([obj.id])
    assert window.command_registry.execute(PREFIX+'material')
    widget = panel(window)
    settle(window)
    assert widget.mode.currentData() == 'mass' and widget.material_group.isVisible()
    assert 'matériau' in widget.detail.text() and widget.result.text() == ''
    original_shape = document.objects[obj.id].shape
    original_revision = document.objects[obj.id].revision
    widget.material_name.setText('Matière d’essai')
    widget.density.setValue(1250.)
    QTest.mouseClick(widget.apply_material, Qt.MouseButton.LeftButton)
    settle(window)
    assert document.objects[obj.id].material.name == 'Matière d’essai'
    assert widget.last_result.value == pytest.approx(80.)
    assert document.objects[obj.id].shape.IsSame(original_shape)
    assert document.objects[obj.id].revision == original_revision
    document.history.undo()
    settle(window)
    assert document.objects[obj.id].material is None and widget.result.text() == ''
    document.history.redo()
    settle(window)
    assert widget.last_result.value == pytest.approx(80.)


def test_actual_two_faces_of_same_parent_emit_topological_selection_and_measure_angle(window):
    document, viewport = window.viewport.document, window.viewport
    events = []
    window.services.selection_changed.connect(lambda items: events.append(items))
    assert window.command_registry.execute(PREFIX + "angle")
    widget = panel(window)
    pick(window, (-55, -55, 20))
    first = window.services.selection_items
    assert len(first) == 1 and first[0].topology == "Face"
    parent_ids = document.selected_ids
    pick(window, (-55, -35, 40), add=True)
    items = window.services.selection_items
    assert len(items) == 2 and all(item.topology == "Face" for item in items)
    assert document.selected_ids == parent_ids and len(events) >= 2
    assert not items[0].shape.IsSame(items[1].shape)
    settle(window)
    assert widget.last_result.value == pytest.approx(90) and widget.result.text() == "90.000 °"
    assert document.history.count == 0 and not document.is_modified


def test_actual_edge_length_and_cylinder_face_radius_are_measured_without_changing_owner(window):
    viewport, document = window.viewport, window.viewport.document
    pick(window, (-55, -55, 40))
    edge, = window.services.selection_items
    assert edge.topology == "Arête"
    assert window.command_registry.execute(PREFIX + "length")
    widget = panel(window)
    assert widget.last_result.value == pytest.approx(40)
    assert window.services.selection_items[0].shape.IsSame(edge.shape)
    pick(window, (0, -57, 26))
    face, = window.services.selection_items
    assert face.topology == "Face" and document.objects[face.object_id].name == "Cylindre"
    assert window.command_registry.execute(PREFIX + "radius")
    assert widget.last_result.value == pytest.approx(22)
    assert window.services.selection_items[0].shape.IsSame(face.shape) and document.history.count == 0


def test_edge_length_in_right_status_bar_without_opening_panel_updates_units_and_clears(window, monkeypatch):
    instance = next(record.instance for record in window.plugins.records if record.manifest.id == PLUGIN_ID)
    viewport = window.viewport
    assert not instance.panel.isVisible()
    pick(window, (-55, -55, 40))
    settle(window)
    assert instance.status.text() == "Longueur : 40.000 mm" and instance.status.isVisible()
    assert window.statusBar().isAncestorOf(instance.status) and not instance.panel.isVisible()
    window.apply_preferences(replace(window.preferences, length_unit="cm", decimals=2))
    settle(window)
    assert instance.status.text() == "Longueur : 4.00 cm"
    pick(window, (-55, -35, 40))
    settle(window)
    assert not instance.status.text()
    document = window.services.active_document
    obj = next(obj for obj in document.objects.values() if not obj.plane)
    monkeypatch.setattr(window.services, "_selection_provider", lambda doc: (SelectionItem(obj.id, "Esquisse", None, obj.revision),))
    instance.refresh_status()
    assert not instance.status.text()
    assert window.plugins.deactivate(PLUGIN_ID)
    assert not instance.status.isVisible()


@pytest.mark.parametrize("arc", [False, True])
def test_circular_edge_in_status_shows_diameter_in_display_units_without_panel_or_mutation(window, arc):
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCC.Core.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt
    from math import pi
    from cadastrophe.document import SceneObject
    circle = gp_Circ(gp_Ax2(gp_Pnt(0, 0, 70), gp_Dir(0, 0, 1)), 12)
    edge = BRepBuilderAPI_MakeEdge(circle, 0, pi/2).Edge() if arc else BRepBuilderAPI_MakeEdge(circle).Edge()
    document = window.viewport.document
    body = SceneObject("Arête circulaire", edge, (.3, .4, .5))
    document.add(body)
    instance = window.plugins._record(PLUGIN_ID).instance
    before, count = pose(window.viewport), document.history.count
    pick(window, (12/2**.5, 12/2**.5, 70))
    item, = window.services.selection_items
    assert item.shape.IsSame(edge) and item.topology == "Arête"
    settle(window)
    assert instance.status.text() == "Diamètre : 24.000 mm" and not instance.panel.isVisible()
    window.apply_preferences(replace(window.preferences, length_unit="cm", decimals=2))
    settle(window)
    assert instance.status.text() == "Diamètre : 2.40 cm"
    assert pose(window.viewport) == before and document.history.count == count
    window.viewport.selection.from_tree([])
    settle(window)
    assert not instance.status.text()


def test_face_area_updates_on_same_parent_then_units_change_without_mutation(window):
    document, viewport = window.viewport.document, window.viewport
    pick(window, (-55, -55, 20))
    item, = window.services.selection_items
    before = pose(viewport)
    obj = document.objects[item.object_id]
    assert window.command_registry.execute(PREFIX + "area")
    widget = panel(window)
    assert widget.last_result.value == pytest.approx(1600)
    assert widget.result.text() == "1600.000 mm²"
    window.apply_preferences(replace(window.preferences, length_unit="cm", decimals=2))
    assert widget.result.text() == ""  # old-unit result is cleared immediately
    settle(window)
    assert widget.result.text() == "16.00 cm²"
    assert document.objects[obj.id] is obj and document.history.count == 0 and pose(viewport) == before
    assert window.services.selection_items[0].shape.IsSame(item.shape)


def test_distance_from_tree_is_minimum_between_bodies_and_never_changes_history_or_camera(window):
    document, viewport = window.viewport.document, window.viewport
    bodies = [obj for obj in document.objects.values() if not obj.plane]
    document.select_objects([bodies[0].id, bodies[1].id])
    document.rename(bodies[0].id, "Nom temporaire")
    document.history.undo()
    state = document.history.count, document.history.index, document.selected_ids, pose(viewport)
    assert window.command_registry.execute(PREFIX + "distance")
    widget = panel(window)
    # cube ends at X=-35; cylinder radius=22, centred at X=0, Y=-35
    assert widget.last_result.value == pytest.approx(13)
    assert (document.history.count, document.history.index, document.selected_ids, pose(viewport)) == state
    assert document.history.can_redo and not document.is_modified


def test_invalid_selection_never_leaves_old_result_and_replacement_clears_transient_shapes(window, monkeypatch):
    document = window.viewport.document
    body = next(obj for obj in document.objects.values() if not obj.plane)
    document.select_objects([body.id])
    assert window.command_registry.execute(PREFIX + "area")
    widget = panel(window)
    assert widget.last_result.value == pytest.approx(9600)
    widget.set_mode("length")
    assert widget.last_result is None and widget.result.text() == "" and "arête" in widget.detail.text()
    stale = window.services.selection_items
    document.replace_shape(body.id, BRepPrimAPI_MakeBox(10, 20, 30).Shape())
    monkeypatch.setattr(window.services, "_selection_provider", lambda doc: stale)
    widget.set_mode("area")
    assert widget.last_result is None and "plus valide" in widget.detail.text()
    monkeypatch.undo()
    document.select_objects([body.id])
    widget.refresh()
    assert widget.last_result.value == pytest.approx(2200)
    assert document.history.count == 1


def test_panel_follows_documents_hidden_plane_and_active_tool_without_preview_in_measurement(window):
    first = window.viewport.document
    plane = next(obj for obj in first.objects.values() if obj.plane)
    first.select_objects([plane.id])
    assert window.command_registry.execute(PREFIX + "area")
    widget = panel(window)
    assert widget.last_result.value == pytest.approx(210 * 170)
    assert not plane.visible
    second = window.create_document()
    settle(window)
    assert widget.last_result is None and not widget.result.text()
    window.documents.activate(first.id)
    settle(window)
    assert widget.last_result.value == pytest.approx(210 * 170)
    assert window.command_registry.execute("cadastrophe.primitives.box")
    assert not window.command_registry.execute(PREFIX + "area")
    widget.refresh()
    assert widget.last_result is None and "Terminez" in widget.detail.text()
    window.tool_sessions.cancel()
    window.confirm_discard = lambda document: True
    window.close_document(first.id)
    window.close_document(second.id)
    widget.refresh()
    assert widget.last_result is None and "Aucun document" in widget.detail.text()
    assert not window.command_registry.action(PREFIX + "distance").isEnabled()


def test_plugin_deactivation_releases_actions_panel_and_connections_without_affecting_document(window):
    document = window.viewport.document
    obj = next(obj for obj in document.objects.values() if not obj.plane)
    document.select_objects([obj.id])
    assert window.command_registry.execute(PREFIX + "area")
    camera = pose(window.viewport)
    assert window.plugins.deactivate(PLUGIN_ID)
    assert window.manager_pane.count() == 4
    assert not any(command.id.startswith(PREFIX) for command in window.command_registry)
    document.select_objects([])
    assert not document.is_modified and document.history.count == 0 and pose(window.viewport) == camera
    assert window.plugins.activate(PLUGIN_ID)
    assert window.manager_pane.count() == 5 and window.command_registry.execute(PREFIX + "area")
    assert panel(window).last_result is None


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_measurement_panel_theme_captures_and_original_icons(window, theme):
    window.apply_preferences(replace(window.preferences, theme=theme))
    document = window.viewport.document
    document.select_objects([next(obj.id for obj in document.objects.values() if obj.name == "Cube")])
    assert window.command_registry.execute(PREFIX + "area")
    widget = panel(window)
    assert widget.result.text() == "9600.000 mm²"
    assert widget.result.palette().color(QPalette.ColorRole.Base).name() == (DARK if theme == "dark" else LIGHT).panel
    assert isinstance(widget.result, QLineEdit) and widget.result.isReadOnly()
    for mode in MODES:
        asset = Path(__file__).resolve().parents[1] / "plugins/measurements/icons" / f"{mode}.svg"
        assert QSvgRenderer(str(asset)).isValid()
    window.command_manager.setCurrentIndex(3)
    QApplication.processEvents()
    path = Path(__file__).resolve().parents[1] / "artifacts" / f"t15-measurements-{theme}.png"
    assert window.manager_pane.grab().save(str(path))
