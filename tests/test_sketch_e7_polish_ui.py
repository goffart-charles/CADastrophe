"""Six a_faire corrections, using real Qt/OCCT and native GPU exports."""
from importlib import import_module
from pathlib import Path
from dataclasses import replace
import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from OCC.Core.gp import gp_Pnt, gp_Dir
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from cadastrophe.plugin_api import SceneObject,SelectionItem
from cadastrophe.geometry import Vec3
from test_sketch_mode import system,enter
from test_sketch_drawing_ui import begin,choose,screen,move
from test_sketch_editing_ui import mouse_move
from test_sketch_interaction_polish import clean_scene,current
from test_sketch_constraints_ui import draw_rectangle


def green_pixels(path,x,y,ratio):
    img = QImage(str(path))
    patch = img.copy(round((x-7)*ratio),round((y-7)*ratio),round(14*ratio),round(14*ratio))
    return sum(1 for y in range(patch.height()) for x in range(patch.width())
               if (c := patch.pixelColor(x,y)).green() > 100 and c.red() < 50 and c.blue() < 90)


def test_relation_icons_are_occluded_by_their_solid_from_behind(system):
    clean_scene(system)
    window,commands,model,editor = system
    supports = import_module(editor.__package__+".supports")
    relations = import_module(editor.__package__+".creation_relations")
    doc,vp = window.viewport.document,window.viewport
    body = SceneObject("Support",BRepPrimAPI_MakeBox(gp_Pnt(-30,-30,-10),60,60,10).Shape(),(.65,.65,.65))
    doc.add(body)
    face = supports.face_map(body.shape).FindKey(6)
    support = supports.from_selection(doc,(SelectionItem(body.id,"Face",face,body.revision),))
    obj = commands.create_sketch(doc,support)
    enter(system,obj)
    s = window.tool_sessions.editor
    p = tuple(model.Vec2(u,v) for u,v in ((15,15),(45,15),(45,45),(15,45)))
    edges = tuple(model.Segment(p[i],p[(i+1)%4]) for i in range(4))
    commands.add_entities(doc,obj.id,edges,title="Rectangle",constraints=relations.creation_relations(obj.sketch,edges,rectangle=True))
    vp.view.Camera().SetScale(120)
    root = Path(__file__).resolve().parents[1]/"artifacts"
    counts = []
    for back in (False,True):
        frame = support.frame
        eye = frame.origin+frame.normal*(vp.view.Camera().Distance()*(-1 if back else 1))
        vp.view.Camera().SetEyeAndCenter(gp_Pnt(*eye.tuple()),gp_Pnt(*frame.origin.tuple()))
        vp.view.Camera().SetUp(gp_Dir(*frame.v.tuple()))
        vp.invalidate_view()
        QApplication.processEvents()
        vp.view.Redraw()
        path = root/f"sketch-relations-occlusion-{back}-{s.surface.pixel_ratio:g}.png"
        assert vp.view.Dump(str(path))
        values = []
        for c,ais in s.surface.presentation.annotations.items.values():
            if c.kind not in {"horizontal","vertical"}:
                continue
            x,y = s.surface.project(Vec3(*ais.LocalTransformation().TranslationPart().Coord()))
            values.append(green_pixels(path,x,y,s.surface.pixel_ratio))
        counts.append(values)
    assert min(counts[0]) > 65*s.surface.pixel_ratio**2
    assert max(counts[1]) < 15*s.surface.pixel_ratio**2


def test_relation_icons_disappear_on_exit_and_return_on_reentry_without_history(system):
    clean_scene(system)
    s = draw_rectangle(system)
    doc = s.document
    key = s.object_id
    sketch = current(s)
    owned = [a for _,a in s.surface.presentation.annotations.items.values()]
    assert len(owned) == 8
    history = doc.history.count
    choose(system,"finish")
    presenter = system[0].viewport.document_adapter.features[key][1]
    assert not presenter.annotations.items
    assert all(not system[0].viewport.context.IsDisplayed(a) for a in owned)
    assert doc.history.count == history and doc.objects[key].sketch == sketch
    enter(system,doc.objects[key])
    assert len(system[0].tool_sessions.editor.surface.presentation.annotations.items) == 8
    assert doc.history.count == history


def test_entity_drag_never_snaps_while_drawing_still_snaps(system,monkeypatch):
    clean_scene(system)
    s = begin(system)
    _,commands,model,_ = system
    line = model.Segment(model.Vec2(-20,-20),model.Vec2(20,-20))
    commands.add_entity(s.document,s.object_id,line)
    vp = system[0].viewport
    start = screen(system,(0,-20))
    end = start+QPoint(40,-20)
    target = s._project(end.x(),end.y())
    commands.add_entity(s.document,s.object_id,model.Point(model.Vec2(target.u,target.v+.2)))
    calls = []
    actual = s.snapper.resolve
    def tracked(*args,**kwargs):
        calls.append(True)
        return actual(*args,**kwargs)
    monkeypatch.setattr(s.snapper,"resolve",tracked)
    QTest.mousePress(vp,Qt.MouseButton.LeftButton,pos=start)
    mouse_move(system,end)
    assert not calls and s.snap_result is None and s.snap_renderer.marker is None
    QTest.mouseRelease(vp,Qt.MouseButton.LeftButton,pos=end)
    assert not calls
    choose(system,"line")
    move(system,(target.u,target.v+.2))
    assert calls and s.snap_result is not None


def test_contextual_propertymanager_and_dismissable_viewport_error(system):
    clean_scene(system)
    s = draw_rectangle(system)
    assert s.context_title.text() == "Esquisse"
    assert not s.navigation_note.isVisible() and not s.entity_panel.list.isVisible()
    assert not s.constraint_panel.group.isVisible()
    s.interaction.set_selected((current(s).entities[0].id,))
    assert s.context_title.text() == "Propriétés de ligne" and s.constraint_panel.group.isVisible()
    s.report_error("Modification impossible : la position est fixée.")
    assert s.notice.isVisible() and s.notice.label.text() == s.error
    assert s.error not in s.values.text()
    assert s.notice.pos() == QPoint(16,52)
    move(system,(0,30))
    assert s.notice.isVisible() and s.notice.label.text() == s.error
    assert system[0].tool_host.panel.layout().indexOf(system[0].tool_host.panel.buttons) == 1
    QTest.mouseClick(s.notice.dismiss,Qt.MouseButton.LeftButton)
    s._feedback()
    assert not s.notice.isVisible()
    s.report_error("")
    s.report_error("Autre conflit")
    assert s.notice.isVisible()
    choose(system,"finish")
    QApplication.processEvents()
    assert s.notice is None


@pytest.mark.parametrize("theme",["light","dark"])
def test_fully_defined_entity_and_fixed_vertex_have_native_status_colors(system,theme):
    from test_sketch_polish_ui import rgb
    clean_scene(system)
    window,commands,model,editor = system
    window.apply_preferences(replace(window.preferences,theme=theme))
    s = begin(system)
    api = import_module(editor.__package__+".constraints")
    renderer = import_module(editor.__package__+".presentation")
    line = model.Segment(model.Vec2(-20,0),model.Vec2(20,0))
    free = model.Segment(model.Vec2(-20,20),model.Vec2(20,20))
    commands.add_entities(s.document,s.object_id,(line,free),title="Lignes")
    cs = (api.Constraint("fixed",(api.Reference(line.id,"start"),),line.start),
          api.Constraint("horizontal",(api.Reference(line.id),)),api.Constraint("length",(api.Reference(line.id),),40))
    commands.set_constraints(s.document,s.object_id,cs)
    base = s.surface.presentation
    assert rgb(base.entities[line.id][1]) == pytest.approx(renderer.DEFINED[theme],abs=1e-6)
    assert rgb(base.entities[free.id][1]) == pytest.approx(renderer.COLORS[theme]["editing"],abs=1e-6)
    s.interaction.set_selected((line.id,))
    assert rgb(base.entities[line.id][1]) == pytest.approx(renderer.COLORS[theme]["selected"],abs=1e-6)
