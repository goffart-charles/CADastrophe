"""Undoable isolation/appearance and schema migration, using actual B-Reps."""
from dataclasses import replace
import json
from zipfile import ZipFile

import pytest

from cadastrophe.core.persistence import DocumentFileError, load_document, save_document
from cadastrophe.sample_scene import demo_document


def bodies(document):
    return [obj for obj in document.objects.values() if not obj.plane]


def test_isolation_is_one_command_and_restores_exact_visibility_pattern():
    doc = demo_document()
    first, second, *_ = bodies(doc)
    plane = next(obj for obj in doc.objects.values() if obj.plane)
    doc.set_visible(first.id, False)
    doc.set_visible(plane.id, True)
    doc.history.mark_clean()
    before = {key: obj.visible for key, obj in doc.objects.items()}
    index = doc.history.index
    doc.isolate([first.id, second.id])
    assert {key for key, obj in doc.objects.items() if obj.visible} == {first.id, second.id}
    assert doc.history.index == index + 1 and doc.is_modified
    doc.history.undo()
    assert {key: obj.visible for key, obj in doc.objects.items()} == before and not doc.is_modified
    doc.history.redo()
    assert {key for key, obj in doc.objects.items() if obj.visible} == {first.id, second.id}
    count = doc.history.count
    doc.isolate([first.id, second.id])
    assert doc.history.count == count
    with pytest.raises(ValueError):
        doc.isolate([])
    with pytest.raises(ValueError):
        doc.isolate([first.id, plane.id])
    assert doc.history.count == count


def test_transparency_batch_is_atomic_and_preserves_identity_shape_revision_and_selection():
    doc = demo_document()
    first, second, *_ = bodies(doc)
    doc.select_objects([first.id, second.id])
    doc.set_transparencies({first.id: 0.5, second.id: 1.})
    assert doc.history.count == 1 and doc.selected_ids == (first.id, second.id)
    for obj, expected in ((first, 0.5), (second, 1.)):
        current = doc.objects[obj.id]
        assert current.transparency == expected and current.shape is obj.shape
        assert current.revision == obj.revision and current.color == obj.color
    doc.history.undo()
    assert not doc.is_modified and doc.objects[first.id].transparency == 0
    doc.history.redo()
    doc.set_transparencies({first.id: 0.5})
    assert doc.history.count == 1
    with pytest.raises(KeyError):
        doc.set_transparencies({first.id: 0., "missing": 0.5})
    assert doc.objects[first.id].transparency == 0.5 and doc.history.count == 1


@pytest.mark.parametrize("value", [-0.01, 1.01, True, "0.5", float("inf"), float("nan")])
def test_invalid_transparency_rejected_before_history_and_in_snapshots(value):
    doc = demo_document()
    obj = bodies(doc)[0]
    with pytest.raises(ValueError):
        doc.set_transparencies({obj.id: value})
    with pytest.raises(ValueError):
        replace(obj, transparency=value)
    assert doc.history.count == 0 and doc.objects[obj.id] is obj


def test_reference_planes_keep_their_default_style_and_cannot_use_body_transparency_command():
    doc = demo_document()
    plane = next(obj for obj in doc.objects.values() if obj.plane)
    assert plane.transparency == 0.82
    with pytest.raises(ValueError):
        doc.set_transparencies({plane.id: 0.5})
    assert doc.history.count == 0


def test_transparency_round_trip_uses_current_schema_for_all_appearances(tmp_path):
    doc = demo_document()
    first, second, *_ = bodies(doc)
    old_path, new_path = tmp_path / "v1.cada", tmp_path / "v3.cada"
    save_document(doc, old_path)
    doc.set_transparencies({first.id: 0.5, second.id: 1.})
    save_document(doc, new_path)
    assert not doc.is_modified and doc.history.can_undo
    with ZipFile(new_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["schema_version"] == 9 and manifest["required_plugins"] == []
    assert all("transparency" in record for record in manifest["objects"])
    old, loaded = load_document(old_path), load_document(new_path)
    assert old.objects[first.id].transparency == 0
    assert loaded.objects[first.id].transparency == 0.5 and loaded.objects[second.id].transparency == 1
    assert not loaded.is_modified and loaded.history.count == 0
    assert all(loaded.objects[key].transparency == obj.transparency for key, obj in doc.objects.items())
    doc.history.undo()
    save_document(doc, old_path)
    with ZipFile(old_path) as archive:
        assert json.loads(archive.read("manifest.json"))["schema_version"] == 9


@pytest.mark.parametrize("damage", ["missing", "null", "bool", "string", "range", "v1_field"])
def test_corrupt_appearance_metadata_is_refused(tmp_path, damage):
    doc = demo_document()
    doc.set_transparencies({bodies(doc)[0].id: 0.5})
    path = tmp_path / "invalid.cada"
    save_document(doc, path)
    with ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries["manifest.json"])
    record = manifest["objects"][0]
    if damage == "missing":
        del record["transparency"]
    elif damage == "v1_field":
        manifest["schema_version"] = 1
    else:
        record["transparency"] = {"null": None, "bool": True, "string": "0.5", "range": -1}[damage]
    entries["manifest.json"] = json.dumps(manifest).encode()
    with ZipFile(path, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    with pytest.raises(DocumentFileError):
        load_document(path)
