"""Real screen axes on located faces, compact native icons and point picking."""
from dataclasses import replace
from importlib import import_module
from math import pi
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cadastrophe.geometry import Vec3
from test_sketch_mode import system, enter
from test_sketch_drawing_ui import choose, screen
from test_sketch_editing_ui import mouse_move
from test_sketch_constraints_ui import draw_rectangle
from test_sketch_interaction_polish import clean_scene, current
from test_sketch_faces import body_document


@pytest.mark.parametrize("index",range(1,7))
@pytest.mark.parametrize("angle,legacy",[(0,False),(pi/3,False),(0,True)])
def test_face_rectangle_horizontal_vertical_match_screen_on_creation_reentry_and_ctrl8(system,index,angle,legacy):
    clean_scene(system)
    window,commands,model,editor = system
    supports = import_module(editor.__package__+".supports")
    from cadastrophe.document import SelectionItem
    _,body = body_document(angle)
    doc = window.viewport.document
    doc.add(body)
    face = supports.face_map(body.shape).FindKey(index)
    support = supports.from_selection(doc,(SelectionItem(body.id,"Face",face,body.revision),))
    if legacy:
        support = replace(support,frame=supports.face_frame(face,support.frame.name,natural=False))
    else:
        assert support.frame.v.tuple() == pytest.approx(window.viewport.camera.natural_up(support.frame).tuple(),abs=1e-12)
    obj = commands.create_sketch(doc,support)
    enter(system,obj)
    s,vp = window.tool_sessions.editor,window.viewport
    vp.view.Camera().SetScale(120)
    choose(system,"rectangle")
    start,end = screen(system,(-20,-10)),screen(system,(20,10))
    QTest.mousePress(vp,Qt.MouseButton.LeftButton,pos=start)
    mouse_move(system,end)
    QTest.mouseRelease(vp,Qt.MouseButton.LeftButton,pos=end)
    choose(system,"select")
    before = current(s)
    assert len(before.constraints) == 8 and len(before.entities) == 4
    for _ in range(2):
        assert vp.view.Camera().Up().Coord() == pytest.approx(support.frame.v.tuple())
        for c in before.constraints:
            if c.kind not in {"horizontal","vertical"}:
                continue
            e = before.entity(c.refs[0].entity_id)
            a,b = screen(system,(e.start.u,e.start.v)),screen(system,(e.end.u,e.end.v))
            dx,dy = abs(a.x()-b.x()),abs(a.y()-b.y())
            assert dx > 10 and dy <= 1 if c.kind == "horizontal" else dy > 10 and dx <= 1
        history = doc.history.count
        window.command_registry.execute("view.standard.isometric")
        window.command_registry.execute("view.normal_to_plane")
        assert current(s) == before and doc.history.count == history
    window.command_registry.execute("cadastrophe.sketch.finish")
    enter(system,doc.objects[obj.id])
    assert current(window.tool_sessions.editor) == before
    assert vp.view.Camera().Up().Coord() == pytest.approx(support.frame.v.tuple())


def marker_position(ais):
    return Vec3(*ais.LocalTransformation().TranslationPart().Coord())


@pytest.mark.parametrize("theme",["light","dark"])
def test_relation_icons_are_black_on_green_screen_sized_and_selectable_with_undo(system,theme):
    clean_scene(system)
    window = system[0]
    window.apply_preferences(replace(window.preferences,theme=theme))
    s = draw_rectangle(system)
    vp = system[0].viewport
    vp.view.Camera().SetScale(120)
    vp.invalidate_view()
    QApplication.processEvents()
    annotations = s.surface.presentation.annotations
    before = current(s)
    c,ais = next((c,a) for c,a in annotations.items.values() if c.kind == "horizontal")
    pos = marker_position(ais)
    x,y = s.surface.project(pos)
    root = Path(__file__).resolve().parents[1]/"artifacts"
    path = root/f"sketch-relations-2025-{theme}-{s.surface.pixel_ratio:g}.png"
    vp.view.Redraw()
    assert vp.view.Dump(str(path))
    img = QImage(str(path))
    ratio = s.surface.pixel_ratio
    # Inspect actual GPU output, not just the marker's configuration.
    patch = img.copy(round(x*ratio-8*ratio),round(y*ratio-8*ratio),round(16*ratio),round(16*ratio))
    patch.scaled(160,160).save(str(root/f"sketch-relation-detail-{theme}-{ratio:g}.png"))
    greens,blacks = [],[]
    for py in range(patch.height()):
        for px in range(patch.width()):
            color = patch.pixelColor(px,py)
            if color.green() > 100 and color.red() < 60 and color.blue() < 100:
                greens.append((px,py))
            elif max(color.red(),color.green(),color.blue()) < 80 and color.green() > color.blue()+3:
                blacks.append((px,py))
    assert len(greens) > 65*ratio**2 and len(blacks) > 5*ratio**2
    assert max(p[0] for p in blacks)-min(p[0] for p in blacks) > max(p[1] for p in blacks)-min(p[1] for p in blacks)
    assert max(p[0] for p in greens)-min(p[0] for p in greens)+1 == pytest.approx(12*ratio,abs=1)
    # Actual native owner -> relation id, including both bitmap layers.
    at = QPoint(round(x),round(y))
    QTest.mousePress(vp,Qt.MouseButton.LeftButton,pos=at)
    assert s.view_tools.selected_constraint == c.id and s.view_tools.drag is not None
    target = at+QPoint(24,-12)
    mouse_move(system,target)
    assert current(s) == before
    history = s.document.history.count
    QTest.mouseRelease(vp,Qt.MouseButton.LeftButton,pos=target)
    after = current(s)
    assert after.entities == before.entities and after.constraints != before.constraints
    assert s.document.history.count == history+1
    s.document.history.undo()
    assert current(s) == before
    s.document.history.redo()
    assert current(s) == after


def test_small_filled_point_has_large_hit_area_and_selected_square_without_halo(system):
    clean_scene(system)
    s = draw_rectangle(system)
    vp = system[0].viewport
    vp.view.Camera().SetScale(120)
    vp.invalidate_view()
    QApplication.processEvents()
    before = current(s)
    p = before.entities[0].start
    at = screen(system,(p.u,p.v))
    # Eight logical pixels from a five-pixel dot still starts the endpoint drag.
    QTest.mousePress(vp,Qt.MouseButton.LeftButton,pos=at+QPoint(-8,0))
    assert s.interaction.pending is not None and s.interaction.pending.get("handle") is not None
    QTest.mouseRelease(vp,Qt.MouseButton.LeftButton,pos=at+QPoint(-8,0))
    assert current(s) == before
    s.interaction.set_selected((before.entities[0].id,))
    aspect = next(marker for ref,(_,marker) in s.surface.presentation.vertices.items()
                  if ref[0] == before.entities[0].id).Attributes().PointAspect()
    assert tuple(aspect.GetTextureSize()) == (round(6*s.surface.pixel_ratio),)*2
