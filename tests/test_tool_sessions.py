"""Generic sessions and document reservations, without native viewport dependencies."""
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox

from cadastrophe.core.documents import DocumentManager
from cadastrophe.core.history import HistoryBusyError
from cadastrophe.core.tool_sessions import ToolSessionManager
from cadastrophe.document import Document, SceneObject


@pytest.mark.parametrize('failure', ['exit', 'cleanup'])
def test_cooperative_preview_failures_release_iterators_and_document_reservation(system, monkeypatch, failure):
    from cadastrophe.ui.computation import Computation
    from test_interactive_computation import wait_for
    documents, doc, host, sessions, tool, original = system
    closed, errors = [], []
    def steps():
        try:
            yield 1, 2, 'Calcul natif'
            raise SystemExit('Extension interrompue')
        finally:
            closed.append(True)
    tool.cooperative_preview = True
    tool.preview_steps = steps
    host.run_preview = lambda steps, title, **callbacks: Computation(None, steps, title, **callbacks)
    sessions.error.connect(errors.append)
    assert sessions.start('fixture.cooperative', tool)
    task = sessions._preview_task
    if failure == 'cleanup':
        def broken():
            raise RuntimeError('Nettoyage interrompu')
        monkeypatch.setattr(host, 'clear_preview', broken)
    wait_for(lambda: task.outcome != 'running')
    assert task.outcome == 'failed' and closed == [True]
    assert not sessions.can_commit and doc.history.count == 0
    if failure == 'cleanup':
        assert errors and not sessions.active and not doc.history.blocked
    else:
        assert 'Extension interrompue' in sessions.message
        assert sessions.cancel() and not doc.history.blocked


def test_cooperative_input_signal_during_native_step_defers_iterator_close(system):
    from cadastrophe.ui.computation import Computation
    from test_interactive_computation import wait_for
    documents, doc, host, sessions, tool, original = system
    started, closed = [], []
    def steps():
        started.append(True)
        try:
            if len(started) == 1:
                tool.changed()
            yield 1, 2, 'Calcul natif'
            return tool.obj
        finally:
            closed.append(True)
    tool.cooperative_preview = True
    tool.preview_steps = steps
    host.run_preview = lambda steps, title, **callbacks: Computation(None, steps, title, **callbacks)
    assert sessions.start('fixture.cooperative', tool)
    task = sessions._preview_task
    wait_for(lambda: sessions.can_commit)
    assert task.outcome == 'cancelled' and len(started) == len(closed) == 2
    assert sessions.preview_object is tool.obj and doc.history.count == 0


class Host:
    ready = True

    def __init__(self):
        self.shown, self.closed = [], 0

    def open(self, title, factory, changed):
        factory(None, changed)

    def preview(self, obj):
        self.shown.append(obj)

    def clear_preview(self):
        pass

    def close(self):
        self.closed += 1


class Tool:
    title = "Outil de validation générique"

    def __init__(self, document):
        self.document = document
        self.obj = SceneObject("Nouveau", BRepPrimAPI_MakeBox(2, 3, 4).Shape(), (0.4, 0.5, 0.6))
        self.disposed, self.editors = 0, 0

    def create_editor(self, parent, changed):
        self.editors += 1
        self.changed = changed

    def preview(self):
        return self.obj

    def commit(self, document, preview):
        document.add(preview)

    def dispose(self):
        self.disposed += 1


@pytest.fixture
def system():
    app = QApplication.instance() or QApplication([])
    documents = DocumentManager()
    doc = documents.add(Document())
    original = SceneObject("Original", BRepPrimAPI_MakeBox(1, 2, 3).Shape(), (0.4, 0.5, 0.6))
    doc.add(original)
    doc.history.clear()
    host = Host()
    sessions = ToolSessionManager(documents, host)
    tool = Tool(doc)
    try:
        yield documents, doc, host, sessions, tool, original
    finally:
        sessions.cancel()


def test_reservation_keeps_clean_marker_and_redo_and_blocks_document_mutations(system):
    documents, doc, host, sessions, tool, original = system
    doc.rename(original.id, "Historique conservé")
    doc.history.undo()
    original = doc.objects[original.id]
    assert not doc.is_modified
    assert sessions.start("fixture.tool", tool)
    assert not doc.is_modified and doc.history.index == 0 and doc.history.count == 1
    operations = [lambda: doc.add(tool.obj), lambda: doc.rename(original.id, "Interdit"),
                  doc.history.undo, doc.history.redo, doc.history.clear, doc.history.mark_clean,
                  lambda: documents.activate(doc.id), lambda: documents.remove(doc.id)]
    for operation in operations:
        with pytest.raises(HistoryBusyError):
            operation()
    with pytest.raises(HistoryBusyError), doc.history.transaction("Interdite"):
        pass
    assert sessions.cancel() and not documents.blocked and not doc.is_modified
    assert doc.history.can_redo and doc.objects[original.id] is original
    doc.history.redo()
    assert doc.objects[original.id].name == "Historique conservé"


def test_exclusive_session_cancel_and_pending_update_cannot_outlive_owner(system):
    documents, doc, host, sessions, tool, original = system
    other = Tool(doc)
    assert sessions.start("fixture.tool", tool)
    assert not sessions.start("fixture.other", other) and other.editors == 0
    tool.changed()
    assert not sessions.can_commit
    sessions.cancel_owner("fixture.other")
    assert sessions.active
    sessions.cancel_owner("fixture.tool")
    count = len(host.shown)
    QTest.qWait(100)
    assert len(host.shown) == count and tool.disposed == 1 and not documents.blocked
    assert not sessions.cancel() and not sessions.commit()
    assert sessions.start("fixture.other", other)
    assert sessions.commit() and other.disposed == 1
    assert doc.history.count == 1 and tool.obj.id not in doc.objects


@pytest.mark.parametrize("fail", [False, True])
def test_commit_groups_mutations_or_rolls_back_without_releasing_lifecycle_guard(system, fail):
    documents, doc, host, sessions, tool, original = system
    doc.rename(original.id, "Branche redo")
    doc.history.undo()
    observed = []
    errors = []
    sessions.error.connect(errors.append)

    def commit(document, preview):
        document.add(preview)
        document.rename(original.id, "Deuxième mutation")
        if fail:
            raise RuntimeError("échec simulé")

    tool.commit = commit
    assert sessions.start("fixture.tool", tool)
    doc.history.changed.connect(lambda: observed.append(documents.blocked))
    assert sessions.commit() is (not fail)
    assert all(observed[:-1]) and observed[-1] is False
    assert not sessions.active and not documents.blocked and tool.disposed == 1
    assert bool(errors) is fail
    if fail:
        assert doc.objects[original.id] == original and tool.obj.id not in doc.objects
        assert not doc.is_modified and doc.history.can_redo and doc.history.index == 0
    else:
        assert doc.history.count == 1 and doc.history.index == 1
        doc.history.undo()
        assert tool.obj.id not in doc.objects and doc.objects[original.id] == original
        doc.history.redo()
        assert doc.objects[tool.obj.id] is tool.obj


@pytest.mark.parametrize("result", [None, "invalid", RuntimeError("paramètres invalides"), SystemExit("échec")])
def test_invalid_preview_disables_commit_then_recovers_in_same_session(system, result):
    documents, doc, host, sessions, tool, original = system

    def preview():
        if isinstance(result, BaseException):
            raise result
        return result

    tool.preview = preview
    assert sessions.start("fixture.tool", tool)
    assert not sessions.can_commit and not sessions.commit()
    assert doc.history.count == 0 and tuple(doc.objects) == (original.id,)
    tool.preview = lambda: tool.obj
    sessions.update_preview()
    assert sessions.can_commit and sessions.commit()
    assert tool.obj.id in doc.objects


@pytest.mark.parametrize("stage", ["editor", "display", "cleanup"])
def test_host_and_tool_errors_cannot_leave_a_locked_document(system, stage):
    documents, doc, host, sessions, tool, original = system
    errors = []
    sessions.error.connect(errors.append)

    def fail(*_):
        raise RuntimeError("échec " + stage)

    if stage == "editor":
        tool.create_editor = fail
        assert not sessions.start("fixture.tool", tool)
    elif stage == "display":
        host.preview = fail
        assert sessions.start("fixture.tool", tool)
        assert not sessions.can_commit
        sessions.cancel()
    else:
        assert sessions.start("fixture.tool", tool)
        host.clear_preview = host.close = tool.dispose = fail
        assert sessions.cancel()
    assert not sessions.active and not documents.blocked and doc.history.count == 0
    assert doc.objects[original.id] is original


def test_preview_cannot_write_to_document_and_unready_hosts_cannot_start(system):
    documents, doc, host, sessions, tool, original = system
    host.ready = False
    assert not sessions.start("fixture.tool", tool)
    host.ready = True
    tool.preview = lambda: doc.rename(original.id, "Écriture interdite")
    assert sessions.start("fixture.tool", tool) and not sessions.can_commit
    assert doc.objects[original.id] is original and doc.history.count == 0
    sessions.cancel()
    documents.remove(doc.id)
    assert not sessions.available and not sessions.start("fixture.tool", tool)


def test_reservation_cannot_be_released_or_committed_by_another_owner(system):
    documents, doc, host, sessions, tool, original = system
    owner = object()
    doc.history.reserve(owner)
    try:
        with pytest.raises(HistoryBusyError):
            doc.history.release(object())
        with pytest.raises(HistoryBusyError), doc.history.commit_reserved(object(), "Interdit"):
            pass
        assert documents.blocked and doc.history.count == 0
    finally:
        doc.history.release(owner)


def test_validation_signals_cannot_reenter_commit_or_cancel_its_transaction(system):
    documents, doc, host, sessions, tool, original = system
    assert sessions.start("fixture.tool", tool)
    attempts = []
    sessions.changed.connect(lambda: attempts.append((sessions.commit(), sessions.cancel())))
    assert sessions.commit()
    assert attempts and all(result == (False, False) for result in attempts)
    assert doc.history.count == 1 and tool.disposed == 1 and not documents.blocked


@pytest.mark.parametrize("stage", ["calculation", "presentation", "calculation_error"])
def test_input_changed_during_preview_cannot_publish_or_commit_stale_geometry(system, monkeypatch, stage):
    documents, doc, host, sessions, tool, original = system
    assert sessions.start("fixture.tool", tool)
    outdated = tool.obj
    latest = SceneObject("Paramètres modifiés", BRepPrimAPI_MakeBox(5, 6, 7).Shape(), (.3, .4, .5))
    build, show = tool.preview, host.preview
    fired = False
    def change_input():
        nonlocal fired
        if not fired:
            fired = True
            tool.obj = latest
            tool.changed()
    def calculating():
        result = build()
        if stage in ("calculation", "calculation_error"):
            change_input()
            if stage == "calculation_error" and result is outdated:
                raise ValueError("Ancienne entrée invalide")
        return result
    def presenting(obj):
        show(obj)
        if stage == "presentation":
            change_input()
    cleared = []
    monkeypatch.setattr(tool, "preview", calculating)
    monkeypatch.setattr(host, "preview", presenting)
    monkeypatch.setattr(host, "clear_preview", lambda: cleared.append(True))
    sessions.update_preview()
    assert fired and not sessions.can_commit and sessions.preview_object is None
    assert cleared and doc.history.count == 0
    # The pending revision is rebuilt once; the stale result never becomes a
    # commit candidate, even if input changes while a host applies the preview.
    QTest.qWait(120)
    assert sessions.can_commit and sessions.preview_object is latest
    assert host.shown[-1] is latest
    assert sessions.commit()
    assert latest.id in doc.objects and outdated.id not in doc.objects
    assert doc.history.count == 1 and not documents.blocked
