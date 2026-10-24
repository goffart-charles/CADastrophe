"""The six related a_faire points, exercised through real Qt/OCCT interactions."""
from dataclasses import replace
from importlib import import_module
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from test_sketch_mode import system
from test_sketch_drawing_ui import begin, choose, click, move, screen
from test_sketch_editing_ui import mouse_move
from test_sketch_view_tools import accept_value
from test_sketch_constraints_ui import draw_rectangle


def clean_scene(system):
    from cadastrophe.plugin_api import ObjectType
    doc = system[0].viewport.document
    doc.remove_objects([e.id for e in doc.objects.values() if e.kind is ObjectType.BODY])


def current(s):
    return s.document.objects[s.object_id].sketch


def test_smart_dimension_hover_edges_and_unselected_vertices_is_transient(system):
    clean_scene(system)
    s = begin(system)
    _,commands,model,editor = system
    c = import_module(editor.__package__+".constraints")
    line = model.Segment(model.Vec2(-25,0),model.Vec2(25,0))
    commands.add_entity(s.document,s.object_id,line)
    s.document.history.clear()
    before = s.document.objects[s.object_id]
    choose(system,"dimension")
    tools = s.view_tools
    move(system,(5,0))
    assert tools.hover_ref == c.Reference(line.id)
    assert tools.hover.entities and not tools.refs and not s.interaction.selected
    from OCC.Core.AIS import AIS_Point
    move(system,(25,0))
    assert tools.hover_ref == c.Reference(line.id,"end")
    assert isinstance(next(iter(tools.hover.entities.values()))[1],AIS_Point)
    point = screen(system,(25,0))+QPoint(6,4)
    QTest.mouseClick(system[0].viewport,Qt.MouseButton.LeftButton,pos=point)
    assert tools.refs == [c.Reference(line.id,"end")]
    move(system,(0,20))
    assert not tools.hover.entities and tools.hover_ref is None
    assert not tools.external and s.document.objects[s.object_id] is before and s.document.history.count == 0
    choose(system,"select")
    # A visible endpoint inside a window must not select its entire crossing edge.
    vp = system[0].viewport
    QTest.mousePress(vp,Qt.MouseButton.LeftButton,pos=screen(system,(-30,-15)))
    mouse_move(system,screen(system,(-20,15)))
    QTest.mouseRelease(vp,Qt.MouseButton.LeftButton,pos=screen(system,(-20,15)))
    assert not s.interaction.selected


def test_smart_dimension_hover_solid_vertex_does_not_capture_a_reference(system):
    clean_scene(system)
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from cadastrophe.plugin_api import SceneObject
    doc = system[0].viewport.document
    doc.add(SceneObject("Bloc",BRepPrimAPI_MakeBox(20,20,10).Shape(),(.6,.6,.7)))
    s = begin(system)
    doc.history.clear()
    choose(system,"dimension")
    move(system,(20,20))
    tools = s.view_tools
    assert tools.hover_ref is not None and tools._body_hover is not None
    assert s.surface.context.IsDisplayed(tools._body_hover)
    assert not tools.refs and not tools.external and not current(s).external and doc.history.count == 0
    move(system,(-15,-10))
    assert tools.hover_ref is None and tools._body_hover is None


def test_click_in_empty_space_and_escape_deselect_a_dimension_completely(system):
    clean_scene(system)
    s = begin(system)
    _,commands,model,editor = system
    c = import_module(editor.__package__+".constraints")
    line = model.Segment(model.Vec2(-30,0),model.Vec2(30,0))
    commands.add_entity(s.document,s.object_id,line)
    dim = c.Constraint("length",(c.Reference(line.id),),60,label_position=model.Vec2(0,-12))
    commands.set_constraints(s.document,s.object_id,(dim,))
    s.document.history.clear()
    tools = s.view_tools
    for mode,escape in ((None,False),(None,True),("relations",False),("relations",True),("dimension",False)):
        choose(system,"select")
        if mode is not None:
            choose(system,mode)
            tools.select_constraint(dim.id)
        else:
            click(system,(0,-12))
        assert tools.selected_constraint == dim.id
        if escape:
            QTest.keyClick(system[0].viewport,Qt.Key.Key_Escape)
        else:
            click(system,(0,15))
        assert tools.selected_constraint is None and not tools.refs and not s.interaction.selected
        assert s.constraint_panel.list.currentItem() is None
        assert s.surface.presentation.annotations.selected is None
    assert s.document.history.count == 0


def test_confirmed_smart_dimension_automatically_starts_the_next_one(system):
    clean_scene(system)
    s = begin(system)
    _,commands,model,_ = system
    lines = (model.Segment(model.Vec2(-20,-15),model.Vec2(20,-15)),
             model.Segment(model.Vec2(-20,15),model.Vec2(20,15)))
    commands.add_entities(s.document,s.object_id,lines,title="Lignes")
    s.document.history.clear()
    choose(system,"dimension")
    for y,target in ((-15,60),(15,70)):
        move(system,(0,y))
        assert s.view_tools.hover_ref is not None
        click(system,(0,y))
        click(system,(0,y+(-10 if y < 0 else 10)))
        accept_value(s,target)
        assert s.view_tools.mode == "dimension" and not s.view_tools.refs
        assert s.view_tools.selected_constraint is None and not s.view_tools.preview.items
        assert system[0].command_registry.action("cadastrophe.sketch.dimension").isChecked()
        assert QApplication.focusWidget() is system[0].viewport
    assert [c.value for c in current(s).constraints] == [60,70]
    assert s.document.history.count == 2


@pytest.mark.parametrize("plane",["XY","XZ","YZ"])
def test_rectangle_edge_drag_preview_and_commit_move_all_related_geometry(system,plane,monkeypatch):
    clean_scene(system)
    s = draw_rectangle(system,plane)
    c = import_module(system[3].__package__+".constraints")
    commands = system[1]
    before = current(s)
    dimensions = tuple(c.Constraint("length",(c.Reference(before.entities[i].id),),
                      abs(before.entities[i].end.u-before.entities[i].start.u) if i == 0 else abs(before.entities[i].end.v-before.entities[i].start.v),
                      label_position=system[2].Vec2(0,-22) if i == 0 else None)
                      for i in (0,1))
    commands.set_constraints(s.document,s.object_id,(*before.constraints,*dimensions))
    s.document.history.clear()
    before = current(s)
    solver = import_module(system[3].__package__+".solver")
    calls,actual = [],solver.solve
    def tracked(*args,**kwargs):
        calls.append(True)
        return actual(*args,**kwargs)
    monkeypatch.setattr(solver,"solve",tracked)
    e = before.entities[0]
    middle = ((e.start.u+e.end.u)/2,(e.start.v+e.end.v)/2)
    start,end = screen(system,middle),screen(system,(middle[0]+8,middle[1]+7))
    vp = system[0].viewport
    QTest.mousePress(vp,Qt.MouseButton.LeftButton,pos=start)
    mouse_move(system,end)
    assert not calls and current(s) == before
    assert len(s.surface.presentation.hidden_ids) == 4
    preview = tuple(e for e,_ in s.interaction.overlay.entities.values())
    assert len(preview) == 4
    annotations = tuple(c for c,_ in s.surface.presentation.annotations.items.values())
    moved_label = next(c for c in annotations if c.id == dimensions[0].id).label_position
    assert moved_label != dimensions[0].label_position
    QTest.mouseRelease(vp,Qt.MouseButton.LeftButton,pos=end)
    assert not s.error,s.error
    assert len(calls) == 1 and current(s).entities == preview
    assert current(s).constraints == annotations and s.document.history.count == 1
    assert all(replace(c,label_position=None) == replace(old,label_position=None) for c,old in zip(annotations,before.constraints))
    s.document.history.undo()
    assert current(s) == before
    s.document.history.redo()
    assert current(s).entities == preview


@pytest.mark.parametrize("plane",["XY","XZ","YZ"])
def test_rectangle_relations_have_correct_symbols_clear_edge_attachment_and_visible_vertices(system,plane):
    clean_scene(system)
    s = draw_rectangle(system,plane)
    sketch = current(s)
    assert len(s.surface.presentation.vertices) == 4
    from OCC.Core.Aspect import Aspect_TOM_USERDEFINED
    for _,marker in s.surface.presentation.vertices.values():
        aspect = marker.Attributes().PointAspect()
        assert aspect.Aspect().Type() == Aspect_TOM_USERDEFINED
        assert tuple(aspect.GetTextureSize()) == (round(5*s.surface.pixel_ratio),)*2
    for constraint,ais in s.surface.presentation.annotations.items.values():
        if constraint.kind not in {"horizontal","vertical"}:
            continue
        e = sketch.entity(constraint.refs[0].entity_id)
        assert constraint.kind == ("horizontal" if abs(e.end.v-e.start.v) < abs(e.end.u-e.start.u) else "vertical")
        from OCC.Core.AIS import AIS_MultipleConnectedInteractive
        assert isinstance(ais,AIS_MultipleConnectedInteractive)
        from cadastrophe.geometry import Vec3
        x,y = s.surface.project(Vec3(*ais.LocalTransformation().TranslationPart().Coord()))
        mx,my = s.surface.project(sketch.to_world(system[2].Vec2((e.start.u+e.end.u)/2,(e.start.v+e.end.v)/2)))
        assert ((x-mx)**2+(y-my)**2)**.5 == pytest.approx(15,abs=.75)
    if plane == "XY":
        root = Path(__file__).resolve().parents[1]/"artifacts"
        assert system[0].viewport.view.Dump(str(root/"sketch-interaction-relations.png"))
    # Default coincidence symbols must not make the rectangle's corner unpickable.
    p = sketch.entities[0].start
    at = screen(system,(p.u,p.v))+QPoint(3,2)
    QTest.mousePress(system[0].viewport,Qt.MouseButton.LeftButton,pos=at)
    assert s.interaction.pending is not None and s.interaction.pending.get("handle") is not None
    assert s.view_tools.selected_constraint is None
    QTest.mouseRelease(system[0].viewport,Qt.MouseButton.LeftButton,pos=at)
    assert current(s) == sketch
