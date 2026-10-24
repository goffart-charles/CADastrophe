"""Native curve clicks and sweep tools through the actual plugin host."""
from dataclasses import replace
from math import pi

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from cadastrophe.core.feature_objects import SweepFeature, BodyObject
from cadastrophe.core.feature_model import ResultStatus
from cadastrophe.core.body_history import body_result
from test_extrusion_tool import extrusion_system
from test_parametric_operations_ui import screen
from test_draft_ui import iso_view
from test_feature_edit import edit
from test_primitives import volume, pose


def inputs(system, *, rectangle=False):
    w, commands, m = system
    doc = w.viewport.document
    xy = next(o for o in doc.objects.values() if o.plane and o.plane.name == "XY")
    xz = next(o for o in doc.objects.values() if o.plane and o.plane.name == "XZ")
    sketch = commands.create_sketch(doc, xy.id)
    entities = (m.Circle(m.Vec2(0, 0), 2),)
    if rectangle:
        points = tuple(m.Vec2(*p) for p in ((-2, -1), (2, -1), (2, 1), (-2, 1)))
        entities = tuple(m.Segment(p, points[(i+1)%4]) for i, p in enumerate(points))
    commands.add_entities(doc, sketch.id, entities, title="Profil")
    path = commands.create_sketch(doc, xz.id)
    line = m.Segment(m.Vec2(0, 0), m.Vec2(0, 20))
    commands.add_entity(doc, path.id, line)
    doc.history.clear()
    return doc, sketch, path, line


def test_sweep_native_curve_pick_atomic_preview_edit_and_reopen(extrusion_system, tmp_path):
    w, commands, m = extrusion_system
    doc, sketch, path, _ = inputs(extrusion_system, rectangle=True)
    iso_view(w)
    camera = pose(w.viewport)
    w.tree_selection_changed([sketch.id])
    action = w.command_registry.action("modeling.sweep")
    button = next(b for b in w.command_manager.findChildren(QToolButton) if b.defaultAction() is action)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    tool = w.tool_sessions.tool
    assert tool is not None
    before = dict(doc.objects)
    assert not w.tool_sessions.can_commit
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=screen(w, (0., 0., 10.)))
    w.tool_sessions.update_preview()
    assert len(tool.path_references) == 1 and tool.path_references[0].object_id == path.id
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(160)
    assert dict(doc.objects) == before and doc.history.count == 0
    assert pose(w.viewport) == camera
    assert w.tool_sessions.commit(), w.tool_sessions.message
    feature, = (o for o in doc.objects.values() if isinstance(o, SweepFeature))
    body, = (o for o in doc.objects.values() if isinstance(o, BodyObject))
    ids = set(doc.objects)
    assert doc.history.count == 1 and feature.id in w.tree_items
    doc.history.undo()
    assert body.id not in doc.objects and doc.objects[path.id].visible
    doc.history.redo()
    assert volume(doc.objects[body.id].shape) == pytest.approx(160)
    tool = edit(w, feature.id, double_click=True)
    tool.roll.setValue(90)
    tool.reverse.setChecked(True)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert set(doc.objects) == ids
    from test_parametric_operations import bounds
    assert bounds(doc.objects[body.id].shape) == pytest.approx((-1, -2, 0, 1, 2, 20), abs=1e-6)
    doc.history.undo()
    doc.history.redo()
    filename = tmp_path/"balayage_native.cada"
    assert w.save_document(path=filename)
    assert w.close_document()
    restored = w.open_document(filename)
    assert restored is not doc and set(restored.objects) == ids
    assert restored.objects[feature.id].parameters.roll == 90
    assert volume(restored.objects[body.id].shape) == pytest.approx(160)


@pytest.mark.parametrize("cut", [False, True])
def test_sweep_body_target_and_cancel_leave_document_intact(extrusion_system, cut):
    from cadastrophe.document import SceneObject
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.gp import gp_Pnt
    w, _, _ = extrusion_system
    doc, sketch, path, _ = inputs(extrusion_system)
    block = SceneObject("Cible", BRepPrimAPI_MakeBox(gp_Pnt(-5, -5, 0 if cut else -5), 10., 10., 20. if cut else 5.).Shape(), (.4, .5, .6))
    doc.add(block)
    doc.history.clear()
    w.tree_selection_changed([sketch.id])
    assert w.command_registry.execute("modeling.cut_sweep" if cut else "modeling.sweep")
    tool = w.tool_sessions.tool
    tool.path_sketch.setCurrentIndex(tool.path_sketch.findData(path.id))
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx((2000 if cut else 500)+(-1 if cut else 1)*80*pi)
    before = dict(doc.objects)
    assert w.tool_sessions.cancel()
    assert dict(doc.objects) == before and doc.history.count == 0
    assert w.command_registry.execute("modeling.cut_sweep" if cut else "modeling.sweep")
    tool = w.tool_sessions.tool
    tool.path_sketch.setCurrentIndex(tool.path_sketch.findData(path.id))
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert doc.objects[block.id].features
    assert volume(doc.objects[block.id].shape) == pytest.approx((2000 if cut else 500)+(-1 if cut else 1)*80*pi)


def test_sweep_lost_path_can_be_reselected_natively_with_same_feature_identity(extrusion_system, tmp_path):
    from uuid import uuid4
    from test_sweep import system, profile, path_sketch, swept, Segment, Vec2
    from cadastrophe.core.persistence import save_document
    from cadastrophe.core.body_history import evaluate_body
    from examples.parametric_example import registry
    doc, service = system()
    section = profile(doc, circle=(0, 0, 2))
    path = path_sketch(doc, (Segment(Vec2(0, 0), Vec2(0, 20)),))
    body, feature, result = swept(doc, service, section, path)
    missing = replace(feature.parameters.path[0], entity_id=str(uuid4()))
    doc.replace_feature(replace(feature, parameters=replace(feature.parameters, path=(missing,))))
    assert evaluate_body(doc, body.id, service.evaluator(doc)).status is ResultStatus.ERROR
    filename = tmp_path/"balayage_a_reparer.cada"
    save_document(doc, filename, registry=registry(service))
    w, _, _ = extrusion_system
    loaded = w.open_document(filename)
    iso_view(w)
    ids = set(loaded.objects)
    tool = edit(w, feature.id)
    assert not w.tool_sessions.can_commit
    tool.path_field.clear_button.click()
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=screen(w, (0., 0., 10.)))
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert set(loaded.objects) == ids and loaded.objects[body.id].features == (feature.id,)
    assert volume(loaded.objects[body.id].shape) == pytest.approx(80*pi)
    loaded.history.undo()
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert body_result(loaded, body.id).status is ResultStatus.ERROR
    loaded.history.redo()
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert body_result(loaded, body.id).status is ResultStatus.READY


def test_sweep_profile_can_be_replaced_without_changing_feature_or_body(extrusion_system):
    w, commands, m = extrusion_system
    doc, sketch, path, _ = inputs(extrusion_system)
    replacement = commands.create_sketch(doc, sketch.sketch.support.plane_id)
    commands.add_entity(doc, replacement.id, m.Circle(m.Vec2(0, 0), 3))
    w.tree_selection_changed([sketch.id])
    assert w.command_registry.execute("modeling.sweep")
    tool = w.tool_sessions.tool
    tool.path_sketch.setCurrentIndex(tool.path_sketch.findData(path.id))
    assert w.tool_sessions.commit(), w.tool_sessions.message
    feature, = (o for o in doc.objects.values() if isinstance(o, SweepFeature))
    ids = set(doc.objects)
    tool = edit(w, feature.id)
    QTest.mouseClick(tool.source.edit, Qt.MouseButton.LeftButton)
    w.tree_selection_changed([replacement.id])
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert set(doc.objects) == ids and doc.objects[feature.id].sketch_id == replacement.id
    assert volume(doc.objects[feature.body_id].shape) == pytest.approx(180*pi)
    doc.history.undo()
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert doc.objects[feature.id].sketch_id == sketch.id
    assert volume(doc.objects[feature.body_id].shape) == pytest.approx(80*pi)
