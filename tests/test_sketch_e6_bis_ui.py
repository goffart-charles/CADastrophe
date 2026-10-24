"""E/F: actual Qt clicks and OCCT annotations, on the private test desktop."""
from dataclasses import replace
from importlib import import_module
from math import hypot, radians
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel

from test_sketch_mode import system
from test_sketch_drawing_ui import begin, choose, click, move, screen
from test_sketch_editing_ui import mouse_move
from test_sketch_view_tools import accept_value


def current(s):
    return s.document.objects[s.object_id].sketch


def assert_connected_rectangle(s, width, height):
    edges = current(s).entities
    for i,e in enumerate(edges):
        next_edge = edges[(i+1)%4]
        assert (e.end.u,e.end.v) == pytest.approx((next_edge.start.u,next_edge.start.v),abs=1e-7)
        assert (e.end.v-e.start.v if i%2 == 0 else e.end.u-e.start.u) == pytest.approx(0,abs=1e-7)
        assert hypot(e.end.u-e.start.u,e.end.v-e.start.v) == pytest.approx(height if i%2 else width,abs=1e-7)


@pytest.mark.parametrize("plane", ["XY","XZ","YZ"])
def test_e_real_rectangle_two_left_clicks_then_empty_space_confirm_and_undo(system, plane, monkeypatch):
    s = begin(system,plane)
    choose(system,"rectangle")
    vp = system[0].viewport
    QTest.mousePress(vp,Qt.MouseButton.LeftButton,pos=screen(system,(-40,-25)))
    mouse_move(system,screen(system,(40,25)))
    QTest.mouseRelease(vp,Qt.MouseButton.LeftButton,pos=screen(system,(40,25)))
    original = current(s)
    assert len(original.entities) == 4 and len(original.constraints) == 8
    width = original.entities[0].end.u-original.entities[0].start.u
    height = original.entities[1].end.v-original.entities[1].start.v
    s.document.history.clear()
    choose(system,"dimension")
    solver = import_module(system[3].__package__+".solver")
    actual_solve, calls = solver.solve, []
    def tracked(sketch,**kwargs):
        calls.append(sketch)
        return actual_solve(sketch,**kwargs)
    monkeypatch.setattr(solver,"solve",tracked)
    tools = s.view_tools
    for e in (original.entities[1],original.entities[3]):
        click(system,((e.start.u+e.end.u)/2,(e.start.v+e.end.v)/2))
        assert tools.dialog is None and current(s) == original
    assert len(tools.refs) == 2
    assert set(s.interaction.selected) == {original.entities[1].id,original.entities[3].id}
    move(system,(0,38))
    assert tools.preview.items and not calls and s.document.history.count == 0
    click(system,(0,38))
    assert tools.dialog is not None and not calls and current(s) == original
    accept_value(s,150)
    assert len(calls) == 1 and s.document.history.count == 1
    changed = current(s)
    assert_connected_rectangle(s,150,height)
    assert tools.mode == "dimension" and not tools.refs and not tools.preview.items
    QTest.keyClick(vp,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
    assert current(s) == original
    QTest.keyClick(vp,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier)
    assert current(s) == changed
    assert_connected_rectangle(s,150,height)
    # Re-select the same sides: edit the existing constraint, no second dimension.
    key = next(c.id for c in changed.constraints if c.kind == "line_distance")
    for e in (changed.entities[3],changed.entities[1]):
        click(system,((e.start.u+e.end.u)/2,(e.start.v+e.end.v)/2))
    click(system,(0,40))
    accept_value(s,120)
    assert len(current(s).constraints) == len(changed.constraints)
    assert next(c for c in current(s).constraints if c.id == key).value == 120
    assert_connected_rectangle(s,120,height)


@pytest.mark.parametrize("cursor,expected", [((20,35),60), ((-20,35),120)])
def test_angle_preview_sector_editor_native_annotation_and_history(system,cursor,expected):
    s = begin(system)
    _, commands, model, editor = system
    c = import_module(editor.__package__+".constraints")
    a = model.Segment(model.Vec2(-30,0),model.Vec2(30,0))
    b = model.Segment(model.Vec2(-15,-26),model.Vec2(15,26))
    commands.add_entities(s.document,s.object_id,(a,b),title="Deux lignes")
    s.document.history.clear()
    choose(system,"dimension")
    click(system,(20,0))
    click(system,(10,17.33))
    assert len(s.view_tools.refs) == 2 and s.view_tools.dialog is None and not current(s).constraints
    move(system,cursor)
    preview = next(iter(s.view_tools.preview.items.values()))[0]
    assert preview.kind == "angle" and preview.value == pytest.approx(radians(expected),abs=.002)
    click(system,cursor)
    assert "°" in s.view_tools.value_editor.suffix()
    accept_value(s,radians(75))
    changed = current(s)
    dimension = changed.constraints[-1]
    from OCC.Core.PrsDim import PrsDim_AngleDimension
    annotation = s.surface.presentation.annotations.items[dimension.id][1]
    assert isinstance(annotation,PrsDim_AngleDimension) and annotation.IsValid()
    assert annotation.GetValue() == pytest.approx(radians(75),abs=1e-7)
    assert annotation.GetCustomValue().Print() == "75.00"
    path = Path(__file__).resolve().parents[1] / "artifacts" / f"e6-bis-angle-{expected}.png"
    assert system[0].viewport.view.Dump(str(path))
    assert s.document.history.count == 1
    s.document.history.undo()
    assert current(s).entities == (a,b) and not current(s).constraints
    s.document.history.redo()
    assert current(s) == changed
    p = s.constraint_panel
    p.list.setCurrentRow(0)
    assert p.kind.currentData() == "angle" and p.angle_value.internal_value == pytest.approx(radians(75))
    p.angle_value.set_internal_value(radians(80))
    assert p.update_constraint()
    assert current(s).constraints[0].id == dimension.id
    assert current(s).constraints[0].value == pytest.approx(radians(80))


def test_conflict_in_modify_dialog_keeps_document_and_redo_then_can_be_corrected(system):
    s = begin(system)
    _,commands,model,editor = system
    c = import_module(editor.__package__+".constraints")
    line = model.Segment(model.Vec2(-20,0),model.Vec2(20,0))
    commands.add_entity(s.document,s.object_id,line)
    constraints = (c.Constraint("fixed",(c.Reference(line.id,"start"),),line.start),
                   c.Constraint("fixed",(c.Reference(line.id,"end"),),line.end))
    commands.set_constraints(s.document,s.object_id,constraints)
    s.document.history.clear()
    before = s.document.objects[s.object_id]
    choose(system,"dimension")
    click(system,(5,0))
    click(system,(0,-20))
    tools = s.view_tools
    tools.value_editor.set_internal_value(60)
    buttons = tools.dialog.findChild(QDialogButtonBox)
    QTest.mouseClick(buttons.button(QDialogButtonBox.StandardButton.Ok),Qt.MouseButton.LeftButton)
    assert tools.dialog is not None
    assert any("Échec" in label.text() for label in tools.dialog.findChildren(QLabel))
    assert s.document.objects[s.object_id] is before and s.document.history.count == 0
    accept_value(s,40)
    assert s.document.history.count == 1 and current(s).entities == before.sketch.entities


def test_escape_incompatible_second_selection_and_repeated_dimensions(system):
    s = begin(system)
    _,commands,model,_ = system
    line = model.Segment(model.Vec2(-25,-10),model.Vec2(25,-10))
    circle = model.Circle(model.Vec2(0,18),10)
    third = model.Point(model.Vec2(30,8))
    commands.add_entities(s.document,s.object_id,(line,circle,third),title="Entités")
    s.document.history.clear()
    choose(system,"dimension")
    click(system,(0,-10))
    click(system,(10,18))
    # A circle center + a line is a valid point-to-line dimension.
    assert len(s.view_tools.refs) == 2 and s.view_tools.dialog is None
    selected = tuple(s.view_tools.refs)
    click(system,(30,8))
    assert tuple(s.view_tools.refs) == selected and "incompatible" in s.error
    assert s.document.history.count == 0 and not current(s).constraints
    QTest.keyClick(system[0].viewport,Qt.Key.Key_Escape)
    assert len(s.view_tools.refs) == 1 and s.view_tools.mode == "dimension"
    QTest.keyClick(system[0].viewport,Qt.Key.Key_Escape)
    assert not s.view_tools.refs and s.view_tools.mode == "dimension"
    click(system,(0,-10))
    click(system,(0,-25))
    accept_value(s,60)
    click(system,(10,18))
    click(system,(26,30))
    tools = s.view_tools
    QTest.keyClick(tools.dialog,Qt.Key.Key_Escape)
    QApplication.processEvents()
    assert tools.dialog is None and tools.mode == "dimension" and s.document.history.count == 1
    click(system,(10,18))
    click(system,(26,30))
    accept_value(s,30)
    assert [c.kind for c in current(s).constraints] == ["length","diameter"]
    assert s.document.history.count == 2


def test_annotation_over_geometry_does_not_turn_selection_into_placement(system):
    s = begin(system)
    _,commands,model,editor = system
    c = import_module(editor.__package__+".constraints")
    line = model.Segment(model.Vec2(-20,0),model.Vec2(20,0))
    commands.add_entity(s.document,s.object_id,line)
    dimension = c.Constraint("length",(c.Reference(line.id),),40,label_position=model.Vec2(5,0))
    commands.set_constraints(s.document,s.object_id,(dimension,),validate_sketch=s.validate_sketch)
    s.document.history.clear()
    choose(system,"dimension")
    click(system,(5,0))
    assert s.view_tools.refs == [c.Reference(line.id)] and s.view_tools.dialog is None
    click(system,(0,-8))
    accept_value(s,60)
    assert len(current(s).constraints) == 1 and current(s).constraints[0].id == dimension.id
    assert s.document.history.count == 1


def test_angle_field_preserves_internal_precision_until_value_changes(system):
    box_type = import_module(system[3].__package__+".dimension_value").AngleSpinBox
    value = radians(73.123456789)
    box = box_type(value)
    box.interpretText()
    assert box.internal_value == value
    box.setValue(80)
    assert box.internal_value == pytest.approx(radians(80))
    for bad in (0,True,float("nan"),radians(180)):
        with pytest.raises(ValueError):
            box.set_internal_value(bad)
    box.deleteLater()
