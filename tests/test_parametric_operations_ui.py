"""Actual tool panels, split menus, preview isolation and native project reload."""
from dataclasses import replace
from math import pi
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication,QToolButton,QStyle,QStyleOptionToolButton
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt,QTimer,QPoint

from cadastrophe.core.reference_objects import DatumPlaneObject
from cadastrophe.core.operation_model import GeometryReference as Ref
from cadastrophe.core.feature_objects import BodyObject,RevolutionFeature,ReplayFeature
from cadastrophe.core.feature_model import Operation,ResultStatus
from cadastrophe.core.body_history import body_result
from test_extrusion_tool import extrusion_system,start
from test_boss_tool import base_body,face_sketch
from test_feature_edit import edit
from test_cut_tool import start_cut
from test_primitives import volume
from test_parametric_operations import bounds
from test_workshop_identity import capture
from test_advanced_cad_tools import collect_native_tool_sessions


def capture_tool(w,name):
    QApplication.processEvents()
    Path("artifacts").mkdir(exist_ok=True)
    capture(w,Path("artifacts")/(name+".png"))


def choose(w,key):
    assert w.command_registry.execute(key)
    assert w.tool_sessions.tool is not None, w.hint.text()
    return w.tool_sessions.tool


def test_plane_panel_sketch_extrusion_edit_rename_and_roundtrip(extrusion_system,tmp_path):
    w,commands,m = extrusion_system
    doc = w.viewport.document
    xy = next(o for o in doc.objects.values() if o.plane is not None and o.plane.name == "XY")
    w.tree_selection_changed([xy.id])
    tool = choose(w,"modeling.reference_geometry")
    count,ids = doc.history.count,set(doc.objects)
    tool.distance.set_internal_value(12.)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit,w.tool_sessions.message
    assert set(doc.objects) == ids and doc.history.count == count
    assert w.tool_sessions.preview_object.plane.origin.z == pytest.approx(12)
    capture_tool(w,"parametric-plane")
    assert w.tool_sessions.commit(),w.tool_sessions.message
    datum, = (o for o in doc.objects.values() if isinstance(o,DatumPlaneObject))
    assert datum.id in w.tree_items and datum.id in w.viewport.presentations
    w.tree_selection_changed([datum.id])
    assert w.command_registry.execute("cadastrophe.sketch.new")
    session = w.tool_sessions.editor
    pts = tuple(m.Vec2(*p) for p in ((0,0),(10,0),(10,10),(0,10)))
    commands.add_entities(doc,session.object_id,tuple(m.Segment(p,pts[(i+1)%4]) for i,p in enumerate(pts)),title="Rectangle")
    key = session.object_id
    assert w.command_registry.execute("cadastrophe.sketch.finish")
    extrusion = start(w,doc.objects[key])
    extrusion.depth.set_internal_value(5)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    body, = (o for o in doc.objects.values() if isinstance(o,BodyObject))
    original_ids = set(doc.objects)
    w.tree_selection_changed([datum.id])
    tool = choose(w,"cadastrophe.modeling.edit_plane")
    tool.distance.set_internal_value(35)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit,w.tool_sessions.message
    assert bounds(doc.objects[body.id].shape)[2] == pytest.approx(12)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    assert set(doc.objects) == original_ids
    assert bounds(doc.objects[body.id].shape)[2] == pytest.approx(35)
    assert volume(doc.objects[body.id].shape) == pytest.approx(500)
    doc.rename(datum.id,"Support décalé")
    doc.set_visible(datum.id,False)
    path = tmp_path/"datum-history.cada"
    assert w.save_document(path=path)
    assert w.close_document()
    assert w.open_document(path) is not None
    QTest.qWait(100)
    loaded = w.viewport.document
    assert set(loaded.objects) == original_ids
    assert loaded.objects[datum.id].name == "Support décalé" and not loaded.objects[datum.id].visible
    assert bounds(loaded.objects[body.id].shape)[2] == pytest.approx(35)


@pytest.mark.parametrize("method",["inclined","three_points"])
def test_other_plane_methods_have_live_preview_without_creating_material(extrusion_system,method):
    w,_,_ = extrusion_system
    doc = w.viewport.document
    xy = next(o for o in doc.objects.values() if o.plane is not None and o.plane.name == "XY")
    w.tree_selection_changed([xy.id])
    tool = choose(w,"cadastrophe.modeling.reference_plane")
    tool.method.setCurrentIndex(tool.method.findData(method))
    if method == "inclined":
        tool.hinge.set_reference(Ref("axis",values=(0,0,0,1,0,0)))
        tool.angle.setValue(90)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit,w.tool_sessions.message
    assert w.tool_sessions.commit(),w.tool_sessions.message
    datum, = (o for o in doc.objects.values() if isinstance(o,DatumPlaneObject))
    assert datum.plane.normal.tuple() == pytest.approx((0,-1,0) if method == "inclined" else (0,0,1))
    assert not any(isinstance(o,BodyObject) for o in doc.objects.values())


def test_revolution_panel_construction_axis_preview_edit_and_roundtrip(extrusion_system,tmp_path):
    w,commands,m = extrusion_system
    doc = w.viewport.document
    xy = next(o for o in doc.objects.values() if o.plane is not None and o.plane.name == "XY")
    source = commands.create_sketch(doc,xy.id)
    points = tuple(m.Vec2(*p) for p in ((5,-5),(10,-5),(10,5),(5,5)))
    axis = m.Segment(m.Vec2(0,-20),m.Vec2(0,20),construction=True)
    commands.add_entities(doc,source.id,(*tuple(m.Segment(p,points[(i+1)%4]) for i,p in enumerate(points)),axis),title="Profil")
    w.tree_selection_changed([source.id])
    tool = choose(w,"modeling.revolve")
    tool.axis.set_reference(Ref("sketch_line",source.id,entity_id=axis.id))
    tool.angle.setValue(90)
    before = set(doc.objects),doc.history.count
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit,w.tool_sessions.message
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(750*pi/4)
    capture_tool(w,"parametric-revolution")
    assert (set(doc.objects),doc.history.count) == before
    assert w.tool_sessions.commit(),w.tool_sessions.message
    feature, = (o for o in doc.objects.values() if isinstance(o,RevolutionFeature))
    body = doc.objects[feature.body_id]
    ids = set(doc.objects)
    tool = edit(w,feature.id)
    tool.angle.setValue(180)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    assert set(doc.objects) == ids and volume(doc.objects[body.id].shape) == pytest.approx(750*pi/2)
    doc.history.undo()
    assert volume(doc.objects[body.id].shape) == pytest.approx(750*pi/4)
    doc.history.redo()
    assert volume(doc.objects[body.id].shape) == pytest.approx(750*pi/2)
    path = tmp_path/"revolution.cada"
    assert w.save_document(path=path)
    assert w.close_document() and w.open_document(path) is not None
    QTest.qWait(100)
    assert set(w.viewport.document.objects) == ids
    assert volume(w.viewport.document.objects[body.id].shape) == pytest.approx(750*pi/2)


@pytest.mark.parametrize("operation",[Operation.LINEAR_PATTERN,Operation.CIRCULAR_PATTERN,Operation.MIRROR])
@pytest.mark.parametrize("cut",[False,True])
def test_transformed_operation_panels_rebuild_save_and_reopen(extrusion_system,tmp_path,operation,cut):
    w,commands,m = extrusion_system
    body = base_body(extrusion_system)
    doc = w.viewport.document
    source = face_sketch(extrusion_system,body,center=(30,30))
    tool = start_cut(w,source) if cut else start(w,source)
    tool.depth.set_internal_value(5)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    body = doc.objects[body.id]
    source_feature = body.features[-1]
    sign = -1 if cut else 1
    w.tree_selection_changed([source_feature])
    tool = choose(w,"cadastrophe.modeling."+operation.value)
    if operation is Operation.MIRROR:
        # Reflect x=20 onto x=80, with a custom mid-plane.
        from cadastrophe.core.operation_model import PlaneParameters
        from cadastrophe.core.reference_geometry import resolve_plane,plane_shape
        # Plane creation is tested separately; a reference is created before the
        # replay session so its snapshot remains immutable.
        w.tool_sessions.cancel()
        yz = next(o for o in doc.objects.values() if o.plane is not None and o.plane.name == "YZ")
        w.tree_selection_changed([yz.id])
        plane_tool = choose(w,"cadastrophe.modeling.reference_plane")
        plane_tool.distance.set_internal_value(50)
        assert w.tool_sessions.commit(),w.tool_sessions.message
        mirror = next(o for o in doc.objects.values() if isinstance(o,DatumPlaneObject))
        w.tree_selection_changed([source_feature])
        tool = choose(w,"cadastrophe.modeling.mirror")
        tool.reference.set_reference(Ref("plane",mirror.id))
        count = 2
    elif operation is Operation.LINEAR_PATTERN:
        tool.reference.set_reference(Ref("axis",values=(0,0,0,1,0,0)))
        tool.count.setValue(4)
        tool.spacing.set_internal_value(20)
        count = 4
    else:
        tool.reference.set_reference(Ref("axis",values=(50,30,0,0,0,1)))
        tool.count.setValue(4)
        count = 4
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit,w.tool_sessions.message
    expected = 120000+sign*count*25*pi*5
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(expected)
    if cut and operation is Operation.CIRCULAR_PATTERN:
        capture_tool(w,"parametric-circular-cut")
    assert w.tool_sessions.commit(),w.tool_sessions.message
    feature = doc.objects[doc.objects[body.id].features[-1]]
    assert isinstance(feature,ReplayFeature) and feature.parameters.sources == (source_feature,)
    circle, = doc.objects[source.id].sketch.entities
    commands.replace_entities(doc,source.id,(replace(circle,radius=3),))
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert volume(doc.objects[body.id].shape) == pytest.approx(120000+sign*count*9*pi*5)
    tool = edit(w,feature.id)
    if operation is not Operation.MIRROR:
        tool.count.setValue(3)
        count = 3
    assert w.tool_sessions.commit(),w.tool_sessions.message
    ids = set(doc.objects)
    path = tmp_path/(operation.value+".cada")
    assert w.save_document(path=path)
    assert w.close_document() and w.open_document(path) is not None
    QTest.qWait(100)
    loaded = w.viewport.document
    assert set(loaded.objects) == ids
    assert volume(loaded.objects[body.id].shape) == pytest.approx(120000+sign*count*9*pi*5)
    assert body_result(loaded,body.id).status is ResultStatus.READY
    assert loaded.objects[feature.id].parameters.merge_result is True


@pytest.mark.parametrize("cut",[False,True])
def test_revolution_adds_to_or_subtracts_from_current_body(extrusion_system,cut):
    w,commands,m = extrusion_system
    body = base_body(extrusion_system)
    doc = w.viewport.document
    xz = next(o for o in doc.objects.values() if o.plane is not None and o.plane.name == "XZ")
    w.tree_selection_changed([xz.id])
    tool = choose(w,"cadastrophe.modeling.reference_plane")
    tool.distance.set_internal_value(-30)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    support = next(o for o in doc.objects.values() if isinstance(o,DatumPlaneObject))
    source = commands.create_sketch(doc,support.id)
    z = 0 if cut else 20
    points = tuple(m.Vec2(*p) for p in ((55,z),(60,z),(60,z+10),(55,z+10)))
    axis = m.Segment(m.Vec2(50,-5),m.Vec2(50,35),construction=True)
    commands.add_entities(doc,source.id,(*tuple(m.Segment(p,points[(i+1)%4]) for i,p in enumerate(points)),axis),title="Profil")
    w.tree_selection_changed([source.id])
    tool = choose(w,"modeling.cut_revolve" if cut else "modeling.revolve")
    tool.axis.set_reference(Ref("sketch_line",source.id,entity_id=axis.id))
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit,w.tool_sessions.message
    assert w.tool_sessions.commit(),w.tool_sessions.message
    assert volume(doc.objects[body.id].shape) == pytest.approx(120000+(-1 if cut else 1)*750*pi)
    assert len([o for o in doc.objects.values() if isinstance(o,BodyObject)]) == 1


def test_revolution_buttons_are_stacked_and_pattern_keeps_its_split_menu(extrusion_system):
    w,_,_ = extrusion_system
    buttons = []
    for key in ("revolve", "cut_revolve"):
        action = w.command_registry.action("modeling."+key)
        button = next(b for b in w.command_manager.findChildren(QToolButton) if b.defaultAction() is action)
        assert button.menu() is None
        buttons.append(button)
    assert buttons[0].parentWidget() is buttons[1].parentWidget()
    assert buttons[0].y() < buttons[1].y()
    for key,labels in (("linear_pattern",["Répétition linéaire","Répétition circulaire"]),):
        action = w.command_registry.action("modeling."+key)
        button = next(b for b in w.command_manager.findChildren(QToolButton) if b.defaultAction() is action)
        w.command_manager.setCurrentIndex(0)
        QApplication.processEvents()
        assert [a.text() for a in button.menu().actions()] == labels
        option = QStyleOptionToolButton()
        button.initStyleOption(option)
        arrow = button.style().subControlRect(QStyle.ComplexControl.CC_ToolButton,option,QStyle.SubControl.SC_ToolButtonMenu,button)
        assert arrow.width() >= 14 and button.width()-arrow.width() >= 40
        observed = []
        def select():
            observed.append(button.menu().isVisible())
            QTest.mouseClick(button.menu(),Qt.MouseButton.LeftButton,pos=button.menu().actionGeometry(button.menu().actions()[1]).center())
        QTimer.singleShot(50,button.menu(),select)
        QTimer.singleShot(1000,button.menu(),button.menu().close)
        QTest.mouseClick(button,Qt.MouseButton.LeftButton,pos=arrow.center())
        assert observed == [True] and w.tool_sessions.tool is not None
        assert w.tool_sessions.cancel()


def screen(w,world):
    x,y = w.viewport.view.Convert(*world)
    ratio = w.viewport.devicePixelRatioF()
    return QPoint(round(x/ratio),round(y/ratio))


def top_view(w):
    w.viewport.view.SetProj(0.,0.,1.)
    w.viewport.view.FitAll()
    w.viewport.view.ZFitAll()
    QApplication.processEvents()


def test_axis_can_be_picked_on_a_construction_line_in_the_native_view(extrusion_system):
    w,commands,m = extrusion_system
    doc = w.viewport.document
    xy = next(o for o in doc.objects.values() if o.plane is not None and o.plane.name == "XY")
    source = commands.create_sketch(doc,xy.id)
    p = tuple(m.Vec2(*v) for v in ((5,-5),(10,-5),(10,5),(5,5)))
    axis = m.Segment(m.Vec2(0,-20),m.Vec2(0,20),construction=True)
    commands.add_entities(doc,source.id,(*tuple(m.Segment(v,p[(i+1)%4]) for i,v in enumerate(p)),axis),title="Profil")
    top_view(w)
    w.tree_selection_changed([source.id])
    tool = choose(w,"cadastrophe.modeling.revolve")
    assert tool.active_reference is tool.axis
    assert tool.axis.field.isVisible() and not tool.axis.combo.isVisible()
    # Switching from profile picking back to axis picking uses the collector.
    tool.activate("sketch")
    assert tool.active_reference is None
    QTest.mouseClick(tool.axis.field.edit, Qt.MouseButton.LeftButton)
    assert tool.active_reference is tool.axis
    QTest.mouseClick(w.viewport,Qt.MouseButton.LeftButton,pos=screen(w,(0.,10.,0.)))
    assert tool.axis.reference() == Ref("sketch_line",source.id,entity_id=axis.id)
    assert tool.axis.field.value == tool.axis.reference()
    assert not tool.axis.pick.isChecked()
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit,w.tool_sessions.message
    assert w.tool_sessions.commit(),w.tool_sessions.message


def test_separate_axis_sketch_is_nested_under_its_revolution_and_survives_reload(extrusion_system,tmp_path):
    w, commands, m = extrusion_system
    doc = w.viewport.document
    xy = next(o for o in doc.objects.values() if o.plane is not None and o.plane.name == "XY")
    axis_sketch = commands.create_sketch(doc, xy.id)
    axis = m.Segment(m.Vec2(0,-20), m.Vec2(0,20), construction=True)
    commands.add_entity(doc, axis_sketch.id, axis)
    source = commands.create_sketch(doc, xy.id)
    points = tuple(m.Vec2(*p) for p in ((5,-5),(10,-5),(10,5),(5,5)))
    commands.add_entities(doc, source.id, tuple(m.Segment(p,points[(i+1)%4]) for i,p in enumerate(points)), title="Profil")
    w.tree_selection_changed([source.id])
    tool = choose(w, "cadastrophe.modeling.revolve")
    tool.axis.set_reference(Ref("sketch_line", axis_sketch.id, entity_id=axis.id))
    assert w.tool_sessions.commit(), w.tool_sessions.message
    body, = (o for o in doc.objects.values() if isinstance(o, BodyObject))
    feature = body.features[0]
    for key in (source.id, axis_sketch.id):
        assert w.tree_items[key].parent() is w.tree_items[feature]
    assert w.tree_items[feature].parent() is w.tree_items[body.id]
    assert all(w.tree.topLevelItem(0).child(i).text(0) != "Fonctions de plugins"
               for i in range(w.tree.topLevelItem(0).childCount()))
    path = tmp_path/"separate-axis.cada"
    assert w.save_document(path=path)
    assert w.open_document(path)
    assert w.tree_items[axis_sketch.id].parent() is w.tree_items[feature]


def test_multisolid_linear_then_circular_panel_edit_and_roundtrip(extrusion_system,tmp_path):
    from cadastrophe.core.operation_effects import checked_solids
    w, _, _ = extrusion_system
    base = base_body(extrusion_system)
    doc = w.viewport.document
    source = doc.objects[base.id].features[0]
    w.tree_selection_changed([source])
    tool = choose(w, "cadastrophe.modeling.linear_pattern")
    tool.reference.set_reference(Ref("axis", values=(0,0,0,1,0,0)))
    tool.spacing.set_internal_value(150)
    tool.count.setValue(2)
    w.tool_sessions.update_preview()
    assert not w.tool_sessions.can_commit and tool.merge.isVisible()
    assert "Fusionner le résultat" in w.tool_sessions.message
    assert len(checked_solids(doc.objects[base.id].shape)) == 1
    tool.merge.setChecked(False)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    linear = doc.objects[base.id].features[-1]
    assert len(checked_solids(doc.objects[base.id].shape)) == 2
    w.tree_selection_changed([linear])
    tool = choose(w, "cadastrophe.modeling.circular_pattern")
    assert tool.source_ids == (linear,)
    tool.reference.set_reference(Ref("axis", values=(-200,-200,0,0,0,1)))
    tool.count.setValue(3)
    w.tool_sessions.update_preview()
    assert not w.tool_sessions.can_commit and "Fusionner le résultat" in w.tool_sessions.message
    tool.merge.setChecked(False)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    circular = doc.objects[base.id].features[-1]
    assert len(checked_solids(doc.objects[base.id].shape)) == 6
    assert volume(doc.objects[base.id].shape) == pytest.approx(720000)
    order = doc.objects[base.id].features
    # Source-motif edits rebuild every occurrence of the circular pattern.
    w.tree_selection_changed([linear])
    tool = choose(w, "cadastrophe.modeling.edit_feature")
    assert not tool.merge.isChecked()
    tool.spacing.set_internal_value(180)
    tool.count.setValue(3)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert len(checked_solids(doc.objects[base.id].shape)) == 9
    assert volume(doc.objects[base.id].shape) == pytest.approx(1080000)
    assert doc.objects[base.id].features == order
    doc.history.undo()
    assert len(checked_solids(doc.objects[base.id].shape)) == 6
    doc.history.redo()
    assert len(checked_solids(doc.objects[base.id].shape)) == 9
    path = tmp_path/"nested-multisolid-pattern.cada"
    assert w.save_document(path=path)
    assert w.open_document(path)
    restored = w.viewport.document
    assert restored.objects[base.id].features == order
    assert restored.objects[circular].parameters.sources == (linear,)
    assert not restored.objects[linear].parameters.merge_result
    assert len(checked_solids(restored.objects[base.id].shape)) == 9
    assert volume(restored.objects[base.id].shape) == pytest.approx(1080000)


def test_three_point_plane_uses_persistent_vertices_picked_in_the_native_view(extrusion_system):
    w,_,_ = extrusion_system
    body = base_body(extrusion_system)
    top_view(w)
    tool = choose(w,"cadastrophe.modeling.reference_plane")
    tool.method.setCurrentIndex(tool.method.findData("three_points"))
    for field,point in zip(tool.points,((0.,0.,20.),(100.,0.,20.),(0.,60.,20.))):
        field.pick.click()
        w.tool_sessions.update_preview()
        QTest.mouseClick(w.viewport,Qt.MouseButton.LeftButton,pos=screen(w,point))
        assert field.reference().kind == "vertex"
        assert field.reference().producer_id == body.features[-1]
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit,w.tool_sessions.message
    assert w.tool_sessions.preview_object.plane.origin.z == pytest.approx(20)
    assert w.tool_sessions.preview_object.plane.normal.tuple() == pytest.approx((0,0,1))
    assert w.tool_sessions.commit(),w.tool_sessions.message


def test_invalid_datum_hides_cached_plane_saves_and_can_be_repaired(extrusion_system,tmp_path):
    from cadastrophe.core.operation_model import PlaneParameters
    w,commands,m = extrusion_system
    doc = w.viewport.document
    tool = choose(w,"cadastrophe.modeling.reference_plane")
    tool.method.setCurrentIndex(tool.method.findData("three_points"))
    assert w.tool_sessions.commit(),w.tool_sessions.message
    datum, = (o for o in doc.objects.values() if isinstance(o,DatumPlaneObject))
    source = commands.create_sketch(doc,datum.id)
    p = tuple(m.Vec2(*v) for v in ((0,0),(10,0),(10,10),(0,10)))
    commands.add_entities(doc,source.id,tuple(m.Segment(v,p[(i+1)%4]) for i,v in enumerate(p)),title="Rectangle")
    start(w,doc.objects[source.id]).depth.set_internal_value(10)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    body, = (o for o in doc.objects.values() if isinstance(o,BodyObject))
    invalid = replace(datum,parameters=replace(datum.parameters,references=(*datum.parameters.references[:2],Ref("point",values=(20,0,0)))))
    doc.replace_feature(invalid)
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert body_result(doc,body.id).status is ResultStatus.ERROR and doc.objects[body.id].shape is None
    assert doc.objects[datum.id].reference_error_message
    assert not w.viewport.context.IsDisplayed(w.viewport.presentations[datum.id])
    ids = set(doc.objects)
    path = tmp_path/"invalid-datum.cada"
    assert w.save_document(path=path)
    assert w.close_document() and w.open_document(path) is not None
    QTest.qWait(100)
    doc = w.viewport.document
    assert set(doc.objects) == ids and doc.objects[body.id].shape is None
    w.tree_selection_changed([datum.id])
    tool = choose(w,"cadastrophe.modeling.edit_plane")
    tool.points[2].values[1].set_internal_value(10)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    assert not doc.objects[datum.id].reference_error_message
    assert body_result(doc,body.id).status is ResultStatus.READY
    assert volume(doc.objects[body.id].shape) == pytest.approx(1000)


def test_circular_holes_rebuild_before_a_later_face_based_boss(extrusion_system,tmp_path):
    w,commands,_ = extrusion_system
    body = base_body(extrusion_system)
    doc = w.viewport.document
    hole = face_sketch(extrusion_system,body,center=(30,30))
    start_cut(w,hole).depth.set_internal_value(5)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    source = doc.objects[body.id].features[-1]
    w.tree_selection_changed([source])
    tool = choose(w,"cadastrophe.modeling.circular_pattern")
    tool.count.setValue(4)
    tool.reference.set_reference(Ref("axis",values=(50,30,0,0,0,1)))
    assert w.tool_sessions.commit(),w.tool_sessions.message
    pattern = doc.objects[body.id].features[-1]
    after = face_sketch(extrusion_system,doc.objects[body.id],center=(80,10))
    circle, = doc.objects[after.id].sketch.entities
    commands.replace_entities(doc,after.id,(replace(circle,radius=2),))
    start(w,doc.objects[after.id]).depth.set_internal_value(5)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    order,ids = doc.objects[body.id].features,set(doc.objects)
    assert order[-2] == pattern
    circle, = doc.objects[hole.id].sketch.entities
    commands.replace_entities(doc,hole.id,(replace(circle,radius=3),))
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert volume(doc.objects[body.id].shape) == pytest.approx(120000-4*45*pi+20*pi)
    tool = edit(w,order[0])
    tool.depth.set_internal_value(40)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    assert doc.objects[body.id].features == order and set(doc.objects) == ids
    assert volume(doc.objects[body.id].shape) == pytest.approx(240000-4*45*pi+20*pi)
    assert bounds(doc.objects[body.id].shape)[5] == pytest.approx(45)
    path = tmp_path/"circular-holes-following-boss.cada"
    assert w.save_document(path=path)
    assert w.close_document() and w.open_document(path) is not None
    QTest.qWait(100)
    assert w.viewport.document.objects[body.id].features == order
    assert volume(w.viewport.document.objects[body.id].shape) == pytest.approx(240000-4*45*pi+20*pi)


def test_edit_plane_can_reselect_a_face_proven_on_its_earlier_producer(extrusion_system):
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.TopoDS import topods
    from OCC.Core.GeomAbs import GeomAbs_Plane
    from cadastrophe.core.native_naming import subshapes
    from cadastrophe.core.reference_geometry import capture_reference
    from cadastrophe.document import SelectionItem
    w,commands,m = extrusion_system
    body = base_body(extrusion_system)
    doc = w.viewport.document
    original = body.features[-1]
    def top_reference():
        body = doc.objects[body_id]
        face = next(f for f in subshapes(body.shape,"Face") if (s:=BRepAdaptor_Surface(topods.Face(f))).GetType() == GeomAbs_Plane and abs(s.Plane().Location().Z()-20)<1e-6)
        return capture_reference(doc,SelectionItem(body.id,"Face",face,body.revision))
    body_id = body.id
    tool = choose(w,"cadastrophe.modeling.reference_plane")
    tool.base.set_reference(top_reference())
    tool.distance.set_internal_value(0)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    datum, = (o for o in doc.objects.values() if isinstance(o,DatumPlaneObject))
    source = commands.create_sketch(doc,datum.id)
    commands.add_entity(doc,source.id,m.Circle(m.Vec2(30,30),5))
    start(w,doc.objects[source.id]).depth.set_internal_value(5)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    current = top_reference()
    assert current.producer_id != original
    w.tree_selection_changed([datum.id])
    tool = choose(w,"cadastrophe.modeling.edit_plane")
    tool.base.set_reference(current)
    assert tool.base.reference().producer_id == original
    tool.distance.set_internal_value(-1)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    assert volume(doc.objects[body.id].shape) == pytest.approx(120000+100*pi)
