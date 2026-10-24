"""Native handle dragging keeps the shared node, atomic preview and undo."""
from math import pi

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from test_sketch_mode import system
from test_sketch_drawing_ui import begin, screen
from test_sketch_editing_ui import mouse_move


def seed(system, plane="XY"):
    session = begin(system, plane)
    _, commands, model, _ = system
    node = model.Vec2(-20, -15)
    edges = (model.Segment(model.Vec2(-40, -15), node),
             model.Segment(node, model.Vec2(-20, 25)),
             model.Segment(node, model.Vec2(20, -25)))
    commands.add_entities(session.document, session.object_id, edges, title="Jonction")
    session.document.history.clear()
    session.interaction.set_selected((edges[0].id,))
    return session, edges, node


@pytest.mark.parametrize("plane", ["XY", "XZ", "YZ"])
def test_native_joint_drag_previews_all_incident_edges_and_commits_one_undo(system, plane):
    session, original, node = seed(system, plane)
    vp = system[0].viewport
    start, end = screen(system, (node.u, node.v)), screen(system, (-5, 10))
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=start)
    assert session.interaction.pending["handle"] == (original[0].id, "end")
    assert len(session.interaction.pending["entities"]) == 3
    mouse_move(system, end)
    doc = session.document
    assert doc.objects[session.object_id].sketch.entities == original and doc.history.count == 0
    assert session.surface.presentation.hidden_ids == {edge.id for edge in original}
    preview = tuple(entity for entity, _ in session.interaction.overlay.entities.values())
    assert len(preview) == 3
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=end)
    changed = doc.objects[session.object_id].sketch.entities
    assert changed[0].end == changed[1].start == changed[2].start
    assert changed[0].end != node and doc.history.count == 1
    assert changed[0].start == original[0].start
    assert tuple(edge.id for edge in changed) == tuple(edge.id for edge in original)
    assert session.interaction.selected == (original[0].id,)
    assert not session.surface.presentation.hidden_ids
    doc.history.undo()
    assert doc.objects[session.object_id].sketch.entities == original
    doc.history.redo()
    assert doc.objects[session.object_id].sketch.entities == changed


def test_escape_cancels_all_joint_previews_and_preserves_redo(system):
    session, original, node = seed(system)
    vp, doc = system[0].viewport, session.document
    _, commands, model, _ = system
    extra = model.Point(model.Vec2(50, 30))
    commands.add_entity(doc, session.object_id, extra)
    doc.history.undo()
    start, end = screen(system, (node.u, node.v)), screen(system, (0, 10))
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=start)
    mouse_move(system, end)
    QTest.keyClick(vp, Qt.Key.Key_Escape)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=end)
    assert doc.objects[session.object_id].sketch.entities == original and doc.history.count == 1
    assert not session.surface.presentation.hidden_ids and session.interaction.pending is None
    doc.history.redo()
    assert doc.objects[session.object_id].sketch.entities == (*original, extra)


@pytest.mark.parametrize("base_detected",[False,True])
def test_native_explicit_point_handle_moves_all_connected_segments(system,monkeypatch,base_detected):
    session, original, node = seed(system)
    _, commands, model, _ = system
    point = model.Point(node)
    commands.add_entity(session.document, session.object_id, point)
    session.document.history.clear()
    session.interaction.set_selected((point.id,))
    vp = system[0].viewport
    start, end = screen(system, (node.u, node.v)), screen(system, (0, 10))
    if base_detected:
        native = session.surface.presentation.vertices[(original[0].id,"end")][1]
        monkeypatch.setattr(session.surface,"detect",lambda *_: native)
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=start)
    assert session.interaction.pending["handle"] == (point.id, "position")
    assert len(session.interaction.pending["entities"]) == 4
    mouse_move(system, end)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=end)
    changed = session.document.objects[session.object_id].sketch.entities
    assert changed[0].end == changed[1].start == changed[2].start == changed[3].position
    assert changed[3].position != node and session.document.history.count == 1


@pytest.mark.parametrize("source_arc", [False, True])
def test_native_arc_line_joint_keeps_real_endpoints_together(system, source_arc):
    session = begin(system)
    _, commands, model, _ = system
    arc = model.Arc(model.Vec2(0, 0), 20, 0, pi/2)
    node = arc.point_at(1)
    line = model.Segment(node, model.Vec2(-30, 35))
    commands.add_entities(session.document, session.object_id, (line, arc), title="Jonction arc")
    session.document.history.clear()
    session.interaction.set_selected(((arc if source_arc else line).id,))
    vp = system[0].viewport
    start, end = screen(system, (node.u, node.v)), screen(system, (-15, 25))
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=start)
    assert len(session.interaction.pending["entities"]) == 2
    mouse_move(system, end)
    assert not session.error
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=end)
    changed_line, changed_arc = session.document.objects[session.object_id].sketch.entities
    actual = changed_arc.point_at(1)
    assert (actual.u, actual.v) == pytest.approx((changed_line.start.u, changed_line.start.v), abs=1e-7)
    assert changed_line.start != node and session.document.history.count == 1
    session.document.history.undo()
    assert session.document.objects[session.object_id].sketch.entities == (line, arc)
