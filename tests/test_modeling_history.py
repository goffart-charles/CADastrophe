"""X1 construction contracts: no widgets, AIS or extrusion engine."""
from dataclasses import replace
import json
from uuid import uuid4
from zipfile import ZipFile

import pytest

from cadastrophe.document import Document, SceneObject, ObjectType
from cadastrophe.core.body_history import BodyHistory, validate_graph, body_result
from cadastrophe.core.feature_model import (Operation, ExtrusionParameters, SketchInput,
                                         BodyStateRef, EvaluationResult, ResultStatus)
from cadastrophe.core.feature_objects import BodyObject, ExtrusionFeature
from cadastrophe.core.object_types import ObjectTypeRegistry
from cadastrophe.core.persistence import save_document, load_document, DocumentFileError
from cadastrophe.core.clipboard import encode_objects, ClipboardError
from cadastrophe.geometry import REFERENCE_PLANES
from plugins.modeling.codec import BODY_CODEC, FEATURE_CODEC
from plugins.sketch.codec import CODEC as SKETCH_CODEC
from plugins.sketch.commands import create_sketch, add_entity
from plugins.sketch.model import Point, Vec2, PlaneSupport, Circle


def key():
    return str(uuid4())


def registry():
    result = ObjectTypeRegistry()
    result.register("cadastrophe.sketch", SKETCH_CODEC)
    result.register("cadastrophe.modeling", BODY_CODEC)
    result.register("cadastrophe.modeling", FEATURE_CODEC)
    return result


def sketch_document(native=False):
    if native:
        from cadastrophe.scene import new_document
        doc = new_document()
    else:
        doc = Document()
        doc.add(SceneObject("XY", None, (.1, .2, .3), REFERENCE_PLANES[0]))
    sketch = create_sketch(doc, next(iter(doc.objects)))
    doc.history.clear()
    return doc, sketch


def feature(body_id, sketch, operation=Operation.NEW_BODY, **values):
    return ExtrusionFeature("Extrusion", None, (.6, .6, .6), kind=ObjectType.FEATURE,
                            body_id=body_id, operation=operation,
                            source=values.pop("source", SketchInput(sketch.id)), **values)


def construction(doc, sketch, operations=(Operation.NEW_BODY, Operation.BOSS, Operation.CUT)):
    body_id = key()
    functions = tuple(feature(body_id, sketch, op) for op in operations)
    body = BodyObject("Corps", None, (.5, .6, .7), id=body_id, features=tuple(f.id for f in functions))
    doc.update_construction((body, *functions), title="Créer la construction")
    return doc.objects[body_id], functions


class TraceAdapter:
    """Test-only symbolic shapes make exact prefix use observable."""
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def evaluate(self, function, sketch, previous, supports):
        self.calls.append((function.id, previous, dict(supports)))
        if function.id == self.fail:
            raise ValueError("calcul de test refusé")
        return EvaluationResult(ResultStatus.READY, (previous, function.id, function.parameters.depth))


def test_ordered_model_atomic_observers_undo_redo_rename_and_independent_documents():
    doc, sketch = sketch_document()
    other, other_sketch = sketch_document()
    observed = []
    def observe(*args):
        validate_graph(doc.objects)
        observed.append(tuple(doc.objects))
    doc.object_added.connect(observe)
    doc.object_removed.connect(observe)
    doc.object_changed.connect(observe)
    body, functions = construction(doc, sketch)
    assert body.features == tuple(f.id for f in functions)
    assert doc.history.count == 1 and observed
    assert not other.history.count and body.id not in other.objects
    doc.rename(functions[1].id, "Bossage")
    assert doc.objects[functions[1].id].name == "Bossage"
    doc.history.undo()
    doc.history.undo()
    assert set(doc.objects) == {sketch.id, sketch.sketch.support.plane_id}
    doc.history.redo()
    doc.history.redo()
    assert doc.objects[body.id].features == body.features
    assert doc.objects[functions[1].id].name == "Bossage"
    assert any(isinstance(obj, BodyObject) for obj in doc.history.retained_objects())


def test_evaluation_uses_previous_prefix_and_invalidation_on_sketch_edit_undo_redo():
    doc, sketch = sketch_document()
    body, functions = construction(doc, sketch)
    service, adapter = BodyHistory(), TraceAdapter()
    result = service.evaluate(doc, body.id, adapter)
    assert result.status is ResultStatus.READY
    assert [call[0] for call in adapter.calls] == list(body.features)
    assert adapter.calls[0][1] is None
    assert adapter.calls[2][1][1] == functions[1].id
    count = doc.history.count
    assert doc.objects[body.id].shape == result.shape
    assert doc.history.count == count
    add_entity(doc, sketch.id, Point(Vec2(2, 3)))
    assert doc.objects[body.id].shape is None
    assert service.result(doc, body.id).status is ResultStatus.DIRTY
    for replay in (doc.history.undo, doc.history.redo):
        service.evaluate(doc, body.id, TraceAdapter())
        replay()
        assert service.result(doc, body.id).status is ResultStatus.DIRTY
        assert doc.objects[body.id].shape is None


def test_unavailable_engine_failure_and_downstream_blocked_never_reuse_old_result():
    doc, sketch = sketch_document()
    body, functions = construction(doc, sketch)
    service = BodyHistory()
    service.evaluate(doc, body.id, TraceAdapter())
    result = service.evaluate(doc, body.id)
    assert result.status is ResultStatus.BLOCKED and "non installé" in result.message
    assert doc.objects[body.id].shape is None
    adapter = TraceAdapter(fail=functions[1].id)
    result = service.evaluate(doc, body.id, adapter)
    assert result.status is ResultStatus.BLOCKED and "refusé" in result.message
    assert [c[0] for c in adapter.calls] == [functions[0].id, functions[1].id]
    assert doc._body_prefixes[body.id][BodyStateRef(body.id, functions[1].id)].status is ResultStatus.ERROR
    assert doc.objects[body.id].shape is None


@pytest.mark.parametrize("fault", ["missing_sketch", "wrong_sketch", "wrong_body", "orphan", "duplicate", "wrong_base", "missing_feature"])
def test_bad_references_rejected_before_history_and_redo_are_touched(fault):
    doc, sketch = sketch_document()
    body, functions = construction(doc, sketch)
    doc.rename(body.id, "Nom")
    doc.history.undo()
    before, index, count = dict(doc.objects), doc.history.index, doc.history.count
    first = functions[0]
    if fault == "missing_sketch":
        updates = (replace(first, source=SketchInput(key())),)
    elif fault == "wrong_sketch":
        updates = (replace(first, source=SketchInput(body.id)),)
    elif fault == "wrong_body":
        updates = (replace(first, body_id=key()),)
    elif fault == "orphan":
        updates = (feature(body.id, sketch, Operation.BOSS),)
    elif fault == "duplicate":
        with pytest.raises(ValueError):
            replace(body, features=(*body.features, first.id))
        return
    elif fault == "wrong_base":
        updates = (replace(first, operation=Operation.BOSS),)
    else:
        updates = (replace(body, features=(*body.features, key())),)
    with pytest.raises(ValueError):
        doc.update_construction(updates)
    assert dict(doc.objects) == before and doc.history.index == index and doc.history.count == count
    assert doc.history.can_redo


def test_parameter_edits_and_explicit_removal_are_one_undo_step():
    doc, sketch = sketch_document()
    body, functions = construction(doc, sketch)
    changed = replace(functions[-1], parameters=ExtrusionParameters(depth=None, end_condition="through_all", reversed=True))
    doc.replace_feature(changed)
    assert doc.objects[changed.id].parameters == changed.parameters
    doc.history.undo()
    assert doc.objects[changed.id].parameters.depth == 10
    doc.history.redo()
    doc.remove(changed.id)
    assert changed.id not in doc.objects
    doc.history.undo()
    assert doc.objects[body.id].features == body.features
    assert doc.objects[changed.id].parameters == changed.parameters
    with pytest.raises(ValueError, match="esquisse"):
        doc.remove(sketch.id)
    doc.remove(body.id)
    assert all(f.id not in doc.objects for f in functions)
    doc.history.undo()
    validate_graph(doc.objects)


@pytest.mark.parametrize("values", [dict(depth=0), dict(depth=-1), dict(depth=float("nan")), dict(depth=True),
                                     dict(end_condition="up_to_face"), dict(end_condition="through_all"),
                                     dict(reversed=1), dict(contours=("index-1",))])
def test_invalid_parameters(values):
    with pytest.raises((ValueError, TypeError)):
        ExtrusionParameters(**values)


def test_roundtrip_preserves_recipes_order_ids_parameters_and_discards_derived_cache(tmp_path):
    doc, sketch = sketch_document(native=True)
    body, functions = construction(doc, sketch)
    BodyHistory().evaluate(doc, body.id, TraceAdapter())
    path = tmp_path / "construction.cada"
    save_document(doc, path, registry=registry())
    with ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema_version"] == 9
        record = next(r for r in manifest["objects"] if r["id"] == body.id)
        assert record["brep"] is None and record["resources"] == {}
        assert not any(name == f"shapes/{body.id}.brep" for name in archive.namelist())
    loaded = load_document(path, registry=registry())
    assert loaded.id == doc.id and tuple(loaded.objects) == tuple(doc.objects)
    assert loaded.objects[sketch.id].sketch == doc.objects[sketch.id].sketch
    assert loaded.objects[body.id].features == body.features
    for function in functions:
        assert loaded.objects[function.id].parameters == function.parameters
        assert loaded.objects[function.id].source == function.source
    assert loaded.objects[body.id].shape is None and body_result(loaded, body.id).status is ResultStatus.DIRTY
    assert not loaded.is_modified and loaded.history.count == 0
    assert BodyHistory().evaluate(loaded, body.id).status is ResultStatus.BLOCKED


@pytest.mark.parametrize("source_kind", ["primitive", "import", "static"])
def test_static_base_kept_independent_of_final_result_and_legacy_adoption_is_opt_in(tmp_path, source_kind):
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from cadastrophe.brep_io import encode_shape
    doc, sketch = sketch_document(native=True)
    original = SceneObject("Base existante", BRepPrimAPI_MakeBox(10, 20, 30).Shape(), (.2, .4, .6))
    doc.add(original)
    from plugins.sketch.supports import face_map, face_frame
    from plugins.sketch.objects import support_status
    support = PlaneSupport(original.id, face_frame(face_map(original.shape).FindKey(1), "Face"), original.revision, 1)
    on_face = create_sketch(doc, support, name="Esquisse sur la base existante")
    legacy = tmp_path / "legacy.cada"
    save_document(doc, legacy, registry=registry())
    before = encode_shape(original.shape, ObjectType.BODY)
    legacy_loaded = load_document(legacy, registry=registry())
    assert type(legacy_loaded.objects[original.id]) is SceneObject
    service = BodyHistory()
    body = service.adopt(doc, original.id, source_kind=source_kind)
    assert body.id == original.id and body.source_shape is original.shape
    assert body.revision == original.revision and not support_status(doc.objects[on_face.id], doc.objects)
    assert service.result(doc, body.id).status is ResultStatus.READY
    base_path = tmp_path / "static-base-only.cada"
    save_document(doc, base_path, registry=registry())
    base_loaded = load_document(base_path, registry=registry())
    base_record = base_loaded.objects[body.id]
    assert base_record.shape is base_record.source_shape and base_record.revision == body.revision
    assert body_result(base_loaded, body.id).status is ResultStatus.READY
    assert not support_status(base_loaded.objects[on_face.id], base_loaded.objects)
    cut = feature(body.id, sketch, Operation.CUT)
    doc.update_construction((replace(body, features=(cut.id,)), cut))
    adapter = TraceAdapter()
    service.evaluate(doc, body.id, adapter)
    assert adapter.calls[0][1] is original.shape
    path = tmp_path / "adopted.cada"
    save_document(doc, path, registry=registry())
    with ZipFile(path) as archive:
        resource = f"resources/{body.id}/source_shape.brep"
        assert archive.read(resource) == before
    loaded = load_document(path, registry=registry())
    restored = loaded.objects[body.id]
    assert restored.source_kind == source_kind and restored.features == (cut.id,)
    assert encode_shape(restored.source_shape, ObjectType.BODY) == before
    assert restored.shape is None
    doc.history.undo()  # remove the cut
    assert doc.objects[body.id].shape is original.shape
    doc.history.undo()  # undo adoption
    assert type(doc.objects[body.id]) is SceneObject and doc.objects[body.id].shape is original.shape
    doc.history.redo()
    assert doc.objects[body.id].source_shape is original.shape


def rewrite(path, change, payload_change=None):
    with ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries["manifest.json"])
    change(manifest)
    entries["manifest.json"] = json.dumps(manifest).encode()
    if payload_change:
        payload_change(entries)
    with ZipFile(path, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)


def test_missing_plugin_invalid_refs_and_corrupt_sources_refuse_load_without_overwriting(tmp_path):
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    doc, sketch = sketch_document(native=True)
    body, functions = construction(doc, sketch)
    path = tmp_path / "invalid.cada"
    save_document(doc, path, registry=registry())
    content = path.read_bytes()
    with pytest.raises(DocumentFileError, match="Plugins nécessaires"):
        load_document(path)
    assert path.read_bytes() == content
    rewrite(path, lambda data: next(r for r in data["objects"] if r["id"] == functions[0].id)["extension"]["data"]["source"].update(sketch_id=key()))
    with pytest.raises(DocumentFileError, match="esquisse"):
        load_document(path, registry=registry())
    original = SceneObject("Statique", BRepPrimAPI_MakeBox(1, 2, 3).Shape(), (.5, .5, .5))
    other = Document()
    other.add(original)
    BodyHistory().adopt(other, original.id)
    save_document(other, path, registry=registry())
    rewrite(path, lambda data: None, lambda entries: entries.update({f"resources/{original.id}/source_shape.brep": b"corruption"}))
    with pytest.raises(DocumentFileError, match="empreinte"):
        load_document(path, registry=registry())


def test_clipboard_refuses_uncomputed_parametric_flattening_and_service_closes():
    doc, sketch = sketch_document()
    body, _ = construction(doc, sketch)
    with pytest.raises(ClipboardError, match="Recalculez"):
        encode_objects(doc, (body.id,), registry())
    service = BodyHistory()
    service.close()
    with pytest.raises(RuntimeError, match="fermé"):
        service.result(doc, body.id)


def face_sketch(doc, template, body_id):
    # X1 only records dependencies. No face creation/reattachment is simulated.
    sketch_id = key()
    support = PlaneSupport(body_id, template.sketch.support.frame, 0, 1)
    obj = replace(template, id=sketch_id, sketch=replace(template.sketch, id=sketch_id, support=support))
    doc.add(obj)
    return obj


def test_face_sketch_uses_explicit_prior_state_and_never_final_body():
    doc, sketch = sketch_document()
    body, (base,) = construction(doc, sketch, (Operation.NEW_BODY,))
    supported = face_sketch(doc, sketch, body.id)
    ref = BodyStateRef(body.id, base.id)
    boss = feature(body.id, supported, Operation.BOSS, source=SketchInput(supported.id, (ref,)))
    doc.update_construction((replace(body, features=(base.id, boss.id)), boss))
    adapter, service = TraceAdapter(), BodyHistory()
    result = service.evaluate(doc, body.id, adapter)
    assert result.status is ResultStatus.READY
    assert [call[0] for call in adapter.calls] == [base.id, boss.id]
    assert adapter.calls[-1][2][ref][1] == base.id
    assert adapter.calls[-1][1] == adapter.calls[-1][2][ref]
    assert service.result_at(doc, ref).shape[1] == base.id
    count = doc.history.count
    for invalid_ref in (BodyStateRef(body.id, boss.id), BodyStateRef(body.id), BodyStateRef(key(), base.id)):
        with pytest.raises(ValueError):
            doc.replace_feature(replace(boss, source=SketchInput(supported.id, (invalid_ref,))))
    with pytest.raises(ValueError, match="explicites"):
        doc.replace_feature(replace(boss, source=SketchInput(supported.id)))
    assert doc.history.count == count and service.result(doc, body.id) is result


def test_cross_body_cycles_refused_and_invalidation_propagates_without_touching_other_document():
    doc, sketch = sketch_document()
    a, (a_base,) = construction(doc, sketch, (Operation.NEW_BODY,))
    b, (b_base,) = construction(doc, sketch, (Operation.NEW_BODY,))
    sa, sb = face_sketch(doc, sketch, a.id), face_sketch(doc, sketch, b.id)
    a_cut = feature(a.id, sb, Operation.CUT)
    b_cut = feature(b.id, sa, Operation.CUT)
    a_cut = replace(a_cut, source=SketchInput(sb.id, (BodyStateRef(b.id, b_cut.id),)))
    b_cut = replace(b_cut, source=SketchInput(sa.id, (BodyStateRef(a.id, a_cut.id),)))
    updates = (replace(a, features=(a_base.id, a_cut.id)), replace(b, features=(b_base.id, b_cut.id)), a_cut, b_cut)
    with pytest.raises(ValueError, match="circulaire"):
        doc.update_construction(updates)
    # B depends on A's base; A does not depend on B.
    b_cut = replace(b_cut, source=SketchInput(sa.id, (BodyStateRef(a.id, a_base.id),)))
    doc.update_construction((replace(b, features=(b_base.id, b_cut.id)), b_cut))
    service = BodyHistory()
    other, other_sketch = sketch_document()
    other_body, _ = construction(other, other_sketch, (Operation.NEW_BODY,))
    other_result = service.evaluate(other, other_body.id, TraceAdapter())
    service.evaluate(doc, b.id, TraceAdapter())
    service.evaluate(doc, a.id, TraceAdapter())
    doc.replace_feature(replace(doc.objects[a_base.id], parameters=ExtrusionParameters(depth=25)))
    assert service.result(doc, a.id).status is ResultStatus.DIRTY
    assert service.result(doc, b.id).status is ResultStatus.DIRTY
    assert service.result(other, other_body.id) is other_result
    with pytest.raises(ValueError):
        doc.remove(a.id)


def test_adapters_cannot_mutate_document_during_evaluation():
    doc, sketch = sketch_document()
    body, _ = construction(doc, sketch, (Operation.NEW_BODY,))
    class MutatingAdapter:
        def evaluate(self, *args):
            doc.rename(sketch.id, "interdit")
    count = doc.history.count
    result = BodyHistory().evaluate(doc, body.id, MutatingAdapter())
    assert result.status is ResultStatus.ERROR
    assert doc.history.count == count and doc.objects[sketch.id].name == sketch.name
    assert not doc.history.blocked


def test_transaction_rollback_preserves_redo_and_cache_is_never_an_undo_source():
    doc, sketch = sketch_document()
    body, functions = construction(doc, sketch)
    doc.rename(body.id, "Nom futur")
    doc.history.undo()
    old_index = doc.history.index
    service = BodyHistory()
    with pytest.raises(ValueError, match="annuler"):
        with doc.history.transaction("Édition refusée"):
            doc.replace_feature(replace(functions[0], parameters=ExtrusionParameters(depth=42)))
            raise ValueError("annuler")
    assert doc.history.index == old_index and doc.history.can_redo
    assert doc.objects[functions[0].id].parameters.depth == 10
    doc.history.redo()
    assert doc.objects[body.id].name == "Nom futur"
    with pytest.raises(ValueError, match="dérivé"):
        doc.replace_shape(body.id, object())


@pytest.mark.parametrize("damage", ["missing_source", "undeclared_resource", "path_traversal", "unknown_version"])
def test_resource_archive_is_strict(tmp_path, damage):
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    doc = Document()
    original = SceneObject("Statique", BRepPrimAPI_MakeBox(1, 2, 3).Shape(), (.5, .5, .5))
    doc.add(original)
    BodyHistory().adopt(doc, original.id)
    path = tmp_path / "strict.cada"
    save_document(doc, path, registry=registry())
    def corrupt(data):
        record = data["objects"][0]
        if damage == "missing_source":
            record["resources"].clear()
        elif damage == "undeclared_resource":
            record["resources"]["unknown"] = record["resources"].pop("source_shape")
            record["resources"]["unknown"]["path"] = f"resources/{original.id}/unknown.brep"
        elif damage == "path_traversal":
            record["resources"]["source_shape"]["path"] = "../outside.brep"
        else:
            record["extension"]["version"] = 2
    rewrite(path, corrupt)
    frozen = path.read_bytes()
    with pytest.raises(DocumentFileError):
        load_document(path, registry=registry())
    assert path.read_bytes() == frozen


def test_long_history_is_evaluated_without_python_recursion_or_extra_undo_entries():
    doc, sketch = sketch_document()
    body, functions = construction(doc, sketch, (Operation.NEW_BODY,) + (Operation.BOSS,) * 1199)
    class CountingAdapter:
        count = 0
        def evaluate(self, function, sketch, previous, supports):
            self.count += 1
            if self.count > 1:
                assert previous == functions[self.count - 2].id
            return EvaluationResult(ResultStatus.READY, function.id)
    adapter = CountingAdapter()
    result = BodyHistory().evaluate(doc, body.id, adapter)
    assert result.shape == functions[-1].id and adapter.count == 1200
    assert doc.history.count == 1


def test_noop_preserves_redo_and_mixed_parent_child_selection_deletes_once():
    doc, sketch = sketch_document()
    body, functions = construction(doc, sketch)
    doc.rename(body.id, "Nom futur")
    doc.history.undo()
    before = doc.history.count, doc.history.index
    doc.replace_feature(doc.objects[functions[0].id])
    doc.update_construction((doc.objects[body.id],))
    assert (doc.history.count, doc.history.index) == before and doc.history.can_redo
    doc.remove_objects((body.id, functions[0].id, functions[-1].id))
    assert body.id not in doc.objects and all(f.id not in doc.objects for f in functions)
    doc.history.undo()
    assert doc.objects[body.id].features == body.features
    validate_graph(doc.objects)


def test_evaluation_order_is_independent_of_dictionary_insertion_order():
    doc, sketch = sketch_document()
    body_id = key()
    first = feature(body_id, sketch)
    second = feature(body_id, sketch, Operation.BOSS)
    third = feature(body_id, sketch, Operation.CUT)
    body = BodyObject("Corps", None, (.5, .6, .7), id=body_id, features=(first.id, second.id, third.id))
    doc.update_construction((third, body, second, first))
    adapter = TraceAdapter()
    BodyHistory().evaluate(doc, body.id, adapter)
    assert [call[0] for call in adapter.calls] == list(body.features)


def test_contour_source_ids_must_belong_to_real_sketch_curves():
    doc, sketch = sketch_document()
    circle, point = Circle(Vec2(0, 0), 5), Point(Vec2(2, 3))
    add_entity(doc, sketch.id, circle)
    add_entity(doc, sketch.id, point)
    body, (base,) = construction(doc, doc.objects[sketch.id], (Operation.NEW_BODY,))
    doc.replace_feature(replace(base, parameters=ExtrusionParameters(contours=(circle.id,))))
    before = dict(doc.objects), doc.history.count
    for invalid in (key(), point.id):
        with pytest.raises(ValueError, match="entité source"):
            doc.replace_feature(replace(base, parameters=ExtrusionParameters(contours=(invalid,))))
    assert dict(doc.objects) == before[0] and doc.history.count == before[1]


def test_reference_plane_change_and_undo_invalidate_bodies_using_its_sketch():
    doc, sketch = sketch_document()
    body, _ = construction(doc, sketch, (Operation.NEW_BODY,))
    service = BodyHistory()
    service.evaluate(doc, body.id, TraceAdapter())
    doc.replace_shape(sketch.sketch.support.plane_id, None)
    assert service.result(doc, body.id).status is ResultStatus.DIRTY
    assert doc.objects[body.id].shape is None
    service.evaluate(doc, body.id, TraceAdapter())
    doc.history.undo()
    assert service.result(doc, body.id).status is ResultStatus.DIRTY
