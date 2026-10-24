"""Detached B-Reps, atomic history and explicit reference remapping contracts."""
from base64 import b64encode
from dataclasses import dataclass, field, fields
from hashlib import sha256
import json

import pytest
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.GProp import GProp_GProps
from PySide6.QtWidgets import QApplication

from cadastrophe.core import clipboard as module
from cadastrophe.core.clipboard import ClipboardError, ObjectClipboard, encode_objects, decode_objects, MIME_TYPE
from cadastrophe.core.object_types import ObjectCodec, ObjectTypeRegistry
from cadastrophe.core.persistence import load_document, save_document
from cadastrophe.document import Document, SceneObject
from cadastrophe.scene import new_document


class MemoryClipboard:
    """Core command tests use bytes; native QClipboard is tested in UI scenarios."""
    def __init__(self):
        self.mime = None

    def mimeData(self):
        return self.mime

    def setMimeData(self, mime):
        self.mime = mime


@pytest.fixture
def model():
    app = QApplication.instance() or QApplication([])
    document = Document("Source")
    first = SceneObject("Boîte éçà", BRepPrimAPI_MakeBox(12, 23, 34).Shape(), (0.2, 0.4, 0.7),
                        visible=False, revision=4, transparency=0.5)
    second = SceneObject("Boîte 2", BRepPrimAPI_MakeBox(7, 8, 9).Shape(), (0.6, 0.5, 0.4))
    document.add(first)
    document.add(second)
    document.history.clear()
    document.select_objects([first.id, second.id])
    yield document, first, second, ObjectClipboard(MemoryClipboard(), ObjectTypeRegistry())


def volume(shape):
    props = GProp_GProps()
    brepgprop.VolumeProperties(shape, props)
    return props.Mass()


def test_copy_is_snapshot_with_independent_breps_new_ids_and_preserved_appearance(model):
    source, first, second, service = model
    assert service.copy(source, reversed(source.selected_ids)) == 2
    assert not source.is_modified and source.history.count == 0
    raw = bytes(service.clipboard.mimeData().data(MIME_TYPE))
    source.rename(first.id, "Changé après copie")
    source.replace_shape(first.id, BRepPrimAPI_MakeBox(1, 1, 1).Shape())
    restored = decode_objects(raw)
    assert [obj.name for obj in restored] == [first.name, second.name]
    assert not {obj.id for obj in restored} & set(source.objects)
    for original, obj in zip((first, second), restored):
        assert obj.color == original.color and obj.visible == original.visible
        assert obj.transparency == original.transparency and obj.revision == 0
        assert not obj.shape.IsPartner(original.shape)
        assert volume(obj.shape) == pytest.approx(volume(original.shape))
    assert decode_objects(raw)[0].id != restored[0].id


def test_multiple_pastes_are_single_undo_steps_with_unique_names_and_stable_redo_ids(model, tmp_path):
    source, first, second, service = model
    service.copy(source, source.selected_ids)
    target = Document("Cible")
    target.add(SceneObject(first.name, BRepPrimAPI_MakeBox(1, 2, 3).Shape(), first.color))
    target.history.clear()
    previous = tuple(target.objects)
    target.select_objects(previous)
    pasted = service.paste(target)
    assert target.history.count == 1 and target.selected_ids == pasted
    assert target.objects[pasted[0]].name == first.name + " (copie)"
    assert not target.objects[pasted[0]].visible
    target.history.undo()
    assert tuple(target.objects) == previous and target.selected_ids == previous and not target.is_modified
    target.history.redo()
    assert target.selected_ids == pasted
    again = service.paste(target)
    assert not set(again) & set(pasted) and target.history.count == 2
    assert target.objects[again[0]].name == first.name + " (copie 2)"
    save_document(target, tmp_path / "pasted.cada")
    loaded = load_document(tmp_path / "pasted.cada")
    assert tuple(loaded.objects) == tuple(target.objects)
    assert volume(loaded.objects[pasted[0]].shape) == pytest.approx(12 * 23 * 34)
    target.history.undo()
    target.history.undo()
    assert tuple(target.objects) == previous


def test_cut_cross_document_stacks_are_independent_and_clipboard_can_be_lost(model):
    source, first, second, service = model
    source_order = tuple(source.objects)
    assert service.copy(source, iter(source.selected_ids), cut=True) == 2
    assert not source.objects and source.history.count == 1 and source.is_modified
    target = Document("Cible")
    pasted = service.paste(target)
    assert target.history.count == 1 and not set(pasted) & set(source_order)
    service.clipboard.mime = None
    source.history.undo()
    assert tuple(source.objects) == source_order and source.selected_ids == source_order
    assert not source.is_modified and tuple(target.objects) == pasted
    source.history.redo()
    target.history.undo()
    assert not source.objects and not target.objects
    target.history.redo()
    assert tuple(target.objects) == pasted


def test_clipboard_write_failure_never_cuts_or_loses_redo(model, monkeypatch):
    source, first, second, service = model
    source.rename(first.id, "Autre")
    source.history.undo()
    old_selection, old_objects = source.selected_ids, tuple(source.objects)
    monkeypatch.setattr(service.clipboard, "setMimeData", lambda mime: None)
    with pytest.raises(ClipboardError, match="aucun objet"):
        service.copy(source, source.selected_ids, cut=True)
    assert tuple(source.objects) == old_objects and source.selected_ids == old_selection
    assert source.history.can_redo and not source.is_modified


def test_reference_planes_and_mixed_selection_are_rejected_as_a_whole(model):
    source, first, second, service = model
    reference_document = new_document("Plans")
    plane = next(iter(reference_document.objects.values()))
    source.add(plane)
    source.history.clear()
    for ids in ([plane.id], [first.id, plane.id]):
        with pytest.raises(ClipboardError, match="plans de référence"):
            service.copy(source, ids, cut=True)
        assert plane.id in source.objects and first.id in source.objects
        assert not service.available and source.history.count == 0


@dataclass(frozen=True)
class LinkedObject(SceneObject):
    links: tuple = field(default=(), kw_only=True)
    label: str = field(default="donnée du plugin", kw_only=True)


def linked_decode(base, data):
    if set(data) != {"links", "label"} or not isinstance(data["links"], list):
        raise ValueError("Données de lien invalides")
    return LinkedObject(**{item.name: getattr(base, item.name) for item in fields(SceneObject)},
                        links=tuple(data["links"]), label=data["label"])


def linked_registry(*, clipboard=True, remap=None):
    hooks = dict(clipboard_references=lambda data: data["links"], clipboard_remap=remap or (
        lambda data, mapping: dict(data, links=[mapping[key] for key in data["links"]]))) if clipboard else {}
    registry = ObjectTypeRegistry()
    registry.register("fixture.links", ObjectCodec("fixture.links.body", LinkedObject, 1,
        lambda obj: dict(links=list(obj.links), label=obj.label), linked_decode, **hooks))
    return registry


def test_internal_references_cycles_and_self_links_are_remapped_and_persisted(model, tmp_path):
    source, first, second, service = model
    a = LinkedObject("A", first.shape, first.color)
    b = LinkedObject("B", second.shape, second.color, links=(a.id,))
    from dataclasses import replace
    a = replace(a, links=(b.id, a.id))
    source.clear()
    source.add(a)
    source.add(b)
    registry = linked_registry()
    raw = encode_objects(source, [a.id, b.id], registry)
    x, y = decode_objects(raw, registry)
    assert x.links == (y.id, x.id) and y.links == (x.id,)
    assert x.label == a.label and not x.shape.IsPartner(a.shape)
    target = Document("Références collées")
    target.insert_objects([x, y])
    assert target.history.retained_objects() == (x, y)
    path = tmp_path / "linked.cada"
    save_document(target, path, registry=registry)
    loaded = load_document(path, registry=registry)
    assert loaded.objects[x.id].links == (y.id, x.id)
    with pytest.raises(ClipboardError, match="Références externes non résolues"):
        encode_objects(source, [a.id], registry)
    damaged = json.loads(raw)
    damaged["objects"] = damaged["objects"][:1]
    with pytest.raises(ClipboardError, match="Références externes non résolues"):
        decode_objects(json.dumps(damaged).encode("utf-8"), registry)


def test_cut_rejects_declared_incoming_references_from_objects_left_in_source(model):
    source, first, second, service = model
    linked = LinkedObject("Dépendant", first.shape, first.color, links=(second.id,))
    source.add(linked)
    source.history.clear()
    service.registry = linked_registry()
    with pytest.raises(ClipboardError, match="Dépendant référence"):
        service.copy(source, [second.id], cut=True)
    assert second.id in source.objects and source.history.count == 0 and not service.available
    assert service.copy(source, [second.id, linked.id], cut=True) == 2
    target = Document("Cible")
    ids = service.paste(target)
    assert target.objects[ids[1]].links == (ids[0],)


@pytest.mark.parametrize("hooks", [dict(clipboard_references=lambda data: []),
                                 dict(clipboard_remap=lambda data, mapping: data),
                                 dict(clipboard_references=False, clipboard_remap=False)])
def test_clipboard_contract_requires_both_callable_hooks(hooks):
    registry = ObjectTypeRegistry()
    with pytest.raises(ValueError, match="deux fonctions"):
        registry.register("fixture.links", ObjectCodec("fixture.links.body", LinkedObject, 1,
            lambda obj: dict(links=list(obj.links), label=obj.label), linked_decode, **hooks))


@pytest.mark.parametrize("problem", ["no_contract", "missing_plugin", "bad_remap", "external"])
def test_plugin_failures_do_not_cut_source_or_modify_target(model, problem):
    source, first, second, service = model
    linked = LinkedObject("Lien", first.shape, first.color, links=(second.id,))
    source.add(linked)
    source.history.clear()
    if problem == "no_contract":
        service.registry = linked_registry(clipboard=False)
    elif problem == "bad_remap":
        service.registry = linked_registry(remap=lambda data, mapping: data)
    else:
        service.registry = linked_registry()
    ids = [linked.id] if problem == "external" else [second.id, linked.id]
    if problem == "missing_plugin":
        service.copy(source, ids)
        service.registry = ObjectTypeRegistry()
        target = Document("Cible")
        with pytest.raises(ClipboardError, match="Plugins nécessaires"):
            service.paste(target)
        assert not target.objects and target.history.count == 0
    else:
        with pytest.raises(ClipboardError):
            service.copy(source, ids, cut=True)
    assert linked.id in source.objects and second.id in source.objects
    assert source.history.count == 0


@pytest.mark.parametrize("problem", ["future", "bool_version", "unit", "duplicate_id", "unknown_field", "hash",
                                    "base64", "shape", "plane", "owner", "empty", "nan", "truncated", "duplicate_key"])
def test_invalid_payload_is_rejected_without_document_or_history_changes(model, problem):
    source, first, second, service = model
    service.copy(source, [first.id])
    data = json.loads(bytes(service.clipboard.mimeData().data(MIME_TYPE)))
    entry = data["objects"][0]
    if problem == "future": data["schema_version"] = 99
    elif problem == "bool_version": data["schema_version"] = True
    elif problem == "unit": data["length_unit"] = "in"
    elif problem == "duplicate_id": data["objects"].append(entry)
    elif problem == "unknown_field": data["unexpected"] = True
    elif problem == "hash": entry["object"]["brep"]["sha256"] = "0" * 64
    elif problem == "base64": entry["payload"] = "!!!"
    elif problem == "shape":
        entry["payload"] = b64encode(b"not a BRep").decode("ascii")
        entry["object"]["brep"]["sha256"] = sha256(b"not a BRep").hexdigest()
    elif problem == "plane": entry["object"]["kind"] = "reference_plane"
    elif problem == "owner": data["required_plugins"] = ["fixture.absent"]
    elif problem == "empty": data["objects"] = []
    elif problem == "nan": entry["object"]["transparency"] = float("nan")
    raw = json.dumps(data).encode("utf-8")
    if problem == "truncated": raw = raw[:-8]
    elif problem == "duplicate_key": raw = raw.replace(b'"length_unit": "mm"', b'"length_unit": "mm", "length_unit": "mm"')
    service.clipboard.mimeData().setData(MIME_TYPE, raw)
    source.rename(first.id, "Undo disponible")
    source.history.undo()
    before = tuple(source.objects), source.selected_ids, source.history.count, source.history.index
    with pytest.raises(ClipboardError):
        service.paste(source)
    assert (tuple(source.objects), source.selected_ids, source.history.count, source.history.index) == before
    assert source.history.can_redo


def test_size_limits_and_history_reservation(model, monkeypatch):
    source, first, second, service = model
    service.copy(source, [first.id])
    monkeypatch.setattr(module, "MAX_PAYLOAD", 8)
    with pytest.raises(ClipboardError, match="taille"):
        service.copy(source, [first.id], cut=True)
    with pytest.raises(ClipboardError, match="taille"):
        service.paste(source)
    assert first.id in source.objects and source.history.count == 0
    monkeypatch.undo()
    token = object()
    source.history.reserve(token)
    try:
        with pytest.raises(ClipboardError):
            service.copy(source, [first.id], cut=True)
        with pytest.raises(RuntimeError):
            service.paste(source)
        assert source.history.count == 0 and first.id in source.objects
    finally:
        source.history.release(token)


def test_insert_batch_rejects_duplicate_before_push(model):
    source, first, second, service = model
    clone = decode_objects(encode_objects(source, [first.id]))[0]
    with pytest.raises(ValueError, match="dupliqué"):
        source.insert_objects([clone, first])
    assert clone.id not in source.objects and source.history.count == 0
