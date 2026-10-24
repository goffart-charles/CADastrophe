"""Construction contracts exercised on native recipes, shapes and persistence."""
from dataclasses import replace
import json
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer

from cadastrophe.core.body_history import BodyHistory, body_result, evaluate_body, result_at
from cadastrophe.core.construction_state import feature_dependencies
from cadastrophe.core.feature_model import (BodyStateRef, Operation, ResultStatus,
    ReferenceSlot, ReferenceResolutionError, ExtrusionParameters, SketchInput)
from cadastrophe.core.feature_objects import ExtrusionFeature, ReplayFeature, ShellFeature, BodyObject, ConstructionFeature
from cadastrophe.core.operation_model import GeometryReference, ReplayParameters, ShellParameters
from cadastrophe.core.persistence import load_document, save_document, DocumentFileError
from cadastrophe.core.prefix_signatures import state_signatures
from cadastrophe.core.topology_refs import TopologyReference
from cadastrophe.document import ObjectType
from test_modeling_rebuild import base, face_source, append, flush
from test_plugins import harness, plugin_root
from test_primitives import volume


def test_construction_contracts_order_is_separate_from_explicit_references(harness):
    data = base(harness)
    doc, sketch, _, _, _, body = data
    first = doc.objects[body.features[0]]
    source = face_source(harness, data, body)
    body, boss = append(harness, data, source, Operation.BOSS, 8)
    service = harness.services.service("cadastrophe.modeling.history")
    inputs = service.dependencies(doc, boss.id)
    before = BodyStateRef(body.id, first.id)
    assert inputs.predecessor == before
    assert before in inputs.evaluation_states
    assert inputs.explicit_producers == {first.id}
    assert any(slot.target_id == source.id for slot in inputs.references)
    initial = feature_dependencies(doc.objects, first)
    assert initial.predecessor is None and initial.explicit_producers == frozenset()
    assert initial.references[0].target_id == sketch.id
    assert service.diagnostics(doc, body.id) == ()


def test_construction_contracts_shell_input_is_structural_but_faces_are_semantic(harness):
    data = base(harness)
    doc, _, _, _, _, body = data
    before = BodyStateRef(body.id, body.features[-1])
    face = GeometryReference("face", body.id, before.producer_id,
                             TopologyReference("Face", ("persisted-face",)))
    shell = ShellFeature("Coque", None, (.5, .6, .7), kind=ObjectType.FEATURE,
        body_id=body.id, operation=Operation.SHELL, input_state=before,
        parameters=ShellParameters((face,)))
    candidate = {**doc.objects, shell.id: shell, body.id: replace(body, features=(*body.features, shell.id))}
    inputs = feature_dependencies(candidate, shell)
    assert inputs.predecessor == before
    assert len(inputs.references) == 1
    assert inputs.references[0].topology == face.topology
    assert inputs.references[0].state == before


def test_construction_contracts_replay_keeps_sources_and_effect_prerequisites(harness):
    data = base(harness)
    doc, _, _, _, _, body = data
    source = face_source(harness, data, body)
    body, boss = append(harness, data, source, Operation.BOSS, 8)
    pattern = ReplayFeature("Motif", None, (.5, .6, .7), kind=ObjectType.FEATURE,
        body_id=body.id, operation=Operation.LINEAR_PATTERN,
        parameters=ReplayParameters((boss.id,), GeometryReference("axis", values=(0., 0., 0., 1., 0., 0.))))
    candidate = {**doc.objects, pattern.id: pattern, body.id: replace(body, features=(*body.features, pattern.id))}
    inputs = feature_dependencies(candidate, pattern)
    assert inputs.predecessor == BodyStateRef(body.id, boss.id)
    assert inputs.explicit_producers == {boss.id}
    assert BodyStateRef(body.id, body.features[0]) in inputs.evaluation_states
    assert any(s.path == "parameters.sources.0" for s in inputs.references)


def test_construction_contracts_effective_inputs_and_cosmetics_preserve_prefixes(harness):
    data = base(harness)
    doc, _, _, _, _, body = data
    source = face_source(harness, data, body)
    child, _ = append(harness, data, source, Operation.NEW_BODY, 5, new=True)
    body, boss = append(harness, data, source, Operation.BOSS, 8)
    original = state_signatures(doc.objects)
    first = BodyStateRef(body.id, body.features[0])
    last = BodyStateRef(body.id, boss.id)
    child_ref = BodyStateRef(child.id, child.features[-1])
    for revised in (replace(boss, enabled=False), replace(boss, parameters=replace(boss.parameters, depth=12))):
        changed = state_signatures({**doc.objects, boss.id: revised})
        assert original[first] == changed[first]
        assert original[child_ref] == changed[child_ref]
        assert original[last] != changed[last]
    assert state_signatures({**doc.objects, boss.id: replace(boss, visible=False, name="Autre nom")}) == original
    # Materialized configuration values use existing recipes and exact-prefix caches.
    child_before = doc.objects[child.id]
    doc.replace_feature(replace(boss, parameters=replace(boss.parameters, depth=12)))
    assert doc.objects[child.id] is child_before
    flush(harness)
    assert doc.objects[child.id] is child_before
    assert BRepCheck_Analyzer(doc.objects[body.id].shape).IsValid()


def failing_chain(harness):
    data = base(harness)
    doc, _, _, _, _, body = data
    source = face_source(harness, data, body, center=(500, 500))
    before = BodyStateRef(body.id, body.features[0])
    def cut():
        return ExtrusionFeature("Trou hors matière", None, (.5, .6, .7), kind=ObjectType.FEATURE,
            body_id=body.id, operation=Operation.CUT, source=SketchInput(source.id, (before,)),
            parameters=ExtrusionParameters(depth=5))
    failed, blocked = cut(), cut()
    doc.update_construction((replace(body, features=(*body.features, failed.id, blocked.id)), failed, blocked))
    flush(harness)
    return doc, body, failed, blocked


def test_construction_contracts_native_error_keeps_original_cause_and_recovers(harness):
    doc, body, failed, blocked = failing_chain(harness)
    service = harness.services.service("cadastrophe.modeling.history")
    result = body_result(doc, body.id)
    assert result.status is ResultStatus.BLOCKED and result.shape is None
    error = result_at(doc, BodyStateRef(body.id, failed.id))
    assert error.status is ResultStatus.ERROR
    assert error.diagnostics[0].root_ids == (failed.id,)
    assert result.diagnostics[0].feature_id == blocked.id
    assert result.diagnostics[0].root_ids == (failed.id,)
    assert [d.feature_id for d in service.diagnostics(doc, body.id)] == [failed.id, blocked.id]
    assert service.last_valid(doc, body.id).status is ResultStatus.READY
    doc.history.undo()
    flush(harness)
    assert service.diagnostics(doc, body.id) == ()
    assert volume(doc.objects[body.id].shape) == pytest.approx(120000)
    doc.history.redo()
    flush(harness)
    assert body_result(doc, body.id).diagnostics[0].root_ids == (failed.id,)


def test_construction_contracts_reference_slot_survives_blocking(harness):
    data = base(harness)
    doc, _, _, _, _, body = data
    source = face_source(harness, data, body)
    body, boss = append(harness, data, source, Operation.BOSS, 8)
    adapter = harness.services.service("cadastrophe.modeling.extrusions").evaluator(doc)
    slot = ReferenceSlot(boss.id, "profiles.0", source.id)
    class ResolverFailure:
        def evaluate(self, feature, *args):
            if feature.id == boss.id:
                raise ReferenceResolutionError("Le profil demandé n'est plus disponible.", slot)
            return adapter.evaluate(feature, *args)
        def prepare_feature(self, *args):
            return adapter.prepare_feature(*args)
    result = evaluate_body(doc, body.id, ResolverFailure())
    assert result.status is ResultStatus.ERROR
    assert result.diagnostics[0].reference == slot
    assert result.diagnostics[0].code == "reference_invalid"


def test_construction_contracts_activation_is_strict_and_never_silently_ignored(harness):
    data = base(harness)
    doc, _, _, _, _, body = data
    first = doc.objects[body.features[0]]
    with pytest.raises(ValueError, match="bool"):
        replace(first, enabled=1)
    doc.replace_feature(replace(first, enabled=False))
    flush(harness)
    result = body_result(doc, body.id)
    assert result.status is ResultStatus.UNAVAILABLE and result.shape is None
    assert result.diagnostics[0].code == "disabled_without_base"
    doc.history.undo()
    flush(harness)
    assert body_result(doc, body.id).status is ResultStatus.READY
    assert volume(doc.objects[body.id].shape) == pytest.approx(120000)


def test_construction_contracts_default_identity_round_trip(harness, tmp_path):
    data = base(harness)
    doc, _, _, _, _, body = data
    path = tmp_path / "contracts.cada"
    identity, ids = doc.default_configuration, tuple(doc.objects)
    save_document(doc, path, registry=harness.types)
    with ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries["manifest.json"])
    first = next(o for o in manifest["objects"] if o["id"] == body.features[0])
    assert first["extension"]["data"]["enabled"] is True
    entries["manifest.json"] = json.dumps(manifest).encode()
    with ZipFile(path, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    loaded = load_document(path, registry=harness.types)
    assert loaded.default_configuration == identity
    assert tuple(loaded.objects) == ids
    assert loaded.objects[body.features[0]].enabled is True
    assert BRepCheck_Analyzer(loaded.objects[body.id].shape).IsValid()
    assert volume(loaded.objects[body.id].shape) == pytest.approx(120000)
    save_document(loaded, path, registry=harness.types)
    reloaded = load_document(path, registry=harness.types)
    assert reloaded.default_configuration == identity
    assert reloaded.default_configuration.id != body.id
    assert tuple(reloaded.objects) == ids
    assert body_result(reloaded, body.id).diagnostics == ()


def test_construction_contracts_diagnostics_are_derived_and_not_serialized(harness, tmp_path):
    doc, body, failed, _ = failing_chain(harness)
    path = tmp_path / "failed.cada"
    save_document(doc, path, registry=harness.types)
    with ZipFile(path) as archive:
        text = archive.read("manifest.json").decode()
    assert '"diagnostics"' not in text and '"root_ids"' not in text
    loaded = load_document(path, registry=harness.types)
    assert body_result(loaded, body.id).status is ResultStatus.BLOCKED
    assert body_result(loaded, body.id).diagnostics[0].root_ids == (failed.id,)


def test_construction_contracts_unknown_reference_locations_are_rejected():
    key = str(uuid4())
    with pytest.raises(ValueError, match="incompatible"):
        ReferenceSlot(key, "support", key, BodyStateRef(str(uuid4())))
    with pytest.raises(ValueError, match="topologique"):
        ReferenceSlot(key, "support", key, topology=object())


@pytest.mark.parametrize("fault", ["missing", "not_boolean", "legacy_with_activation"])
def test_construction_contracts_corrupt_activation_is_rejected(harness, tmp_path, fault):
    data = base(harness)
    doc, _, _, _, _, body = data
    path = tmp_path / "corrupt.cada"
    save_document(doc, path, registry=harness.types)
    with ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries["manifest.json"])
    extension = next(o for o in manifest["objects"] if o["id"] == body.features[0])["extension"]
    if fault == "missing":
        extension["data"].pop("enabled")
    elif fault == "not_boolean":
        extension["data"]["enabled"] = 1
    else:
        extension["version"] -= 1
    entries["manifest.json"] = json.dumps(manifest).encode()
    with ZipFile(path, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    before = dict(doc.objects)
    with pytest.raises(DocumentFileError):
        load_document(path, registry=harness.types)
    assert dict(doc.objects) == before
    assert body_result(doc, body.id).status is ResultStatus.READY


def test_construction_contracts_old_edge_codec_is_rejected(harness, tmp_path):
    base(harness)
    source = Path(__file__).parent / "fonctions/conge/02-apres.cada"
    with ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries["manifest.json"])
    found = False
    for obj in manifest["objects"]:
        extension = obj.get("extension", {})
        if extension.get("type_id") == "cadastrophe.modeling.edges":
            extension["version"] = 1
            extension["data"].pop("enabled", None)
            extension["data"]["parameters"].pop("references")
            found = True
    assert found
    path = tmp_path / "legacy-edges.cada"
    entries["manifest.json"] = json.dumps(manifest).encode()
    with ZipFile(path, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    frozen = path.read_bytes()
    with pytest.raises(DocumentFileError, match="Version du type"):
        load_document(path, registry=harness.types)
    assert path.read_bytes() == frozen


@pytest.mark.parametrize("relative", [
    "fonctions/equations/01-plaque-pilotee.cada",
    "fonctions/conge/02-apres.cada",
    "pieces/boitier/01-piece.cada",
    "pieces/adaptateur-lisse/01-piece.cada",
])
def test_construction_contracts_existing_projects_round_trip(harness, tmp_path, relative):
    base(harness)  # Register the real sketch/modeling providers and restorers.
    loaded = load_document(Path(__file__).parent / relative, registry=harness.types)
    identity, ids = loaded.default_configuration, tuple(loaded.objects)
    variables = loaded.variables
    expressions = {key: obj.expressions for key, obj in loaded.objects.items()}
    expected = {}
    for obj in loaded.objects.values():
        if isinstance(obj, ConstructionFeature):
            assert obj.enabled
        if isinstance(obj, BodyObject):
            assert body_result(loaded, obj.id).status is ResultStatus.READY
            assert BRepCheck_Analyzer(obj.shape).IsValid()
            expected[obj.id] = volume(obj.shape)
    assert expected
    path = tmp_path / "migrated.cada"
    save_document(loaded, path, registry=harness.types)
    reopened = load_document(path, registry=harness.types)
    assert reopened.default_configuration == identity
    assert tuple(reopened.objects) == ids
    assert reopened.variables == variables
    assert {key: obj.expressions for key, obj in reopened.objects.items()} == expressions
    for key, expected_volume in expected.items():
        assert body_result(reopened, key).status is ResultStatus.READY
        assert BRepCheck_Analyzer(reopened.objects[key].shape).IsValid()
        assert volume(reopened.objects[key].shape) == pytest.approx(expected_volume, rel=1e-10)
        assert BodyHistory().diagnostics(reopened, key) == ()
