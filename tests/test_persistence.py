"""Native round trips and rejection/atomicity checks against real B-Reps."""
from hashlib import sha256
import json
import os
import struct
from zipfile import ZipFile

import pytest
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.GProp import GProp_GProps
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.gp import gp_Trsf, gp_Vec
from OCC.Core.TopLoc import TopLoc_Location

from cadastrophe.core import persistence
from cadastrophe.core.history import HistoryBusyError
from cadastrophe.core.persistence import DocumentFileError, UnsupportedVersionError, load_document, save_document
from cadastrophe.document import Document
from cadastrophe.sample_scene import demo_document


def geometry(shape):
    mass = GProp_GProps()
    brepgprop.VolumeProperties(shape, mass)
    area = GProp_GProps()
    brepgprop.SurfaceProperties(shape, area)
    bounds = Bnd_Box()
    # Ignore triangulation caches, which are transient view data.
    brepbndlib.Add(shape, bounds, False)
    count = 0
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        count += 1
        explorer.Next()
    return (mass.Mass(), area.Mass(), *bounds.Get()), count


def contents(path):
    with ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def rewrite(path, entries):
    with ZipFile(path, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)


def test_round_trip_preserves_real_geometry_metadata_planes_and_identity(tmp_path):
    document = demo_document("Pièce de précision éçà")
    objects = list(document.objects.values())
    transform = gp_Trsf()
    transform.SetTranslation(gp_Vec(0.123456789123, -1.234567891234, 3.456789123456))
    document.replace_shape(objects[0].id, objects[0].shape.Moved(TopLoc_Location(transform)))
    document.rename(objects[0].id, "Corps modifié éçà")
    document.set_visible(objects[1].id, False)
    document.set_visible(objects[-1].id, True)
    document.select_objects([objects[0].id])
    path = tmp_path / "dossier éçà" / "pièce précise.cada"
    path.parent.mkdir()
    original = tuple(document.objects.values())
    save_document(document, path)
    assert not document.is_modified and document.history.can_undo and document.selected_ids
    loaded = load_document(path)
    assert loaded is not document and loaded.id == document.id and loaded.name == document.name
    assert loaded.file_path == path.resolve() and loaded.length_unit == "mm"
    assert list(loaded.objects) == list(document.objects) and not loaded.selected_ids
    assert loaded.history.count == 0 and not loaded.is_modified
    for before, after in zip(original, loaded.objects.values()):
        assert (before.id, before.name, before.visible, before.color, before.kind, before.revision, before.plane) == (
            after.id, after.name, after.visible, after.color, after.kind, after.revision, after.plane)
        assert BRepCheck_Analyzer(after.shape).IsValid()
        expected, count = geometry(before.shape)
        actual, loaded_count = geometry(after.shape)
        assert actual == pytest.approx(expected, rel=1e-12, abs=1e-10) and count == loaded_count
    data = contents(path)
    manifest = json.loads(data["manifest.json"])
    assert manifest["schema_version"] == 9 and len(data) == len(document.objects) + 1
    assert all(record["brep"]["path"] in data for record in manifest["objects"])
    # Subsequent edits and undo are independent, not mutations of the saved shapes.
    first = original[0]
    loaded.rename(first.id, "Chargé puis modifié")
    loaded.history.undo()
    assert not loaded.is_modified and document.objects[first.id].name == first.name


def test_saved_revision_survives_undo_redo_and_save_as_does_not_change_uuid(tmp_path):
    document = demo_document()
    obj = next(iter(document.objects.values()))
    first, second = tmp_path / "premier.cada", tmp_path / "second.cada"
    save_document(document, first)
    old_bytes = first.read_bytes()
    document.rename(obj.id, "Final")
    save_document(document, second)
    assert document.file_path == second.resolve() and first.read_bytes() == old_bytes
    assert load_document(first).id == load_document(second).id == document.id
    assert load_document(first).objects[obj.id].name == obj.name
    assert load_document(second).objects[obj.id].name == "Final"
    document.history.undo()
    assert document.is_modified
    document.history.redo()
    assert not document.is_modified


def test_empty_document_round_trip_does_not_regenerate_demo_or_planes(tmp_path):
    document = Document("Vide")
    path = tmp_path / "vide.cada"
    save_document(document, path)
    loaded = load_document(path)
    assert loaded.id == document.id and loaded.name == "Vide" and not loaded.objects


@pytest.mark.parametrize("version", [0, persistence.SCHEMA_VERSION + 1, 999])
def test_unsupported_schema_is_explicitly_refused(tmp_path, version):
    path = tmp_path / "version.cada"
    rewrite(path, {"manifest.json": json.dumps(dict(format=persistence.FORMAT, schema_version=version))})
    with pytest.raises(UnsupportedVersionError, match="Version de document non prise en charge"):
        load_document(path)


@pytest.mark.parametrize("damage", [
    "missing_payload", "changed_payload", "bad_geometry", "duplicate_id", "missing_metadata", "unknown_field",
    "bad_uuid", "bad_color", "nonfinite", "bad_visibility", "bad_revision", "unknown_type", "missing_plugin",
    "wrong_unit", "bad_plane", "unsafe_path", "unknown_member",
])
def test_corrupted_or_unrecognized_data_is_refused_without_partial_document(tmp_path, damage):
    path = tmp_path / "abîmé.cada"
    save_document(demo_document(), path)
    entries = contents(path)
    manifest = json.loads(entries["manifest.json"])
    record = manifest["objects"][0]
    member = record["brep"]["path"]
    if damage == "missing_payload":
        del entries[member]
    elif damage == "changed_payload":
        entries[member] += b"garbage"
    elif damage == "bad_geometry":
        entries[member] = b"not a B-Rep"
        record["brep"]["sha256"] = sha256(entries[member]).hexdigest()
    elif damage == "duplicate_id":
        manifest["objects"].append(record)
    elif damage == "missing_metadata":
        del record["name"]
    elif damage == "unknown_field":
        record["future_feature"] = {"must_not_drop": True}
    elif damage == "bad_uuid":
        record["id"] = "invalid-uuid"
    elif damage == "bad_color":
        record["color"] = [1, 2, 3]
    elif damage == "nonfinite":
        record["color"] = [float("nan"), 0, 0]
    elif damage == "bad_visibility":
        record["visible"] = "false"
    elif damage == "bad_revision":
        record["revision"] = -1
    elif damage == "unknown_type":
        record["kind"] = "plugin.sketch"
    elif damage == "missing_plugin":
        manifest["required_plugins"] = ["unavailable.plugin"]
    elif damage == "wrong_unit":
        manifest["document"]["length_unit"] = "inch"
    elif damage == "bad_plane":
        manifest["objects"][-1]["plane"]["v"] = [2, 0, 0]
    elif damage == "unsafe_path":
        record["brep"]["path"] = "../outside.brep"
    elif damage == "unknown_member":
        entries["future-data.json"] = b"{}"
    entries["manifest.json"] = json.dumps(manifest)
    rewrite(path, entries)
    with pytest.raises(DocumentFileError):
        load_document(path)


@pytest.mark.parametrize("payload", [b"", b"not a zip", b"PK\x03\x04truncated"])
def test_incomplete_archive_is_refused(tmp_path, payload):
    path = tmp_path / "incomplet.cada"
    path.write_bytes(payload)
    with pytest.raises(DocumentFileError):
        load_document(path)


def test_duplicate_archive_members_and_duplicate_json_keys_are_refused(tmp_path):
    path = tmp_path / "duplicates.cada"
    with pytest.warns(UserWarning):
        with ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", "{}")
            archive.writestr("manifest.json", "{}")
    with pytest.raises(DocumentFileError, match="dupliquées"):
        load_document(path)
    rewrite(path, {"manifest.json": '{"format":"cadastrophe.document","schema_version":1,"schema_version":1}'})
    with pytest.raises(DocumentFileError, match="dupliquée"):
        load_document(path)


def test_damaged_compressed_data_is_reported_as_a_file_error(tmp_path):
    path = tmp_path / "compression.cada"
    save_document(demo_document(), path)
    with ZipFile(path) as archive:
        info = archive.getinfo("manifest.json")
    data = bytearray(path.read_bytes())
    name_length, extra_length = struct.unpack_from("<HH", data, info.header_offset + 26)
    start = info.header_offset + 30 + name_length + extra_length
    data[start] = 0xff  # invalid DEFLATE block type
    path.write_bytes(data)
    with pytest.raises(DocumentFileError, match="corrompu"):
        load_document(path)


@pytest.mark.parametrize("failure", ["write", "flush", "replace"])
def test_atomic_failure_preserves_previous_file_path_dirty_state_and_history(tmp_path, monkeypatch, failure):
    document = demo_document()
    previous, destination = tmp_path / "previous.cada", tmp_path / "destination.cada"
    save_document(document, previous)
    save_document(demo_document("Autre"), destination)
    before = destination.read_bytes()
    obj = next(iter(document.objects.values()))
    document.rename(obj.id, "Non enregistré")
    index = document.history.index

    def fail(*args, **kwargs):
        raise OSError("simulated disk failure")

    if failure == "write":
        monkeypatch.setattr(ZipFile, "writestr", fail)
    elif failure == "flush":
        monkeypatch.setattr(os, "fsync", fail)
    else:
        monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(DocumentFileError, match="Enregistrement impossible"):
        save_document(document, destination)
    assert destination.read_bytes() == before
    assert document.file_path == previous and document.is_modified and document.history.index == index
    assert not list(tmp_path.glob("*.tmp"))
    assert load_document(destination).name == "Autre"


def test_invalid_shape_and_pending_transaction_cannot_overwrite_existing_file(tmp_path):
    document = demo_document()
    obj = next(iter(document.objects.values()))
    path = tmp_path / "valide.cada"
    save_document(document, path)
    before = path.read_bytes()
    with document.history.transaction("Lot"):
        document.rename(obj.id, "Temporaire")
        with pytest.raises(HistoryBusyError):
            save_document(document, path)
    document.replace_shape(obj.id, None)
    with pytest.raises(DocumentFileError, match="B-Rep"):
        save_document(document, path)
    assert path.read_bytes() == before and document.is_modified


def test_missing_parent_and_size_limit_fail_without_claiming_save_success(tmp_path, monkeypatch):
    document = demo_document()
    with pytest.raises(DocumentFileError):
        save_document(document, tmp_path / "absent" / "document.cada")
    assert document.file_path is None
    path = tmp_path / "taille.cada"
    save_document(document, path)
    monkeypatch.setattr(persistence, "MAX_SHAPE", 16)
    with pytest.raises(DocumentFileError, match="taille"):
        load_document(path)
