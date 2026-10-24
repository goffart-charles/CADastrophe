"""Explicit face/plane repair, intact identities, consumer reconstruction and UI."""
from dataclasses import replace
from importlib import import_module
from math import pi
from uuid import uuid4

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from cadastrophe.core.body_history import body_result, result_at
from cadastrophe.core.feature_model import BodyStateRef, ResultStatus
from cadastrophe.core.topology_refs import TopologyReference
from cadastrophe.core.native_naming import subshapes
from cadastrophe.geometry import Vec3
from test_extrusion_tool import extrusion_system
from test_boss_tool import base_body, face_sketch
from test_extrusion_tool import start
from test_parametric_operations_ui import screen, top_view
from test_primitives import volume


def module(w, name):
    return import_module(w.plugins._record("cadastrophe.sketch").namespace+"."+name)


def damaged_boss(system):
    w, _, _ = system
    body = base_body(system)
    sketch = face_sketch(system, body)
    tool = start(w, sketch)
    tool.depth.set_internal_value(10)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    doc = w.viewport.document
    current = doc.objects[sketch.id]
    support = replace(current.sketch.support, reference=TopologyReference("Face", ("lost:face",)))
    doc.replace_feature(replace(current, sketch=replace(current.sketch, support=support)))
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert body_result(doc, body.id).status is ResultStatus.ERROR
    doc.history.clear()
    return doc, body.id, sketch.id


def test_native_face_support_repair_rebuilds_consumers_atomically_and_roundtrips(extrusion_system, tmp_path):
    w, _, _ = extrusion_system
    doc, body_id, sketch_id = damaged_boss(extrusion_system)
    ids, order = set(doc.objects), doc.objects[body_id].features
    entities = doc.objects[sketch_id].sketch.entities
    w.tree_selection_changed([sketch_id])
    assert w.command_registry.execute("cadastrophe.sketch.reattach_support")
    tool = w.tool_sessions.tool
    assert not w.tool_sessions.can_commit
    top_view(w)
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=screen(w, (50., 30., 20.)))
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(120000+250*pi)
    assert doc.objects[body_id].shape is None and doc.history.count == 0
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert doc.history.count == 1 and set(doc.objects) == ids
    assert doc.objects[body_id].features == order and doc.objects[sketch_id].sketch.entities == entities
    assert volume(doc.objects[body_id].shape) == pytest.approx(120000+250*pi)
    assert doc.objects[sketch_id].sketch.support.reference.labels != ("lost:face",)
    doc.history.undo()
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert body_result(doc, body_id).status is ResultStatus.ERROR
    doc.history.redo()
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert volume(doc.objects[body_id].shape) == pytest.approx(120000+250*pi)
    path = tmp_path/"support_repare.cada"
    assert w.save_document(path=path) and w.close_document()
    restored = w.open_document(path)
    assert set(restored.objects) == ids and restored.objects[body_id].features == order
    assert volume(restored.objects[body_id].shape) == pytest.approx(120000+250*pi)


def test_face_support_repair_cancel_never_publishes_recipe_or_results(extrusion_system):
    w, _, _ = extrusion_system
    doc, body_id, sketch_id = damaged_boss(extrusion_system)
    before = dict(doc.objects)
    w.tree_selection_changed([sketch_id])
    assert w.command_registry.execute("cadastrophe.sketch.reattach_support")
    top_view(w)
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=screen(w, (50., 30., 20.)))
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert w.tool_sessions.cancel()
    assert dict(doc.objects) == before and not doc.history.count and doc.objects[body_id].shape is None


def test_lost_plane_can_be_replaced_without_changing_sketch_entities(extrusion_system):
    w, commands, model = extrusion_system
    doc = w.viewport.document
    xy = next(o for o in doc.objects.values() if o.plane is not None and o.plane.name == "XY")
    sketch = commands.create_sketch(doc, xy.id)
    commands.add_entity(doc, sketch.id, model.Segment(model.Vec2(5, 7), model.Vec2(10, 7)))
    obj = doc.objects[sketch.id]
    # A translated in-plane origin must survive reattachment to a datum.
    frame = replace(obj.sketch.support.frame, origin=Vec3(4, 6, 3))
    support = replace(obj.sketch.support, plane_id=str(uuid4()), frame=frame)
    doc.replace_feature(replace(obj, sketch=replace(obj.sketch, support=support)))
    before = doc.objects[sketch.id].sketch.entities
    w.tree_selection_changed([sketch.id])
    assert w.command_registry.execute("cadastrophe.sketch.reattach_support")
    w.tree_selection_changed([xy.id])
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert w.tool_sessions.commit(), w.tool_sessions.message
    repaired = doc.objects[sketch.id]
    assert repaired.sketch.entities == before
    assert repaired.sketch.support.frame.origin == Vec3(4, 6, 0)
    assert not repaired.reference_error(doc)
    resolved = module(w, "construction_context").resolve_object(repaired, doc)
    assert resolved.sketch.support.frame.origin == Vec3(4, 6, 0)


def test_reassociation_refuses_future_producer_and_requires_explicit_reorientation(extrusion_system):
    w, _, _ = extrusion_system
    doc, body_id, sketch_id = damaged_boss(extrusion_system)
    repair = module(w, "support_repair")
    support = module(w, "supports")
    original = doc.objects[sketch_id]
    base = doc.objects[body_id].features[0]
    state = result_at(doc, BodyStateRef(body_id, base))
    side = next(f for f in subshapes(state.shape, "Face") if support.face_frame(f, "Face").normal.x > .99)
    selected = support.from_face_state(doc, body_id, side, base)
    before = dict(doc.objects)
    with pytest.raises(ValueError, match="orientation"):
        repair.reassociation_objects(doc, sketch_id, selected)
    objects = repair.reassociation_objects(doc, sketch_id, selected, preserve_frame=False)
    assert objects[0].sketch.support.frame.normal.x == pytest.approx(1)
    assert objects[0].sketch.entities == original.sketch.entities
    future = doc.objects[body_id].features[-1]
    with pytest.raises(ValueError, match="futur"):
        repair.reassociation_objects(doc, sketch_id, replace(selected, producer_id=future), preserve_frame=False)
    assert dict(doc.objects) == before
