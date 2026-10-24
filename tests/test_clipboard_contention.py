"""Real Windows clipboard contention, bounded retry, and safe failed cuts."""
import os
import subprocess
import sys
from threading import Thread
from time import monotonic

import pytest
from PySide6.QtCore import QMimeData
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cadastrophe.core.clipboard import MimeClipboard, ObjectClipboard, ClipboardError, MIME_TYPE
from cadastrophe.core.object_types import ObjectTypeRegistry
from cadastrophe.sample_scene import demo_document


@pytest.fixture
def clipboard():
    app = QApplication.instance() or QApplication([])
    backend = app.clipboard()
    old = backend.mimeData()
    snapshot = QMimeData()
    if old is not None:
        for fmt in old.formats():
            snapshot.setData(fmt, old.data(fmt))
    yield backend
    backend.setMimeData(snapshot)


def lock_clipboard(short):
    code = '''
import ctypes, sys, time
u = ctypes.WinDLL("user32", use_last_error=True)
deadline = time.monotonic() + 1.
while not u.OpenClipboard(None):
    if time.monotonic() >= deadline:
        raise ctypes.WinError(ctypes.get_last_error())
    time.sleep(.01)
print("locked", flush=True)
try:
    time.sleep(.025) if sys.argv[1] == "short" else input()
finally:
    assert u.CloseClipboard()
'''
    child = subprocess.Popen([sys.executable, "-u", "-c", code, "short" if short else "held"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW)
    # The preceding test may have made this Qt thread the OLE clipboard owner.
    # Keep its event loop available during setup, before the lock is confirmed.
    # Production copy/cut itself must still run without pumping any events.
    readiness = []
    reader = Thread(target=lambda: readiness.append(child.stdout.readline().strip()), daemon=True)
    reader.start()
    deadline = monotonic()+5
    while reader.is_alive() and monotonic() < deadline:
        QTest.qWait(5)
    if reader.is_alive():
        child.kill()
        reader.join(timeout=5)
        child.communicate(timeout=10)
        pytest.fail("Le processus de verrouillage ne répond pas.")
    assert readiness == ["locked"], child.stderr.read()
    return child


@pytest.mark.skipif(os.name != "nt", reason="Verrou natif Windows du presse-papiers")
def test_native_clipboard_retries_after_short_external_lock(clipboard):
    transport = MimeClipboard(clipboard)
    child = lock_clipboard(True)
    try:
        transport.write(MIME_TYPE, b"native-contention-probe")
        child.communicate(timeout=10)
        assert child.returncode == 0
        assert transport.read(MIME_TYPE) == b"native-contention-probe"
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="Verrou natif Windows du presse-papiers")
def test_native_clipboard_held_lock_never_cuts_source_and_recovers(clipboard):
    doc = demo_document()
    selected = next(o.id for o in doc.objects.values() if o.plane is None)
    before = dict(doc.objects)
    copier = ObjectClipboard(clipboard, ObjectTypeRegistry())
    child = lock_clipboard(False)
    try:
        with pytest.raises(ClipboardError, match="aucun objet n’a été coupé"):
            copier.copy(doc, (selected,), cut=True)
        assert dict(doc.objects) == before and not doc.history.count
        child.communicate("release\n", timeout=10)
        assert child.returncode == 0
        assert copier.copy(doc, (selected,), cut=True) == 1
        assert selected not in doc.objects
        doc.history.undo()
        assert dict(doc.objects) == before
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
