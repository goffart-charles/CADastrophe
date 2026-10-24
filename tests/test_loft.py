"""Native multi-section effects, correspondence and persistent dependencies."""
from dataclasses import replace
from math import pi
from uuid import uuid4

import pytest

from cadastrophe.document import ObjectType
from cadastrophe.core.feature_model import Operation, SketchInput, ResultStatus
from cadastrophe.core.feature_objects import BodyObject, LoftFeature
from cadastrophe.core.operation_model import GeometryReference as Ref, PlaneParameters, LoftSection, LoftParameters, ReplayParameters
from cadastrophe.core.body_history import evaluate_body, body_result
from cadastrophe.core.native_naming import capture, resolve, subshapes
from cadastrophe.core.persistence import save_document, load_document
from examples.parametric_example import registry
from plugins.sketch import commands
from plugins.sketch.model import Circle, Vec2
from test_parametric_operations import system, profile, plane, datum, append, bounds
from test_primitives import volume


def section_at(doc, z, *, radius=2, points=None, holes=()):
    support = datum(doc, PlaneParameters("offset", (Ref("plane", plane(doc).id),), distance=z)) if z else plane(doc)
    sketch = profile(doc, points, support=support, circle=None if points else (0, 0, radius))
    for x, y, r in holes:
        commands.add_entity(doc, sketch.id, Circle(Vec2(x, y), r))
    return doc.objects[sketch.id]


def section(sketch, *, explicit=False):
    # The helpers create an outer circle/polygon followed by circular holes.
    entities = sketch.sketch.entities
    anchors = tuple(e.id for i, e in enumerate(entities) if i == 0 or isinstance(e, Circle)) if explicit else ()
    return LoftSection(SketchInput(sketch.id, sketch.producer_refs), anchors=anchors, reversed=(False,)*len(anchors))


def lofted(doc, service, sketches, *, parameters=None, body=None, operation=Operation.NEW_BODY):
    from plugins.modeling.recipe_session import adopted
    body = adopted(doc.objects.get(body.id, body)) if body else BodyObject("Corps lissé", None, (.5, .6, .7))
    feature = LoftFeature("Lissage1", None, body.color, kind=ObjectType.FEATURE, body_id=body.id,
        operation=operation, parameters=parameters or LoftParameters(tuple(section(s) for s in sketches)))
    doc.update_construction((replace(body, features=(*body.features, feature.id)), feature))
    result = evaluate_body(doc, body.id, service.evaluator(doc))
    return doc.objects[body.id], feature, result


@pytest.mark.parametrize("mode", ["smooth", "ruled"])
@pytest.mark.parametrize("matching", ["automatic", "explicit"])
@pytest.mark.parametrize("hole", [False, True])
def test_loft_conical_volume_holes_and_named_faces(mode, matching, hole):
    doc, service = system()
    a = section_at(doc, 0, radius=2, holes=((0, 0, 1),) if hole else ())
    b = section_at(doc, 10, radius=4, holes=((0, 0, 2),) if hole else ())
    params = LoftParameters(tuple(section(s, explicit=matching == "explicit") for s in (a, b)), mode, matching)
    body, feature, result = lofted(doc, service, (a, b), parameters=params)
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(10*pi/3*(21 if hole else 28), abs=1e-4)
    assert bounds(result.shape) == pytest.approx((-4, -4, 0, 4, 4, 10), abs=1e-5)
    assert result.effects[0].generator_id == feature.id
    for face in subshapes(result.shape, "Face"):
        ref = capture(result.shape, face, "Face", result.topology)
        assert ref.labels and all(":untracked:" not in name for name in ref.labels), ref


@pytest.mark.parametrize("mode", ["smooth", "ruled"])
def test_loft_three_sections_rebuilds_middle_and_last_sketch_without_mutating_inputs(mode, tmp_path):
    doc, service = system()
    sketches = tuple(section_at(doc, z, radius=r) for z, r in ((0, 2), (5, 3), (10, 4)))
    original = tuple(s.sketch for s in sketches)
    body, feature, result = lofted(doc, service, sketches, parameters=LoftParameters(tuple(section(s) for s in sketches), mode))
    assert result.status is ResultStatus.READY, result.message
    refs = tuple(capture(result.shape, f, "Face", result.topology) for f in subshapes(result.shape, "Face"))
    initial = volume(result.shape)
    assert tuple(doc.objects[s.id].sketch for s in sketches) == original
    doc.history.clear()
    circle = sketches[-1].sketch.entities[0]
    commands.replace_entity(doc, sketches[-1].id, replace(circle, radius=5))
    assert body_result(doc, body.id).status is ResultStatus.DIRTY
    result = evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) > initial
    assert all(not resolve(result.shape, ref, result.topology).IsNull() for ref in refs)
    doc.history.undo()
    result = evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert volume(result.shape) == pytest.approx(initial)
    doc.history.redo()
    result = evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    changed = volume(result.shape)
    middle = sketches[1].sketch.entities[0]
    commands.replace_entity(doc, sketches[1].id, replace(middle, radius=3.5))
    result = evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert result.status is ResultStatus.READY and volume(result.shape) > changed, result.message
    save_document(doc, tmp_path/"lissage.cada", registry=registry(service))
    loaded = load_document(tmp_path/"lissage.cada", registry=registry(service))
    assert set(loaded.objects) == set(doc.objects)
    assert loaded.objects[feature.id].parameters == feature.parameters
    assert volume(loaded.objects[body.id].shape) == pytest.approx(volume(result.shape))


@pytest.mark.parametrize("bad", ["contact", "holes", "anchor", "edge_count"])
def test_loft_invalid_correspondence_has_no_stale_solid(bad):
    doc, service = system()
    a = section_at(doc, 0, radius=4)
    b = section_at(doc, 0 if bad == "contact" else 10, radius=4,
                   holes=((0, 0, 1),) if bad == "holes" else (),
                   points=((-2, -2), (2, -2), (0, 2)) if bad == "edge_count" else None)
    sections = tuple(section(s, explicit=True) for s in (a, b))
    if bad == "anchor":
        sections = (sections[0], replace(sections[1], anchors=(str(uuid4()),)))
    body, _, result = lofted(doc, service, (a, b), parameters=LoftParameters(sections, matching="explicit"))
    assert result.status is ResultStatus.ERROR and result.message
    assert doc.objects[body.id].shape is None


def test_loft_automatic_different_edge_counts_and_explicit_polygon_anchors():
    doc, service = system()
    a = section_at(doc, 0, points=((-3, -3), (3, -3), (3, 3), (-3, 3)))
    b = section_at(doc, 10, points=((-2, -2), (2, -2), (0, 2)))
    body, feature, result = lofted(doc, service, (a, b))
    assert result.status is ResultStatus.READY, result.message
    assert all(":untracked:" not in label for label, _ in result.topology.faces)
    c = section_at(doc, 10, points=((-3, -3), (3, -3), (3, 3), (-3, 3)))
    sections = tuple(section(s, explicit=True) for s in (a, c))
    doc.replace_feature(replace(feature, parameters=LoftParameters(sections, matching="explicit")))
    straight = evaluate_body(doc, body.id, service.evaluator(doc))
    assert straight.status is ResultStatus.READY, straight.message
    assert volume(straight.shape) == pytest.approx(360)
    sections = (sections[0], replace(sections[1], anchors=(c.sketch.entities[1].id,)))
    doc.replace_feature(replace(feature, parameters=LoftParameters(sections, matching="explicit")))
    twisted = evaluate_body(doc, body.id, service.evaluator(doc))
    assert twisted.status is ResultStatus.READY, twisted.message
    assert volume(twisted.shape) < volume(straight.shape)


@pytest.mark.parametrize("operation", [Operation.BOSS, Operation.CUT])
def test_loft_additive_subtractive_pattern_transforms_only_generated_volume(operation):
    from cadastrophe.document import SceneObject
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.gp import gp_Pnt
    doc, service = system()
    base = SceneObject("Bloc", BRepPrimAPI_MakeBox(gp_Pnt(-10, -10, 0 if operation is Operation.CUT else -5), 40., 20., 20. if operation is Operation.CUT else 5.).Shape(), (.4, .5, .6))
    doc.add(base)
    sketches = (section_at(doc, 0), section_at(doc, 20))
    body, feature, result = lofted(doc, service, sketches, body=base, operation=operation)
    assert result.status is ResultStatus.READY, result.message
    expected = 40*20*(20 if operation is Operation.CUT else 5)+(-1 if operation is Operation.CUT else 1)*80*pi
    assert volume(result.shape) == pytest.approx(expected, abs=1e-5)
    _, _, result = append(doc, service, body=body, operation=Operation.LINEAR_PATTERN,
        replay=ReplayParameters((feature.id,), Ref("axis", values=(0, 0, 0, 1, 0, 0)), count=3, spacing=8))
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(expected+(-1 if operation is Operation.CUT else 1)*160*pi, abs=1e-4)
    assert bounds(result.shape)[3] == pytest.approx(30, abs=1e-6)


def test_loft_clipboard_remaps_all_section_dependencies_retains_local_anchors(tmp_path):
    from cadastrophe.core.clipboard import encode_objects, decode_objects
    doc, service = system()
    sketches = (section_at(doc, 0), section_at(doc, 10))
    params = LoftParameters(tuple(section(s, explicit=True) for s in sketches), matching="explicit")
    body, feature, result = lofted(doc, service, sketches, parameters=params)
    assert result.status is ResultStatus.READY, result.message
    batch = decode_objects(encode_objects(doc, (body.id,), registry(service)), registry(service))
    assert not set(batch.document.objects) & set(doc.objects)
    copied = batch.document.objects[batch.roots[0]]
    new = batch.document.objects[copied.features[0]]
    for old, fresh in zip(params.sections, new.parameters.sections):
        assert old.source.sketch_id != fresh.source.sketch_id
        assert old.anchors == fresh.anchors
    last = batch.document.objects[new.parameters.sections[-1].source.sketch_id]
    commands.replace_entity(batch.document, last.id, replace(last.sketch.entities[0], radius=4))
    rebuilt = evaluate_body(batch.document, copied.id, service.evaluator(batch.document))
    assert rebuilt.status is ResultStatus.READY, rebuilt.message
    assert volume(rebuilt.shape) > volume(result.shape)


@pytest.mark.parametrize("bad", ["unknown", "few", "duplicate", "matching", "anchor", "reversed", "section"])
def test_loft_codec_rejects_corrupt_recipe(bad):
    from plugins.modeling.operation_codec import LOFT_CODEC
    doc, service = system()
    sketches = (section_at(doc, 0), section_at(doc, 10))
    _, feature, result = lofted(doc, service, sketches)
    assert result.status is ResultStatus.READY
    data = LOFT_CODEC.encode(feature)
    assert LOFT_CODEC.decode(feature, data) == feature
    p = data["parameters"]
    if bad == "unknown": p["unexpected"] = True
    elif bad == "few": p["sections"] = p["sections"][:1]
    elif bad == "duplicate": p["sections"][1] = p["sections"][0]
    elif bad == "matching": p["matching"] = "guess"
    elif bad == "anchor": p["sections"][0]["anchors"] = ["bad"]
    elif bad == "reversed": p["sections"][0]["reversed"] = [1]
    elif bad == "section": p["sections"][0]["surprise"] = 1
    with pytest.raises(ValueError):
        LOFT_CODEC.decode(feature, data)


@pytest.mark.parametrize("mode", ["smooth", "ruled"])
def test_loft_explicit_reverse_both_sections_preserves_volume_and_input_edges(mode):
    doc, service = system()
    points = ((-3, -2), (3, -2), (3, 2), (-3, 2))
    sketches = (section_at(doc, 0, points=points), section_at(doc, 10, points=points))
    sections = tuple(replace(section(s, explicit=True), reversed=(True,)) for s in sketches)
    _, _, result = lofted(doc, service, sketches, parameters=LoftParameters(sections, mode, "explicit"))
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(240)
    assert all(doc.objects[s.id].sketch == s.sketch for s in sketches)


def test_loft_two_holes_require_explicit_pairing_and_refuse_crossed_cavities():
    doc, service = system()
    sketches = tuple(section_at(doc, z, radius=8, holes=((-3, 0, 1), (3, 0, 1))) for z in (0, 10))
    body, feature, result = lofted(doc, service, sketches)
    assert result.status is ResultStatus.ERROR and "correspondance" in result.message
    sections = tuple(section(s, explicit=True) for s in sketches)
    doc.replace_feature(replace(feature, parameters=LoftParameters(sections)))
    result = evaluate_body(doc, body.id, service.evaluator(doc))
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(620*pi, abs=1e-4)
    last = sections[-1]
    swapped = replace(last, anchors=(last.anchors[0], last.anchors[2], last.anchors[1]))
    doc.replace_feature(replace(feature, parameters=LoftParameters((sections[0], swapped))))
    result = evaluate_body(doc, body.id, service.evaluator(doc))
    assert result.status is ResultStatus.ERROR and doc.objects[body.id].shape is None


def test_loft_end_cap_consumer_follows_section_plane_and_blocks_after_bad_anchor(tmp_path):
    from cadastrophe.document import SelectionItem
    from cadastrophe.geometry import Vec3
    from cadastrophe.core.reference_geometry import capture_reference
    from cadastrophe.core.feature_model import ExtrusionParameters, BodyStateRef
    from cadastrophe.core.feature_objects import ExtrusionFeature
    from cadastrophe.core.body_history import result_at
    from plugins.modeling.datums import refresh_datums
    from test_hollowing import horizontal_face
    doc, service = system()
    sketches = (section_at(doc, 0, radius=3), section_at(doc, 10, radius=3))
    body, feature, result = lofted(doc, service, sketches, parameters=LoftParameters(tuple(section(s, explicit=True) for s in sketches)))
    ref = capture_reference(doc, SelectionItem(body.id, "Face", horizontal_face(result.shape, 10), body.revision))
    support = datum(doc, PlaneParameters("offset", (ref,), distance=0))
    local = support.plane.to_local(Vec3(0, 0, 10))
    hole = profile(doc, support=support, circle=(*local[:2], 1))
    cut = ExtrusionFeature("Trou", None, body.color, kind=ObjectType.FEATURE, body_id=body.id, operation=Operation.CUT,
        source=SketchInput(hole.id, hole.producer_refs), parameters=ExtrusionParameters(depth=2, reversed=True))
    doc.update_construction((replace(body, features=(*body.features, cut.id)), cut))
    assert evaluate_body(doc, body.id, service.evaluator(doc)).status is ResultStatus.READY
    top = doc.objects[sketches[-1].sketch.support.plane_id]
    doc.replace_feature(replace(top, parameters=replace(top.parameters, distance=20)))
    result = evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert result.status is ResultStatus.READY, result.message
    refresh_datums(doc)
    assert doc.objects[support.id].plane.origin.z == pytest.approx(20)
    assert volume(result.shape) == pytest.approx(178*pi, abs=1e-4)
    save_document(doc, tmp_path/"lissage_dependants.cada", registry=registry(service))
    loaded = load_document(tmp_path/"lissage_dependants.cada", registry=registry(service))
    assert volume(loaded.objects[body.id].shape) == pytest.approx(178*pi, abs=1e-4)
    sections = feature.parameters.sections
    bad = replace(sections[-1], anchors=(str(uuid4()),))
    doc.replace_feature(replace(feature, parameters=replace(feature.parameters, sections=(sections[0], bad))))
    result = evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert result.status is ResultStatus.BLOCKED and doc.objects[body.id].shape is None
    assert result_at(doc, BodyStateRef(body.id, feature.id)).status is ResultStatus.ERROR
    doc.history.undo()
    assert evaluate_body(doc, body.id, service.evaluator(doc), reuse=True).status is ResultStatus.READY


def test_loft_each_section_keeps_its_producer_rebuilds_and_repairs_only_its_support(tmp_path):
    from cadastrophe.core.feature_model import ExtrusionParameters, BodyStateRef
    from cadastrophe.geometry import Vec3
    from plugins.sketch.supports import from_face_state
    from plugins.sketch.support_repair import reassociation_objects
    from test_hollowing import horizontal_face
    doc, service = system()
    block = profile(doc, ((-10, -10), (10, -10), (10, 10), (-10, 10)))
    producer_body, producer, state = append(doc, service, block, parameters=ExtrusionParameters(depth=10))
    first = section_at(doc, 0)
    ref = BodyStateRef(producer_body.id, producer.id)
    support = from_face_state(doc, producer_body.id, horizontal_face(state.shape, 10), producer.id)
    top = commands.create_sketch(doc, support)
    uv = support.frame.to_local(Vec3(0, 0, 10))
    commands.add_entity(doc, top.id, Circle(Vec2(*uv[:2]), 2))
    top = doc.objects[top.id]
    body, feature, result = lofted(doc, service, (first, top))
    assert result.status is ResultStatus.READY, result.message
    assert feature.parameters.sections[-1].source.supports == (ref,)
    doc.replace_feature(replace(producer, parameters=replace(producer.parameters, depth=20)))
    assert body_result(doc, body.id).status is ResultStatus.DIRTY
    result = evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(80*pi, abs=1e-4)
    assert bounds(result.shape)[5] == pytest.approx(20)
    # Reassociate the last section to a datum; retain every other recipe input.
    target = datum(doc, PlaneParameters("offset", (Ref("plane", plane(doc).id),), distance=30))
    from plugins.sketch.model import PlaneSupport
    replacements = reassociation_objects(doc, top.id, PlaneSupport(target.id, target.plane, target.revision))
    fresh = next(o for o in replacements if o.id == feature.id)
    assert fresh.parameters.sections[0] == feature.parameters.sections[0]
    assert fresh.parameters.sections[-1].source.supports == ()
    doc.update_construction(replacements)
    result = evaluate_body(doc, body.id, service.evaluator(doc))
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(120*pi, abs=1e-4)
    save_document(doc, tmp_path/"lissage_supports.cada", registry=registry(service))
    loaded = load_document(tmp_path/"lissage_supports.cada", registry=registry(service))
    assert volume(loaded.objects[body.id].shape) == pytest.approx(120*pi, abs=1e-4)


def test_loft_native_compatibility_does_not_split_or_reorient_input_wires():
    from cadastrophe.core.loft import loft_faces
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.TopoDS import topods
    doc, service = system()
    sketches = (section_at(doc, 0, points=((-3, -3), (3, -3), (3, 3), (-3, 3))),
                section_at(doc, 10, points=((-2, -2), (2, -2), (0, 2))))
    faces = tuple(service.region_faces(doc, s.id).regions[0].face for s in sketches)
    edges = tuple(subshapes(f, "Edge") for f in faces)
    def samples(groups):
        result = []
        for group in groups:
            for edge in group:
                curve = BRepAdaptor_Curve(topods.Edge(edge))
                result.append((edge.Orientation(), tuple(curve.Value(curve.FirstParameter()+(curve.LastParameter()-curve.FirstParameter())*t).Coord() for t in (0., .5, 1.))))
        return tuple(result)
    before = samples(edges)
    shape = loft_faces(faces, tuple(service._profiles.source_edges(doc, s.id) for s in sketches),
                       LoftParameters(tuple(section(s) for s in sketches)))
    assert volume(shape) > 0
    assert tuple(len(subshapes(f, "Edge")) for f in faces) == (4, 3)
    assert samples(edges) == before


def test_loft_deletion_retains_and_reveals_all_section_sketches():
    from cadastrophe.core.body_history import without_features, sketch_history_display
    doc, service = system()
    sketches = (section_at(doc, 0), section_at(doc, 10))
    body, feature, result = lofted(doc, service, sketches)
    assert result.status is ResultStatus.READY
    for sketch in sketches:
        doc.set_visible(sketch.id, False)
        states, _ = sketch_history_display(doc, sketch.id)
        assert states[body.id] is None
    remaining = without_features(doc.objects, (feature.id,))
    assert body.id not in remaining and feature.id not in remaining
    assert all(remaining[s.id].visible for s in sketches)


def test_loft_self_producer_section_is_rejected_atomically():
    from cadastrophe.document import SelectionItem
    from cadastrophe.core.reference_geometry import capture_reference
    from test_hollowing import horizontal_face
    doc, service = system()
    sketches = (section_at(doc, 0), section_at(doc, 10))
    body, feature, result = lofted(doc, service, sketches)
    ref = capture_reference(doc, SelectionItem(body.id, "Face", horizontal_face(result.shape, 10), body.revision))
    support = datum(doc, PlaneParameters("offset", (ref,), distance=10))
    future = profile(doc, support=support, circle=(0, 0, 2))
    sections = (feature.parameters.sections[0], section(future))
    before = dict(doc.objects)
    with pytest.raises(ValueError, match="futur"):
        doc.replace_feature(replace(feature, parameters=replace(feature.parameters, sections=sections)))
    assert dict(doc.objects) == before
