"""Two real processes, external edits, and unchanged undo state on conflicts."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cadastrophe.core.persistence import DocumentConflictError, load_document, save_document
from cadastrophe.sample_scene import demo_document


def project(tmp_path):
    path = tmp_path/"partage.cada"
    doc = demo_document()
    save_document(doc, path)
    return doc, path, next(iter(doc.objects))


def worker(code, path):
    return subprocess.Popen([sys.executable, "-u", "-c", code, str(path)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def test_two_instances_refuse_to_overwrite_a_newer_project(tmp_path):
    doc, path, key = project(tmp_path)
    child = worker('''
import json, sys
from cadastrophe.core.persistence import load_document, save_document, DocumentConflictError
doc = load_document(sys.argv[1])
doc.rename(next(iter(doc.objects)), "Version enfant")
print("ready", flush=True)
input()
try:
    save_document(doc, sys.argv[1])
    print("unexpected save")
except DocumentConflictError:
    print(json.dumps(dict(conflict=True, dirty=doc.is_modified, history=doc.history.count)))
''', path)
    try:
        assert child.stdout.readline().strip() == "ready"
        doc.rename(key, "Version parent")
        save_document(doc, path)
        content = path.read_bytes()
        output, error = child.communicate("continue\n", timeout=30)
        assert child.returncode == 0, error
        assert json.loads(output) == dict(conflict=True, dirty=True, history=1)
        assert path.read_bytes() == content and load_document(path).objects[key].name == "Version parent"
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)


def test_active_process_lock_refuses_save_and_releases_for_retry(tmp_path):
    doc, path, key = project(tmp_path)
    child = worker('''
import sys
from PySide6.QtCore import QLockFile
lock = QLockFile(sys.argv[1]+".lock")
lock.setStaleLockTime(0)
assert lock.tryLock(0)
print("locked", flush=True)
input()
lock.unlock()
''', path)
    try:
        assert child.stdout.readline().strip() == "locked"
        doc.rename(key, "Modification conservée")
        before = path.read_bytes()
        with pytest.raises(DocumentConflictError, match="autre instance"):
            save_document(doc, path)
        assert doc.is_modified and doc.history.count == 1 and path.read_bytes() == before
        child.communicate("release\n", timeout=15)
        assert child.returncode == 0
        save_document(doc, path)
        assert not doc.is_modified and load_document(path).objects[key].name == "Modification conservée"
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)


def test_crashed_writer_lock_does_not_permanently_block_saving(tmp_path):
    doc, path, key = project(tmp_path)
    child = worker('''
import sys
from PySide6.QtCore import QLockFile
lock = QLockFile(sys.argv[1]+".lock")
lock.setStaleLockTime(0)
assert lock.tryLock(0)
print("locked", flush=True)
input()
''', path)
    try:
        assert child.stdout.readline().strip() == "locked"
        child.kill()
        child.communicate(timeout=10)
        assert Path(str(path)+".lock").exists()
        doc.rename(key, "Après arrêt anormal")
        save_document(doc, path)
        assert not doc.is_modified and load_document(path).objects[key].name == "Après arrêt anormal"
        assert not Path(str(path)+".lock").exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)


@pytest.mark.parametrize("delete", [False, True])
def test_external_change_is_detected_even_with_same_file_size_and_timestamp(tmp_path, delete):
    doc, path, key = project(tmp_path)
    stat = path.stat()
    external = bytearray(path.read_bytes())
    external[-1] ^= 1
    if delete:
        path.unlink()
    else:
        path.write_bytes(external)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    doc.rename(key, "Travail local")
    before = doc.file_digest
    with pytest.raises(DocumentConflictError, match="modifié ou supprimé"):
        save_document(doc, path)
    assert doc.is_modified and doc.history.count == 1 and doc.file_digest == before
    assert not path.exists() if delete else path.read_bytes() == external
    copy = tmp_path/"copie.cada"
    save_document(doc, copy)
    assert load_document(copy).objects[key].name == "Travail local"


def test_external_change_during_write_preserves_foreign_content_and_cleans_temporary(tmp_path, monkeypatch):
    from cadastrophe.core import persistence
    doc, path, key = project(tmp_path)
    doc.rename(key, "Travail local")
    external = b"external update during native serialization"
    fsync = persistence.os.fsync
    def change(fd):
        fsync(fd)
        path.write_bytes(external)
    monkeypatch.setattr(persistence.os, "fsync", change)
    with pytest.raises(DocumentConflictError, match="pendant l’enregistrement"):
        save_document(doc, path)
    assert path.read_bytes() == external and doc.is_modified
    assert not list(tmp_path.glob("*.tmp")) and not Path(str(path)+".lock").exists()
