"""Native 3D source/reference collectors, independent of construction-tree picks."""
from dataclasses import replace
from math import pi

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cadastrophe.core.body_history import body_result
from cadastrophe.core.feature_model import ResultStatus
from cadastrophe.core.reference_geometry import capture_reference, resolve_axis_reference
from cadastrophe.core.native_naming import subshapes
from cadastrophe.document import SelectionItem
from cadastrophe.geometry import Vec3
from plugins.modeling.feature_selection import face_sources
from test_extrusion_tool import extrusion_system, start
from test_boss_tool import base_body, face_sketch
from test_cut_tool import start_cut
from test_parametric_operations_ui import choose, screen, top_view, capture_tool
from test_primitives import volume
from test_advanced_cad_tools import collect_native_tool_sessions


def click(w, point, *, ctrl=False):
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier,
                    pos=screen(w, point))
    w.tool_sessions.update_preview()


def model(system):
    w, _, _ = system
    body = base_body(system)
    doc = w.viewport.document
    sketch = face_sketch(system, body, center=(20,20))
    tool = start(w, sketch)
    tool.depth.set_internal_value(8)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    boss = doc.objects[body.id].features[-1]
    return doc, doc.objects[body.id], boss


@pytest.mark.parametrize("operation", ["linear_pattern", "circular_pattern", "mirror"])
def test_source_and_reference_are_picked_in_3d_without_tree(extrusion_system, operation):
    # Native input, rather than calling set_sources/set_reference directly.
    w, _, _ = extrusion_system
    doc, body, boss = model(extrusion_system)
    top_view(w)
    doc.select_objects(())
    snapshot, history = dict(doc.objects), doc.history.count
    tool = choose(w, "cadastrophe.modeling."+operation)
    assert tool.sources.isVisible() and tool._selecting_sources
    assert not tool.reference.combo.isVisible()
    if operation == "linear_pattern":
        capture_tool(w,"modeling-source-selection")
    click(w, (20,20,28))
    assert tool.source_ids == (boss,), w.tool_sessions.message
    assert tool.active_reference is tool.reference
    assert dict(doc.objects) == snapshot and doc.history.count == history
    guides = tool.surface._reference_guides
    assert guides is not None and guides.items
    if operation == "linear_pattern":
        capture_tool(w,"modeling-axis-selection")
        # A principal axis is a selectable guide in the viewport.
        click(w, (75,0,0))
        assert tool.reference.reference().kind == "axis", w.tool_sessions.message
        assert abs(resolve_axis_reference(doc,tool.reference.reference())[1].x) == pytest.approx(1)
        tool.spacing.set_internal_value(25)
        expected = 120000+2*25*pi*8
    elif operation == "circular_pattern":
        # A circular edge defines the axis of the source boss, with no list.
        # Instead choose a visible base vertical edge for an offset axis.
        w.viewport.view.SetProj(1.,-1.,1.)
        w.viewport.frame_objects((body.id,))
        QApplication.processEvents()
        click(w, (100,0,10))
        assert tool.reference.reference().kind == "edge", w.tool_sessions.message
        tool.merge.setChecked(False)
        expected = 120000+2*25*pi*8
    else:
        # The XZ reference plane is hidden in the document but its guide is selectable.
        camera = w.viewport.view.Camera()
        camera.SetScale(camera.Scale()*3)
        w.viewport.view.Redraw()
        capture_tool(w,"modeling-plane-selection")
        click(w, (-35,0,70))
        assert tool.reference.reference().kind == "plane", w.tool_sessions.message
        tool.merge.setChecked(False)
        expected = 120000+2*25*pi*8
    w.tool_sessions.update_preview()
    assert tool.active_reference is None
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(expected)
    assert dict(doc.objects) == snapshot and doc.history.count == history
    assert not guides.items
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert doc.objects[body.id].features[-1] != boss
    assert body_result(doc, body.id).status is ResultStatus.READY


def test_native_multiselection_toggle_and_cancel_preserve_document(extrusion_system):
    w, _, _ = extrusion_system
    doc, body, boss = model(extrusion_system)
    sketch = face_sketch(extrusion_system, body, center=(65,20))
    tool = start(w, sketch)
    tool.depth.set_internal_value(6)
    assert w.tool_sessions.commit()
    second = doc.objects[body.id].features[-1]
    top_view(w)
    doc.select_objects(())
    snapshot = dict(doc.objects)
    tool = choose(w,"cadastrophe.modeling.linear_pattern")
    click(w,(20,20,28),ctrl=True)
    click(w,(65,20,26),ctrl=True)
    assert tool.source_ids == (boss,second), w.tool_sessions.message
    click(w,(20,20,28),ctrl=True)
    assert tool.source_ids == (second,)
    click(w,(20,20,28))
    assert tool.source_ids == (second,boss)
    guides = tool.surface._reference_guides
    assert guides.items
    assert w.tool_sessions.cancel()
    assert not guides.items
    assert dict(doc.objects) == snapshot


def test_hole_wall_selects_cut_operation_and_its_axis_survives_rebuild(extrusion_system):
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
    from OCC.Core.GeomAbs import GeomAbs_Cylinder, GeomAbs_Circle
    from OCC.Core.TopoDS import topods
    w, _, _ = extrusion_system
    body = base_body(extrusion_system)
    doc = w.viewport.document
    sketch = face_sketch(extrusion_system, body, center=(20,20))
    tool = start_cut(w,sketch)
    tool.depth.set_internal_value(20)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    current = doc.objects[body.id]
    cut = current.features[-1]
    wall = next(f for f in subshapes(current.shape,"Face")
                if BRepAdaptor_Surface(topods.Face(f)).GetType() == GeomAbs_Cylinder)
    item = SelectionItem(body.id,"Face",wall,current.revision)
    assert face_sources(doc,item) == cut
    ref = capture_reference(doc,item)
    origin,direction = resolve_axis_reference(doc,ref)
    assert origin.x == pytest.approx(20) and origin.y == pytest.approx(20)
    assert abs(direction.z) == pytest.approx(1)
    circular = next(e for e in subshapes(current.shape,"Edge")
                    if BRepAdaptor_Curve(topods.Edge(e)).GetType() == GeomAbs_Circle)
    edge_ref = capture_reference(doc,SelectionItem(body.id,"Edge",circular,current.revision))
    assert resolve_axis_reference(doc,edge_ref)[0].x == pytest.approx(20)
    # The tree remains available for explicitly choosing the hole operation.
    w.tree_selection_changed([cut])  # The tree remains a supported fallback.
    tool = choose(w,"cadastrophe.modeling.circular_pattern")
    tool.reference.set_reference(ref)
    # Around its own axis a single hole overlaps itself and must fail clearly.
    w.tool_sessions.update_preview()
    assert not w.tool_sessions.can_commit
    assert w.tool_sessions.cancel()
    base = doc.objects[current.features[0]]
    doc.replace_feature(replace(base,parameters=replace(base.parameters,depth=30)))
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert resolve_axis_reference(doc,ref)[0].x == pytest.approx(20)


def test_revolution_profile_and_hidden_construction_axis_without_tree(extrusion_system):
    w, commands, m = extrusion_system
    doc = w.viewport.document
    xy = next(o for o in doc.objects.values() if o.plane is not None and o.plane.name == "XY")
    source = commands.create_sketch(doc, xy.id)
    points = tuple(m.Vec2(*p) for p in ((5,-5),(10,-5),(10,5),(5,5)))
    commands.add_entities(doc, source.id, tuple(m.Segment(p, points[(i+1)%4]) for i,p in enumerate(points)), title="Profil")
    axis_sketch = commands.create_sketch(doc, xy.id)
    axis = m.Segment(m.Vec2(0,-20),m.Vec2(0,20),construction=True)
    commands.add_entity(doc, axis_sketch.id, axis)
    doc.set_visible(axis_sketch.id, False)
    doc.select_objects(())
    top_view(w)
    tool = choose(w,"cadastrophe.modeling.revolve")
    assert tool.active_reference is None and tool.source.value is None
    click(w,(10,2,0))
    assert tool.source.value == source.id, w.tool_sessions.message
    QTest.mouseClick(tool.axis.field.edit, Qt.MouseButton.LeftButton)
    assert tool.active_reference is tool.axis
    guides = tool.surface._reference_guides
    assert any(ref.object_id == axis_sketch.id for _,_,ref in guides.items)
    click(w,(0,10,0))
    assert tool.axis.reference().object_id == axis_sketch.id, w.tool_sessions.message
    assert tool.axis.reference().entity_id == axis.id
    assert w.tool_sessions.can_commit,w.tool_sessions.message
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(750*pi)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    assert not guides.items and not doc.objects[axis_sketch.id].visible


def test_picked_circular_edge_axis_is_persisted_and_rebuilt(extrusion_system,tmp_path):
    w, _, _ = extrusion_system
    body = base_body(extrusion_system)
    doc = w.viewport.document
    hole = face_sketch(extrusion_system, body, center=(50,30))
    tool = start_cut(w,hole)
    tool.depth.set_internal_value(20)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    boss_sketch = face_sketch(extrusion_system,doc.objects[body.id],center=(70,30))
    tool = start(w,boss_sketch)
    tool.depth.set_internal_value(8)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    boss = doc.objects[body.id].features[-1]
    doc.select_objects(())
    top_view(w)
    tool = choose(w,"cadastrophe.modeling.circular_pattern")
    click(w,(70,30,28))
    assert tool.source_ids == (boss,)
    # Avoid the projected cylinder seam: the circular rim is the intended
    # persistent reference, independently of the scale of visible supports.
    click(w,(50,35,20))
    ref = tool.reference.reference()
    assert ref.kind == "edge",w.tool_sessions.message
    assert resolve_axis_reference(doc,ref)[0].tuple()[:2] == pytest.approx((50,30))
    assert w.tool_sessions.commit(),w.tool_sessions.message
    pattern = doc.objects[body.id].features[-1]
    assert doc.objects[pattern].parameters.reference == ref
    base = doc.objects[doc.objects[body.id].features[0]]
    doc.replace_feature(replace(base,parameters=replace(base.parameters,depth=30)))
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    expected = 180000-25*pi*20+2*25*pi*8
    assert body_result(doc,body.id).status is ResultStatus.READY
    assert volume(doc.objects[body.id].shape) == pytest.approx(expected)
    path = tmp_path/"picked-axis.cada"
    assert w.save_document(path=path)
    assert w.open_document(path)
    restored = w.viewport.document
    assert restored.objects[pattern].parameters.reference == ref
    assert resolve_axis_reference(restored,ref)[0].tuple()[:2] == pytest.approx((50,30))
    assert volume(restored.objects[body.id].shape) == pytest.approx(expected)


def test_repeated_occurrence_maps_to_pattern_not_original_solid(extrusion_system):
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.GeomAbs import GeomAbs_Cylinder
    from OCC.Core.TopoDS import topods
    w, _, _ = extrusion_system
    doc, body, boss = model(extrusion_system)
    w.tree_selection_changed([boss])
    tool = choose(w,"cadastrophe.modeling.linear_pattern")
    from cadastrophe.core.operation_model import GeometryReference
    tool.reference.set_reference(GeometryReference("axis",values=(0,0,0,1,0,0)))
    tool.spacing.set_internal_value(25)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    body = doc.objects[body.id]
    pattern = body.features[-1]
    face = next(f for f in subshapes(body.shape,"Face")
        if (s:=BRepAdaptor_Surface(topods.Face(f))).GetType() == GeomAbs_Cylinder
        and abs(s.Cylinder().Location().X()-45) < 1e-6)
    item = SelectionItem(body.id,"Face",face,body.revision)
    assert face_sources(doc,item) == pattern
    with pytest.raises(ValueError,match="antérieure"):
        face_sources(doc,item,before=doc.objects[pattern])


def test_coplanar_overlap_rejects_ambiguous_function_selection(extrusion_system):
    from OCC.Core.BRepClass import BRepClass_FaceClassifier
    from OCC.Core.gp import gp_Pnt
    from OCC.Core.TopAbs import TopAbs_IN
    w, commands, m = extrusion_system
    body = base_body(extrusion_system)
    doc = w.viewport.document
    xy = next(o for o in doc.objects.values() if o.plane is not None and o.plane.name == "XY")
    sketch = commands.create_sketch(doc,xy.id)
    points = tuple(m.Vec2(*p) for p in ((80,0),(120,0),(120,60),(80,60)))
    commands.add_entities(doc,sketch.id,tuple(m.Segment(p,points[(i+1)%4]) for i,p in enumerate(points)),title="Rectangle")
    tool = start(w,doc.objects[sketch.id])
    tool.depth.set_internal_value(20)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    body = doc.objects[body.id]
    def item_at(point):
        face = next(f for f in subshapes(body.shape,"Face")
                    if BRepClass_FaceClassifier(f,gp_Pnt(*point.tuple()),1e-7).State() == TopAbs_IN)
        return SelectionItem(body.id,"Face",face,body.revision)
    for point,key in ((Vec3(20,30,20),body.features[0]),(Vec3(110,30,20),body.features[-1])):
        assert face_sources(doc,item_at(point),point) == key
    point = Vec3(90,30,20)
    item = item_at(point)
    with pytest.raises(ValueError,match="unique"):
        face_sources(doc,item,point)
    with pytest.raises(ValueError,match="unique"):
        face_sources(doc,item)
    with pytest.raises(ValueError,match="changé"):
        face_sources(doc,replace(item,revision=body.revision-1))
