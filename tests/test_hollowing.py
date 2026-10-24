"""Real OCCT thickness, persistent faces and native shell tool integration."""
from dataclasses import replace
from math import pi
from uuid import uuid4

import pytest
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCC.Core.BRepClass3d import BRepClass3d_SolidClassifier
from OCC.Core.TopAbs import TopAbs_IN, TopAbs_OUT
from OCC.Core.gp import gp_Pnt

from cadastrophe.document import ObjectType, SceneObject
from cadastrophe.core.feature_model import Operation, BodyStateRef, ResultStatus, ExtrusionParameters
from cadastrophe.core.feature_objects import ShellFeature
from cadastrophe.core.operation_model import ShellParameters, GeometryReference, ReplayParameters
from cadastrophe.core.native_naming import capture, resolve, subshapes, NamingTrace, static_topology
from cadastrophe.core.operation_effects import checked_solids
from cadastrophe.core.body_history import evaluate_body, body_result, result_at
from cadastrophe.core.persistence import save_document, load_document, DocumentFileError
from plugins.modeling.shell_geometry import apply_shell
from test_primitives import volume
from test_parametric_operations import system, profile, append, bounds
from test_extrusion_tool import extrusion_system
from examples.parametric_example import registry as modeling_registry


def horizontal_face(shape, z):
    candidates = [f for f in subshapes(shape, "Face") if abs(bounds(f)[2]-z) < 1e-6 and abs(bounds(f)[5]-z) < 1e-6]
    assert len(candidates) == 1
    return candidates[0]


def shell_params(shape, heights, thickness=2., outward=False, *, body_id=None, producer_id=None, topology=None):
    topology = topology or static_topology(shape)
    return ShellParameters(tuple(GeometryReference("face", body_id or str(uuid4()), producer_id,
        capture(shape, horizontal_face(shape, z), "Face", topology)) for z in heights), thickness, outward)


def material(shape, point, inside):
    assert BRepClass3d_SolidClassifier(shape, gp_Pnt(*point), 1e-7).State() == (TopAbs_IN if inside else TopAbs_OUT)


@pytest.mark.parametrize("outward", [False, True])
@pytest.mark.parametrize("open_bottom", [False, True])
def test_shell_box_constant_thickness_and_multiple_openings(outward, open_bottom):
    source = BRepPrimAPI_MakeBox(30., 20., 10.).Shape()
    params = shell_params(source, (10, 0) if open_bottom else (10,), outward=outward)
    result = apply_shell(source, params)
    assert len(checked_solids(result)) == 1
    if outward:
        expected = 34*24*(10 if open_bottom else 12)-30*20*10
        expected_bounds = (-2, -2, 0 if open_bottom else -2, 32, 22, 10)
        material(result, (-1, 10, 5), True)
        material(result, (1, 10, 5), False)
    else:
        expected = 30*20*10-26*16*(10 if open_bottom else 8)
        expected_bounds = (0, 0, 0, 30, 20, 10)
        material(result, (1, 10, 5), True)
        material(result, (3, 10, 5), False)
    assert volume(result) == pytest.approx(expected, abs=1e-5)
    assert bounds(result) == pytest.approx(expected_bounds, abs=1e-6)
    material(result, (15, 10, 9), False)
    material(result, (15, 10, -1 if outward else 1), not open_bottom)
    assert volume(source) == pytest.approx(6000)  # Preview did not mutate the input.


@pytest.mark.parametrize("outward", [False, True])
def test_shell_cylinder_has_real_curved_constant_wall(outward):
    shape = BRepPrimAPI_MakeCylinder(10., 20.).Shape()
    result = apply_shell(shape, shell_params(shape, (20,), outward=outward))
    expected = pi*(12**2*22-10**2*20) if outward else pi*(10**2*20-8**2*18)
    assert volume(result) == pytest.approx(expected, abs=1e-5)
    assert len(checked_solids(result)) == 1
    material(result, (0, 0, 10), False)
    material(result, (11 if outward else 9, 0, 10), True)


@pytest.mark.parametrize("thickness", [0, -1, True, float("nan"), float("inf")])
def test_shell_parameters_reject_invalid_thickness(thickness):
    shape = BRepPrimAPI_MakeBox(30., 20., 10.).Shape()
    with pytest.raises(ValueError):
        shell_params(shape, (10,), thickness)


def test_shell_rejects_empty_duplicate_missing_faces_and_excessive_thickness():
    shape = BRepPrimAPI_MakeBox(30., 20., 10.).Shape()
    params = shell_params(shape, (10,))
    with pytest.raises(ValueError):
        replace(params, faces=())
    with pytest.raises(ValueError):
        replace(params, faces=(params.faces[0], params.faces[0]))
    with pytest.raises(ValueError, match="Coque impossible"):
        apply_shell(shape, replace(params, thickness=30))
    from cadastrophe.core.topology_refs import TopologyReference
    missing = replace(params.faces[0], topology=TopologyReference("Face", ("missing:face",)))
    with pytest.raises(ValueError, match="perdue"):
        apply_shell(shape, replace(params, faces=(missing,)), topology=static_topology(shape))


@pytest.mark.parametrize("thickness", [10, 11, 15, 20, 30])
def test_shell_rejects_crossed_walls_even_with_a_valid_native_brep(thickness):
    source = BRepPrimAPI_MakeBox(30., 20., 10.).Shape()
    with pytest.raises(ValueError, match="Coque impossible"):
        apply_shell(source, shell_params(source, (10,), thickness=thickness))


def test_shell_naming_distinguishes_original_and_offset_faces():
    shape = BRepPrimAPI_MakeBox(30., 20., 10.).Shape()
    topology = static_topology(shape)
    from cadastrophe.core.feature_model import EvaluationResult
    trace = NamingTrace(str(uuid4()), EvaluationResult(ResultStatus.READY, shape, topology=topology))
    result = apply_shell(shape, shell_params(shape, (10,), topology=topology), topology=topology, trace=trace)
    for face in subshapes(result, "Face"):
        ref = capture(result, face, "Face", trace.topology)
        assert ref.labels and not any(":untracked:" in label for label in ref.labels), ref.labels
        assert resolve(result, ref, trace.topology).IsSame(face)


def shell_chain():
    doc, service = system()
    sketch = profile(doc, ((0, 0), (30, 0), (30, 20), (0, 20)))
    body, base, _ = append(doc, service, sketch, parameters=ExtrusionParameters(depth=10))
    state = body_result(doc, body.id)
    params = shell_params(state.shape, (10,), body_id=body.id, producer_id=base.id, topology=state.topology)
    shell = ShellFeature("Coque1", None, body.color, kind=ObjectType.FEATURE, body_id=body.id,
                         operation=Operation.SHELL, input_state=BodyStateRef(body.id, base.id), parameters=params)
    doc.update_construction((replace(body, features=(*body.features, shell.id)), shell))
    result = evaluate_body(doc, body.id, service.evaluator(doc))
    assert result.status is ResultStatus.READY, result.message
    return doc, service, body.id, base, shell


def test_shell_rebuild_stable_reference_errors_undo_redo_and_replay_refusal():
    doc, service, body_id, base, shell = shell_chain()
    ids = set(doc.objects)
    assert volume(doc.objects[body_id].shape) == pytest.approx(2672)
    doc.history.clear()
    doc.replace_feature(replace(base, parameters=replace(base.parameters, depth=18)))
    result = evaluate_body(doc, body_id, service.evaluator(doc), reuse=True)
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(30*20*18-26*16*16)
    assert set(doc.objects) == ids
    assert doc.objects[shell.id].parameters == shell.parameters
    material(result.shape, (15, 10, 17), False)
    doc.history.undo()
    assert evaluate_body(doc, body_id, service.evaluator(doc), reuse=True).status is ResultStatus.READY
    assert volume(doc.objects[body_id].shape) == pytest.approx(2672)
    doc.history.redo()
    assert evaluate_body(doc, body_id, service.evaluator(doc), reuse=True).status is ResultStatus.READY

    from cadastrophe.core.feature_objects import ReplayFeature
    replay = ReplayFeature("Motif refusé", None, shell.color, kind=ObjectType.FEATURE, body_id=body_id,
        operation=Operation.LINEAR_PATTERN, parameters=ReplayParameters((shell.id,), GeometryReference("axis", values=(0, 0, 0, 1, 0, 0))))
    before = dict(doc.objects)
    with pytest.raises(ValueError, match="rejouables"):
        doc.update_construction((replace(doc.objects[body_id], features=(*doc.objects[body_id].features, replay.id)), replay))
    assert dict(doc.objects) == before and not shell.replayable


def test_shell_offset_face_support_follows_thickness_and_blocks_after_error(tmp_path):
    from cadastrophe.core.feature_objects import ExtrusionFeature
    from cadastrophe.core.feature_model import SketchInput
    from cadastrophe.core.operation_model import PlaneParameters
    from cadastrophe.core.reference_geometry import capture_reference
    from cadastrophe.document import SelectionItem
    from cadastrophe.geometry import Vec3
    from plugins.modeling.datums import refresh_datums
    from test_parametric_operations import datum
    doc, service, body_id, base, shell = shell_chain()
    body = doc.objects[body_id]
    floor = horizontal_face(body.shape, 2)
    reference = capture_reference(doc, SelectionItem(body_id, "Face", floor, body.revision))
    support = datum(doc, PlaneParameters("offset", (reference,), distance=0))
    local = support.plane.to_local(Vec3(15, 10, 2))
    sketch = profile(doc, support=support, circle=(*local[:2], 1))
    cut = ExtrusionFeature("Trou du fond", None, body.color, kind=ObjectType.FEATURE, body_id=body_id,
        operation=Operation.CUT, source=SketchInput(sketch.id, sketch.producer_refs),
        parameters=ExtrusionParameters(depth=None, end_condition="through_all", reversed=support.plane.normal.z > 0))
    doc.update_construction((replace(body, features=(*body.features, cut.id)), cut))
    assert evaluate_body(doc, body_id, service.evaluator(doc)).status is ResultStatus.READY
    assert volume(doc.objects[body_id].shape) == pytest.approx(2672-2*pi)
    ids, order = set(doc.objects), doc.objects[body_id].features
    doc.replace_feature(replace(shell, parameters=replace(shell.parameters, thickness=3)))
    result = evaluate_body(doc, body_id, service.evaluator(doc), reuse=True)
    assert result.status is ResultStatus.READY, result.message
    refresh_datums(doc)
    assert doc.objects[support.id].plane.origin.z == pytest.approx(3)
    assert volume(result.shape) == pytest.approx(6000-24*14*7-3*pi)
    material(result.shape, (15, 10, 1), False)
    material(result.shape, (17, 10, 1), True)

    codecs = modeling_registry(service)
    path = tmp_path/"coque_dependances.cada"
    save_document(doc, path, registry=codecs)
    restored = load_document(path, registry=codecs)
    assert set(restored.objects) == ids and restored.objects[body_id].features == order
    assert restored.objects[shell.id].parameters == doc.objects[shell.id].parameters
    assert restored.objects[support.id].plane.origin.z == pytest.approx(3)
    assert volume(restored.objects[body_id].shape) == pytest.approx(6000-24*14*7-3*pi)

    doc.replace_feature(replace(shell, parameters=replace(shell.parameters, thickness=30)))
    failed = evaluate_body(doc, body_id, service.evaluator(doc), reuse=True)
    assert failed.status is ResultStatus.BLOCKED and failed.message
    assert result_at(doc, BodyStateRef(body_id, shell.id)).status is ResultStatus.ERROR
    assert doc.objects[body_id].shape is None
    doc.history.undo()
    assert evaluate_body(doc, body_id, service.evaluator(doc), reuse=True).status is ResultStatus.READY
    assert set(doc.objects) == ids and doc.objects[body_id].features == order


@pytest.mark.parametrize("outward", [False, True])
def test_shell_native_face_click_preview_commit_history_and_reopen(extrusion_system, tmp_path, outward):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QToolButton
    from test_parametric_operations_ui import screen, top_view
    from test_feature_edit import edit
    from zipfile import ZipFile
    import json
    w, _, _ = extrusion_system
    doc = w.viewport.document
    block = SceneObject("Bloc à creuser", BRepPrimAPI_MakeBox(30., 20., 10.).Shape(), (.4, .6, .8))
    doc.add(block)
    doc.history.clear()
    top_view(w)
    action = w.command_registry.action("modeling.shell")
    button = next(b for b in w.command_manager.findChildren(QToolButton) if b.defaultAction() is action)
    assert button.isEnabled()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    tool = w.tool_sessions.tool
    assert tool is not None and not w.tool_sessions.can_commit
    before = dict(doc.objects)
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=screen(w, (15., 10., 10.)))
    tool.outward.setChecked(outward)
    w.tool_sessions.update_preview()
    assert len(tool.face_references) == 1
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    expected = 34*24*12-6000 if outward else 2672
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(expected)
    assert dict(doc.objects) == before and doc.history.count == 0
    # A second face click still targets the source while the final AIS is hidden.
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=screen(w, (15., 10., 10.)))
    w.tool_sessions.update_preview()
    assert not tool.face_references and not w.tool_sessions.can_commit
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=screen(w, (15., 10., 10.)))
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert w.tool_sessions.commit(), w.tool_sessions.message
    shell, = (o for o in doc.objects.values() if isinstance(o, ShellFeature))
    assert shell.input_state == BodyStateRef(block.id) and not shell.replayable
    assert doc.objects[block.id].features == (shell.id,)
    assert doc.history.count == 1 and shell.id in w.tree_items
    assert volume(doc.objects[block.id].shape) == pytest.approx(expected)
    assert doc.history.undo() is None
    assert type(doc.objects[block.id]) is SceneObject
    doc.history.redo()
    assert volume(doc.objects[block.id].shape) == pytest.approx(expected)
    ids = set(doc.objects)
    session = edit(w, shell.id, double_click=True)
    session.thickness.set_internal_value(3)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    expected_edited = 36*26*13-6000 if outward else 3648
    assert volume(doc.objects[block.id].shape) == pytest.approx(expected_edited)
    assert set(doc.objects) == ids and doc.objects[block.id].features == (shell.id,)
    doc.history.undo()
    assert volume(doc.objects[block.id].shape) == pytest.approx(expected)
    doc.history.redo()
    path = tmp_path/"coque_native.cada"
    assert w.save_document(path=path)
    with ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        record = next(o for o in manifest["objects"] if o["id"] == shell.id)
        assert record["brep"] is None and record["extension"]["version"] == 3
        assert record["extension"]["data"]["enabled"] is True
    assert w.close_document()
    restored = w.open_document(path)
    assert restored is not doc and set(restored.objects) == ids
    assert volume(restored.objects[block.id].shape) == pytest.approx(expected_edited)
    assert restored.objects[shell.id].parameters.thickness == 3
    assert restored.objects[shell.id].parameters.outward is outward
    QApplication.processEvents()


def test_shell_invalid_preview_cancel_and_explicit_face_repair(extrusion_system, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from cadastrophe.core.topology_refs import TopologyReference
    from test_feature_edit import edit
    from test_parametric_operations_ui import top_view, screen
    doc, service, body_id, _, shell = shell_chain()
    path = tmp_path/"coque_reparation.cada"
    save_document(doc, path, registry=modeling_registry(service))
    w, _, _ = extrusion_system
    opened = w.open_document(path)
    QTest.qWait(100)
    ids = set(opened.objects)
    top_view(w)
    session = edit(w, shell.id)
    session.thickness.set_internal_value(30)
    w.tool_sessions.update_preview()
    assert not w.tool_sessions.can_commit and "Coque impossible" in w.tool_sessions.message
    assert volume(opened.objects[body_id].shape) == pytest.approx(2672)
    assert w.tool_sessions.cancel()
    assert set(opened.objects) == ids and not opened.history.count

    original = opened.objects[shell.id]
    ref = replace(original.parameters.faces[0], topology=TopologyReference("Face", ("missing:face",)))
    opened.replace_feature(replace(original, parameters=replace(original.parameters, faces=(ref,))))
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert body_result(opened, body_id).status is ResultStatus.ERROR
    assert opened.objects[body_id].shape is None
    session = edit(w, shell.id)
    assert not w.tool_sessions.can_commit
    session.clear_faces()
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=screen(w, (15., 10., 10.)))
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert set(opened.objects) == ids and volume(opened.objects[body_id].shape) == pytest.approx(2672)


@pytest.mark.parametrize("damage", ["unknown_field", "empty_faces", "duplicate_faces", "not_a_face", "no_names",
                                   "non_finite", "wrong_sense", "wrong_operation", "wrong_body", "wrong_producer"])
def test_shell_codec_rejects_corrupt_recipes(damage):
    from plugins.modeling.operation_codec import SHELL_CODEC
    doc, _, body_id, _, shell = shell_chain()
    data = SHELL_CODEC.encode(shell)
    assert SHELL_CODEC.decode(shell, data) == shell
    if damage == "unknown_field":
        data["parameters"]["unknown"] = 1
    elif damage == "empty_faces":
        data["parameters"]["faces"] = []
    elif damage == "duplicate_faces":
        data["parameters"]["faces"] *= 2
    elif damage == "not_a_face":
        data["parameters"]["faces"][0]["kind"] = "edge"
    elif damage == "no_names":
        data["parameters"]["faces"][0]["topology"]["labels"] = []
    elif damage == "non_finite":
        data["parameters"]["thickness"] = float("nan")
    elif damage == "wrong_sense":
        data["parameters"]["outward"] = 1
    elif damage == "wrong_operation":
        data["operation"] = "cut"
    elif damage == "wrong_body":
        data["parameters"]["faces"][0]["object_id"] = str(uuid4())
    elif damage == "wrong_producer":
        data["parameters"]["faces"][0]["producer_id"] = str(uuid4())
    with pytest.raises(ValueError):
        decoded = SHELL_CODEC.decode(shell, data)
        doc.update_construction((decoded,))  # Producer existence/order belongs to the complete graph.


def test_shell_error_roundtrip_and_producer_deletion_use_document_graph(tmp_path):
    from cadastrophe.core.body_history import without_features
    doc, service, body_id, base, shell = shell_chain()
    ids = set(doc.objects)
    candidate = without_features(doc.objects, (base.id,))
    assert base.id not in candidate and shell.id not in candidate and body_id not in candidate
    doc.remove(base.id)
    doc.history.undo()
    assert set(doc.objects) == ids
    assert evaluate_body(doc, body_id, service.evaluator(doc)).status is ResultStatus.READY

    doc.replace_feature(replace(shell, parameters=replace(shell.parameters, thickness=30)))
    result = evaluate_body(doc, body_id, service.evaluator(doc))
    assert result.status is ResultStatus.ERROR
    path = tmp_path/"coque_erreur.cada"
    codecs = modeling_registry(service)
    save_document(doc, path, registry=codecs)
    restored = load_document(path, registry=codecs)
    assert set(restored.objects) == ids and restored.objects[body_id].shape is None
    assert body_result(restored, body_id).status is ResultStatus.ERROR
    broken = restored.objects[shell.id]
    restored.replace_feature(replace(broken, parameters=replace(broken.parameters, thickness=2)))
    assert evaluate_body(restored, body_id, service.evaluator(restored)).status is ResultStatus.READY
    assert volume(restored.objects[body_id].shape) == pytest.approx(2672)


def test_shell_rejects_multiple_solids_and_faces_from_other_producers():
    from cadastrophe.core.operation_effects import compound
    first = BRepPrimAPI_MakeBox(30., 20., 10.).Shape()
    second = BRepPrimAPI_MakeBox(gp_Pnt(50, 0, 0), 30., 20., 10.).Shape()
    with pytest.raises(ValueError, match="un seul solide"):
        apply_shell(compound((first, second)), shell_params(first, (10,)))
    doc, service, body_id, base, shell = shell_chain()
    before = dict(doc.objects)
    with pytest.raises(ValueError, match="immédiatement précédent"):
        other = replace(shell, input_state=BodyStateRef(body_id, shell.id),
                        parameters=replace(shell.parameters, faces=tuple(replace(r, producer_id=shell.id) for r in shell.parameters.faces)))
        doc.replace_feature(other)
    assert dict(doc.objects) == before


def test_shell_concave_prism_has_the_expected_interior_clearance():
    from cadastrophe.core.extrusion import extrude_face
    from plugins.sketch.profile_service import SketchProfiles
    doc, _ = system()
    sketch = profile(doc, ((0, 0), (30, 0), (30, 8), (12, 8), (12, 20), (0, 20)))
    face = SketchProfiles().to_faces(doc, sketch.id).regions[0].face
    source = extrude_face(face, 10).shape
    result = apply_shell(source, shell_params(source, (10,)))
    assert len(checked_solids(result)) == 1
    assert volume(result) == pytest.approx(3840-200*8)
    material(result, (1, 10, 5), True)
    material(result, (5, 10, 5), False)
    material(result, (20, 4, 5), False)
    material(result, (20, 7, 5), True)
