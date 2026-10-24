"""Native neutral-plane draft, named references and editable persistent recipes."""
from dataclasses import replace
from math import pi, radians, tan, cos, sin
from uuid import uuid4

import pytest
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeSphere

from cadastrophe.document import ObjectType
from cadastrophe.geometry import Vec3, WorkPlane
from cadastrophe.core.feature_model import Operation, BodyStateRef, ResultStatus, EvaluationResult, ExtrusionParameters
from cadastrophe.core.feature_objects import DraftFeature, ReplayFeature
from cadastrophe.core.operation_model import DraftParameters, GeometryReference as Ref, ReplayParameters, PlaneParameters
from cadastrophe.core.native_naming import capture, resolve, subshapes, static_topology, NamingTrace
from cadastrophe.core.body_history import evaluate_body, body_result, result_at, validate_graph
from cadastrophe.core.operation_effects import checked_solids
from cadastrophe.core.persistence import save_document, load_document
from plugins.modeling.draft_geometry import apply_draft
from examples.parametric_example import registry
from test_primitives import volume
from test_parametric_operations import system, profile, append, plane, datum, bounds


def side_face(shape, x=30):
    faces = [f for f in subshapes(shape, "Face") if abs(bounds(f)[0]-x) < 1e-6 and abs(bounds(f)[3]-x) < 1e-6]
    assert len(faces) == 1
    return faces[0]


def parameters(shape, faces=None, *, body_id=None, producer_id=None, topology=None, neutral=None, angle=5, reversed=False):
    topology = topology or static_topology(shape)
    body_id = body_id or str(uuid4())
    return DraftParameters(tuple(Ref("face", body_id, producer_id, capture(shape, f, "Face", topology))
                                 for f in (faces if faces is not None else (side_face(shape),))),
        neutral or Ref("plane", str(uuid4())), Ref("axis", values=(0, 0, 0, 0, 0, 1)), angle, reversed)


def neutral_plane(z=0):
    return WorkPlane("Neutre", Vec3(0, 0, z), Vec3(1, 0, 0), Vec3(0, 1, 0))


@pytest.mark.parametrize("reversed", [False, True])
@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("height", [0, 4, 10])
def test_draft_planar_face_keeps_neutral_section_and_changes_real_volume(reversed, direction, height):
    source = BRepPrimAPI_MakeBox(30., 20., 10.).Shape()
    p = parameters(source, reversed=reversed)
    result = apply_draft(source, p, neutral_plane(height), Vec3(0, 0, direction))
    slope = tan(radians(p.angle)) * (-1 if reversed else 1) * direction
    assert len(checked_solids(result)) == 1
    assert volume(result) == pytest.approx(20*10*(30+(height-5)*slope), abs=1e-5)
    assert volume(source) == pytest.approx(6000)
    # Exact vertices on the same neutral section stay at x=30.
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Section
    from cadastrophe.core.reference_geometry import plane_shape
    section = BRepAlgoAPI_Section(result, plane_shape(neutral_plane(height), 100))
    section.Build()
    assert section.IsDone()
    assert bounds(section.Shape())[3] == pytest.approx(30, abs=1e-6)


@pytest.mark.parametrize("reverse", [False, True])
def test_draft_multiple_faces_and_cylinder_are_real_tapered_solids(reverse):
    shape = BRepPrimAPI_MakeBox(30., 20., 10.).Shape()
    faces = tuple(f for f in subshapes(shape, "Face") if bounds(f)[5]-bounds(f)[2] > 9)
    assert len(faces) == 4
    p = parameters(shape, faces, reversed=reverse)
    result = apply_draft(shape, p, neutral_plane(), Vec3(0, 0, 1))
    slope = tan(radians(p.angle))*(-1 if reverse else 1)
    assert volume(result) == pytest.approx(6000-5000*slope+4000/3*slope*slope, abs=1e-5)
    cylinder = BRepPrimAPI_MakeCylinder(10., 20.).Shape()
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.GeomAbs import GeomAbs_Cylinder
    from OCC.Core.TopoDS import topods
    lateral, = (f for f in subshapes(cylinder, "Face") if BRepAdaptor_Surface(topods.Face(f)).GetType() == GeomAbs_Cylinder)
    result = apply_draft(cylinder, parameters(cylinder, (lateral,), reversed=reverse), neutral_plane(), Vec3(0, 0, 1))
    assert len(checked_solids(result)) == 1
    assert volume(result) == pytest.approx(pi*20*(100-200*slope+400/3*slope*slope), abs=1e-4)


def test_draft_conical_face_and_rotated_world_frame():
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeCone
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.GeomAbs import GeomAbs_Cone
    from OCC.Core.TopoDS import topods
    cone = BRepPrimAPI_MakeCone(10., 8., 20.).Shape()
    lateral, = (f for f in subshapes(cone, "Face") if BRepAdaptor_Surface(topods.Face(f)).GetType() == GeomAbs_Cone)
    result = apply_draft(cone, parameters(cone, (lateral,)), neutral_plane(), Vec3(0, 0, 1))
    # The conical draft defines the cone's resulting semi-angle.
    slope = tan(radians(5))
    assert volume(result) == pytest.approx(pi*20*(100-200*slope+400/3*slope*slope), abs=1e-4)
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCC.Core.gp import gp_Trsf
    source = BRepPrimAPI_MakeBox(30., 20., 10.).Shape()
    transform = gp_Trsf()
    transform.SetValues(0., 0., 1., 50., 1., 0., 0., 20., 0., 1., 0., -30.)
    builder = BRepBuilderAPI_Transform(source, transform, True)
    rotated = builder.Shape()
    face = builder.ModifiedShape(side_face(source))
    neutral = WorkPlane("Neutre tourné", Vec3(50, 20, -30), Vec3(0, 1, 0), Vec3(0, 0, 1))
    result = apply_draft(rotated, parameters(rotated, (face,)), neutral, Vec3(1, 0, 0))
    assert volume(result) == pytest.approx(6000-1000*tan(radians(5)), abs=1e-5)


def test_draft_tangential_propagation_accepts_repeated_group_selections_and_tracks_faces():
    from OCC.Core.BRepFilletAPI import BRepFilletAPI_MakeFillet
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.GeomAbs import GeomAbs_Cylinder
    from OCC.Core.TopoDS import topods
    box = BRepPrimAPI_MakeBox(30., 20., 10.).Shape()
    edge = next(e for e in subshapes(box, "Edge") if bounds(e) == pytest.approx((30, 20, 0, 30, 20, 10)))
    maker = BRepFilletAPI_MakeFillet(box)
    maker.Add(2., topods.Edge(edge))
    maker.Build()
    assert maker.IsDone()
    source = maker.Shape()
    curved, = (f for f in subshapes(source, "Face") if BRepAdaptor_Surface(topods.Face(f)).GetType() == GeomAbs_Cylinder)
    planar = side_face(source)
    first = apply_draft(source, parameters(source, (planar,)), neutral_plane(), Vec3(0, 0, 1))
    topology = static_topology(source)
    trace = NamingTrace(str(uuid4()), EvaluationResult(ResultStatus.READY, source, topology=topology))
    both = apply_draft(source, parameters(source, (curved, planar)), neutral_plane(), Vec3(0, 0, 1), topology=topology, trace=trace)
    assert len(checked_solids(both)) == 1
    assert volume(both) == pytest.approx(volume(first), abs=1e-5)
    assert volume(both) < volume(source)
    assert all(not any(":untracked:" in label for label in trace.topology.labels(f, "Face")) for f in subshapes(both, "Face"))


def test_draft_rejects_multiple_solids_and_ambiguous_face_names():
    from OCC.Core.gp import gp_Pnt
    from cadastrophe.core.operation_effects import compound
    from cadastrophe.core.native_naming import NamedTopology
    from cadastrophe.core.topology_refs import TopologyReference
    source = BRepPrimAPI_MakeBox(30., 20., 10.).Shape()
    p = parameters(source)
    other = BRepPrimAPI_MakeBox(gp_Pnt(50, 0, 0), 30., 20., 10.).Shape()
    with pytest.raises(ValueError, match="un seul solide"):
        apply_draft(compound((source, other)), p, neutral_plane(), Vec3(0, 0, 1))
    topology = NamedTopology(tuple(("ambiguous", face) for face in subshapes(source, "Face")))
    ref = replace(p.faces[0], topology=TopologyReference("Face", ("ambiguous",)))
    with pytest.raises(ValueError, match="ambiguë"):
        apply_draft(source, replace(p, faces=(ref,)), neutral_plane(), Vec3(0, 0, 1), topology=topology)


def test_draft_direction_sketch_dependency_invalidates_and_undo_restores():
    from plugins.sketch import commands
    from plugins.sketch.model import Segment, Vec2
    doc, service, body_id, _, draft = draft_chain()
    sketch = commands.create_sketch(doc, plane(doc, "XZ").id)
    line = Segment(Vec2(0, 0), Vec2(0, 10), construction=True)
    commands.add_entity(doc, sketch.id, line)
    doc.replace_feature(replace(draft, parameters=replace(draft.parameters, direction=Ref("sketch_line", sketch.id, entity_id=line.id))))
    assert evaluate_body(doc, body_id, service.evaluator(doc)).status is ResultStatus.READY
    expected = volume(doc.objects[body_id].shape)
    commands.replace_entity(doc, sketch.id, replace(line, end=Vec2(10, 0)))
    assert body_result(doc, body_id).status is ResultStatus.DIRTY
    result = evaluate_body(doc, body_id, service.evaluator(doc), reuse=True)
    assert result.status is ResultStatus.ERROR and "parallèle" in result.message
    doc.history.undo()
    assert evaluate_body(doc, body_id, service.evaluator(doc), reuse=True).status is ResultStatus.READY
    assert volume(doc.objects[body_id].shape) == pytest.approx(expected)


@pytest.mark.parametrize("angle", [0, -1, 90, 180, True, float("nan"), float("inf")])
def test_draft_rejects_invalid_angles(angle):
    shape = BRepPrimAPI_MakeBox(30., 20., 10.).Shape()
    with pytest.raises(ValueError):
        parameters(shape, angle=angle)


def test_draft_rejects_empty_duplicate_missing_and_unsupported_faces_without_mutation():
    source = BRepPrimAPI_MakeBox(30., 20., 10.).Shape()
    p = parameters(source)
    for changes in (dict(faces=()), dict(faces=(p.faces[0],)*2), dict(reversed=1), dict(neutral=p.direction),
                    dict(direction=Ref("point", values=(0, 0, 0)))):
        with pytest.raises(ValueError):
            replace(p, **changes)
    with pytest.raises(ValueError, match="parallèle"):
        apply_draft(source, p, neutral_plane(), Vec3(1, 0, 0))
    from cadastrophe.core.topology_refs import TopologyReference
    missing = replace(p.faces[0], topology=TopologyReference("Face", ("absent",)))
    with pytest.raises(ValueError, match="perdue"):
        apply_draft(source, replace(p, faces=(missing,)), neutral_plane(), Vec3(0, 0, 1))
    sphere = BRepPrimAPI_MakeSphere(10.).Shape()
    with pytest.raises(ValueError, match="planes, cylindriques ou coniques"):
        apply_draft(sphere, parameters(sphere, subshapes(sphere, "Face")), neutral_plane(), Vec3(0, 0, 1))
    with pytest.raises(ValueError, match="Dépouille impossible"):
        apply_draft(source, replace(p, angle=89), neutral_plane(), Vec3(0, 0, 1))
    assert volume(source) == pytest.approx(6000)


def draft_chain():
    doc, service = system()
    sketch = profile(doc, ((0, 0), (30, 0), (30, 20), (0, 20)))
    body, base, _ = append(doc, service, sketch, parameters=ExtrusionParameters(depth=10))
    state = body_result(doc, body.id)
    p = parameters(state.shape, body_id=body.id, producer_id=base.id, topology=state.topology,
                   neutral=Ref("plane", plane(doc).id))
    draft = DraftFeature("Dépouille1", None, body.color, kind=ObjectType.FEATURE, body_id=body.id,
                         operation=Operation.DRAFT, input_state=BodyStateRef(body.id, base.id), parameters=p)
    doc.update_construction((replace(body, features=(*body.features, draft.id)), draft))
    result = evaluate_body(doc, body.id, service.evaluator(doc))
    assert result.status is ResultStatus.READY, result.message
    return doc, service, body.id, base, draft


def test_draft_rebuild_undo_redo_naming_and_roundtrip(tmp_path):
    doc, service, body_id, base, draft = draft_chain()
    original = volume(doc.objects[body_id].shape)
    ids, order = set(doc.objects), doc.objects[body_id].features
    state = body_result(doc, body_id)
    refs = tuple(capture(state.shape, f, "Face", state.topology) for f in subshapes(state.shape, "Face"))
    assert all(r.labels and all(":untracked:" not in label for label in r.labels) for r in refs)
    doc.history.clear()
    doc.replace_feature(replace(base, parameters=replace(base.parameters, depth=18)))
    rebuilt = evaluate_body(doc, body_id, service.evaluator(doc), reuse=True)
    assert rebuilt.status is ResultStatus.READY, rebuilt.message
    assert volume(rebuilt.shape) == pytest.approx(30*20*18-20*18**2*tan(radians(5))/2)
    assert all(not resolve(rebuilt.shape, r, rebuilt.topology).IsNull() for r in refs)
    doc.history.undo()
    assert evaluate_body(doc, body_id, service.evaluator(doc), reuse=True).status is ResultStatus.READY
    assert volume(doc.objects[body_id].shape) == pytest.approx(original)
    doc.history.redo()
    assert evaluate_body(doc, body_id, service.evaluator(doc), reuse=True).status is ResultStatus.READY
    path = tmp_path/"depouille.cada"
    save_document(doc, path, registry=registry(service))
    restored = load_document(path, registry=registry(service))
    assert set(restored.objects) == ids and restored.objects[body_id].features == order
    assert restored.objects[draft.id].parameters == draft.parameters
    assert volume(restored.objects[body_id].shape) == pytest.approx(volume(doc.objects[body_id].shape))


def test_draft_rejects_wrong_producer_future_cycle_and_pattern_source_atomically():
    doc, service, body_id, base, draft = draft_chain()
    before = dict(doc.objects)
    with pytest.raises(ValueError, match="immédiatement"):
        doc.update_construction((replace(draft, input_state=BodyStateRef(body_id)),))
    from cadastrophe.core.reference_objects import DatumPlaneObject
    from cadastrophe.core.reference_geometry import plane_shape
    future_face = replace(draft.parameters.faces[0], producer_id=draft.id)
    cyclic_plane = DatumPlaneObject("Plan cyclique", plane_shape(neutral_plane()), (.5, .5, .5), plane=neutral_plane(),
        parameters=PlaneParameters("offset", (future_face,), distance=0))
    with pytest.raises(ValueError, match="futur"):
        doc.update_construction((cyclic_plane, replace(draft, parameters=replace(draft.parameters, neutral=Ref("plane", cyclic_plane.id)))))
    replay = ReplayFeature("Motif refusé", None, draft.color, kind=ObjectType.FEATURE, body_id=body_id,
        operation=Operation.LINEAR_PATTERN, parameters=ReplayParameters((draft.id,), Ref("axis", values=(0, 0, 0, 1, 0, 0))))
    with pytest.raises(ValueError, match="rejouables"):
        doc.update_construction((replace(doc.objects[body_id], features=(*doc.objects[body_id].features, replay.id)), replay))
    assert dict(doc.objects) == before and not draft.replayable


def test_draft_neutral_datum_and_direction_are_recomputed_dependencies():
    doc, service, body_id, base, draft = draft_chain()
    support = datum(doc, PlaneParameters("offset", (Ref("plane", plane(doc).id),), distance=2))
    draft = replace(draft, parameters=replace(draft.parameters, neutral=Ref("plane", support.id)))
    doc.replace_feature(draft)
    assert evaluate_body(doc, body_id, service.evaluator(doc), reuse=True).status is ResultStatus.READY
    slope = tan(radians(draft.parameters.angle))
    assert volume(doc.objects[body_id].shape) == pytest.approx(6000-600*slope)
    doc.replace_feature(replace(support, parameters=replace(support.parameters, distance=8)))
    assert body_result(doc, body_id).status is ResultStatus.DIRTY
    assert evaluate_body(doc, body_id, service.evaluator(doc), reuse=True).status is ResultStatus.READY
    assert volume(doc.objects[body_id].shape) == pytest.approx(6000+600*slope)
    # An edge on the exact upstream prefix is a persistent direction as well.
    state = result_at(doc, BodyStateRef(body_id, base.id))
    edge = next(e for e in subshapes(state.shape, "Edge") if bounds(e)[5]-bounds(e)[2] > 9)
    direction = Ref("edge", body_id, base.id, capture(state.shape, edge, "Edge", state.topology))
    doc.replace_feature(replace(draft, parameters=replace(draft.parameters, direction=direction)))
    assert evaluate_body(doc, body_id, service.evaluator(doc), reuse=True).status is ResultStatus.READY
    assert volume(doc.objects[body_id].shape) == pytest.approx(6000+600*slope)


def test_draft_downstream_face_support_rebuilds_and_blocks_on_failure(tmp_path):
    from cadastrophe.document import SelectionItem
    from cadastrophe.core.reference_geometry import capture_reference, resolve_plane
    from cadastrophe.core.feature_objects import ExtrusionFeature
    from cadastrophe.core.feature_model import SketchInput
    from plugins.modeling.datums import refresh_datums
    doc, service, body_id, base, draft = draft_chain()
    state = body_result(doc, body_id)
    inclined = resolve(state.shape, draft.parameters.faces[0].topology, state.topology)
    body = doc.objects[body_id]
    ref = capture_reference(doc, SelectionItem(body_id, "Face", inclined, body.revision))
    support = datum(doc, PlaneParameters("offset", (ref,), distance=0))
    point = support.plane.to_local(Vec3(30-5*tan(radians(5)), 10, 5))
    sketch = profile(doc, support=support, circle=(*point[:2], .5))
    cut = ExtrusionFeature("Trou sur face dépouillée", None, body.color, kind=ObjectType.FEATURE, body_id=body_id,
        operation=Operation.CUT, source=SketchInput(sketch.id, sketch.producer_refs), parameters=ExtrusionParameters(depth=1, reversed=True))
    doc.update_construction((replace(body, features=(*body.features, cut.id)), cut))
    assert evaluate_body(doc, body_id, service.evaluator(doc)).status is ResultStatus.READY
    draft_volume = volume(result_at(doc, BodyStateRef(body_id, draft.id)).shape)
    assert volume(doc.objects[body_id].shape) == pytest.approx(draft_volume-pi*.25, abs=1e-5)
    doc.history.clear()
    doc.replace_feature(replace(draft, parameters=replace(draft.parameters, angle=8)))
    rebuilt = evaluate_body(doc, body_id, service.evaluator(doc), reuse=True)
    assert rebuilt.status is ResultStatus.READY, rebuilt.message
    refresh_datums(doc)
    frame = doc.objects[support.id].plane
    assert abs(frame.normal.x) == pytest.approx(cos(radians(8)))
    assert abs(frame.normal.z) == pytest.approx(sin(radians(8)))
    expected = 6000-1000*tan(radians(8))-pi*.25
    assert volume(rebuilt.shape) == pytest.approx(expected, abs=1e-5)
    path = tmp_path/"depouille_support.cada"
    save_document(doc, path, registry=registry(service))
    loaded = load_document(path, registry=registry(service))
    assert volume(loaded.objects[body_id].shape) == pytest.approx(expected, abs=1e-5)
    assert loaded.objects[sketch.id].sketch == doc.objects[sketch.id].sketch
    # A lost named face is never replaced by a neighbouring face.
    from cadastrophe.core.topology_refs import TopologyReference
    missing = replace(draft.parameters.faces[0], topology=TopologyReference("Face", ("absent",)))
    doc.replace_feature(replace(draft, parameters=replace(draft.parameters, faces=(missing,))))
    result = evaluate_body(doc, body_id, service.evaluator(doc), reuse=True)
    assert result.status is ResultStatus.BLOCKED and doc.objects[body_id].shape is None
    assert result_at(doc, BodyStateRef(body_id, draft.id)).status is ResultStatus.ERROR
    assert "perdue" in result_at(doc, BodyStateRef(body_id, draft.id)).message
    error_path = tmp_path/"depouille_erreur.cada"
    save_document(doc, error_path, registry=registry(service))
    failed = load_document(error_path, registry=registry(service))
    assert failed.objects[body_id].shape is None and body_result(failed, body_id).status is ResultStatus.BLOCKED
    doc.history.undo()
    assert evaluate_body(doc, body_id, service.evaluator(doc), reuse=True).status is ResultStatus.READY
    assert volume(doc.objects[body_id].shape) == pytest.approx(expected, abs=1e-5)


@pytest.mark.parametrize("damage", ["unknown", "empty", "duplicate", "no_names", "wrong_sense", "angle", "direction",
                                  "neutral", "body", "producer", "operation"])
def test_draft_codec_rejects_corrupt_recipes(damage):
    from plugins.modeling.operation_codec import DRAFT_CODEC
    doc, _, _, _, draft = draft_chain()
    data = DRAFT_CODEC.encode(draft)
    assert DRAFT_CODEC.decode(draft, data) == draft
    p = data["parameters"]
    if damage == "unknown":
        p["unknown"] = 1
    elif damage == "empty":
        p["faces"] = []
    elif damage == "duplicate":
        p["faces"] *= 2
    elif damage == "no_names":
        p["faces"][0]["topology"]["labels"] = []
    elif damage == "wrong_sense":
        p["reversed"] = 1
    elif damage == "angle":
        p["angle"] = float("nan")
    elif damage in {"direction", "neutral"}:
        p[damage]["kind"] = "vertex"
    elif damage in {"body", "producer"}:
        p["faces"][0]["object_id" if damage == "body" else "producer_id"] = str(uuid4())
    elif damage == "operation":
        data["operation"] = "shell"
    with pytest.raises(ValueError):
        decoded = DRAFT_CODEC.decode(draft, data)
        doc.update_construction((decoded,))


def test_draft_clipboard_remaps_named_faces_and_dependencies_then_remains_editable(tmp_path):
    from cadastrophe.core.clipboard import encode_objects, decode_objects
    doc, service, body_id, base, draft = draft_chain()
    codecs = registry(service)
    before = dict(doc.objects)
    batch = decode_objects(encode_objects(doc, (body_id,), codecs), codecs)
    assert dict(doc.objects) == before
    copied = batch.document.objects[batch.roots[0]]
    assert not set(batch.document.objects) & set(doc.objects)
    new_base, new_draft = (batch.document.objects[k] for k in copied.features)
    assert new_draft.input_state == BodyStateRef(copied.id, new_base.id)
    assert new_draft.parameters.faces[0].producer_id == new_base.id
    assert all(base.id not in label for label in new_draft.parameters.faces[0].topology.labels)
    assert volume(copied.shape) == pytest.approx(volume(doc.objects[body_id].shape))
    batch.document.replace_feature(replace(new_draft, parameters=replace(new_draft.parameters, angle=10)))
    result = evaluate_body(batch.document, copied.id, service.evaluator(batch.document), reuse=True)
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(6000-1000*tan(radians(10)))
    save_document(batch.document, tmp_path/"copie_depouille.cada", registry=codecs)
    loaded = load_document(tmp_path/"copie_depouille.cada", registry=codecs)
    assert volume(loaded.objects[copied.id].shape) == pytest.approx(volume(result.shape))
