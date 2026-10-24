"""Document lifetime and activation, independent of widgets and OCCT contexts."""
from contextlib import contextmanager

from PySide6.QtCore import QObject, Signal

from .history import HistoryBusyError


class DocumentManager(QObject):
    about_to_change = Signal()
    document_added = Signal(object)
    document_removed = Signal(object)
    document_replaced = Signal(object, object)
    active_changed = Signal(object)
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._documents = {}
        self._active_id = None
        self._changing = False

    @property
    def documents(self):
        return tuple(self._documents.values())

    @property
    def active_document(self):
        return self._documents.get(self._active_id)

    @property
    def blocked(self):
        return self._changing or any(doc.history.blocked for doc in self.documents)

    @contextmanager
    def _change(self):
        if self.blocked:
            raise HistoryBusyError("Terminez l’opération documentaire avant de changer les documents.")
        self._changing = True
        try:
            self.about_to_change.emit()
            yield
        finally:
            self._changing = False
            self.changed.emit()

    def add(self, document):
        with self._change():
            if document.id in self._documents:
                raise ValueError("Document already open")
            if document.history.blocked:
                raise HistoryBusyError("Le document est en cours de modification.")
            self._documents[document.id] = document
            document.history.changed.connect(self.changed.emit)
            document.metadata_changed.connect(self.changed.emit)
            document.selection_changed.connect(self._selection_changed)
            self.document_added.emit(document)
            self._active_id = document.id
            self.active_changed.emit(document)
        return document

    def activate(self, document_id):
        with self._change():
            document = self._documents[document_id]
            if self._active_id != document_id:
                self._active_id = document_id
                self.active_changed.emit(document)

    def remove(self, document_id):
        """Remove after the caller has resolved any unsaved-change confirmation."""
        with self._change():
            document = self._documents[document_id]
            ids = list(self._documents)
            index = ids.index(document_id)
            del self._documents[document_id]
            document.history.changed.disconnect(self.changed.emit)
            document.metadata_changed.disconnect(self.changed.emit)
            document.selection_changed.disconnect(self._selection_changed)
            if self._active_id == document_id:
                remaining = list(self._documents)
                self._active_id = remaining[min(index, len(remaining) - 1)] if remaining else None
                self.active_changed.emit(self.active_document)
            self.document_removed.emit(document)
        return document

    def replace(self, document_id, document):
        """Adopt an explicit low-level replacement (e.g. demo reload)."""
        with self._change():
            previous = self._documents[document_id]
            if document.id in self._documents:
                raise ValueError("Document already open")
            if document.history.blocked:
                raise HistoryBusyError("Le document est en cours de modification.")
            self._documents = {document.id if key == document_id else key:
                               document if key == document_id else value
                               for key, value in self._documents.items()}
            previous.history.changed.disconnect(self.changed.emit)
            previous.metadata_changed.disconnect(self.changed.emit)
            previous.selection_changed.disconnect(self._selection_changed)
            document.history.changed.connect(self.changed.emit)
            document.metadata_changed.connect(self.changed.emit)
            document.selection_changed.connect(self._selection_changed)
            self.document_replaced.emit(previous, document)
            if self._active_id == document_id:
                self._active_id = document.id
                self.active_changed.emit(document)

    def _selection_changed(self, _ids):
        self.changed.emit()
