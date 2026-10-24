"""Native viewport clicks, previews, atomic edits and project round trips."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox
from test_sketch_mode import system
from test_sketch_drawing_ui import begin, choose, click


@pytest.mark.parametrize("plane", ("XY", "XZ", "YZ"))
def test_reshape_point_native_clicks_single_undo_and_reopen(system, tmp_path, plane):
    window, commands, model, _ = system
    session = begin(system, plane)
    doc, key = session.document, session.object_id
    count = doc.history.count
    choose(system, "point")
    position = click(system, (-15, 10))
    snapshot = doc.objects[key].sketch
    assert len(snapshot.entities) == 1 and type(snapshot.entities[0]) is model.Point
    assert snapshot.entities[0].position == position
    assert not session.error and doc.history.count == count+1
    doc.history.undo()
    assert not doc.objects[key].sketch.entities
    doc.history.redo()
    assert doc.objects[key].sketch == snapshot
    path = tmp_path / "point.cada"
    assert window.save_document(path=path)
    assert window.close_document()
    assert window.open_document(path).objects[key].sketch == snapshot


@pytest.mark.parametrize("mode", ("text", "trim", "offset", "mirror", "linear_pattern", "circular_pattern"))
def test_reshape_panel_native_pick_preview_cancel_apply_undo_and_reopen(system, tmp_path, mode):
    window, commands, model, _ = system
    session = begin(system)
    doc, key = session.document, session.object_id
    if mode == "trim":
        entities = (model.Segment(model.Vec2(-25, 0), model.Vec2(25, 0)),
                    model.Segment(model.Vec2(0, -15), model.Vec2(0, 15), construction=True))
        target = (15, 0)
    else:
        entities = (model.Circle(model.Vec2(-20, 0), 5),)
        target = (-15, 0)
    commands.add_entities(doc, key, entities, title="Sources")
    before, count = doc.objects[key].sketch, doc.history.count
    choose(system, mode)
    panel = session.corner_panel
    assert panel.isVisible() and QApplication.activeModalWidget() is None
    click(system, target)
    if mode == "text":
        panel.text.setText("B8")
    assert panel.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled(), panel.error.text()
    assert doc.objects[key].sketch == before and doc.history.count == count
    QTest.keyClick(window.viewport, Qt.Key.Key_Escape)
    assert session.corner_panel is None and doc.objects[key].sketch == before
    choose(system, mode)
    panel = session.corner_panel
    click(system, target)
    if mode == "text":
        panel.text.setText("B8")
    if mode == "circular_pattern":
        panel.set_pick_mode("position")
        position = click(system, (10, 10))
        assert panel.u.internal_value == pytest.approx(position.u)
    assert panel.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled(), panel.error.text()
    QTest.mouseClick(panel.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
    assert session.corner_panel is None, panel.error.text()
    after = doc.objects[key].sketch
    assert after != before and doc.history.count == count+1
    doc.history.undo()
    assert doc.objects[key].sketch == before
    doc.history.redo()
    assert doc.objects[key].sketch == after
    path = tmp_path / (mode+".cada")
    assert window.save_document(path=path)
    assert window.close_document()
    loaded = window.open_document(path)
    QApplication.processEvents()
    assert loaded.objects[key].sketch == after
    service = window.services.service("cadastrophe.sketch.profiles")
    if mode != "trim":
        assert service.analyze(loaded, key).valid
        assert service.to_faces(loaded, key).regions


def test_reshape_invalid_pattern_and_text_do_not_mutate_document(system):
    window, commands, model, _ = system
    session = begin(system)
    commands.add_entities(session.document, session.object_id,
        (model.Circle(model.Vec2(-20, 0), 5),), title="Source")
    before = session.document.objects[session.object_id].sketch
    count = session.document.history.count
    choose(system, "linear_pattern")
    click(system, (-15, 0))
    panel = session.corner_panel
    panel.du.setValue(0.)
    assert not panel.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    panel.commit()
    assert session.document.objects[session.object_id].sketch == before
    choose(system, "text")
    panel = session.corner_panel
    panel.text.clear()
    assert not panel.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    panel.commit()
    assert session.document.objects[session.object_id].sketch == before
    assert session.document.history.count == count
