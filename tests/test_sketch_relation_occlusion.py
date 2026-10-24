"""Native glyphs must remain whole while orbiting a sketch on a solid face."""
from importlib import import_module
from math import cos,sin,radians
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.Graphic3d import Graphic3d_Camera
from OCC.Core.gp import gp_Pnt,gp_Dir
from cadastrophe.geometry import Vec3
from cadastrophe.plugin_api import SceneObject,SelectionItem
from test_sketch_mode import system,enter
from test_sketch_interaction_polish import clean_scene,current
from test_sketch_e7_polish_ui import green_pixels


def supported_line(system,label_v=30):
    clean_scene(system)
    window,commands,model,editor = system
    supports = import_module(editor.__package__+".supports")
    api = import_module(editor.__package__+".constraints")
    doc = window.viewport.document
    body = SceneObject("Support",BRepPrimAPI_MakeBox(gp_Pnt(-30,-30,-10),60,60,10).Shape(),(.65,.65,.65))
    doc.add(body)
    face = supports.face_map(body.shape).FindKey(6)
    support = supports.from_selection(doc,(SelectionItem(body.id,"Face",face,body.revision),))
    obj = commands.create_sketch(doc,support)
    enter(system,obj)
    session = window.tool_sessions.editor
    line = model.Segment(model.Vec2(15,30),model.Vec2(45,30))
    relation = api.Constraint("horizontal",(api.Reference(line.id),),label_position=model.Vec2(30,label_v))
    commands.add_entities(doc,obj.id,(line,),title="Ligne",constraints=(relation,))
    window.viewport.view.Camera().SetScale(120)
    return session,body,relation


def orbit(session,angle):
    vp = session.surface.widget
    frame = session.workplane
    center = frame.to_world(30,30)
    angle = radians(angle)
    eye = center+(frame.normal*cos(angle)+frame.v*sin(angle))*vp.view.Camera().Distance()
    up = frame.v*cos(angle)-frame.normal*sin(angle)
    vp.view.Camera().SetEyeAndCenter(gp_Pnt(*eye.tuple()),gp_Pnt(*center.tuple()))
    vp.view.Camera().SetUp(gp_Dir(*up.tuple()))
    vp.invalidate_view()
    QApplication.processEvents()
    vp.view.Redraw()


@pytest.mark.parametrize("perspective",[False,True])
@pytest.mark.parametrize("label_v",[1,30,59])
def test_native_relation_is_whole_at_oblique_angles_and_hidden_from_behind(system,perspective,label_v):
    s,_,relation = supported_line(system,label_v)
    vp = system[0].viewport
    # Isolate the bitmap from the topmost origin trihedron and sketch stroke.
    vp.context.Erase(vp.local_axes,False)
    for _,curve in s.surface.presentation.entities.values():
        vp.context.Erase(curve,False)
    if perspective:
        vp.view.Camera().SetProjectionType(Graphic3d_Camera.Projection_Perspective)
    snapshot,history = current(s),s.document.history.count
    counts = []
    root = Path(__file__).resolve().parents[1]/"artifacts"
    for angle in (0,45,70,85,89,90,91,100,135,180,0):
        orbit(s,angle)
        ais = s.surface.presentation.annotations.items[relation.id][1]
        x,y = s.surface.project(Vec3(*ais.LocalTransformation().TranslationPart().Coord()))
        path = root/f"relation-orbit-{label_v}-{perspective}-{angle}-{s.surface.pixel_ratio:g}.png"
        assert vp.view.Dump(str(path))
        count = green_pixels(path,x,y,s.surface.pixel_ratio)
        counts.append(count)
        if angle < 90:
            assert count == pytest.approx(counts[0],abs=4*s.surface.pixel_ratio**2),(angle,counts)
            assert count > 110*s.surface.pixel_ratio**2
        elif angle == 90:
            assert count == 0 or count == pytest.approx(counts[0],abs=4*s.surface.pixel_ratio**2)
        else:
            assert count == 0,(angle,counts)
    assert current(s) == snapshot and s.document.history.count == history


def test_occluded_relation_is_not_pickable_and_body_visibility_invalidates_cache(system):
    s,_,relation = supported_line(system)
    vp = system[0].viewport
    orbit(s,0)
    center = s.workplane.to_world(30,30)
    obstacle = SceneObject("Obstacle",BRepPrimAPI_MakeBox(gp_Pnt(-5,-5,5),10,10,5).Shape(),(.6,.6,.6))
    s.document.add(obstacle)
    orbit(s,0)
    history = s.document.history.count
    annotations = s.surface.presentation.annotations
    ais = annotations.items[relation.id][1]
    assert not s.surface.context.IsDisplayed(ais)
    x,y = s.surface.project(center)
    assert annotations.key(s.surface.detect(x,y)) is None
    QTest.mouseClick(vp,Qt.MouseButton.LeftButton,pos=QPoint(round(x),round(y)))
    assert s.view_tools.selected_constraint is None
    assert s.document.history.count == history
    s.document.set_visible(obstacle.id,False)
    assert s.surface.context.IsDisplayed(annotations.items[relation.id][1])
    s.document.history.undo()
    assert not s.surface.context.IsDisplayed(annotations.items[relation.id][1])
    s.document.set_transparencies({obstacle.id:1.})
    assert s.surface.context.IsDisplayed(annotations.items[relation.id][1])
    s.document.set_transparencies({obstacle.id:0.})
    assert not s.surface.context.IsDisplayed(annotations.items[relation.id][1])
    s.document.replace_shape(obstacle.id,BRepPrimAPI_MakeBox(gp_Pnt(20,20,5),2,2,5).Shape())
    assert s.surface.context.IsDisplayed(annotations.items[relation.id][1])


def test_visibility_batches_reuse_geometry_during_orbit_without_changing_selection(system,monkeypatch):
    import cadastrophe.selection as native
    s,_,_ = supported_line(system)
    actual = native.IntCurvesFace_ShapeIntersector
    loads = []
    def tracked():
        loads.append(True)
        return actual()
    monkeypatch.setattr(native,"IntCurvesFace_ShapeIntersector",tracked)
    obstacle = SceneObject("Obstacle",BRepPrimAPI_MakeBox(gp_Pnt(20,20,5),2,2,5).Shape(),(.6,.6,.6))
    s.document.add(obstacle)
    orbit(s,0)
    assert len(loads) == 2
    before = current(s),s.document.history.count,s.document.selected_ids
    for angle in (20,40,70,100,140,180,0):
        orbit(s,angle)
    assert len(loads) == 2
    assert (current(s),s.document.history.count,s.document.selected_ids) == before
