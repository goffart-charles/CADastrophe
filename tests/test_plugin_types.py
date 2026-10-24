"""Plugin codecs preserve B-Reps and data or reject the file without loss."""
from dataclasses import dataclass, field, fields
import json
from zipfile import ZipFile

import pytest
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.GProp import GProp_GProps

from cadastrophe.core.object_types import ObjectCodec, ObjectTypeRegistry
from cadastrophe.core.persistence import DocumentFileError, load_document, save_document
from cadastrophe.document import Document, SceneObject


@dataclass(frozen=True)
class TaggedObject(SceneObject):
    tag: str = field(default="test", kw_only=True)


def decode(base, data):
    if set(data) != {"tag"} or not isinstance(data["tag"], str):
        raise ValueError("Invalid tag")
    return TaggedObject(**{item.name: getattr(base, item.name) for item in fields(SceneObject)}, tag=data["tag"])


@pytest.fixture
def typed():
    registry = ObjectTypeRegistry()
    codec = ObjectCodec("fixture.tagged.part", TaggedObject, 1, lambda obj: {"tag": obj.tag}, decode)
    registry.register("fixture.tagged", codec)
    document = Document("Document typé")
    obj = TaggedObject("Corps marqué", BRepPrimAPI_MakeBox(12, 23, 34).Shape(), (0.2, 0.4, 0.7), tag="Précision éçà")
    document.add(obj)
    return document, obj, registry


def edit_manifest(path, mutate):
    with ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries["manifest.json"])
    mutate(manifest)
    entries["manifest.json"] = json.dumps(manifest)
    with ZipFile(path, "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)


@pytest.mark.parametrize("transparency", [0., 0.5])
def test_plugin_type_round_trip_restores_its_class_data_and_native_geometry(typed, tmp_path, transparency):
    document, obj, registry = typed
    document.set_transparencies({obj.id: transparency})
    path = tmp_path / "extension.cada"
    save_document(document, path, registry=registry)
    loaded = load_document(path, registry=registry)
    restored = loaded.objects[obj.id]
    assert type(restored) is TaggedObject and restored.tag == obj.tag and loaded.id == document.id
    assert restored.name == obj.name and restored.color == obj.color and restored.revision == obj.revision
    assert restored.transparency == transparency
    mass = GProp_GProps()
    brepgprop.VolumeProperties(restored.shape, mass)
    assert mass.Mass() == pytest.approx(12 * 23 * 34)
    assert not loaded.is_modified and loaded.history.count == 0
    with ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["schema_version"] == 9 and manifest["required_plugins"] == ["fixture.tagged"]
    assert manifest["objects"][0]["extension"]["data"] == {"tag": obj.tag}
    loaded.rename(obj.id, "Annulable")
    loaded.history.undo()
    assert type(loaded.objects[obj.id]) is TaggedObject and loaded.objects[obj.id].tag == obj.tag


def test_missing_plugin_refuses_load_and_save_without_touching_existing_file(typed, tmp_path):
    document, obj, registry = typed
    path = tmp_path / "extension.cada"
    save_document(document, path, registry=registry)
    before = path.read_bytes()
    with pytest.raises(DocumentFileError, match="Plugins nécessaires"):
        load_document(path)
    document.rename(obj.id, "Non enregistré")
    with pytest.raises(DocumentFileError, match="Sérialiseur indisponible"):
        save_document(document, path)
    assert path.read_bytes() == before and document.is_modified


@pytest.mark.parametrize("damage", ["version", "unknown_type", "bad_data", "unknown_field", "requirements"])
def test_plugin_specific_data_must_be_supported_in_full(typed, tmp_path, damage):
    document, _, registry = typed
    path = tmp_path / "extension.cada"
    save_document(document, path, registry=registry)

    def mutate(data):
        extension = data["objects"][0]["extension"]
        if damage == "version":
            extension["version"] = 2
        elif damage == "unknown_type":
            extension["type_id"] = "fixture.tagged.unknown"
        elif damage == "bad_data":
            extension["data"] = {"tag": 123}
        elif damage == "unknown_field":
            extension["future"] = True
        else:
            data["required_plugins"] = []

    edit_manifest(path, mutate)
    with pytest.raises(DocumentFileError):
        load_document(path, registry=registry)


@pytest.mark.parametrize("problem", ["base_class", "namespace", "duplicate", "version"])
def test_type_registration_refuses_invalid_or_conflicting_contributions(typed, problem):
    _, _, registry = typed
    cls = SceneObject if problem == "base_class" else TaggedObject
    type_id = "other.owner.type" if problem == "namespace" else "fixture.tagged.part"
    codec = ObjectCodec(type_id, cls, 0 if problem == "version" else 1, lambda obj: {}, decode)
    with pytest.raises(ValueError):
        registry.register("fixture.tagged", codec)
    assert registry.binding("fixture.tagged.part").codec.object_class is TaggedObject


@pytest.mark.parametrize("problem", ["lost_data", "base_identity", "non_json", "decoder_exception"])
def test_invalid_serializers_cannot_commit_an_unreadable_or_lossy_file(typed, tmp_path, problem):
    document, obj, registry = typed
    path = tmp_path / "extension.cada"
    save_document(document, path, registry=registry)
    before = path.read_bytes()
    registry.unregister("fixture.tagged.part", "fixture.tagged")

    def broken_decode(base, data):
        if problem == "decoder_exception":
            raise SystemExit("bad codec")
        restored = decode(base, data)
        from dataclasses import replace
        return replace(restored, id=SceneObject("Other", None, obj.color).id) if problem == "base_identity" else restored

    encode = (lambda obj: {"tag": "lost"}) if problem == "lost_data" else (
        (lambda obj: {"tag": object()}) if problem == "non_json" else lambda obj: {"tag": obj.tag})
    registry.register("fixture.tagged", ObjectCodec("fixture.tagged.part", TaggedObject, 1, encode, broken_decode))
    document.rename(obj.id, "Non enregistré")
    with pytest.raises(DocumentFileError):
        save_document(document, path, registry=registry)
    assert path.read_bytes() == before and document.is_modified
