"""Editable graph copies, producer-name remapping, atomic cut/paste and codecs."""
from dataclasses import replace
import json
from weakref import ref

import pytest

from cadastrophe.core.clipboard import ObjectClipboard, encode_objects, decode_objects, ClipboardError, MIME_TYPE
from cadastrophe.core.body_history import evaluate_body, body_result
from cadastrophe.core.feature_model import ResultStatus
from cadastrophe.core.feature_objects import BodyObject, SketchSourceObject
from cadastrophe.core.persistence import load_document, save_document
from cadastrophe.scene import new_document
from examples.reference_parts import PARTS, build_reference
from examples.parametric_example import registry
from test_clipboard import MemoryClipboard, volume
from test_hollowing import shell_chain
from test_extrusion_tool import extrusion_system
from cadastrophe.geometry import Vec3


@pytest.mark.parametrize("spec", PARTS, ids=lambda spec: spec.key)
def test_copy_paste_reference_body_preserves_editable_graph_and_rebuild(spec, tmp_path):
    source = build_reference(spec)
    codecs = registry(source.service)
    copier = ObjectClipboard(MemoryClipboard(), codecs)
    before = dict(source.document.objects)
    assert copier.copy(source.document, (source.body.id,)) == 1
    payload = bytes(copier.clipboard.mimeData().data(MIME_TYPE))
    data = json.loads(payload)
    assert data["schema_version"] == 2
    bodies = [e for e in data["objects"] if e["object"]["kind"] == "body"]
    assert bodies and all(e["payload"] is None for e in bodies)
    assert dict(source.document.objects) == before
    target = new_document("Cible")
    previous = set(target.objects)
    pasted, = copier.paste(target)
    assert target.selected_ids == (pasted,) and target.history.count == 1
    added = set(target.objects)-previous
    assert not added.intersection(source.document.objects)
    body = target.objects[pasted]
    assert isinstance(body, BodyObject) and body.features != source.body.features
    assert len(body.features) == spec.feature_count
    assert body_result(target, body.id).status is ResultStatus.READY
    assert volume(body.shape) == pytest.approx(spec.expected_volume(spec.initial_depth), abs=1e-4)
    feature_index = source.body.features.index(source.handles[spec.edited_feature])
    feature = target.objects[body.features[feature_index]]
    target.replace_feature(replace(feature, parameters=replace(feature.parameters, depth=spec.variant_depth)))
    result = evaluate_body(target, pasted, source.service.evaluator(target), reuse=True)
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(spec.expected_volume(spec.variant_depth), abs=1e-4)
    assert dict(source.document.objects) == before
    path = tmp_path/f"{spec.key}_copie.cada"
    save_document(target, path, registry=codecs)
    restored = load_document(path, registry=codecs)
    assert set(restored.objects) == set(target.objects)
    assert volume(restored.objects[pasted].shape) == pytest.approx(spec.expected_volume(spec.variant_depth), abs=1e-4)
    target.history.undo()
    evaluate_body(target, pasted, source.service.evaluator(target), reuse=True)
    target.history.undo()
    assert set(target.objects) == previous
    target.history.redo()
    assert target.selected_ids == (pasted,) and set(target.objects)-previous == added
    assert volume(target.objects[pasted].shape) == pytest.approx(spec.expected_volume(spec.initial_depth), abs=1e-4)


def test_shell_body_copy_renames_producers_inside_offset_face_labels(tmp_path):
    from test_hollowing import horizontal_face
    from cadastrophe.document import SelectionItem
    from cadastrophe.core.reference_geometry import capture_reference
    from cadastrophe.core.operation_model import PlaneParameters
    from test_parametric_operations import datum
    doc, service, body_id, _, shell = shell_chain()
    body = doc.objects[body_id]
    ref = capture_reference(doc, SelectionItem(body_id, "Face", horizontal_face(body.shape, 2), body.revision))
    plane = datum(doc, PlaneParameters("offset", (ref,), distance=0))
    # Use the plane in a sketch consumed by the same copied body.
    from test_parametric_operations import profile
    from cadastrophe.core.feature_objects import ExtrusionFeature
    from cadastrophe.core.feature_model import SketchInput, ExtrusionParameters, Operation
    local = plane.plane.to_local(Vec3(15, 10, 2))
    sketch = profile(doc, support=plane, circle=(*local[:2], 1))
    cut = ExtrusionFeature("Trou", None, body.color, kind=shell.kind, body_id=body_id, operation=Operation.CUT,
                          source=SketchInput(sketch.id, sketch.producer_refs),
                          parameters=ExtrusionParameters(depth=None, end_condition="through_all", reversed=plane.plane.normal.z > 0))
    doc.update_construction((replace(body, features=(*body.features, cut.id)), cut))
    assert evaluate_body(doc, body_id, service.evaluator(doc)).status is ResultStatus.READY
    codecs = registry(service)
    payload = encode_objects(doc, (body_id,), codecs)
    batch = decode_objects(payload, codecs)
    assert len(batch.roots) == 1 and not {o.id for o in batch}.intersection(doc.objects)
    assert all(not o.reference_error(batch.document) for o in batch if isinstance(o, SketchSourceObject))
    copied = batch.document.objects[batch.roots[0]]
    assert volume(copied.shape) == pytest.approx(volume(doc.objects[body_id].shape))


def test_parametric_cut_removes_only_owned_recipes_and_undo_restores_source():
    spec = PARTS[0]
    model = build_reference(spec)
    doc, codecs = model.document, registry(model.service)
    copier = ObjectClipboard(MemoryClipboard(), codecs)
    before = dict(doc.objects)
    doc.select_objects((model.body.id,))
    assert copier.copy(doc, (model.body.id,), cut=True) == 1
    assert model.body.id not in doc.objects and not any(isinstance(o, BodyObject) for o in doc.objects.values())
    assert doc.history.count == 1
    doc.history.undo()
    assert set(doc.objects) == set(before) and doc.selected_ids == (model.body.id,)
    assert evaluate_body(doc, model.body.id, model.service.evaluator(doc)).status is ResultStatus.READY
    assert volume(doc.objects[model.body.id].shape) == pytest.approx(spec.expected_volume(spec.initial_depth))


@pytest.mark.parametrize("damage", ["root", "reference", "hash", "final_cache", "missing_object", "plugin"])
def test_corrupt_construction_clipboard_is_rejected_before_publication(damage):
    model = build_reference(PARTS[0])
    codecs = registry(model.service)
    data = json.loads(encode_objects(model.document, (model.body.id,), codecs))
    if damage == "root":
        data["roots"] = ["missing"]
    elif damage == "reference":
        body = next(e for e in data["objects"] if e["object"]["kind"] == "body")
        body["object"]["extension"]["data"]["features"] = [model.body.id]
    elif damage == "hash":
        native = next(e for e in data["objects"] if e["payload"] is not None)
        native["object"]["brep"]["sha256"] = "0"*64
    elif damage == "final_cache":
        next(e for e in data["objects"] if e["object"]["kind"] == "body")["payload"] = "cached result"
    elif damage == "missing_object":
        data["objects"] = data["objects"][:-1]
    else:
        data["required_plugins"].append("missing.plugin")
    before = dict(model.document.objects)
    with pytest.raises(ClipboardError):
        decode_objects(json.dumps(data).encode(), codecs)
    assert dict(model.document.objects) == before


def test_native_parametric_clipboard_paste_edit_undo_and_real_reopen(extrusion_system, tmp_path):
    from PySide6.QtTest import QTest
    from test_feature_edit import edit
    model = build_reference(PARTS[0])
    path = tmp_path/"source.cada"
    save_document(model.document, path, registry=registry(model.service))
    w, _, _ = extrusion_system
    source = w.open_document(path)
    QTest.qWait(100)
    w.tree_selection_changed((model.body.id,))
    assert not w.clipboard_selection_error()
    assert w.command_registry.execute("edit.copy")
    target = w.create_document()
    QTest.qWait(100)
    previous = set(target.objects)
    assert w.command_registry.execute("edit.paste")
    assert len(target.selected_ids) == 1, w.hint.text()
    pasted = target.selected_ids[0]
    body = target.objects[pasted]
    assert isinstance(body, BodyObject) and len(body.features) == 3
    assert pasted in w.viewport.presentations and target.history.count == 1
    session = edit(w, body.features[0])
    session.depth.set_internal_value(40)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert volume(target.objects[pasted].shape) == pytest.approx(PARTS[0].expected_volume(40))
    assert volume(source.objects[model.body.id].shape) == pytest.approx(PARTS[0].expected_volume(30))
    output = tmp_path/"copie_native.cada"
    assert w.save_document(path=output) and w.close_document()
    reopened = w.open_document(output)
    assert volume(reopened.objects[pasted].shape) == pytest.approx(PARTS[0].expected_volume(40))


def test_failed_publication_does_not_cut_graph_and_static_sources_are_preserved():
    from cadastrophe.document import Document, SceneObject
    from cadastrophe.core.body_history import BodyHistory
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    source = Document("Source statique adoptée")
    obj = SceneObject("Bloc", BRepPrimAPI_MakeBox(2., 3., 4.).Shape(), (.4, .6, .8))
    source.add(obj)
    BodyHistory().adopt(source, obj.id)
    source.history.clear()
    from plugins.modeling.extrusion_service import Extrusions
    from plugins.sketch.profile_service import SketchProfiles
    codecs = registry(Extrusions(SketchProfiles()))
    payload = encode_objects(source, (obj.id,), codecs)
    copied = decode_objects(payload, codecs)
    clone = copied.document.objects[copied.roots[0]]
    assert clone.source_kind == "static" and clone.source_shape is not None
    assert volume(clone.shape) == pytest.approx(24)
    class Rejecting(MemoryClipboard):
        def setMimeData(self, mime):
            pass
    copier = ObjectClipboard(Rejecting(), codecs)
    before = dict(source.objects)
    with pytest.raises(ClipboardError, match="aucun objet n’a été coupé"):
        copier.copy(source, (obj.id,), cut=True)
    assert dict(source.objects) == before and not source.history.count


@pytest.mark.parametrize("operation", ["fillet", "chamfer"])
def test_finished_body_copy_remaps_persistent_edge_names_and_recomputes(operation):
    from examples.parametric_example import ParametricExample, polygon
    from cadastrophe.core.feature_model import Operation, ExtrusionParameters, EdgeParameters
    from cadastrophe.core.native_naming import capture
    from plugins.modeling.edge_geometry import edges_of, edge_reference
    model = ParametricExample("Finition")
    sketch = model.sketch("profile", "Rectangle", model.plane("XY"), polygon(((0, 0), (30, 0), (30, 20), (0, 20))))
    model.append("base", "Base", Operation.NEW_BODY, ExtrusionParameters(depth=10), sketch)
    state = body_result(model.document, model.body.id)
    edge = edges_of(state.shape)[0]
    model.append("finish", "Finition", Operation(operation),
                 EdgeParameters((edge_reference(state.shape, edge),), size=1,
                                references=(capture(state.shape, edge, "Edge", state.topology),)))
    batch = decode_objects(encode_objects(model.document, (model.body.id,), registry(model.service)), registry(model.service))
    copied = batch.document.objects[batch.roots[0]]
    assert volume(copied.shape) == pytest.approx(volume(model.body.shape))
    finish = batch.document.objects[copied.features[-1]]
    batch.document.replace_feature(replace(finish, parameters=replace(finish.parameters, size=2)))
    result = evaluate_body(batch.document, copied.id, model.service.evaluator(batch.document), reuse=False)
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) < volume(copied.shape)


def test_cut_preserves_shared_sketch_and_refuses_external_consumer_before_publication():
    from test_parametric_operations import system, profile, append, datum
    from cadastrophe.core.operation_model import PlaneParameters
    from cadastrophe.core.reference_geometry import capture_reference
    from cadastrophe.document import SelectionItem
    from cadastrophe.core.native_naming import subshapes
    doc, service = system()
    sketch = profile(doc)
    first, _, _ = append(doc, service, sketch)
    second, _, _ = append(doc, service, sketch)
    copier = ObjectClipboard(MemoryClipboard(), registry(service))
    copier.copy(doc, (first.id,), cut=True)
    assert sketch.id in doc.objects and second.id in doc.objects
    assert evaluate_body(doc, second.id, service.evaluator(doc), reuse=False).status is ResultStatus.READY
    doc.history.undo()
    assert evaluate_body(doc, first.id, service.evaluator(doc), reuse=False).status is ResultStatus.READY
    body = doc.objects[first.id]
    ref = capture_reference(doc, SelectionItem(body.id, "Face", subshapes(body.shape, "Face")[0], body.revision))
    datum(doc, PlaneParameters("offset", (ref,), distance=0))
    copier = ObjectClipboard(MemoryClipboard(), registry(service))
    before, count = dict(doc.objects), doc.history.count
    with pytest.raises(ClipboardError, match="dépend"):
        copier.copy(doc, (first.id,), cut=True)
    assert dict(doc.objects) == before and doc.history.count == count
    assert copier.clipboard.mimeData() is None


def test_cut_refuses_reentrant_source_edit_after_publication():
    model = build_reference(PARTS[0])
    doc = model.document
    # A backend callback must not retain the native document in a class cycle.
    document_ref, body_id = ref(doc), model.body.id
    class Editing(MemoryClipboard):
        def setMimeData(self, mime):
            super().setMimeData(mime)
            document_ref().set_visible(body_id, False)
    copier = ObjectClipboard(Editing(), registry(model.service))
    with pytest.raises(ClipboardError, match="document a changé"):
        copier.copy(doc, (model.body.id,), cut=True)
    assert model.body.id in doc.objects and doc.history.count == 1
