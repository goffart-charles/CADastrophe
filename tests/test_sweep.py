"""Native swept effects, persistent paths and reconstruction contracts."""
from dataclasses import replace
from math import pi
from uuid import uuid4

import pytest

from cadastrophe.document import ObjectType
from cadastrophe.core.feature_model import Operation, SketchInput, ResultStatus, BodyStateRef
from cadastrophe.core.feature_objects import BodyObject, SweepFeature
from cadastrophe.core.operation_model import GeometryReference as Ref, SweepParameters, ReplayParameters
from cadastrophe.core.body_history import evaluate_body, body_result, result_at
from cadastrophe.core.native_naming import capture, resolve, subshapes
from cadastrophe.core.persistence import save_document, load_document
from examples.parametric_example import registry
from plugins.sketch import commands
from plugins.sketch.model import Segment, Circle, Arc, Vec2
from test_parametric_operations import system, profile, plane, append, bounds
from test_primitives import volume


def path_sketch(doc, entities, support=None):
    obj = commands.create_sketch(doc, (support or plane(doc, "XZ")).id)
    commands.add_entities(doc, obj.id, tuple(entities), title="Parcours")
    return doc.objects[obj.id]


def path_refs(sketch):
    return tuple(Ref("sketch_curve", sketch.id, entity_id=e.id) for e in sketch.sketch.entities)


def swept(doc, service, sketch, path, *, parameters=None, body=None, operation=Operation.NEW_BODY):
    from plugins.modeling.recipe_session import adopted
    body = body or BodyObject("Corps balayé", None, (.5, .6, .7))
    body = adopted(doc.objects.get(body.id, body))
    feature = SweepFeature("Balayage1", None, body.color, kind=ObjectType.FEATURE, body_id=body.id,
        operation=operation, source=SketchInput(sketch.id, sketch.producer_refs), parameters=parameters or SweepParameters(path_refs(path)))
    doc.update_construction((replace(body, features=(*body.features, feature.id)), feature))
    result = evaluate_body(doc, body.id, service.evaluator(doc))
    return doc.objects[body.id], feature, result


@pytest.mark.parametrize("transport", ["corrected", "frenet", "fixed"])
@pytest.mark.parametrize("hole", [False, True])
def test_sweep_straight_has_exact_volume_holes_and_named_faces(transport, hole):
    doc, service = system()
    sketch = profile(doc, circle=(0, 0, 2))
    if hole:
        commands.add_entity(doc, sketch.id, Circle(Vec2(0, 0), 1))
        sketch = doc.objects[sketch.id]
    path = path_sketch(doc, (Segment(Vec2(0, 0), Vec2(0, 20)),))
    body, feature, result = swept(doc, service, sketch, path, parameters=SweepParameters(path_refs(path), transport=transport))
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(pi*(3 if hole else 4)*20, abs=1e-5)
    assert bounds(result.shape) == pytest.approx((-2, -2, 0, 2, 2, 20), abs=1e-5)
    assert result.effects and result.effects[0].generator_id == feature.id
    for face in subshapes(result.shape, "Face"):
        ref = capture(result.shape, face, "Face", result.topology)
        assert ref.labels and not any(":untracked:" in label for label in ref.labels), ref


@pytest.mark.parametrize("closed", [False, True])
def test_sweep_curved_open_and_closed_path_has_exact_tube_volume(closed):
    doc, service = system()
    sketch = profile(doc, circle=(10 if closed else 0, 0, 2))
    curve = Circle(Vec2(0, 0), 10) if closed else Arc(Vec2(10, 0), 10, pi, -pi/2)
    path = path_sketch(doc, (curve,))
    body, feature, result = swept(doc, service, sketch, path)
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx((80 if closed else 20)*pi*pi, abs=1e-4)


def test_sweep_angled_profile_orientation_and_roll_are_explicit():
    from cadastrophe.core.operation_model import PlaneParameters
    from test_parametric_operations import datum
    doc, service = system()
    support = datum(doc, PlaneParameters("inclined", (Ref("plane", plane(doc).id), Ref("axis", values=(0, 0, 0, 1, 0, 0))), angle=30))
    sketch = profile(doc, ((-2, -1), (2, -1), (2, 1), (-2, 1)), support=support)
    path = path_sketch(doc, (Segment(Vec2(0, 0), Vec2(0, 20)),))
    body, feature, result = swept(doc, service, sketch, path)
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(160, abs=1e-5)
    doc.replace_feature(replace(feature, parameters=replace(feature.parameters, orientation="keep")))
    result = evaluate_body(doc, body.id, service.evaluator(doc))
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(160*3**.5/2, abs=1e-5)


def test_sweep_path_edit_naming_undo_redo_and_save_reopen(tmp_path):
    doc, service = system()
    sketch = profile(doc, ((-2, -1), (2, -1), (2, 1), (-2, 1)))
    segment = Segment(Vec2(0, 0), Vec2(0, 20))
    path = path_sketch(doc, (segment,))
    body, feature, result = swept(doc, service, sketch, path)
    assert result.status is ResultStatus.READY, result.message
    refs = tuple(capture(result.shape, f, "Face", result.topology) for f in subshapes(result.shape, "Face"))
    ids = set(doc.objects)
    doc.history.clear()
    commands.replace_entity(doc, path.id, replace(segment, end=Vec2(0, 35)))
    assert body_result(doc, body.id).status is ResultStatus.DIRTY
    rebuilt = evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert rebuilt.status is ResultStatus.READY, rebuilt.message
    assert volume(rebuilt.shape) == pytest.approx(280)
    assert all(not resolve(rebuilt.shape, ref, rebuilt.topology).IsNull() for ref in refs)
    doc.history.undo()
    assert evaluate_body(doc, body.id, service.evaluator(doc), reuse=True).status is ResultStatus.READY
    assert volume(doc.objects[body.id].shape) == pytest.approx(160)
    doc.history.redo()
    assert evaluate_body(doc, body.id, service.evaluator(doc), reuse=True).status is ResultStatus.READY
    filename = tmp_path/"balayage.cada"
    save_document(doc, filename, registry=registry(service))
    loaded = load_document(filename, registry=registry(service))
    assert set(loaded.objects) == ids and loaded.objects[feature.id].parameters == feature.parameters
    assert volume(loaded.objects[body.id].shape) == pytest.approx(280)


@pytest.mark.parametrize("bad", ["discontinuous", "branch", "missing", "self_intersection", "crossing"])
def test_sweep_invalid_path_or_solid_returns_error_without_a_stale_result(bad):
    doc, service = system()
    sketch = profile(doc, circle=(10 if bad == "self_intersection" else 0, 0, 12 if bad == "self_intersection" else 2))
    curves = [Segment(Vec2(0, 0), Vec2(0, 20))]
    if bad == "discontinuous":
        curves.append(Segment(Vec2(1, 21), Vec2(1, 30)))
    elif bad == "branch":
        curves.extend((Segment(Vec2(0, 20), Vec2(10, 20)), Segment(Vec2(0, 20), Vec2(-10, 20))))
    elif bad == "self_intersection":
        curves = [Circle(Vec2(0, 0), 10)]
    elif bad == "crossing":
        points = tuple(Vec2(*p) for p in ((0, 0), (0, 20), (10, 20), (-10, 10), (10, 10)))
        curves = [Segment(a, b) for a, b in zip(points, points[1:])]
    path = path_sketch(doc, curves)
    params = SweepParameters(path_refs(path))
    if bad == "missing":
        params = replace(params, path=(replace(params.path[0], entity_id=str(uuid4())),))
    body, _, result = swept(doc, service, sketch, path, parameters=params)
    assert result.status is ResultStatus.ERROR and result.message
    assert doc.objects[body.id].shape is None


@pytest.mark.parametrize("operation", [Operation.BOSS, Operation.CUT])
def test_sweep_additive_subtractive_effect_and_pattern_never_copy_base(operation):
    from cadastrophe.document import SceneObject
    from plugins.modeling.recipe_session import adopted
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.gp import gp_Pnt
    doc, service = system()
    base = SceneObject("Bloc", BRepPrimAPI_MakeBox(gp_Pnt(-10, -10, 0 if operation is Operation.CUT else -5), 40., 20., 20. if operation is Operation.CUT else 5.).Shape(), (.4, .5, .6))
    doc.add(base)
    sketch = profile(doc, circle=(0, 0, 2))
    path = path_sketch(doc, (Segment(Vec2(0, 0), Vec2(0, 20)),))
    body, feature, result = swept(doc, service, sketch, path, body=adopted(base), operation=operation)
    assert result.status is ResultStatus.READY, result.message
    expected = 40*20*(20 if operation is Operation.CUT else 5)+(-1 if operation is Operation.CUT else 1)*80*pi
    assert volume(result.shape) == pytest.approx(expected, abs=1e-5)
    _, pattern, result = append(doc, service, body=body, operation=Operation.LINEAR_PATTERN,
        replay=ReplayParameters((feature.id,), Ref("axis", values=(0, 0, 0, 1, 0, 0)), count=3, spacing=8))
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(expected+(-1 if operation is Operation.CUT else 1)*160*pi, abs=1e-4)
    assert bounds(result.shape)[3] == pytest.approx(30, abs=1e-6)  # The block was never translated or duplicated.


def test_sweep_roll_reverse_and_multi_edge_corner_modes():
    doc, service = system()
    sketch = profile(doc, ((-2, -1), (2, -1), (2, 1), (-2, 1)))
    path = path_sketch(doc, (Segment(Vec2(0, 0), Vec2(0, 20)),))
    body, feature, result = swept(doc, service, sketch, path)
    assert result.status is ResultStatus.READY, result.message
    doc.replace_feature(replace(feature, parameters=replace(feature.parameters, roll=90, reversed=True)))
    result = evaluate_body(doc, body.id, service.evaluator(doc))
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(160)
    assert bounds(result.shape) == pytest.approx((-1, -2, 0, 1, 2, 20), abs=1e-6)
    # A polyline can turn out of the initial sketch plane and has explicit raccords.
    path2 = path_sketch(doc, (Segment(Vec2(0, 0), Vec2(0, 20)), Segment(Vec2(0, 20), Vec2(20, 20))))
    for mode in ("right", "round"):
        doc.replace_feature(replace(feature, parameters=SweepParameters(path_refs(path2), transition=mode)))
        result = evaluate_body(doc, body.id, service.evaluator(doc))
        assert result.status is ResultStatus.READY, result.message
        assert volume(result.shape) > 200


def test_sweep_codec_and_clipboard_retain_recipe_and_remap_dependencies(tmp_path):
    from cadastrophe.core.clipboard import encode_objects, decode_objects
    from plugins.modeling.operation_codec import SWEEP_CODEC
    doc, service = system()
    sketch = profile(doc, circle=(0, 0, 2))
    line = Segment(Vec2(0, 0), Vec2(0, 20), construction=True)
    path = path_sketch(doc, (line,))
    body, feature, result = swept(doc, service, sketch, path)
    assert result.status is ResultStatus.READY, result.message
    data = SWEEP_CODEC.encode(feature)
    assert SWEEP_CODEC.decode(feature, data) == feature
    batch = decode_objects(encode_objects(doc, (body.id,), registry(service)), registry(service))
    assert not set(batch.document.objects) & set(doc.objects)
    copied = batch.document.objects[batch.roots[0]]
    new_feature = batch.document.objects[copied.features[0]]
    ref = new_feature.parameters.path[0]
    assert ref.object_id != path.id and ref.entity_id == line.id
    commands.replace_entity(batch.document, ref.object_id, replace(line, end=Vec2(0, 30)))
    result = evaluate_body(batch.document, copied.id, service.evaluator(batch.document))
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(120*pi)
    assert volume(doc.objects[body.id].shape) == pytest.approx(80*pi)
    save_document(batch.document, tmp_path/"balayage_copie.cada", registry=registry(service))
    restored = load_document(tmp_path/"balayage_copie.cada", registry=registry(service))
    assert volume(restored.objects[copied.id].shape) == pytest.approx(120*pi)


@pytest.mark.parametrize("damage", ["unknown", "empty", "duplicate", "not_curve", "wrong_mode", "not_finite", "sense", "no_names"])
def test_sweep_codec_rejects_corrupt_recipe(damage):
    from plugins.modeling.operation_codec import SWEEP_CODEC
    doc, service = system()
    sketch = profile(doc, circle=(0, 0, 2))
    path = path_sketch(doc, (Segment(Vec2(0, 0), Vec2(0, 20)),))
    body, feature, result = swept(doc, service, sketch, path)
    assert result.status is ResultStatus.READY
    data = SWEEP_CODEC.encode(feature)
    p = data["parameters"]
    if damage == "unknown":
        p["unknown"] = 1
    elif damage == "empty":
        p["path"] = []
    elif damage == "duplicate":
        p["path"] *= 2
    elif damage == "not_curve":
        p["path"][0]["kind"] = "sketch_point"
    elif damage == "wrong_mode":
        p["transport"] = "magic"
    elif damage == "not_finite":
        p["roll"] = float("nan")
    elif damage == "sense":
        p["reversed"] = 1
    elif damage == "no_names":
        p["path"][0].update(kind="edge", entity_id=None, topology=dict(kind="Edge", labels=[], geometry=[1., 2., 3.]))
    with pytest.raises(ValueError):
        SWEEP_CODEC.decode(feature, data)


def test_sweep_named_body_edge_path_recalculates_producer_and_rejects_missing_and_future(tmp_path):
    from cadastrophe.core.feature_model import ExtrusionParameters
    from cadastrophe.core.topology_refs import TopologyReference
    doc, service = system()
    source_profile = profile(doc, ((0, 0), (20, 0), (20, 20), (0, 20)))
    producer_body, producer, state = append(doc, service, source_profile, parameters=ExtrusionParameters(depth=10))
    edge = next(e for e in subshapes(state.shape, "Edge") if bounds(e) == pytest.approx((0, 0, 0, 0, 0, 10)))
    ref = Ref("edge", producer_body.id, producer.id, capture(state.shape, edge, "Edge", state.topology))
    section = profile(doc, circle=(0, 0, 1))
    body, feature, result = swept(doc, service, section, None, parameters=SweepParameters((ref,)))
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(10*pi)
    doc.history.clear()
    doc.replace_feature(replace(producer, parameters=replace(producer.parameters, depth=25)))
    assert body_result(doc, body.id).status is ResultStatus.DIRTY
    result = evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(25*pi)
    # Referencing this feature's own result is a cycle, rejected before publication.
    before = dict(doc.objects)
    future = replace(ref, object_id=body.id, producer_id=feature.id)
    with pytest.raises(ValueError, match="futur"):
        doc.replace_feature(replace(feature, parameters=replace(feature.parameters, path=(future,))))
    assert dict(doc.objects) == before
    missing = replace(ref, topology=TopologyReference("Edge", ("absent",)))
    doc.replace_feature(replace(feature, parameters=replace(feature.parameters, path=(missing,))))
    result = evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert result.status is ResultStatus.ERROR and "perdue" in result.message
    filename = tmp_path/"balayage_erreur.cada"
    save_document(doc, filename, registry=registry(service))
    loaded = load_document(filename, registry=registry(service))
    assert loaded.objects[body.id].shape is None and body_result(loaded, body.id).status is ResultStatus.ERROR
    doc.history.undo()
    assert evaluate_body(doc, body.id, service.evaluator(doc), reuse=True).status is ResultStatus.READY


def test_sweep_end_face_consumer_follows_path_length_and_blocks_after_lost_curve(tmp_path):
    from cadastrophe.document import SelectionItem
    from cadastrophe.core.reference_geometry import capture_reference
    from cadastrophe.core.operation_model import PlaneParameters
    from cadastrophe.core.feature_objects import ExtrusionFeature
    from cadastrophe.core.feature_model import ExtrusionParameters
    from plugins.modeling.datums import refresh_datums
    from test_parametric_operations import datum
    from test_hollowing import horizontal_face
    from cadastrophe.geometry import Vec3
    doc, service = system()
    sketch = profile(doc, circle=(0, 0, 3))
    line = Segment(Vec2(0, 0), Vec2(0, 20))
    path = path_sketch(doc, (line,))
    body, feature, result = swept(doc, service, sketch, path)
    assert result.status is ResultStatus.READY, result.message
    ref = capture_reference(doc, SelectionItem(body.id, "Face", horizontal_face(result.shape, 20), body.revision))
    support = datum(doc, PlaneParameters("offset", (ref,), distance=0))
    local = support.plane.to_local(Vec3(0, 0, 20))
    hole = profile(doc, support=support, circle=(*local[:2], 1))
    cut = ExtrusionFeature("Trou du balayage", None, body.color, kind=ObjectType.FEATURE, body_id=body.id, operation=Operation.CUT,
        source=SketchInput(hole.id, hole.producer_refs), parameters=ExtrusionParameters(depth=2, reversed=True))
    doc.update_construction((replace(body, features=(*body.features, cut.id)), cut))
    assert evaluate_body(doc, body.id, service.evaluator(doc)).status is ResultStatus.READY
    commands.replace_entity(doc, path.id, replace(line, end=Vec2(0, 30)))
    result = evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert result.status is ResultStatus.READY, result.message
    refresh_datums(doc)
    assert doc.objects[support.id].plane.origin.z == pytest.approx(30)
    assert volume(result.shape) == pytest.approx(268*pi, abs=1e-4)
    filename = tmp_path/"balayage_dependants.cada"
    save_document(doc, filename, registry=registry(service))
    restored = load_document(filename, registry=registry(service))
    assert volume(restored.objects[body.id].shape) == pytest.approx(268*pi, abs=1e-4)
    commands.remove_entity(doc, path.id, line.id)
    result = evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert result.status is ResultStatus.BLOCKED and doc.objects[body.id].shape is None
    assert result_at(doc, BodyStateRef(body.id, feature.id)).status is ResultStatus.ERROR
    doc.history.undo()
    assert evaluate_body(doc, body.id, service.evaluator(doc), reuse=True).status is ResultStatus.READY


def test_sweep_real_three_dimensional_path_and_two_arc_closed_path():
    from cadastrophe.core.operation_model import PlaneParameters
    from test_parametric_operations import datum
    doc, service = system()
    sketch = profile(doc, circle=(0, 0, 1))
    first = path_sketch(doc, (Segment(Vec2(0, 0), Vec2(0, 20)), Segment(Vec2(0, 20), Vec2(20, 20))))
    support = datum(doc, PlaneParameters("offset", (Ref("plane", plane(doc).id),), distance=20))
    second = path_sketch(doc, (Segment(Vec2(20, 0), Vec2(20, 20)),), support)
    body, feature, result = swept(doc, service, sketch, None, parameters=SweepParameters((*path_refs(first), *path_refs(second))))
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(60*pi, abs=1e-4)
    doc2, service2 = system()
    section = profile(doc2, circle=(10, 0, 1))
    path = path_sketch(doc2, (Arc(Vec2(0, 0), 10, 0, pi), Arc(Vec2(0, 0), 10, pi, pi)))
    body, feature, result = swept(doc2, service2, section, path)
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(20*pi*pi, abs=1e-4)


def test_sweep_all_vertical_producer_edges_are_anchored_by_profile_plane():
    from cadastrophe.core.feature_model import ExtrusionParameters
    doc, service = system()
    base_sketch = profile(doc, ((0, 0), (20, 0), (20, 20), (0, 20)))
    base_body, producer, state = append(doc, service, base_sketch, parameters=ExtrusionParameters(depth=10))
    edges = tuple(e for e in subshapes(state.shape, "Edge") if abs(bounds(e)[5]-bounds(e)[2]-10) < 1e-6)
    assert len(edges) == 4
    for edge in edges:
        box = bounds(edge)
        section = profile(doc, circle=(box[0], box[1], 1))
        ref = Ref("edge", base_body.id, producer.id, capture(state.shape, edge, "Edge", state.topology))
        body, feature, result = swept(doc, service, section, None, parameters=SweepParameters((ref,)))
        assert result.status is ResultStatus.READY, result.message
        assert volume(result.shape) == pytest.approx(10*pi)


@pytest.mark.parametrize("transport", ["corrected", "frenet", "fixed"])
def test_sweep_frame_transport_changes_curved_rectangle_volume(transport):
    doc, service = system()
    sketch = profile(doc, ((-2, -1), (2, -1), (2, 1), (-2, 1)))
    path = path_sketch(doc, (Arc(Vec2(10, 0), 10, pi, -pi/4),))
    _, _, result = swept(doc, service, sketch, path, parameters=SweepParameters(path_refs(path), transport=transport))
    assert result.status is ResultStatus.READY, result.message
    expected = 40*2**.5 if transport == "fixed" else 20*pi
    assert volume(result.shape) == pytest.approx(expected, abs=1e-4)
