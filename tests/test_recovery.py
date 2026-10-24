"""Native snapshots, atomic failures, dead-process locks and safe restoration."""
from dataclasses import asdict, replace, dataclass, field, fields
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.GProp import GProp_GProps
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cadastrophe.document import Document, SceneObject
from cadastrophe.core.documents import DocumentManager
from cadastrophe.core.object_types import ObjectTypeRegistry, ObjectCodec
from cadastrophe.core.persistence import save_document, load_document
from cadastrophe.core.preferences import Preferences, PreferenceStore, validate_preferences
from cadastrophe.core.recovery import RecoveryService, RecoveryError


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def model(app, tmp_path):
    documents = DocumentManager()
    document = documents.add(Document("Pièce éçà"))
    body = SceneObject("Boîte", BRepPrimAPI_MakeBox(12, 23, 34).Shape(), (.2, .4, .6))
    document.add(body)
    document.history.clear()
    service = RecoveryService(documents, ObjectTypeRegistry(), tmp_path / "recovery")
    service.configure(True, 5)
    yield documents, document, body, service
    service.shutdown()


def reader(service):
    documents = DocumentManager()
    return RecoveryService(documents, service.registry, service.directory)


def make_abandoned(document, body, service):
    document.rename(body.id, "Version à récupérer")
    assert service.snapshot_now() == 1
    entry = service._current[document.id]
    service.shutdown()  # release without a normal document close
    return entry


def test_recovery_rejects_old_snapshot_extension_without_deleting_it(model):
    _, document, body, service = model
    entry = make_abandoned(document, body, service)
    assert entry.snapshot_path.suffix == ".cada"
    legacy = entry.snapshot_path.with_suffix(".caop")
    entry.snapshot_path.rename(legacy)
    record = json.loads(entry.record_path.read_text(encoding="utf-8"))
    record["snapshot"] = legacy.name
    entry.record_path.write_text(json.dumps(record), encoding="utf-8")
    other = reader(service)
    try:
        entries = other.entries()
        assert entries == ()
        assert legacy.exists() and entry.record_path.exists()
    finally:
        other.shutdown()


def test_snapshot_does_not_save_user_file_or_change_history_selection_and_metadata(model, tmp_path):
    documents, document, body, service = model
    original = tmp_path / "original.cada"
    save_document(document, original)
    old_bytes = original.read_bytes()
    document.rename(body.id, "Version A")
    document.rename(body.id, "Version B")
    document.history.undo()
    document.select_objects([body.id])
    before = (document.file_path, document.history.count, document.history.index, document.selected_ids)
    assert service.snapshot_now() == 1 and service.snapshot_now() == 0
    assert (document.file_path, document.history.count, document.history.index, document.selected_ids) == before
    assert document.history.can_redo and document.is_modified and original.read_bytes() == old_bytes
    entry = service._current[document.id]
    assert entry.original_path == str(original.resolve())
    loaded = load_document(entry.snapshot_path)
    assert loaded.objects[body.id].name == "Version A" and document.objects[body.id] is not loaded.objects[body.id]
    assert not loaded.objects[body.id].shape.IsPartner(body.shape)
    document.history.undo()  # return to saved baseline: stale recovery copy removed
    assert not document.is_modified and not entry.record_path.exists() and not entry.snapshot_path.exists()


def test_timer_saves_all_dirty_documents_and_skips_clean_reserved_and_unchanged_ones(model):
    documents, document, body, service = model
    other = documents.add(Document("Autre"))
    other.add(SceneObject("Autre boîte", body.shape, body.color))
    document.rename(body.id, "Modifié")
    token = object()
    document.history.reserve(token)
    assert service.snapshot_now() == 0
    document.history.release(token)
    service.timer.setInterval(5)
    # Wait for the observable timer result, not a 30 ms scheduling assumption.
    for _ in range(100):
        if set(service._current) == {document.id, other.id}:
            break
        QTest.qWait(10)
    assert set(service._current) == {document.id, other.id}
    assert service.snapshot_now() == 0
    assert document.is_modified and other.is_modified
    service.configure(False, 2)
    document.rename(body.id, "Après désactivation")
    assert not service.timer.isActive() and service.snapshot_now() == 0
    assert service._current[document.id].snapshot_path.exists()


@pytest.mark.parametrize("stage", ["native", "notice"])
def test_failed_atomic_generation_preserves_previous_snapshot_and_removes_temporary(model, monkeypatch, stage):
    documents, document, body, service = model
    document.rename(body.id, "Avant échec")
    assert service.snapshot_now() == 1
    entry = service._current[document.id]
    old_notice, old_payload = entry.record_path.read_bytes(), entry.snapshot_path.read_bytes()
    document.rename(body.id, "Après échec")
    original_replace = os.replace

    def fail(source, destination):
        if Path(destination).suffix == (".cada" if stage == "native" else ".json"):
            raise OSError("écriture simulée impossible")
        return original_replace(source, destination)

    errors = []
    service.message.connect(errors.append)
    monkeypatch.setattr("cadastrophe.core.recovery.os.replace", fail)
    assert service.snapshot_now() == 0 and errors
    assert entry.record_path.read_bytes() == old_notice and entry.snapshot_path.read_bytes() == old_payload
    assert set(entry.record_path.parent.iterdir()) == {entry.record_path, entry.snapshot_path, entry.record_path.parent / "session.lock"}
    assert document.is_modified
    monkeypatch.undo()
    assert service.snapshot_now() == 1
    latest = service._current[document.id]
    assert latest.snapshot != entry.snapshot and not entry.snapshot_path.exists()
    assert load_document(latest.snapshot_path).objects[body.id].name == "Après échec"


def test_live_session_is_excluded_then_recovery_stays_dirty_and_requires_save_as(model, tmp_path):
    documents, document, body, service = model
    original = tmp_path / "original.cada"
    save_document(document, original)
    original_bytes = original.read_bytes()
    document.rename(body.id, "Récupérer ceci")
    assert service.snapshot_now() == 1
    other = reader(service)
    try:
        assert other.entries() == ()
        service.shutdown()
        entry, = other.entries()
        recovered = other.restore(entry)
        assert recovered.id == document.id and recovered.file_path is None
        assert recovered.is_modified and recovered.history.count == 0 and not recovered.history.can_undo
        assert "récupéré" in recovered.name and recovered.objects[body.id].name == "Récupérer ceci"
        assert original.read_bytes() == original_bytes and entry.snapshot_path.exists()
        other.configure(True, 5)
        assert other.snapshot_now() == 1  # protect edits in a recovered document too
        recovered.rename(body.id, "Après récupération")
        recovered.history.undo()
        assert recovered.is_modified  # no false clean baseline after recovery
        target = tmp_path / "récupéré.cada"
        save_document(recovered, target)
        assert not recovered.is_modified and recovered.file_path == target.resolve()
        assert not entry.record_path.exists() and not entry.snapshot_path.exists()
        assert not list(other.directory.glob("*/*.cada"))
        assert original.read_bytes() == original_bytes
    finally:
        other.shutdown()


def test_normal_document_close_and_replacement_clean_snapshots(model):
    documents, document, body, service = model
    document.rename(body.id, "Fermeture")
    assert service.snapshot_now() == 1
    entry = service._current[document.id]
    documents.replace(document.id, Document("Remplacement"))
    assert not entry.record_path.exists() and not entry.snapshot_path.exists()
    replacement = documents.active_document
    replacement.add(SceneObject("Corps", body.shape, body.color))
    assert service.snapshot_now() == 1
    entry = service._current[replacement.id]
    documents.remove(replacement.id)
    assert not entry.record_path.exists() and not entry.snapshot_path.exists()


@pytest.mark.parametrize("damage", ["future", "json", "traversal", "identity", "date", "duplicate", "payload", "missing"])
def test_corrupt_or_incompatible_recovery_is_preserved_without_opening_a_document(model, damage):
    documents, document, body, service = model
    entry = make_abandoned(document, body, service)
    data = json.loads(entry.record_path.read_text(encoding="utf-8"))
    if damage == "future": data["schema_version"] = 99
    elif damage == "traversal": data["snapshot"] = "../original.cada"
    elif damage == "identity": data["document_id"] = service.session_id
    elif damage == "date": data["saved_at"] = "not a date"
    text = json.dumps(data)
    if damage == "json": text = "incomplete {"
    elif damage == "duplicate": text = text.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')
    entry.record_path.write_text(text, encoding="utf-8")
    if damage == "payload": entry.snapshot_path.write_bytes(b"corrupt")
    elif damage == "missing": entry.snapshot_path.unlink()
    old_notice = entry.record_path.read_bytes()
    other = reader(service)
    try:
        entries = other.entries()
        if damage in ("payload", "missing"):
            assert entries == (entry,)
        else:
            assert entries == ()
        with pytest.raises(RecoveryError):
            other.restore(entry)
        assert not other.documents.documents and entry.record_path.read_bytes() == old_notice
    finally:
        other.shutdown()


def test_restore_collision_does_not_change_tabs_or_delete_candidate_and_discard_is_explicit(model):
    documents, document, body, service = model
    entry = make_abandoned(document, body, service)
    other = reader(service)
    try:
        other.documents.add(Document("Déjà ouvert", document_id=document.id))
        with pytest.raises(RecoveryError, match="déjà ouvert"):
            other.restore(entry)
        assert len(other.documents.documents) == 1 and entry.snapshot_path.exists()
        other.discard(entry)
        assert not entry.snapshot_path.exists() and not entry.record_path.exists()
    finally:
        other.shutdown()


def test_missing_plugin_does_not_damage_recovery_and_available_codec_restores_data(model):
    documents, document, body, service = model

    @dataclass(frozen=True)
    class Tagged(SceneObject):
        tag: str = field(default="donnée éçà", kw_only=True)

    def decode(base, data):
        return Tagged(**{item.name: getattr(base, item.name) for item in fields(SceneObject)}, tag=data["tag"])

    codec = ObjectCodec("fixture.tagged.body", Tagged, 1, lambda obj: dict(tag=obj.tag), decode)
    service.registry.register("fixture.tagged", codec)
    tagged = Tagged("Corps d’extension", body.shape, body.color)
    document.add(tagged)
    entry = make_abandoned(document, body, service)
    other = RecoveryService(DocumentManager(), ObjectTypeRegistry(), service.directory)
    original_bytes = entry.snapshot_path.read_bytes()
    try:
        with pytest.raises(RecoveryError, match="Plugins nécessaires"):
            other.restore(entry)
        assert not other.documents.documents and entry.snapshot_path.read_bytes() == original_bytes
        other.registry.register("fixture.tagged", codec)
        restored = other.restore(entry)
        assert type(restored.objects[tagged.id]) is Tagged and restored.objects[tagged.id].tag == tagged.tag
        assert restored.is_modified and restored.file_path is None
    finally:
        other.shutdown()


def test_real_crash_process_releases_stale_lock_and_recovers_without_overwriting_original(app, tmp_path):
    worker = Path(__file__).parent / "fixtures/recovery_crash.py"
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(root)
    result = subprocess.run([sys.executable, str(worker), str(tmp_path)], cwd=root,
                            env=environment, capture_output=True, timeout=30)
    assert result.returncode == 73, result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace")
    path = tmp_path / "original.cada"
    original_bytes = path.read_bytes()
    original = load_document(path)
    body = next(iter(original.objects.values()))
    assert body.name == "Avant crash" and body.transparency == 0
    documents = DocumentManager()
    service = RecoveryService(documents, ObjectTypeRegistry(), tmp_path / "recovery")
    try:
        entry, = service.entries()  # QLockFile must detect the actual dead PID
        recovered = service.restore(entry)
        obj = recovered.objects[body.id]
        assert obj.name == "Après crash éçà" and obj.transparency == .5
        properties = GProp_GProps()
        brepgprop.VolumeProperties(obj.shape, properties)
        assert properties.Mass() == pytest.approx(12 * 23 * 34)
        assert recovered.file_path is None and recovered.is_modified and path.read_bytes() == original_bytes
        documents.remove(recovered.id)
        assert not entry.record_path.exists() and not entry.snapshot_path.exists()
    finally:
        service.shutdown()


def test_old_preferences_are_preserved_and_rejected(tmp_path):
    path = tmp_path/"preferences.json"
    raw = json.dumps(dict(schema_version=1, preferences=asdict(Preferences()), layout={}))
    path.write_text(raw, encoding="utf8")
    store = PreferenceStore(path)
    assert store.load() == (Preferences(), {})
    assert store.error and not store.writable
    with pytest.raises(ValueError):
        store.save(Preferences(), {})
    assert path.read_text(encoding="utf8") == raw


@pytest.mark.parametrize("change", [dict(autosave_enabled=1), dict(autosave_interval_minutes=True),
                                    dict(autosave_interval_minutes=0), dict(autosave_interval_minutes=61),
                                    dict(autosave_interval_minutes=2.5)])
def test_autosave_preferences_are_strictly_validated(change):
    with pytest.raises(ValueError):
        validate_preferences(dict(asdict(Preferences()), **change))
