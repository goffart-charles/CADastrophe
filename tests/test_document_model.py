"""Document invariants and notifications without an AIS context or QApplication."""
from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest

from cadastrophe.document import Document, ObjectType, SceneObject
from cadastrophe.geometry import REFERENCE_PLANES


def body(name="Corps"):
    return SceneObject(name, None, (0.5, 0.6, 0.7))


def test_identity_types_and_canonical_unit_are_explicit():
    document = Document()
    solid = body()
    plane = SceneObject("Plan XY", None, (0.5, 0.6, 0.7), REFERENCE_PLANES[0])
    assert str(UUID(document.id)) == document.id
    assert str(UUID(solid.id)) == solid.id
    assert document.length_unit == "mm"
    assert solid.kind is ObjectType.BODY and plane.kind is ObjectType.REFERENCE_PLANE
    assert solid.id != plane.id
    assert body().id != solid.id
    assert SceneObject("Copie", None, solid.color, id=solid.id.upper()).id == solid.id


@pytest.mark.parametrize("values", [
    {"id": "not-a-uuid"}, {"name": "  "}, {"revision": -1},
    {"kind": ObjectType.REFERENCE_PLANE},
    {"kind": ObjectType.BODY, "plane": REFERENCE_PLANES[0]},
])
def test_invalid_objects_are_rejected(values):
    arguments = dict(name="Corps", shape=None, color=(0.5, 0.6, 0.7))
    arguments.update(values)
    with pytest.raises(ValueError):
        SceneObject(**arguments)


def test_consumers_cannot_silently_mutate_document_state():
    document = Document()
    obj = body()
    document.add(obj)
    with pytest.raises(FrozenInstanceError):
        obj.visible = False
    with pytest.raises(TypeError):
        document.objects[obj.id] = obj
    with pytest.raises(AttributeError):
        document.selected_ids = (obj.id,)
    with pytest.raises(AttributeError):
        document.length_unit = "inch"
    with pytest.raises(TypeError):
        document.add(object())


def test_add_edit_remove_notifications_and_live_readonly_view():
    document = Document()
    objects = document.objects
    events = []
    document.object_added.connect(lambda key: events.append(("add", key)))
    document.object_changed.connect(lambda key, fields: events.append((key, fields)))
    document.object_removed.connect(lambda key: events.append(("remove", key)))
    obj = body()
    document.add(obj)
    document.rename(obj.id, "  Nouveau nom  ")
    document.set_visible(obj.id, False)
    assert objects[obj.id].name == "Nouveau nom" and not objects[obj.id].visible
    assert objects[obj.id].revision == 0
    assert obj.name == "Corps" and obj.visible  # old records are snapshots
    count = len(events)
    document.rename(obj.id, "Nouveau nom")
    document.set_visible(obj.id, False)
    assert len(events) == count
    with pytest.raises(ValueError):
        document.add(obj)
    with pytest.raises(ValueError):
        document.rename(obj.id, " ")
    with pytest.raises(TypeError):
        document.set_visible(obj.id, "false")
    removed = document.remove(obj.id)
    assert removed.name == "Nouveau nom" and not objects
    assert events == [("add", obj.id), (obj.id, ("name",)),
                      (obj.id, ("visible",)), ("remove", obj.id)]


def test_selection_is_unique_valid_and_pruned_before_removal():
    document = Document()
    first, second = body("Premier"), body("Second")
    document.add(first)
    document.add(second)
    changes = []
    document.selection_changed.connect(changes.append)
    document.select_objects([first.id, first.id, second.id])
    document.select_objects([first.id, second.id])
    assert changes == [(first.id, second.id)]
    with pytest.raises(KeyError):
        document.select_objects([first.id, "missing"])
    assert document.selected_ids == (first.id, second.id)
    document.object_removed.connect(lambda key: changes.append(document.selected_ids))
    document.remove(first.id)
    assert document.selected_ids == (second.id,)
    assert changes[-2:] == [(second.id,), (second.id,)]
    document.set_visible(second.id, False)
    assert not document.selected_ids
    document.select_objects([second.id])  # explicit tree selection may target a hidden object
    assert document.selected_ids == (second.id,)


def test_shape_replacement_invalidates_selection_and_preserves_identity():
    document = Document()
    obj = body()
    document.add(obj)
    document.select_objects([obj.id])
    payload = object()
    document.replace_shape(obj.id, payload)
    updated = document.objects[obj.id]
    assert updated.id == obj.id and updated.shape is payload and updated.revision == 1
    assert not document.selected_ids and obj.revision == 0


def test_reference_protection_clear_and_document_isolation():
    first, second = Document(), Document()
    plane = SceneObject("Plan XY", None, (0.5, 0.6, 0.7), REFERENCE_PLANES[0])
    first.add(plane)
    first.select_objects([plane.id])
    with pytest.raises(ValueError, match="protected"):
        first.remove(plane.id)
    assert first.selected_ids == (plane.id,) and plane.id in first.objects
    events = []
    first.cleared.connect(lambda: events.append((dict(first.objects), first.selected_ids)))
    first.clear()
    first.clear()
    assert events == [({}, ())]
    assert not second.objects and not second.selected_ids and first.id != second.id
