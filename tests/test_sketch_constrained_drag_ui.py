"""The feasible constrained preview is the committed snapshot, or stays put."""
from importlib import import_module
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from test_sketch_mode import system
from test_sketch_drawing_ui import screen
from test_sketch_constraints_ui import draw_rectangle
from test_sketch_interaction_polish import clean_scene,current
from test_sketch_editing_ui import mouse_move


@pytest.mark.parametrize("plane",["XY","XZ","YZ"])
@pytest.mark.parametrize("handle",[False,True])
@pytest.mark.parametrize("rigidity",["free","partial","full"])
def test_drag_obeys_all_constraints_in_preview_and_commit_or_refuses_without_undo(system,plane,handle,rigidity):
    clean_scene(system)
    s = draw_rectangle(system,plane)
    window,commands,model,editor = system
    api = import_module(editor.__package__+".constraints")
    solver = import_module(editor.__package__+".solver")
    vp = window.viewport
    vp.view.Camera().SetScale(120)
    vp.invalidate_view()
    QApplication.processEvents()
    sketch = current(s)
    a,b = sketch.entities[:2]
    width,height = a.end.u-a.start.u,b.end.v-b.start.v
    extras = [api.Constraint("length",(api.Reference(b.id),),height)]
    if rigidity != "free":
        extras.append(api.Constraint("fixed",(api.Reference(a.id,"start"),),a.start))
    if rigidity != "partial":
        extras.append(api.Constraint("length",(api.Reference(a.id),),width))
    commands.set_constraints(s.document,s.object_id,(*sketch.constraints,*extras))
    s.document.history.clear()
    before = current(s)
    anchor = a.end if handle else model.Vec2(b.start.u,(b.start.v+b.end.v)/2)
    start = screen(system,(anchor.u,anchor.v))
    end = start+QPoint(40,-25)
    QTest.mousePress(vp,Qt.MouseButton.LeftButton,pos=start)
    assert s.interaction.pending is not None
    mouse_move(system,end)
    assert current(s) == before and s.document.history.count == 0
    if rigidity == "full":
        assert "bloqué" in s.error
        assert not s.surface.presentation.hidden_ids
    else:
        assert not s.error,s.error
        preview = tuple(e for e,_ in s.interaction.overlay.entities.values())
        assert len(preview) == 4
        assert solver.diagnose(replace(before,entities=preview)).success
        assert s.surface.presentation.hidden_ids == {e.id for e in before.entities}
    QTest.mouseRelease(vp,Qt.MouseButton.LeftButton,pos=end)
    after = current(s)
    assert after.constraints == before.constraints and solver.diagnose(after).success
    if rigidity == "full":
        assert after == before and s.document.history.count == 0
        return
    assert after.entities == preview and s.document.history.count == 1
    if rigidity == "partial":
        assert (after.entities[0].start.u,after.entities[0].start.v) == pytest.approx((a.start.u,a.start.v),abs=1e-8)
        assert after.entities[0].end.u > a.end.u+1
    s.document.history.undo()
    assert current(s) == before
    s.document.history.redo()
    assert current(s) == after


def test_escape_drops_feasible_constrained_preview_and_keeps_redo_and_annotation_positions(system):
    clean_scene(system)
    s = draw_rectangle(system)
    window,commands,model,editor = system
    api = import_module(editor.__package__+".constraints")
    sketch = current(s)
    a,b = sketch.entities[:2]
    dim = api.Constraint("length",(api.Reference(a.id),),a.end.u-a.start.u,label_position=model.Vec2(0,-25))
    height = api.Constraint("length",(api.Reference(b.id),),b.end.v-b.start.v)
    commands.set_constraints(s.document,s.object_id,(*sketch.constraints,dim,height))
    s.document.history.clear()
    commands.add_entity(s.document,s.object_id,model.Point(model.Vec2(50,30)))
    s.document.history.undo()
    before = current(s)
    vp = window.viewport
    start = screen(system,(a.start.u,a.start.v))
    end = start+QPoint(45,-30)
    QTest.mousePress(vp,Qt.MouseButton.LeftButton,pos=start)
    mouse_move(system,end)
    assert not s.error and current(s) == before
    annotations = s.surface.presentation.annotations
    shown = next(c for c,_ in annotations.items.values() if c.id == dim.id)
    assert shown.label_position != dim.label_position
    QTest.keyClick(vp,Qt.Key.Key_Escape)
    QTest.mouseRelease(vp,Qt.MouseButton.LeftButton,pos=end)
    assert current(s) == before and s.document.history.count == 1
    assert not s.surface.presentation.hidden_ids and s.interaction.pending is None
    s.document.history.redo()
    assert len(current(s).entities) == 5
