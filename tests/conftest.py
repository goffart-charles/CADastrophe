"""Finish Qt deferred destruction between tests without a running app.exec()."""
import gc
import pytest
from PySide6.QtCore import QCoreApplication, QEvent


@pytest.fixture(autouse=True,scope="session")
def isolated_file_dialog_directory(tmp_path_factory):
    """Never create project folders in the real user's Documents during tests."""
    patch = pytest.MonkeyPatch()
    location = tmp_path_factory.mktemp("dialog-documents")/"Documents"
    patch.setattr("cadastrophe.ui.file_dialogs.documents_directory",lambda:location)
    yield
    patch.undo()


@pytest.fixture
def discard_document_changes(monkeypatch):
    """Legacy scenarios opt into discarding their own temporary test documents."""
    from cadastrophe.app import MainWindow
    monkeypatch.setattr(MainWindow, "confirm_discard", lambda self, document: True)


@pytest.fixture(autouse=True)
def finish_deferred_qt_deletion(request):
    yield
    if QCoreApplication.instance() is not None:
        # processEvents() alone does not deliver DeferredDelete in this setup.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    # A full scan after every scalar geometry case spends most of its time
    # traversing the same imported Qt/OCCT modules. Bound retained cycles to
    # eight tests, and always collect before a module's fixtures are released.
    session = request.session
    if not hasattr(session, "_native_cleanup_last"):
        session._native_cleanup_last = {item.path: item.nodeid for item in session.items}
        session._native_cleanup_count = 0
    session._native_cleanup_count += 1
    if (session._native_cleanup_count >= 8
            or session._native_cleanup_last[request.node.path] == request.node.nodeid):
        gc.collect()
        session._native_cleanup_count = 0
    if QCoreApplication.instance() is not None:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def pytest_sessionfinish(session, exitstatus):
    # Do not defer remaining native callback cycles to DLL/interpreter shutdown.
    if QCoreApplication.instance() is not None:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    if QCoreApplication.instance() is not None:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
