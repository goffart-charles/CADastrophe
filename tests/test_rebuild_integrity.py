"""Topology changes and exact dependency boundaries with real OCCT geometry."""
from dataclasses import replace
from math import pi
from time import perf_counter
import json
from pathlib import Path

import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from PySide6.QtTest import QTest

from cadastrophe.core.body_history import body_result, result_at, evaluate_body, evaluate_body_steps, last_valid_result
from cadastrophe.core.feature_model import BodyStateRef, Operation, ResultStatus
from cadastrophe.core.native_naming import NamedTopology, NamingTrace, capture, resolve, subshapes
from cadastrophe.core.persistence import save_document
from cadastrophe.core.operation_effects import checked_solids
from cadastrophe.core.topology_refs import TopologyReference
from test_extrusion_tool import extrusion_system, start
from test_modeling_rebuild import base, face_source, append, flush
from test_parametric_rebuild import two_extrusions, edge_operation, resolved, bounds
from test_boss_tool import face_sketch
from test_feature_edit import edit
from test_plugins import harness, plugin_root
from test_primitives import volume


def test_hole_inserted_upstream_preserves_cap_support_and_corner_fillet(extrusion_system, tmp_path):
    w, commands, m = extrusion_system
    body_id, attached_id = two_extrusions(extrusion_system)
    doc = w.viewport.document
    fillet = edge_operation(w, body_id, "fillet", 0., 0., 2.)
    first = doc.objects[doc.objects[body_id].features[0]]
    source = doc.objects[first.sketch_id]
    hole = m.Circle(m.Vec2(20, 20), 3)
    ids, features = set(doc.objects), doc.objects[body_id].features
    original_volume = volume(doc.objects[body_id].shape)
    commands.add_entity(doc, source.id, hole)
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    result = body_result(doc, body_id)
    assert result.status is ResultStatus.READY, result.message
    assert result_at(doc, BodyStateRef(body_id, fillet)).status is ResultStatus.READY
    assert volume(result.shape) == pytest.approx(original_volume-180*pi)
    assert BRepCheck_Analyzer(result.shape).IsValid()
    assert resolved(w, attached_id).sketch.support.frame.origin.z == pytest.approx(20.)
    assert set(doc.objects) == ids and doc.objects[body_id].features == features
    doc.history.undo()
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert volume(doc.objects[body_id].shape) == pytest.approx(original_volume)
    doc.history.redo()
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert volume(doc.objects[body_id].shape) == pytest.approx(original_volume-180*pi)
    path = tmp_path / "changed-topology.cada"
    save_document(doc, path, registry=w.object_types)
    assert w.close_document() and w.open_document(path) is not None
    reopened = w.viewport.document
    assert body_result(reopened, body_id).status is ResultStatus.READY
    assert volume(reopened.objects[body_id].shape) == pytest.approx(original_volume-180*pi)
    commands.remove_entity(reopened, source.id, hole.id)
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert body_result(reopened, body_id).status is ResultStatus.READY
    assert volume(reopened.objects[body_id].shape) == pytest.approx(original_volume)


def test_native_merge_of_two_named_faces_requires_reassociation():
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.gp import gp_Pnt
    from cadastrophe.core.reference_geometry import face_plane
    from cadastrophe.core.feature_model import EvaluationResult
    left = BRepPrimAPI_MakeBox(10., 20., 30.).Shape()
    right = BRepPrimAPI_MakeBox(gp_Pnt(10., 0., 0.), 10., 20., 30.).Shape()
    def top(shape):
        return next(f for f in subshapes(shape, "Face") if face_plane(f).normal.z > .9)
    a, b = top(left), top(right)
    naming = NamedTopology((("left-cap", a),))
    ref = capture(left, a, "Face", naming)
    trace = NamingTrace("merged", EvaluationResult(ResultStatus.READY, left, topology=naming))
    trace.known.append((right, NamedTopology((("right-cap", b),))))
    maker = BRepAlgoAPI_Fuse(left, right)
    maker.SimplifyResult(True, True)
    shape = maker.Shape()
    assert BRepCheck_Analyzer(shape).IsValid()
    assert len(subshapes(shape, "Face")) == 6
    trace.operation(maker, (left, right), shape)
    assert set(trace.topology.labels(top(shape), "Face")) == {"left-cap", "right-cap"}
    with pytest.raises(ValueError, match="fusion|ambigu"):
        resolve(shape, ref, trace.topology)


def test_edit_after_a_consumed_prefix_keeps_the_independent_body_current(harness, monkeypatch):
    data = base(harness)
    doc, _, _, _, _, body = data
    source = face_source(harness, data, body)
    child, _ = append(harness, data, source, Operation.NEW_BODY, 10, new=True)
    parent, boss = append(harness, data, source, Operation.BOSS, 10)
    child_before = doc.objects[child.id]
    state = body_result(doc, child.id)
    prefix = result_at(doc, BodyStateRef(parent.id, parent.features[0]))
    service = harness.services.service("cadastrophe.modeling.extrusions")
    cls = type(service.evaluator(doc))
    native, calls = cls.evaluate, []
    def traced(self, feature, *args):
        calls.append(feature.id)
        return native(self, feature, *args)
    monkeypatch.setattr(cls, "evaluate", traced)
    doc.replace_feature(replace(boss, parameters=replace(boss.parameters, depth=12)))
    assert body_result(doc, child.id) is state
    assert doc.objects[child.id] is child_before
    assert result_at(doc, BodyStateRef(parent.id, parent.features[0])) is prefix
    flush(harness)
    assert doc.objects[child.id] is child_before
    assert volume(doc.objects[parent.id].shape) == pytest.approx(120000+300*pi)
    assert bounds(child_before.shape)[2:] == pytest.approx(bounds(doc.objects[child.id].shape)[2:])
    assert calls == [boss.id]
    for replay in (doc.history.undo, doc.history.redo):
        replay()
        flush(harness)
        assert body_result(doc, child.id) is state and doc.objects[child.id] is child_before
    assert set(calls) == {boss.id}
    expected = body_result(doc, parent.id)
    labels = {label for label, _ in expected.topology.faces}
    calls.clear()
    durations = {}
    for reuse in (True, False):
        started = perf_counter()
        for _ in range(3):
            rebuilt = evaluate_body(doc, parent.id, service.evaluator(doc), reuse=reuse)
            assert rebuilt.status is ResultStatus.READY
            assert len(checked_solids(rebuilt.shape)) == 1
            assert volume(rebuilt.shape) == pytest.approx(volume(expected.shape))
            assert bounds(rebuilt.shape) == pytest.approx(bounds(expected.shape))
            assert {label for label, _ in rebuilt.topology.faces} == labels
            assert doc.objects[child.id] is child_before
        durations["cached_seconds" if reuse else "full_seconds"] = perf_counter()-started
        assert calls == ([] if reuse else list(parent.features)*3)
    Path("artifacts/rebuild-integrity.json").write_text(json.dumps({
        "features_per_rebuild": len(parent.features), "repetitions": 3,
        "cached_native_calls": 0, "full_native_calls": len(calls), **durations,
    }, indent=2), encoding="utf-8")


def test_native_split_keeps_all_descendants_and_refuses_a_choice():
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.gp import gp_Pnt
    from cadastrophe.core.reference_geometry import face_plane
    from cadastrophe.core.feature_model import EvaluationResult
    source = BRepPrimAPI_MakeBox(20., 20., 30.).Shape()
    cap = next(f for f in subshapes(source, "Face") if face_plane(f).normal.z > .9)
    names = NamedTopology((("cap", cap),))
    ref = capture(source, cap, "Face", names)
    slot = BRepPrimAPI_MakeBox(gp_Pnt(8., -1., 25.), 4., 22., 10.).Shape()
    maker = BRepAlgoAPI_Cut(source, slot)
    shape = maker.Shape()
    assert len(checked_solids(shape)) == 1
    trace = NamingTrace("slot", EvaluationResult(ResultStatus.READY, source, topology=names))
    trace.operation(maker, (source, slot), shape)
    assert len({i for i, f in enumerate(subshapes(shape, "Face")) if "cap" in trace.topology.labels(f, "Face")}) == 2
    with pytest.raises(ValueError, match="ambigu"):
        resolve(shape, ref, trace.topology)


def test_failure_and_cancel_keep_last_valid_separate_then_repair(extrusion_system):
    w, _, _ = extrusion_system
    body_id, _ = two_extrusions(extrusion_system)
    doc = w.viewport.document
    fillet = edge_operation(w, body_id, "fillet", 0., 0., 2.)
    previous = last_valid_result(doc, body_id)
    first, boss, _ = doc.objects[body_id].features
    feature = doc.objects[first]
    doc.replace_feature(replace(feature, parameters=replace(feature.parameters, depth=25)))
    current = dict(doc.objects)
    service = w.services.service("cadastrophe.modeling.extrusions")
    steps = evaluate_body_steps(doc, body_id, service.evaluator(doc), reuse=True)
    next(steps)
    assert all(doc.objects[key] is obj for key, obj in current.items())
    assert last_valid_result(doc, body_id) is previous
    steps.close()
    assert doc.objects[body_id].shape is None and not doc.history.blocked
    from test_parametric_rebuild import resize_base
    resize_base(extrusion_system, body_id, 40.)
    assert result_at(doc, BodyStateRef(body_id, boss)).status is ResultStatus.ERROR
    assert result_at(doc, BodyStateRef(body_id, fillet)).status is ResultStatus.BLOCKED
    assert doc.objects[body_id].shape is None and last_valid_result(doc, body_id) is previous
    assert volume(previous.shape) > 0 and BRepCheck_Analyzer(previous.shape).IsValid()
    resize_base(extrusion_system, body_id, 120.)
    assert body_result(doc, body_id).status is ResultStatus.READY
    assert last_valid_result(doc, body_id) is body_result(doc, body_id)
    assert last_valid_result(doc, body_id) is not previous
    assert len(checked_solids(doc.objects[body_id].shape)) == 1


def test_cylinder_radius_changes_keep_planar_sketch_support_and_native_height(extrusion_system, tmp_path):
    w, commands, m = extrusion_system
    doc = w.viewport.document
    plane = next(o for o in doc.objects.values() if o.plane and o.plane.name == "XY")
    source = commands.create_sketch(doc, plane.id)
    circle = m.Circle(m.Vec2(0, 0), 20.)
    commands.add_entity(doc, source.id, circle)
    start(w, doc.objects[source.id]).depth.setValue(20.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    from cadastrophe.core.feature_objects import BodyObject
    body = next(o for o in doc.objects.values() if isinstance(o, BodyObject))
    attached = face_sketch(extrusion_system, body, center=(0, 0))
    start(w, attached).depth.setValue(10.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    order = doc.objects[body.id].features
    for radius in (25., 30., 22.):
        commands.replace_entity(doc, source.id, replace(circle, radius=radius))
        w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
        result = body_result(doc, body.id)
        assert result.status is ResultStatus.READY, result.message
        assert volume(result.shape) == pytest.approx(pi*(radius**2*20+250))
        assert bounds(result.shape) == pytest.approx((-radius, -radius, 0., radius, radius, 30.))
        assert len(checked_solids(result.shape)) == 1
        assert doc.objects[body.id].features == order
        assert resolved(w, attached.id).sketch.support.frame.origin.z == pytest.approx(20.)
    path = tmp_path / "cylinder-attachment.cada"
    save_document(doc, path, registry=w.object_types)
    assert w.close_document() and w.open_document(path) is not None
    QTest.qWait(100)
    tool = edit(w, order[0])
    tool.depth.setValue(35.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert bounds(w.viewport.document.objects[body.id].shape)[5] == pytest.approx(45.)


def test_old_named_recipes_migrate_before_topology_changes(extrusion_system, tmp_path):
    w, commands, m = extrusion_system
    body_id, attached_id = two_extrusions(extrusion_system)
    doc = w.viewport.document
    fillet = edge_operation(w, body_id, "fillet", 0., 0., 2.)
    first, boss, _ = doc.objects[body_id].features
    source = doc.objects[doc.objects[first].sketch_id]
    # Recreate the actual persisted names used before the new naming convention.
    attachment = doc.objects[attached_id]
    support = attachment.sketch.support
    state = result_at(doc, BodyStateRef(body_id, first))
    cap = resolve(state.shape, support.reference, state.topology)
    old_cap = TopologyReference("Face", state.topology.labels(cap, "Face", legacy=True), support.reference.geometry)
    feature = doc.objects[fillet]
    state = result_at(doc, BodyStateRef(body_id, boss))
    old_edges = tuple(TopologyReference("Edge", state.topology.labels(resolve(state.shape, r, state.topology), "Edge", legacy=True), r.geometry)
                      for r in feature.parameters.references)
    assert old_cap.labels != support.reference.labels
    doc.update_construction((replace(attachment, sketch=replace(attachment.sketch, support=replace(support, reference=old_cap))),
                             replace(feature, parameters=replace(feature.parameters, references=old_edges))))
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert body_result(doc, body_id).status is ResultStatus.READY
    path = tmp_path / "old-named-references.cada"
    save_document(doc, path, registry=w.object_types)
    assert w.close_document() and w.open_document(path) is not None
    loaded = w.viewport.document
    assert loaded.history.count == 0 and not loaded.is_modified
    assert loaded.objects[attached_id].sketch.support.reference.labels != old_cap.labels
    commands.add_entity(loaded, source.id, m.Circle(m.Vec2(20, 20), 3))
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert body_result(loaded, body_id).status is ResultStatus.READY
    assert len(checked_solids(loaded.objects[body_id].shape)) == 1


def test_shared_sketch_and_variable_publish_all_bodies_atomically_and_roundtrip(tmp_path):
    from cadastrophe.core.expressions import Expression
    from cadastrophe.core.persistence import load_document
    from examples.parametric_example import registry
    from test_parametric_operations import system, profile, append as add_feature
    from test_equations import variable
    doc, service = system()
    source = profile(doc, circle=(0., 0., 10.))
    a, fa, _ = add_feature(doc, service, source)
    b, fb, _ = add_feature(doc, service, source)
    separate = profile(doc, circle=(100., 0., 3.))
    independent, _, _ = add_feature(doc, service, separate)
    untouched = doc.objects[independent.id]
    doc.equation_evaluator = service.evaluator
    doc.set_variables((variable("hauteur", "5"),))
    doc.set_equation(source.id, source.sketch.entities[0].id+".radius", "=hauteur*2")
    doc.set_equation(fa.id, "depth", "=hauteur*2")
    doc.set_equation(fb.id, "depth", "=hauteur*3")
    ids, notifications = set(doc.objects), []
    def observe(*_):
        notifications.append(tuple(body_result(doc, key).status for key in (a.id, b.id)))
    doc.object_changed.connect(observe)
    def check(document, height, *, hole=False):
        for body, factor in ((a, 2), (b, 3)):
            result = body_result(document, body.id)
            assert result.status is ResultStatus.READY
            assert len(checked_solids(result.shape)) == 1
            assert volume(result.shape) == pytest.approx(pi*((height*2)**2-(1 if hole else 0))*height*factor)
    for height in (8., 12., 6.):
        doc.set_variables((replace(doc.variables[0], expression=Expression("value", f"={height}")),))
        check(doc, height)
        assert doc.objects[independent.id] is untouched
        assert set(doc.objects) == ids
    assert notifications and all(all(s is ResultStatus.READY for s in states) for states in notifications)
    doc.history.undo()
    check(doc, 12.)
    doc.history.redo()
    check(doc, 6.)
    from plugins.sketch import commands
    from plugins.sketch.model import Circle, Vec2
    hole = Circle(Vec2(0., 0.), 1.)
    commands.add_entity(doc, source.id, hole)
    check(doc, 6., hole=True)
    commands.remove_entity(doc, source.id, hole.id)
    check(doc, 6.)
    doc.history.undo()
    check(doc, 6., hole=True)
    doc.history.redo()
    check(doc, 6.)
    assert doc.objects[independent.id] is untouched
    path = tmp_path / "shared-variable.cada"
    codecs = registry(service)
    save_document(doc, path, registry=codecs)
    loaded = load_document(path, registry=codecs)
    loaded.equation_evaluator = service.evaluator
    check(loaded, 6.)
    loaded.set_variables((replace(loaded.variables[0], expression=Expression("value", "=9")),))
    check(loaded, 9.)
    check(doc, 6.)
    assert loaded.variables[0].id == doc.variables[0].id


def test_removing_a_referenced_producer_cascades_then_undo_restores_native_results(harness):
    data = base(harness)
    doc, _, _, _, _, body = data
    attached = face_source(harness, data, body)
    child, _ = append(harness, data, attached, Operation.NEW_BODY, 10, new=True)
    ids, old_volume = set(doc.objects), volume(doc.objects[body.id].shape)
    doc.remove(body.features[0])
    assert body.id not in doc.objects and child.id not in doc.objects
    assert not doc._body_last_valid_results
    assert not doc._body_evaluation_cache
    doc.history.undo()
    flush(harness)
    assert set(doc.objects) == ids
    assert volume(doc.objects[body.id].shape) == pytest.approx(old_volume)
    assert len(checked_solids(doc.objects[body.id].shape)) == 1
    assert volume(doc.objects[child.id].shape) == pytest.approx(250*pi)
    doc.history.redo()
    assert body.id not in doc.objects and child.id not in doc.objects


@pytest.mark.parametrize("operation,expected", [("Cut", 4000.), ("Common", 4000.), ("Fuse", 12000.)])
def test_boolean_build_preserves_reusable_native_inputs(operation, expected):
    from OCC.Core import BRepAlgoAPI
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.gp import gp_Pnt
    from cadastrophe.brep_io import encode_shape
    from cadastrophe.document import ObjectType
    from cadastrophe.core.native_boolean import build_boolean
    left = BRepPrimAPI_MakeBox(20., 20., 20.).Shape()
    right = BRepPrimAPI_MakeBox(gp_Pnt(10., 0., 0.), 20., 20., 20.).Shape()
    snapshots = tuple(encode_shape(s, ObjectType.BODY) for s in (left, right))
    for _ in range(3):
        builder = build_boolean(getattr(BRepAlgoAPI, "BRepAlgoAPI_"+operation), left, right)
        assert builder.IsDone()
        result = builder.Shape()
        assert len(checked_solids(result)) == 1
        assert volume(result) == pytest.approx(expected)
        assert tuple(encode_shape(s, ObjectType.BODY) for s in (left, right)) == snapshots


def test_three_matching_points_do_not_certify_source_curve_provenance():
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCC.Core.GeomAPI import GeomAPI_Interpolate
    from OCC.Core.TColgp import TColgp_HArray1OfPnt
    from OCC.Core.gp import gp_Pnt
    points = TColgp_HArray1OfPnt(1, 5)
    for i, (x, y) in enumerate(((0., 0.), (1., 2.), (3.7, 0.), (7., -2.), (10., 0.)), 1):
        points.SetValue(i, gp_Pnt(x, y, 0.))
    interpolate = GeomAPI_Interpolate(points, False, 1e-9)
    interpolate.Perform()
    assert interpolate.IsDone()
    crossing = BRepBuilderAPI_MakeEdge(interpolate.Curve()).Edge()
    line = BRepBuilderAPI_MakeEdge(gp_Pnt(0., 0., 0.), gp_Pnt(10., 0., 0.)).Edge()
    original = BRepBuilderAPI_MakeEdge(gp_Pnt(-5., 0., 0.), gp_Pnt(15., 0., 0.)).Edge()
    trace = NamingTrace("producer", sources=(("real-entity", original), ("crossing-entity", crossing)))
    assert trace._source(line, None) == "real-entity"


@pytest.mark.parametrize("outward", [False, True])
def test_shell_preview_and_failure_preserve_the_original_native_brep(outward):
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from cadastrophe.brep_io import encode_shape
    from cadastrophe.document import ObjectType
    from plugins.modeling.shell_geometry import apply_shell
    from test_hollowing import shell_params
    source = BRepPrimAPI_MakeBox(30., 20., 10.).Shape()
    original = encode_shape(source, ObjectType.BODY)
    parameters = shell_params(source, (10,), outward=outward)
    for thickness in (1., 2., 3.):
        shape = apply_shell(source, replace(parameters, thickness=thickness))
        assert len(checked_solids(shape)) == 1
        assert encode_shape(source, ObjectType.BODY) == original
    if not outward:
        with pytest.raises(ValueError):
            apply_shell(source, replace(parameters, thickness=30.))
        assert encode_shape(source, ObjectType.BODY) == original


@pytest.mark.parametrize("operation", ["fillet", "chamfer", "draft"])
def test_native_retreatments_preserve_the_shared_input_brep(operation):
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.TopoDS import topods
    from cadastrophe.brep_io import encode_shape
    from cadastrophe.document import ObjectType
    from cadastrophe.geometry import Vec3
    from cadastrophe.core.native_naming import static_topology
    from cadastrophe.core.feature_model import EdgeParameters
    from plugins.modeling.edge_geometry import apply_edges, edge_reference
    from plugins.modeling.draft_geometry import apply_draft
    from test_draft import parameters, neutral_plane
    source = BRepPrimAPI_MakeBox(30., 20., 10.).Shape()
    naming = static_topology(source)
    original = encode_shape(source, ObjectType.BODY)
    for value in (1., 2., 3.):
        if operation == "draft":
            shape = apply_draft(source, parameters(source, angle=value*2), neutral_plane(), Vec3(0, 0, 1))
        else:
            edge = topods.Edge(next(e for e in subshapes(source, "Edge")
                                   if max(abs(v) for v in bounds(e)[:2]) < 1e-6 and bounds(e)[5]-bounds(e)[2] > 9))
            p = EdgeParameters((edge_reference(source, edge),), size=value,
                               references=(capture(source, edge, "Edge", naming),))
            shape = apply_edges(source, Operation.FILLET if operation == "fillet" else Operation.CHAMFER,
                                p, topology=naming)
        assert len(checked_solids(shape)) == 1
        assert encode_shape(source, ObjectType.BODY) == original


@pytest.mark.parametrize("bosses", [8, 32])
def test_incremental_rebuild_cost_and_determinism_on_growing_native_models(bosses, tmp_path, monkeypatch):
    from cadastrophe.core.feature_model import ExtrusionParameters
    from cadastrophe.core.persistence import load_document
    from examples.parametric_example import ParametricExample, polygon, registry
    model = ParametricExample("Reprises mesurées")
    xy = model.plane("XY")
    source = model.sketch("base", "Semelle", xy, polygon(((0, 0), (100, 0), (100, 70), (0, 70))))
    model.append("base_feature", "Semelle", Operation.NEW_BODY, ExtrusionParameters(depth=10), source)
    for index in range(bosses):
        sketch = model.circle(f"sketch_{index}", f"Plot {index}", xy, (5+6*(index%16), 5+8*(index//16)), 2)
        last = model.append(f"feature_{index}", f"Bossage {index}", Operation.BOSS, ExtrusionParameters(depth=12), sketch)
    doc, service = model.document, model.service
    cls = type(service.evaluator(doc))
    native, calls = cls.evaluate, []
    def traced(self, feature, *args):
        calls.append(feature.id)
        return native(self, feature, *args)
    monkeypatch.setattr(cls, "evaluate", traced)
    expected = 70000+bosses*8*pi
    started = perf_counter()
    result = evaluate_body(doc, model.body.id, service.evaluator(doc), reuse=False)
    full_seconds = perf_counter()-started
    assert len(calls) == bosses+1
    assert result.status is ResultStatus.READY and volume(result.shape) == pytest.approx(expected, abs=1e-4)
    assert len(checked_solids(result.shape)) == 1
    calls.clear()
    prefix = result_at(doc, BodyStateRef(model.body.id, doc.objects[model.body.id].features[-2]))
    doc.replace_feature(replace(last, parameters=replace(last.parameters, depth=14.)))
    started = perf_counter()
    edited = evaluate_body(doc, model.body.id, service.evaluator(doc), reuse=True)
    incremental_seconds = perf_counter()-started
    assert calls == [last.id]
    assert result_at(doc, BodyStateRef(model.body.id, doc.objects[model.body.id].features[-2])) is prefix
    assert volume(edited.shape) == pytest.approx(expected+8*pi, abs=1e-4)
    assert len(checked_solids(edited.shape)) == 1
    path = tmp_path / "measured.cada"
    save_document(doc, path, registry=registry(service))
    loaded = load_document(path, registry=registry(service))
    assert volume(loaded.objects[model.body.id].shape) == pytest.approx(expected+8*pi, abs=1e-4)
    assert len(checked_solids(loaded.objects[model.body.id].shape)) == 1
    Path(f"artifacts/rebuild-integrity-{bosses+1}.json").write_text(json.dumps({
        "features": bosses+1, "full_seconds": full_seconds,
        "incremental_seconds": incremental_seconds, "full_native_calls": bosses+1,
        "incremental_native_calls": 1,
    }, indent=2), encoding="utf-8")
