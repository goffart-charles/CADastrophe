"""Document lifetime and modified-state contracts without a native viewport."""
import pytest

from cadastrophe.core.documents import DocumentManager
from cadastrophe.core.history import HistoryBusyError
from cadastrophe.document import Document, SceneObject
from cadastrophe.scene import new_document


def populated():
    doc = Document()
    obj = SceneObject("Corps", object(), (0.5, 0.6, 0.7))
    doc.add(obj)
    doc.history.clear()
    return doc, obj


def test_modified_state_tracks_undo_redo_and_saved_revision():
    doc, obj = populated()
    changes = []
    doc.modified_changed.connect(changes.append)
    assert not doc.is_modified
    doc.rename(obj.id, "A")
    doc.select_objects([obj.id])
    assert doc.is_modified and changes == [True]
    doc.history.undo()
    assert not doc.is_modified
    doc.history.redo()
    doc.history.mark_clean()
    assert not doc.is_modified and doc.history.can_undo
    doc.history.undo()
    assert doc.is_modified  # older than the marked revision is also unsaved
    doc.history.redo()
    assert not doc.is_modified
    assert changes == [True, False, True, False, True, False]


def test_branch_after_undo_cannot_reach_old_clean_revision():
    doc, obj = populated()
    doc.rename(obj.id, "A")
    doc.history.mark_clean()
    doc.history.undo()
    doc.rename(obj.id, "B")
    assert doc.is_modified and not doc.history.can_redo
    doc.history.undo()
    assert doc.is_modified  # the saved A branch was discarded
    doc.history.clear()
    assert not doc.is_modified


def test_transaction_dirty_state_rolls_back_and_cannot_be_marked_clean_midway():
    doc, obj = populated()
    with pytest.raises(ValueError):
        with doc.history.transaction("Échec"):
            doc.rename(obj.id, "A")
            assert doc.is_modified
            with pytest.raises(HistoryBusyError):
                doc.history.mark_clean()
            raise ValueError("Annuler le lot")
    assert not doc.is_modified and doc.objects[obj.id].name == obj.name


def test_new_documents_have_independent_hidden_reference_planes_and_clean_history():
    first, second = new_document("Pièce1"), new_document("Pièce2")
    assert len(first.objects) == len(second.objects) == 3
    assert not set(first.objects).intersection(second.objects)
    assert all(obj.plane and obj.visible for obj in first.objects.values())
    assert not first.is_modified and first.history.count == second.history.count == 0
    obj = next(iter(first.objects.values()))
    first.set_visible(obj.id, False)
    assert first.is_modified and not second.is_modified
    assert all(obj.visible for obj in second.objects.values())


def test_manager_activation_close_order_and_empty_state():
    manager = DocumentManager()
    docs = [Document(name) for name in ("A", "B", "C")]
    activated = []
    manager.active_changed.connect(activated.append)
    for doc in docs:
        manager.add(doc)
    assert manager.documents == tuple(docs) and manager.active_document is docs[2]
    manager.activate(docs[1].id)
    manager.remove(docs[0].id)  # inactive: active stays B
    assert manager.active_document is docs[1]
    manager.remove(docs[1].id)
    assert manager.active_document is docs[2]
    manager.remove(docs[2].id)
    assert manager.documents == () and manager.active_document is None
    assert activated == [*docs, docs[1], docs[2], None]
    manager.add(Document("D"))
    assert manager.active_document.name == "D"


def test_manager_replacement_preserves_order_and_detaches_old_history():
    manager = DocumentManager()
    first, obj = populated()
    second = Document("Second")
    manager.add(first)
    manager.add(second)
    manager.activate(first.id)
    replacement = Document("Remplacement")
    manager.replace(first.id, replacement)
    assert manager.documents == (replacement, second) and manager.active_document is replacement
    events = []
    manager.changed.connect(lambda: events.append(True))
    first.rename(obj.id, "Détaché")
    assert events == []
    manager.remove(replacement.id)
    manager.remove(second.id)
    assert manager.documents == ()


def test_manager_refuses_duplicate_and_unknown_documents_without_changing_state():
    manager = DocumentManager()
    first, second = Document("A"), Document("B")
    manager.add(first)
    manager.add(second)
    with pytest.raises(ValueError):
        manager.add(first)
    with pytest.raises(ValueError):
        manager.replace(first.id, second)
    with pytest.raises(KeyError):
        manager.activate("missing")
    assert manager.documents == (first, second) and manager.active_document is second
    assert not manager.blocked


def test_manager_blocks_lifecycle_during_inactive_document_transaction():
    manager = DocumentManager()
    first, second = Document("A"), Document("B")
    manager.add(first)
    manager.add(second)
    with first.history.transaction("En cours"):
        assert manager.blocked
        for operation in (lambda: manager.activate(first.id), lambda: manager.remove(second.id),
                          lambda: manager.add(Document("C")), lambda: manager.replace(second.id, Document("D"))):
            with pytest.raises(HistoryBusyError):
                operation()
    assert not manager.blocked and manager.documents == (first, second)


def test_removed_document_does_not_notify_the_manager():
    manager = DocumentManager()
    doc, obj = populated()
    manager.add(doc)
    manager.remove(doc.id)
    events = []
    manager.changed.connect(lambda: events.append(True))
    doc.rename(obj.id, "Après fermeture")
    assert not events and not manager.documents
