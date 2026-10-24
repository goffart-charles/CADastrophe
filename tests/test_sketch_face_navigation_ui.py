"""Face creation through real picking and editing with ordinary 3D navigation."""
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cadastrophe.geometry import Vec3
from cadastrophe.interaction import Gesture
from test_sketch_mode import system, camera_state
from test_sketch_drawing_ui import begin, choose, click, screen, move, entities


def navigation_move(vp, position, modifiers=Qt.KeyboardModifier.NoModifier):
    point = QPointF(position)
    QApplication.sendEvent(vp, QMouseEvent(QEvent.Type.MouseMove, point,
        QPointF(vp.mapToGlobal(point.toPoint())), Qt.MouseButton.NoButton,
        Qt.MouseButton.MiddleButton, modifiers))


def test_real_face_pick_create_draw_save_reopen_and_reedit(system, tmp_path):
    window, _, model, _ = system
    vp, doc = window.viewport, window.viewport.document
    previous = camera_state(vp)
    x, y = vp.view.Convert(-55., -55., 20.)
    pos = QPoint(round(x/vp.devicePixelRatioF()), round(y/vp.devicePixelRatioF()))
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=pos)
    selected = window.services.selection_items
    assert len(selected) == 1 and selected[0].topology == "Face"
    assert window.command_registry.execute("cadastrophe.sketch.new")
    session = window.tool_sessions.editor
    support = doc.objects[session.object_id].sketch.support
    assert support.face_index is not None and support.plane_id == selected[0].object_id
    assert support.frame.normal.dot(Vec3(*vp.view.Camera().Direction().Coord())) == pytest.approx(-1)
    choose(system, "line")
    click(system, (5, 8))
    click(system, (25, 18))
    key = session.object_id
    assert len(entities(system)) == 1
    assert window.command_registry.execute("edit.undo") and not entities(system)
    assert window.command_registry.execute("edit.redo") and len(entities(system)) == 1
    path = tmp_path / "face.cada"
    assert window.save_document(path=path)
    previous = camera_state(vp)
    assert window.command_registry.execute("cadastrophe.sketch.finish") and camera_state(vp) == previous
    restored = vp.selection.items()
    assert len(restored) == 1 and restored[0].shape.IsSame(selected[0].shape)
    assert window.close_document()
    loaded = window.open_document(path)
    QApplication.processEvents()
    window.tree_selection_changed([key])
    assert window.command_registry.execute("cadastrophe.sketch.edit")
    assert window.tool_sessions.editor.workplane == support.frame
    assert loaded.objects[key].sketch.support == support


@pytest.mark.parametrize("plane", ["XY", "XZ", "YZ"])
def test_orbit_roll_pan_modifiers_and_drawing_in_oblique_view_keep_support_and_history(system, plane):
    window, commands, model, _ = system
    vp = window.viewport
    previous = camera_state(vp)
    session = begin(system, plane)
    doc = session.document
    support = doc.objects[session.object_id].sketch.support
    target = model.Vec2(20, 10)
    commands.add_entity(doc, session.object_id, model.Point(target))
    history = doc.history.count
    pose = camera_state(vp)
    start, end = QPoint(410, 310), QPoint(480, 345)
    QTest.mousePress(vp, Qt.MouseButton.MiddleButton, pos=start)
    assert vp.state.gesture is Gesture.ROTATE
    navigation_move(vp, end)
    assert camera_state(vp) != pose
    pose = camera_state(vp)
    ctrl, alt = Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.AltModifier
    QApplication.sendEvent(vp, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Control, ctrl))
    assert vp.state.gesture is Gesture.PAN and camera_state(vp) == pose
    direction, up = vp.view.Camera().Direction().Coord(), vp.view.Camera().Up().Coord()
    navigation_move(vp, QPoint(490, 353), ctrl)
    assert vp.view.Camera().Direction().Coord() == direction and vp.view.Camera().Up().Coord() == up
    pose = camera_state(vp)
    QApplication.sendEvent(vp, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Alt, ctrl | alt))
    assert vp.state.gesture is Gesture.PAN and camera_state(vp) == pose
    QApplication.sendEvent(vp, QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Control, alt))
    assert vp.state.gesture is Gesture.ROLL and camera_state(vp) == pose
    navigation_move(vp, QPoint(510, 375), alt)
    assert camera_state(vp) != pose
    QTest.mouseRelease(vp, Qt.MouseButton.MiddleButton, alt, QPoint(510, 375))
    assert vp.state.gesture is Gesture.IDLE and doc.history.count == history
    assert doc.objects[session.object_id].sketch.support == support and vp.selection.suspended
    # Snap in an oblique projection, then draw onto the original plane.
    choose(system, "line")
    move(system, (target.u, target.v))
    assert session.snap_result.kind == "endpoint" and session.draft.current == target
    click(system, (target.u, target.v))
    click(system, (-20, -15))
    edge = entities(system)[-1]
    assert edge.start == target and doc.history.count == history+1
    for point in (edge.start, edge.end):
        assert support.frame.to_local(doc.objects[session.object_id].sketch.to_world(point))[2] == pytest.approx(0)
    assert window.command_registry.execute("view.standard.isometric")
    assert window.command_registry.execute("view.normal_to_plane")
    assert Vec3(*vp.view.Camera().Direction().Coord()).dot(support.frame.normal) == pytest.approx(-1)
    previous = camera_state(vp)
    assert window.command_registry.execute("cadastrophe.sketch.finish") and camera_state(vp) == previous


def test_viewcube_during_rectangle_does_not_start_draw_or_leave_sketch(system):
    window = system[0]
    vp = window.viewport
    session = begin(system)
    choose(system, "rectangle")
    history = session.document.history.count
    previous = camera_state(vp)
    # Native hit testing finds a real cube owner at each DPI, without relying on
    # window-coordinate constants or reimplementing cube orientation dispatch.
    found = None
    for y in range(30, min(vp.height(), 170), 5):
        for x in range(max(0, vp.width()-160), vp.width()-25, 5):
            if vp.cube_at(QPointF(x, y)):
                found = QPoint(x, y)
                break
        if found is not None:
            break
    assert found is not None
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=found)
    assert not session.draft.anchors and not session._rectangle_active
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=found)
    assert window.tool_sessions.editing and vp.workplane == session.workplane
    assert not entities(system) and session.document.history.count == history
    assert camera_state(vp) != previous
    assert window.command_registry.execute("view.normal_to_plane")


def test_edge_on_support_rejects_drawing_without_mutation_and_zoom_window_remains_available(system):
    window = system[0]
    session = begin(system)
    vp = window.viewport
    choose(system, "line")
    history = session.document.history.count
    assert window.command_registry.execute("view.standard.front")
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=QPoint(350, 270))
    assert not session.draft.anchors and not entities(system) and session.document.history.count == history
    assert window.command_registry.execute("view.normal_to_plane")
    assert window.command_registry.execute("view.zoom_window") and vp.window_zoom
    scale = vp.view.Camera().Scale()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=QPoint(300, 220))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=QPoint(520, 420))
    assert vp.view.Camera().Scale() < scale and not vp.window_zoom
    assert not entities(system) and session.document.history.count == history
    root = Path(__file__).resolve().parents[1] / "artifacts"
    assert vp.view.Dump(str(root / "sketch-free-navigation.png"))
