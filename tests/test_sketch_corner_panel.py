"""Choose a corner tool first, then collect edges and edit sizes in the left pane."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from test_sketch_mode import system
from test_sketch_drawing_ui import begin, screen, choose


@pytest.mark.parametrize("mode", ["fillet", "chamfer"])
def test_corner_tool_collects_edges_after_launch_and_applies_atomically(system, mode):
    w, commands, m, _ = system
    session = begin(system)
    doc = w.viewport.document
    points = tuple(m.Vec2(*p) for p in ((-30, -20), (10, -20), (10, 10), (-30, 10)))
    commands.add_entities(doc, session.object_id, tuple(m.Segment(p, points[(i+1)%4]) for i, p in enumerate(points)), title="Rectangle")
    before = doc.objects[session.object_id].sketch
    count = doc.history.count
    choose(system, mode)
    panel = session.corner_panel
    assert panel.isVisible() and w.tool_host.panel.isAncestorOf(panel)
    assert QApplication.activeModalWidget() is None
    assert not panel.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    # Consecutive edge clicks collect both sides without Ctrl or a modal picker.
    for p in ((-10, -20), (10, -5)):
        QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=screen(system, p))
    assert len(session.interaction.selected) == 2
    panel.size.setValue(100.)
    assert not panel.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    assert doc.objects[session.object_id].sketch == before and doc.history.count == count
    panel.size.setValue(3.)
    assert panel.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled(), panel.error.text()
    assert doc.objects[session.object_id].sketch == before
    # Selected sides are picked through their trimmed preview as well.
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=screen(system, (-10, -20)))
    assert len(session.interaction.selected) == 1
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=screen(system, (-10, -20)))
    assert len(session.interaction.selected) == 2
    QApplication.processEvents()
    assert panel.size.height() >= panel.size.sizeHint().height()
    if mode == "chamfer":
        assert not panel.size.geometry().intersects(panel.size2.geometry())
        assert not panel.size2.geometry().intersects(panel.angle.geometry())
    from pathlib import Path
    assert w.grab().save(str(Path("artifacts") / f"sketch-corner-panel-{mode}.png"))
    QTest.mouseClick(panel.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
    assert session.corner_panel is None and doc.history.count == count+1
    assert len(doc.objects[session.object_id].sketch.entities) == 5
    doc.history.undo()
    assert doc.objects[session.object_id].sketch == before


def test_corner_escape_discards_preview_and_keeps_sketch_editor(system):
    w, _, _, _ = system
    session = begin(system)
    count = w.viewport.document.history.count
    choose(system, "fillet")
    assert session.corner_panel is not None
    QTest.keyClick(w.viewport, Qt.Key.Key_Escape)
    assert session.corner_panel is None and w.tool_sessions.editing
    assert w.viewport.document.history.count == count
