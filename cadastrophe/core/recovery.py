"""Atomic recovery generations, separate from user files and document history."""
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import tempfile
from uuid import UUID, uuid4

from PySide6.QtCore import QObject, QLockFile, QStandardPaths, QTimer, Signal

from .persistence import DocumentFileError, load_document, write_document, _unique_keys, MAX_TOTAL, MAX_MANIFEST, EXTENSION

log = logging.getLogger(__name__)


class RecoveryError(ValueError):
    pass


@dataclass(frozen=True)
class RecoveryEntry:
    record_path: Path
    document_id: str
    name: str
    original_path: str | None
    saved_at: str
    snapshot: str
    sha256: str

    @property
    def snapshot_path(self):
        return self.record_path.parent / self.snapshot


def default_recovery_directory():
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)) / "CADastrophe" / "recovery"


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise RecoveryError("UUID de récupération invalide.")
    return value


def _digest(path):
    if path.is_symlink() or path.stat().st_size > MAX_TOTAL + MAX_MANIFEST + 16 * 1024 * 1024:
        raise RecoveryError("Copie de récupération invalide ou trop volumineuse.")
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_record(path, data):
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False)
    if len(payload.encode("utf-8")) > 32 * 1024 or (data["original_path"] is not None and len(data["original_path"]) > 4096):
        raise RecoveryError("Notice de récupération trop volumineuse.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".recovery-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class RecoveryService(QObject):
    message = Signal(str)

    def __init__(self, documents, registry, directory=None, parent=None):
        super().__init__(parent)
        self.documents, self.registry = documents, registry
        self.directory = Path(directory).resolve() if directory is not None else None
        self.session_id = str(uuid4())
        self.session_path = None
        self._locks, self._current, self._sources = {}, {}, {}
        self._tokens, self._written_tokens, self._subscriptions = {}, {}, {}
        self._stopped = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.snapshot_now)
        documents.document_added.connect(self._added)
        documents.document_removed.connect(self._removed)
        documents.document_replaced.connect(self._replaced)
        for document in documents.documents:
            self._added(document)

    def configure(self, enabled, minutes):
        if type(enabled) is not bool or type(minutes) is not int or not 1 <= minutes <= 60:
            raise ValueError("Intervalle de sauvegarde automatique attendu entre 1 et 60 minutes.")
        self.enabled = enabled
        self.timer.stop()
        self.timer.setInterval(minutes * 60_000)
        if enabled and self.directory is not None and not self._stopped:
            self.timer.start()

    def _error(self, error):
        text = "Récupération : " + str(error)
        log.warning(text)
        self.message.emit(text)

    def _added(self, document):
        slot = partial(self._changed, document)
        self._subscriptions[document.id] = (document, slot)
        self._tokens[document.id] = 0
        document.history.changed.connect(slot)

    def _changed(self, document):
        if self._stopped:
            return
        self._tokens[document.id] += 1
        if not document.history.blocked and not document.is_modified:
            self._clean(document.id)

    def _removed(self, document):
        _, slot = self._subscriptions.pop(document.id)
        document.history.changed.disconnect(slot)
        if not self._stopped:
            self._clean(document.id)
        self._tokens.pop(document.id, None)
        self._written_tokens.pop(document.id, None)

    def _replaced(self, previous, document):
        self._removed(previous)
        self._added(document)

    def _lock(self, session):
        if session in self._locks:
            return self._locks[session]
        lock = QLockFile(str(session / "session.lock"))
        # A long-running live process is not stale. Qt still checks dead PIDs.
        lock.setStaleLockTime(0)
        return lock if lock.tryLock(0) else None

    def _ensure_session(self):
        if self.session_path is None:
            self.directory.mkdir(parents=True, exist_ok=True)
            session = self.directory / self.session_id
            session.mkdir()
            lock = self._lock(session)
            if lock is None:
                raise RecoveryError("Impossible de verrouiller la session de sauvegarde.")
            self._locks[session] = lock
            self.session_path = session

    def _read_entry(self, path):
        path = Path(path).absolute()
        session = path.parent
        if (self.directory is None or session.parent not in self._directories() or session.parent.is_symlink() or session.is_symlink()
                or path.is_symlink() or path.suffix != ".json"):
            raise RecoveryError("Emplacement de récupération invalide.")
        _uuid(session.name)
        _uuid(path.stem)
        if path.stat().st_size > 32 * 1024:
            raise RecoveryError("Notice de récupération trop volumineuse.")
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys)
        fields = {"format", "schema_version", "document_id", "name", "original_path", "saved_at", "snapshot", "sha256"}
        if type(data) is not dict or set(data) != fields or data["format"] != "cadastrophe.recovery":
            raise RecoveryError("Notice de récupération invalide.")
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise RecoveryError("Version de récupération non prise en charge.")
        if _uuid(data["document_id"]) != path.stem:
            raise RecoveryError("Identité de récupération incohérente.")
        if type(data["name"]) is not str or not data["name"].strip():
            raise RecoveryError("Nom de récupération invalide.")
        if data["original_path"] is not None and (type(data["original_path"]) is not str or len(data["original_path"]) > 4096):
            raise RecoveryError("Chemin d’origine invalide.")
        snapshot = data["snapshot"]
        if type(snapshot) is not str:
            raise RecoveryError("Nom de copie de récupération invalide.")
        suffix = Path(snapshot).suffix
        if suffix != EXTENSION or snapshot != f"{_uuid(snapshot.removesuffix(suffix))}{suffix}":
            raise RecoveryError("Nom de copie de récupération invalide.")
        when = datetime.fromisoformat(data["saved_at"])
        if when.utcoffset() is None or when.utcoffset().total_seconds() != 0:
            raise RecoveryError("Date de récupération invalide.")
        digest = data["sha256"]
        if type(digest) is not str or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise RecoveryError("Empreinte de récupération invalide.")
        return RecoveryEntry(path, data["document_id"], data["name"], data["original_path"],
                             data["saved_at"], snapshot, digest)

    def snapshot_now(self):
        if self._stopped or self.directory is None or not getattr(self, "enabled", True) or self.documents.blocked:
            return 0
        count = 0
        for document in self.documents.documents:
            if not document.is_modified or self._written_tokens.get(document.id) == self._tokens[document.id]:
                continue
            snapshot = None
            try:
                self._ensure_session()
                snapshot = self.session_path / f"{uuid4()}{EXTENSION}"
                write_document(document, snapshot, registry=self.registry)
                record = dict(format="cadastrophe.recovery", schema_version=1, document_id=document.id,
                              name=document.display_name, original_path=str(document.file_path) if document.file_path else None,
                              saved_at=datetime.now(timezone.utc).isoformat(), snapshot=snapshot.name, sha256=_digest(snapshot))
                path = self.session_path / f"{document.id}.json"
                _write_record(path, record)
                previous = self._current.get(document.id)
                self._current[document.id] = RecoveryEntry(path, document.id, record["name"], record["original_path"],
                                                         record["saved_at"], record["snapshot"], record["sha256"])
                self._written_tokens[document.id] = self._tokens[document.id]
                snapshot = None  # committed generation must survive subsequent errors
                count += 1
                if previous is not None:
                    previous.snapshot_path.unlink(missing_ok=True)
            except (OSError, ValueError, TypeError, DocumentFileError, RuntimeError) as exc:
                self._error(exc)
            finally:
                if snapshot is not None:
                    try:
                        snapshot.unlink(missing_ok=True)
                    except OSError as exc:
                        self._error(exc)
        return count

    def _directories(self):
        return () if self.directory is None else (self.directory,)

    def entries(self):
        if self.directory is None:
            return ()
        entries = []
        try:
            sessions = [session for directory in self._directories() if directory.exists() and not directory.is_symlink()
                        for session in directory.iterdir()]
            for session in sessions:
                if not session.is_dir() or session.is_symlink() or session == self.session_path:
                    continue
                try:
                    _uuid(session.name)
                except ValueError:
                    continue
                lock = self._lock(session)
                if lock is None:
                    continue  # another running instance still owns these files
                try:
                    for path in session.glob("*.json"):
                        try:
                            entry = self._read_entry(path)
                            if entry not in self._sources.values():
                                entries.append(entry)
                        except (OSError, ValueError, TypeError, DocumentFileError, RecursionError) as exc:
                            self._error(exc)
                finally:
                    if session not in self._locks:
                        lock.unlock()
        except OSError as exc:
            self._error(exc)
        return tuple(sorted(entries, key=lambda entry: entry.saved_at, reverse=True))

    def _claim(self, entry):
        # Revalidate the location and generation under the session lock.
        current = self._read_entry(entry.record_path)
        lock = self._lock(current.record_path.parent)
        if lock is None:
            raise RecoveryError("Cette copie appartient à une autre instance encore ouverte.")
        self._locks[current.record_path.parent] = lock
        if self._read_entry(entry.record_path) != entry:
            raise RecoveryError("La copie de récupération a changé ; actualisez la liste.")

    def restore(self, entry):
        if self.documents.blocked:
            raise RecoveryError("Terminez l’opération documentaire avant de récupérer une copie.")
        if any(document.id == entry.document_id for document in self.documents.documents):
            raise RecoveryError("Ce document est déjà ouvert. Fermez-le avant de récupérer cette copie.")
        try:
            self._claim(entry)
            if _digest(entry.snapshot_path) != entry.sha256:
                raise RecoveryError("Copie de récupération corrompue : empreinte différente.")
            document = load_document(entry.snapshot_path, registry=self.registry)
            if document.id != entry.document_id:
                raise RecoveryError("Identité de la copie de récupération incohérente.")
            document.mark_recovered()
            self.documents.add(document)
            self._sources[document.id] = entry
            return document
        except (OSError, ValueError, TypeError, DocumentFileError, RuntimeError) as exc:
            raise RecoveryError(str(exc)) from exc

    def _delete_entry(self, entry):
        self._claim(entry)
        entry.record_path.unlink(missing_ok=True)
        entry.snapshot_path.unlink(missing_ok=True)

    def discard(self, entry):
        if entry in self._sources.values():
            raise RecoveryError("Enregistrez ou fermez d’abord le document récupéré.")
        try:
            self._delete_entry(entry)
        except (OSError, ValueError, TypeError, DocumentFileError) as exc:
            raise RecoveryError(str(exc)) from exc

    def _clean(self, document_id):
        for mapping in (self._current, self._sources):
            entry = mapping.get(document_id)
            if entry is not None:
                try:
                    self._delete_entry(entry)
                    del mapping[document_id]
                except (OSError, ValueError, TypeError, DocumentFileError) as exc:
                    self._error(exc)
        self._written_tokens.pop(document_id, None)

    def shutdown(self):
        """Stop and release locks. Normal document removal does the cleanup."""
        self._stopped = True
        self.timer.stop()
        for session, lock in self._locks.items():
            lock.unlock()
            try:
                session.rmdir()  # only empty directories; never recursive deletion
            except OSError:
                pass
        self._locks.clear()
