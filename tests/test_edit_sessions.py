"""Generic persistent sessions reuse document undo without reserving the stack."""
from uuid import uuid4

import pytest

from cadastrophe.core.documents import DocumentManager
from cadastrophe.core.tool_sessions import ToolSessionManager
from cadastrophe.document import Document, ObjectType, SceneObject
from cadastrophe.geometry import REFERENCE_PLANES


class Host:
    ready = True

    def __init__(self):
        self.closed = self.cancelled = self.opened = 0
        self.fail = False

    def open_edit(self, editor):
        self.opened += 1
        if self.fail:
            raise RuntimeError("Échec hôte simulé")

    def close_edit(self, *, restore_camera=False):
        self.closed += 1

    def cancel_edit_gesture(self):
        self.cancelled += 1


class Editor:
    title = "Éditeur de validation"
    workplane = REFERENCE_PLANES[0]

    def __init__(self, key):
        self.object_id = key
        self.disposed = self.refreshed = 0
        self.fail = False

    def create_editor(self, parent, changed):
        pass

    def is_valid(self, document):
        return self.object_id in document.objects and document.objects[self.object_id].visible

    def refresh(self, document):
        self.refreshed += 1
        if self.fail:
            raise RuntimeError("Échec de rafraîchissement simulé")

    def dispose(self):
        self.disposed += 1


@pytest.fixture
def system():
    documents = DocumentManager()
    document = documents.add(Document())
    obj = SceneObject("Fonction", None, (0.2, 0.3, 0.4), kind=ObjectType.FEATURE)
    document.add(obj)
    document.history.clear()
    host = Host()
    manager = ToolSessionManager(documents, host)
    editor = Editor(obj.id)
    yield documents, document, obj, host, manager, editor
    manager.end_edit()


def test_edit_is_exclusive_but_undo_save_and_cancel_gesture_remain_available(system):
    documents, document, obj, host, manager, editor = system
    assert manager.start_edit("example.editor", editor)
    assert not document.history.blocked and not documents.blocked and not manager.available
    assert not manager.start_edit("other.editor", Editor(obj.id))
    document.rename(obj.id, "Annulable")
    assert manager.editing and document.history.can_undo
    document.history.undo()
    assert document.objects[obj.id].name == obj.name and manager.editing
    assert manager.cancel() and host.cancelled == 1 and manager.editing
    assert manager.commit() and not manager.active and editor.disposed == host.closed == 1


@pytest.mark.parametrize("stage", ["host", "refresh"])
def test_failed_start_rolls_back_creation_and_preserves_previous_redo(system, stage):
    documents, document, obj, host, manager, _ = system
    document.rename(obj.id, "Branche redo")
    document.history.undo()
    new = SceneObject("Autre", None, obj.color, kind=ObjectType.FEATURE)
    editor = Editor(new.id)
    host.fail = stage == "host"
    editor.fail = stage == "refresh"
    errors = []
    manager.error.connect(errors.append)
    assert not manager.start_edit("example.editor", editor, new_object=new)
    assert new.id not in document.objects and document.history.can_redo and not document.is_modified
    assert editor.disposed == host.closed == 1 and errors and not manager.active


@pytest.mark.parametrize("operation", ["hide", "delete", "clear", "switch", "owner"])
def test_editor_lifetime_ends_before_document_or_owner_can_disappear(system, operation):
    documents, document, obj, host, manager, editor = system
    assert manager.start_edit("example.editor", editor)
    if operation == "hide":
        document.set_visible(obj.id, False)
    elif operation == "delete":
        document.remove(obj.id)
    elif operation == "clear":
        document.clear()
    elif operation == "switch":
        documents.add(Document())
    else:
        manager.cancel_owner("example.editor")
    assert not manager.active and host.closed == editor.disposed == 1 and not document.history.blocked
    assert not manager.end_edit()


def test_invalid_and_unready_editor_never_acquire_a_context(system):
    documents, document, obj, host, manager, editor = system
    host.ready = False
    assert not manager.start_edit("example.editor", editor)
    host.ready = True
    editor.workplane = None
    with pytest.raises(ValueError):
        manager.start_edit("example.editor", editor)
    assert not manager.active and host.opened == 0
