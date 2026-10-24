"""E1 document commands and persistence, without widgets or AIS."""
from dataclasses import replace
import json
from math import pi
from uuid import uuid4
from zipfile import ZipFile

import pytest

from cadastrophe.core.clipboard import ClipboardError, encode_objects
from cadastrophe.core.history import HistoryBusyError
from cadastrophe.core.object_types import ObjectTypeRegistry
from cadastrophe.core.persistence import DocumentFileError, load_document, save_document
from cadastrophe.document import Document, ObjectType, SceneObject
from cadastrophe.geometry import REFERENCE_PLANES, Vec3, WorkPlane
from cadastrophe.scene import new_document
from cadastrophe.sample_scene import demo_document
from plugins.sketch.codec import CODEC
from plugins.sketch.commands import create_sketch, add_entity, replace_entity, remove_entity
from plugins.sketch.model import Arc, Circle, Point, Segment, Vec2
from plugins.sketch.objects import support_status


@pytest.fixture
def registry():
    result = ObjectTypeRegistry()
    result.register("cadastrophe.sketch", CODEC)
    return result


def setup_document(native=False):
    doc = new_document() if native else Document()
    if not native:
        doc.add(SceneObject("XY", None, (0.1, 0.2, 0.3), REFERENCE_PLANES[0]))
        doc.history.clear()
    obj = create_sketch(doc, next(iter(doc.objects)))
    return doc, obj


def populate(doc, obj):
    for entity in (Point(Vec2(1, 2)), Segment(Vec2(0, 0), Vec2(5, 3)),
                   Circle(Vec2(-2, 5), 7.5), Arc(Vec2(10, 20), 3, pi / 4, -pi)):
        add_entity(doc, obj.id, entity)
    return doc.objects[obj.id]


def test_entity_commands_share_document_undo_redo_and_keep_uuid_and_revisions():
    doc, original = setup_document()
    point = Point(Vec2(2, 3))
    add_entity(doc, original.id, point)
    replace_entity(doc, original.id, replace(point, position=Vec2(8, 9)))
    remove_entity(doc, original.id, point.id)
    assert doc.history.count == 4 and doc.objects[original.id].revision == 3
    doc.history.undo()
    assert doc.objects[original.id].sketch.entity(point.id).position == Vec2(8, 9)
    doc.history.undo()
    assert doc.objects[original.id].sketch.entities == (point,)
    doc.history.undo()
    assert not doc.objects[original.id].sketch.entities and doc.objects[original.id].revision == 6
    doc.history.undo()
    assert original.id not in doc.objects and doc.history.is_clean
    for _ in range(4):
        doc.history.redo()
    assert doc.objects[original.id].sketch.id == original.id
    assert not doc.objects[original.id].sketch.entities
    assert any(obj.id == original.id for obj in doc.history.retained_objects())


def test_noop_validation_and_failed_transactions_preserve_redo():
    doc, obj = setup_document()
    point = Point(Vec2(2, 3))
    add_entity(doc, obj.id, point)
    doc.history.undo()
    count = doc.history.count
    doc.replace_feature(doc.objects[obj.id])
    with pytest.raises(KeyError):
        remove_entity(doc, obj.id, point.id)
    with pytest.raises(ValueError):
        with doc.history.transaction("Groupe invalide"):
            add_entity(doc, obj.id, point)
            add_entity(doc, obj.id, point)
    assert doc.history.count == count and doc.history.can_redo
    assert not doc.objects[obj.id].sketch.entities
    doc.history.redo()
    assert doc.objects[obj.id].sketch.entities == (point,)


@pytest.mark.parametrize("entity,changes", [
    (Point(Vec2(2, 3)), {"position": Vec2(-4, 5)}),
    (Segment(Vec2(0, 0), Vec2(10, 0)), {"end": Vec2(10, 20)}),
    (Circle(Vec2(1, 2), 5), {"radius": 12}),
    (Arc(Vec2(1, 2), 5, 0, pi/2), {"start_angle": pi/4, "sweep": -pi}),
])
def test_each_entity_type_can_be_modified_removed_and_undone(entity, changes):
    doc, obj = setup_document()
    add_entity(doc, obj.id, entity)
    modified = replace(entity, **changes)
    replace_entity(doc, obj.id, modified)
    assert doc.objects[obj.id].sketch.entity(entity.id) == modified
    remove_entity(doc, obj.id, entity.id)
    doc.history.undo()
    assert doc.objects[obj.id].sketch.entity(entity.id) == modified
    doc.history.undo()
    assert doc.objects[obj.id].sketch.entity(entity.id) == entity
    doc.history.redo()
    assert doc.objects[obj.id].sketch.entity(entity.id) == modified


def test_history_reservation_guards_and_independent_documents():
    doc, obj = setup_document()
    other, other_obj = setup_document()
    token = object()
    doc.history.reserve(token)
    with pytest.raises(HistoryBusyError):
        add_entity(doc, obj.id, Point(Vec2(1, 2)))
    doc.history.release(token)
    add_entity(other, other_obj.id, Point(Vec2(3, 4)))
    assert not doc.objects[obj.id].sketch.entities
    assert len(other.objects[other_obj.id].sketch.entities) == 1


def test_logical_object_guards_and_tree_operations_are_undoable():
    doc, obj = setup_document()
    doc.rename(obj.id, "Profil")
    doc.set_visible(obj.id, False)
    doc.remove(obj.id)
    doc.history.undo()
    assert doc.objects[obj.id].name == "Profil" and not doc.objects[obj.id].visible
    for action in (lambda: doc.replace_shape(obj.id, object()),
                   lambda: doc.set_transparencies({obj.id: 0.5}), lambda: doc.isolate([obj.id]),
                   lambda: doc.insert_objects([obj])):
        with pytest.raises(ValueError):
            action()
    with pytest.raises(ClipboardError):
        encode_objects(doc, [obj.id])


@pytest.mark.parametrize("populated", [False, True])
def test_current_roundtrip_has_no_fake_brep_and_preserves_all_local_parameters(tmp_path, registry, populated):
    doc, obj = setup_document(native=True)
    if populated:
        obj = populate(doc, obj)
    path = tmp_path / "sketch.cada"
    save_document(doc, path, registry=registry)
    loaded = load_document(path, registry=registry)
    assert loaded.objects[obj.id] == obj
    assert loaded.history.count == 0 and not loaded.is_modified and loaded.id == doc.id
    with ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        record = next(item for item in manifest["objects"] if item["id"] == obj.id)
        assert manifest["schema_version"] == 9 and manifest["required_plugins"] == ["cadastrophe.sketch"]
        assert record["brep"] is None and record["kind"] == "feature"
        assert f"shapes/{obj.id}.brep" not in archive.namelist()
        assert len(archive.namelist()) == 4  # manifest + three real support planes
    assert support_status(loaded.objects[obj.id], loaded.objects) == ""
    loaded.rename(obj.id, "Autre")
    loaded.history.undo()
    assert loaded.objects[obj.id] == obj


def rewrite(path, mutate):
    with ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    data = json.loads(entries["manifest.json"])
    record = next(item for item in data["objects"] if item["kind"] == "feature")
    mutate(data, record)
    entries["manifest.json"] = json.dumps(data).encode()
    with ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)


@pytest.mark.parametrize("damage", ["version", "type", "radius", "duplicate", "unknown_field", "bad_uuid",
                                   "axes", "support_type", "missing_codec", "brep", "schema"])
def test_corrupt_payloads_are_rejected_before_adoption(tmp_path, registry, damage):
    doc, obj = setup_document(native=True)
    populate(doc, obj)
    path = tmp_path / "damaged.cada"
    save_document(doc, path, registry=registry)

    def corrupt(data, record):
        extension = record["extension"]
        payload = extension["data"]
        if damage == "version":
            extension["version"] = CODEC.version + 1
        elif damage == "type":
            payload["entities"][0]["type"] = "unknown"
        elif damage == "radius":
            payload["entities"][2]["radius"] = -2
        elif damage == "duplicate":
            payload["entities"].append(payload["entities"][0])
        elif damage == "unknown_field":
            payload["future"] = 1
        elif damage == "bad_uuid":
            payload["support"]["plane_id"] = "wrong"
        elif damage == "axes":
            payload["support"]["frame"]["u"] = [2, 0, 0]
        elif damage == "support_type":
            payload["support"]["plane_id"] = record["id"]
        elif damage == "missing_codec":
            del record["extension"]
        elif damage == "brep":
            record["brep"] = {"path": "fake", "sha256": "0" * 64}
        else:
            data["schema_version"] = 3

    rewrite(path, corrupt)
    with pytest.raises(DocumentFileError):
        load_document(path, registry=registry)
    assert doc.objects[obj.id].sketch.entities and not doc.is_modified


def test_missing_or_incompatible_plugin_never_overwrites_existing_file(tmp_path, registry):
    doc, obj = setup_document(native=True)
    path = tmp_path / "plugin.cada"
    save_document(doc, path, registry=registry)
    before = path.read_bytes()
    with pytest.raises(DocumentFileError, match="Plugins nécessaires"):
        load_document(path)
    add_entity(doc, obj.id, Point(Vec2(1, 2)))
    with pytest.raises(DocumentFileError, match="Sérialiseur indisponible"):
        save_document(doc, path)
    assert path.read_bytes() == before and doc.is_modified


def test_unresolved_support_keeps_data_but_refuses_editing(tmp_path, registry):
    doc, obj = setup_document(native=True)
    obj = populate(doc, obj)
    orphan = replace(obj, sketch=replace(obj.sketch, support=replace(obj.sketch.support, plane_id=str(uuid4()))))
    doc.replace_feature(orphan)
    path = tmp_path / "unresolved.cada"
    save_document(doc, path, registry=registry)
    loaded = load_document(path, registry=registry)
    restored = loaded.objects[obj.id]
    assert restored.sketch.entities == obj.sketch.entities and restored.sketch.support.frame == obj.sketch.support.frame
    assert "plan absent" in support_status(restored, loaded.objects)
    with pytest.raises(ValueError, match="non résolu"):
        add_entity(loaded, obj.id, Point(Vec2(9, 9)))


def test_current_schema_preserves_static_geometry_and_logical_recipes(tmp_path, registry):
    doc = demo_document()
    path = tmp_path / "legacy.cada"
    save_document(doc, path, registry=registry)
    with ZipFile(path) as archive:
        assert json.loads(archive.read("manifest.json"))["schema_version"] == 9
    loaded = load_document(path, registry=registry)
    plane = next(obj for obj in loaded.objects.values() if obj.plane)
    obj = create_sketch(loaded, plane.id)
    save_document(loaded, path, registry=registry)
    restored = load_document(path, registry=registry)
    assert len(restored.objects) == 8 and restored.objects[obj.id].shape is None
    assert all(not item.shape.IsNull() for item in restored.objects.values() if item.kind is ObjectType.BODY)


def test_changed_support_remains_explicitly_unresolved_after_roundtrip(tmp_path, registry):
    doc, obj = setup_document(native=True)
    plane_id = obj.sketch.support.plane_id
    doc.replace_shape(plane_id, doc.objects[plane_id].shape)
    assert "plan modifié" in support_status(obj, doc.objects)
    path = tmp_path / "changed-support.cada"
    save_document(doc, path, registry=registry)
    loaded = load_document(path, registry=registry)
    assert "plan modifié" in support_status(loaded.objects[obj.id], loaded.objects)
    with pytest.raises(ValueError, match="non résolu"):
        add_entity(loaded, obj.id, Point(Vec2(1, 2)))


def test_oblique_frame_codec_is_lossless(registry):
    frame = WorkPlane("Oblique", Vec3(12, -34, 56), Vec3(1, 2, 3), Vec3(-2, 1, 0))
    doc = Document()
    plane = SceneObject("Oblique", None, (0.2, 0.3, 0.4), frame)
    doc.add(plane)
    obj = create_sketch(doc, plane.id)
    extension, owner = registry.encode(obj)
    base = SceneObject(obj.name, None, obj.color, id=obj.id, kind=ObjectType.FEATURE)
    restored, _ = registry.decode(base, extension)
    assert restored == obj and owner == "cadastrophe.sketch"


def test_forward_reference_is_validated_after_all_records_are_decoded(tmp_path, registry):
    doc, obj = setup_document(native=True)
    path = tmp_path / "forward.cada"
    save_document(doc, path, registry=registry)
    rewrite(path, lambda data, record: data["objects"].reverse())
    loaded = load_document(path, registry=registry)
    assert next(iter(loaded.objects)) == obj.id
    assert support_status(loaded.objects[obj.id], loaded.objects) == ""


def test_feature_api_cannot_convert_a_body_or_accept_an_invalid_snapshot():
    doc = Document()
    body = SceneObject("Corps", object(), (0.1, 0.2, 0.3))
    doc.add(body)
    doc.history.clear()
    feature = replace(body, shape=None, kind=ObjectType.FEATURE)
    with pytest.raises(ValueError):
        doc.replace_feature(feature)
    with pytest.raises(ValueError):
        replace(feature, shape=object())
    assert doc.objects[body.id] is body and doc.history.count == 0
