"""Document history contracts, independent of widgets and AIS presentations."""
import pytest

from cadastrophe.core.history import HistoryBusyError
from cadastrophe.document import Document, SceneObject
from cadastrophe.geometry import REFERENCE_PLANES


def populated(count=3):
    document = Document()
    objects = [SceneObject(f"Corps {i}", object(), (0.5, 0.6, 0.7)) for i in range(count)]
    for obj in objects:
        document.add(obj)
    document.history.clear()
    return document, objects


def test_create_rename_visibility_remove_round_trip_preserves_identity_and_payload():
    document = Document()
    obj = SceneObject("Corps", object(), (0.5, 0.6, 0.7))
    document.add(obj)
    document.rename(obj.id, "Final")
    document.set_visible(obj.id, False)
    document.remove(obj.id)
    history = document.history
    assert history.count == history.index == 4 and not document.objects
    history.undo()
    restored = document.objects[obj.id]
    assert restored.name == "Final" and not restored.visible and restored.shape is obj.shape
    history.undo()
    assert document.objects[obj.id].visible
    history.undo()
    assert document.objects[obj.id].name == "Corps"
    history.undo()
    assert not document.objects and not history.can_undo and history.can_redo
    for _ in range(4):
        history.redo()
    assert not document.objects and history.index == 4 and not history.can_redo


def test_multi_remove_and_visibility_are_single_entries_and_restore_order():
    document, objects = populated(5)
    ids = [obj.id for obj in objects]
    document.set_visibilities({ids[1]: False, ids[3]: False})
    assert document.history.count == 1
    document.remove_objects([ids[3], ids[1], ids[3]])
    assert list(document.objects) == [ids[0], ids[2], ids[4]]
    document.history.undo()
    assert list(document.objects) == ids
    assert not document.objects[ids[1]].visible and not document.objects[ids[3]].visible
    document.history.undo()
    assert all(obj.visible for obj in document.objects.values())
    document.history.redo()
    document.history.redo()
    assert list(document.objects) == [ids[0], ids[2], ids[4]]


def test_noops_and_validation_preserve_redo_but_new_edit_discards_it():
    document, (obj,) = populated(1)
    document.rename(obj.id, "Nouveau")
    document.history.undo()
    document.rename(obj.id, obj.name)
    document.set_visible(obj.id, True)
    document.remove_objects([])
    with pytest.raises(ValueError):
        document.rename(obj.id, " ")
    with pytest.raises(KeyError):
        document.set_visibilities({obj.id: False, "missing": True})
    with pytest.raises(TypeError):
        document.set_visible(obj.id, "false")
    assert document.history.index == 0 and document.history.can_redo
    assert document.objects[obj.id].visible
    document.set_visible(obj.id, False)
    assert document.history.count == 1 and not document.history.can_redo


def test_reference_protection_and_bulk_validation_are_atomic():
    document, (obj,) = populated(1)
    plane = SceneObject("Plan XY", None, obj.color, REFERENCE_PLANES[0])
    document.add(plane)
    document.history.clear()
    with pytest.raises(ValueError, match="protected"):
        document.remove_objects([obj.id, plane.id])
    with pytest.raises(KeyError):
        document.remove_objects([obj.id, "missing"])
    with pytest.raises(TypeError):
        document.set_visibilities({obj.id: False, plane.id: "false"})
    assert len(document.objects) == 2 and document.objects[obj.id].visible
    assert document.history.count == 0


def test_histories_are_independent_and_selection_has_no_history():
    first, (obj,) = populated(1)
    second, (other,) = populated(1)
    first.rename(obj.id, "A")
    second.rename(other.id, "B")
    first.select_objects([obj.id])
    first.history.undo()
    assert first.objects[obj.id].name == obj.name
    assert first.selected_ids == (obj.id,) and first.history.count == 1
    assert second.objects[other.id].name == "B" and second.history.index == 1
    second.history.clear()
    assert second.objects[other.id].name == "B" and second.history.count == 0
    assert first.history.can_redo


def test_transaction_commits_one_entry_and_replays_in_order():
    document, (obj,) = populated(1)
    extra = SceneObject("Ajout", object(), obj.color)
    with document.history.transaction("Opération groupée"):
        document.rename(obj.id, "Nouveau")
        document.add(extra)
        document.set_visible(extra.id, False)
    assert document.history.count == 1 and document.history.undo_text == "Opération groupée"
    document.history.undo()
    assert list(document.objects) == [obj.id] and document.objects[obj.id].name == obj.name
    document.history.redo()
    assert document.objects[obj.id].name == "Nouveau" and not document.objects[extra.id].visible


def test_aborted_transaction_restores_content_and_existing_redo_branch():
    document, objects = populated()
    document.rename(objects[0].id, "À refaire")
    document.history.undo()
    before = tuple(document.objects.values())
    with pytest.raises(ValueError):
        with document.history.transaction("Échec"):
            document.rename(objects[1].id, "Temporaire")
            document.remove(objects[0].id)
            document.rename(objects[2].id, " ")
    assert tuple(document.objects.values()) == before
    assert document.history.index == 0 and document.history.can_redo
    document.history.redo()
    assert document.objects[objects[0].id].name == "À refaire"
    assert not document.history.blocked


def test_transaction_guards_and_notifications_recover_after_exit():
    document, (obj,) = populated(1)
    flags = []
    document.history.changed.connect(lambda: flags.append(document.history.blocked))
    with document.history.transaction("Vide"):
        assert document.history.blocked and not document.history.can_undo
        for operation in (document.history.undo, document.history.redo, document.history.clear):
            with pytest.raises(HistoryBusyError):
                operation()
        with pytest.raises(HistoryBusyError):
            with document.history.transaction("Imbriquée"):
                pass
    assert flags == [True, False] and document.history.count == 0
    document.rename(obj.id, "Renommé")
    assert flags[-2:] == [True, False] and document.history.can_undo


def test_notification_listeners_cannot_reenter_a_replay():
    document, (obj,) = populated(1)
    refused = []

    def attempt_edit(*_):
        with pytest.raises(HistoryBusyError):
            document.set_visible(obj.id, False)
        refused.append(True)

    document.object_changed.connect(attempt_edit)
    document.rename(obj.id, "Renommé")
    document.history.undo()
    document.history.redo()
    assert len(refused) == 3 and document.objects[obj.id].visible
    assert document.history.count == 1


def test_shape_undo_invalidates_selection_and_keeps_revisions_monotonic():
    document, (obj,) = populated(1)
    replacement = object()
    document.replace_shape(obj.id, replacement)
    document.select_objects([obj.id])
    document.history.undo()
    assert document.objects[obj.id].shape is obj.shape
    assert document.objects[obj.id].revision == 2 and not document.selected_ids
    document.select_objects([obj.id])
    document.history.redo()
    assert document.objects[obj.id].shape is replacement
    assert document.objects[obj.id].revision == 3 and not document.selected_ids


def test_clear_round_trip_restores_every_snapshot_and_baseline_order():
    document, objects = populated()
    document.set_visible(objects[1].id, False)
    before = tuple(document.objects.values())
    document.clear()
    document.history.undo()
    assert tuple(document.objects.values()) == before
    document.history.redo()
    assert not document.objects
