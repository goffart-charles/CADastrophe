"""Native editor selection, equation drafts, read-only occurrences and baking."""
from dataclasses import replace
from math import pi

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox

from cadastrophe.core.expressions import Expression, Variable
from test_sketch_mode import system
from test_sketch_drawing_ui import begin, choose, click


@pytest.mark.parametrize("mode", ("linear_pattern", "circular_pattern"))
def test_sketch_patterns_native_editor_equations_cancel_modify_select_detach_and_reload(system, tmp_path, mode):
    window, commands, model, _ = system
    session = begin(system)
    doc, key = session.document, session.object_id
    source = model.Circle(model.Vec2(-20, 0), 5.)
    commands.add_entities(doc, key, (source,), title="Source")
    session.mutate(lambda: doc.set_variables((Variable("nombre", Expression("value", "=4"), unit="number"),)))
    before, count = doc.objects[key], doc.history.count
    choose(system, mode)
    click(system, (-15, 0))
    panel = session.corner_panel
    panel.count.lineEdit().setText("=nombre")
    panel.count.lineEdit().textEdited.emit("=nombre")
    panel.count._completer.popup().hide()
    assert panel.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled(), panel.error.text()
    assert doc.objects[key] == before and doc.history.count == count
    panel.count.lineEdit().setText("=inconnue")
    panel.count.lineEdit().textEdited.emit("=inconnue")
    panel.count._completer.popup().hide()
    assert not panel.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    panel.commit()
    assert doc.objects[key] == before
    panel.count.lineEdit().setText("=nombre")
    panel.count.lineEdit().textEdited.emit("=nombre")
    panel.count._completer.popup().hide()
    QTest.mouseClick(panel.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
    assert session.corner_panel is None, panel.error.text()
    patterned = doc.objects[key].sketch
    pattern = patterned.patterns[0]
    assert len(patterned.entities) == 4 and doc.history.count == count+1
    assert next(e for e in doc.objects[key].expressions if e.target == pattern.id+".count").text == "=nombre"
    # Real viewport picking remains available for generated contours.
    copy = patterned.entities[1]
    click(system, (copy.center.u+5, copy.center.v))
    assert session.interaction.selected == (copy.id,)
    assert not session.entity_panel.apply.isEnabled()
    assert session.entity_panel.patterns.currentData() == pattern.id
    assert not session.interaction.handles
    QTest.mouseClick(session.entity_panel.edit_pattern, Qt.MouseButton.LeftButton)
    panel = session.corner_panel
    assert panel.editing_id == pattern.id and session.interaction.selected == (source.id,)
    assert set(panel.preview.entities) == set(pattern.generated_ids)
    assert panel.count.equation_expression().text == "=nombre"
    field = panel.du if mode == "linear_pattern" else panel.angle
    field.setValue(30. if mode == "linear_pattern" else 180.)
    assert panel.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled(), panel.error.text()
    QTest.keyClick(window.viewport, Qt.Key.Key_Escape)
    assert session.corner_panel is None and doc.objects[key].sketch == patterned
    QTest.mouseClick(session.entity_panel.edit_pattern, Qt.MouseButton.LeftButton)
    panel = session.corner_panel
    (panel.du if mode == "linear_pattern" else panel.angle).setValue(30. if mode == "linear_pattern" else 180.)
    QTest.mouseClick(panel.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
    assert session.corner_panel is None, panel.error.text()
    edited = doc.objects[key].sketch
    assert [e.id for e in edited.entities] == [e.id for e in patterned.entities]
    if mode == "linear_pattern":
        assert edited.entity(copy.id).center.u == pytest.approx(10.)
    else:
        assert edited.patterns[0].angle == pytest.approx(pi)
    doc.history.undo()
    assert doc.objects[key].sketch == patterned
    doc.history.redo()
    assert doc.objects[key].sketch == edited
    session.mutate(lambda: doc.set_variables((replace(doc.variables[0], expression=Expression("value", "=3")),)))
    reduced = doc.objects[key].sketch
    assert len(reduced.entities) == 3
    session.mutate(lambda: doc.set_variables((replace(doc.variables[0], expression=Expression("value", "=4")),)))
    assert doc.objects[key].sketch == edited
    QTest.mouseClick(session.entity_panel.detach_pattern, Qt.MouseButton.LeftButton)
    assert not doc.objects[key].sketch.patterns
    assert doc.objects[key].sketch.entities == edited.entities
    assert not any(e.target.startswith(pattern.id) for e in doc.objects[key].expressions)
    doc.history.undo()
    assert doc.objects[key].sketch == edited
    doc.history.redo()
    path = tmp_path / (mode+"-dissocie.cada")
    assert window.save_document(path=path)
    assert window.close_document()
    loaded = window.open_document(path)
    assert not loaded.objects[key].sketch.patterns and loaded.objects[key].sketch.entities == edited.entities


def test_sketch_patterns_native_dimensions_are_measurements_and_driving_relations_are_refused(system):
    # Use the plugin's isolated classes, like the ordinary editor tools.
    window, commands, model, _ = system
    session = begin(system)
    doc, key = session.document, session.object_id
    source = model.Circle(model.Vec2(-20, 0), 5.)
    commands.add_entities(doc, key, (source,), title="Source")
    choose(system, "linear_pattern")
    click(system, (-15, 0))
    panel = session.corner_panel
    QTest.mouseClick(panel.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
    assert session.corner_panel is None, panel.error.text()
    tools = session.view_tools
    # The dynamic plugin module supplies Reference/Constraint to these methods.
    reference_type = tools.dimension.__globals__["Reference"]
    constraint_type = tools.dimension.__globals__["Constraint"]
    copy = doc.objects[key].sketch.entities[1]
    tools.refs = [reference_type(copy.id)]
    assert tools.available() == ()
    dimension = tools.dimension(model.Vec2(5, 10))
    assert dimension.driven and dimension.value == pytest.approx(10.)
    tools.open_editor(dimension, 300, 200, new=True)
    assert tools.value_editor.isReadOnly()
    tools.dialog.findChild(QDialogButtonBox).accepted.emit()
    QTest.qWait(30)
    assert tools.dialog is None and doc.objects[key].sketch.constraints[-1].driven
    before, count = doc.objects[key], doc.history.count
    with pytest.raises(ValueError, match="pilotantes"):
        commands.set_constraints(doc, key, (*before.sketch.constraints,
            constraint_type("radius", (reference_type(copy.id),), 12.)))
    assert doc.objects[key] == before and doc.history.count == count
    commands.replace_entity(doc, key, replace(source, radius=7.))
    assert doc.objects[key].sketch.constraints[-1].value == pytest.approx(14.)
    session.interaction.set_selected((copy.id,))
    assert session.entity_panel.fields["radius"][0].isReadOnly()
    QTest.mouseClick(session.entity_panel.detach_pattern, Qt.MouseButton.LeftButton)
    assert not doc.objects[key].sketch.patterns
    assert session.entity_panel.apply.isEnabled()
    assert not session.entity_panel.fields["radius"][0].isReadOnly()
