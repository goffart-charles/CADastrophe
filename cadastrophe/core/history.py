"""Guarded per-document undo, including atomic groups with rollback on failure."""
from contextlib import contextmanager

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack


class HistoryBusyError(RuntimeError):
    pass


class _AppliedGroup(QUndoCommand):
    def __init__(self, title, commands):
        super().__init__(title)
        self.commands = tuple(commands)
        self._first_redo = True

    def redo(self):
        if self._first_redo:
            self._first_redo = False
        else:
            for command in self.commands:
                command.redo()

    def undo(self):
        for command in reversed(self.commands):
            command.undo()

    def retained_objects(self):
        return tuple(obj for command in self.commands for obj in command.retained_objects())


class DocumentHistory(QObject):
    changed = Signal()

    def __init__(self, document):
        super().__init__(document)
        self._stack = QUndoStack(self)
        self._busy = False
        self._transaction = None
        self._reservation = None

    @property
    def blocked(self):
        return self._busy or self._transaction is not None or self._reservation is not None

    @property
    def can_undo(self):
        return not self.blocked and self._stack.canUndo()

    @property
    def can_redo(self):
        return not self.blocked and self._stack.canRedo()

    @property
    def undo_text(self):
        return self._stack.undoText()

    @property
    def redo_text(self):
        return self._stack.redoText()

    @property
    def count(self):
        return self._stack.count()

    @property
    def index(self):
        return self._stack.index()

    @property
    def is_clean(self):
        return self._stack.isClean() and not self._transaction

    def mark_clean(self):
        """Record the current revision as a saved/generated baseline."""
        self._check_idle()
        self._stack.setClean()
        self.changed.emit()

    def mark_dirty(self):
        """A recovered baseline has no saved revision, even with an empty stack."""
        self._check_idle()
        self._stack.resetClean()
        self.changed.emit()

    def retained_objects(self):
        """Snapshots needed by either undo or redo (e.g. plugin-owned types)."""
        objects = []
        for index in range(self._stack.count()):
            command = self._stack.command(index)
            provider = getattr(command, "retained_objects", None)
            if not callable(provider):
                raise RuntimeError("Une commande personnalisée ne déclare pas ses objets retenus.")
            objects.extend(provider())
        return tuple(objects)

    def _check_idle(self, *, allow_transaction=False):
        if self._reservation is not None or self._busy or (self._transaction is not None and not allow_transaction):
            raise HistoryBusyError("Une opération documentaire est déjà en cours.")

    def reserve(self, owner):
        """Lock mutations without touching the stack, its clean marker or redo."""
        self._check_idle()
        if owner is None:
            raise ValueError("Une réservation doit avoir un propriétaire.")
        self._reservation = owner
        self.changed.emit()

    def release(self, owner):
        if self._reservation is not owner or owner is None:
            raise HistoryBusyError("Réservation documentaire différente.")
        self._reservation = None
        self.changed.emit()

    @contextmanager
    def commit_reserved(self, owner, title):
        """Convert a reservation into an atomic transaction, without an idle signal."""
        if self._reservation is not owner or owner is None:
            raise HistoryBusyError("Réservation documentaire différente.")
        self._reservation = None
        try:
            with self.transaction(title):
                try:
                    yield
                finally:
                    # Keep lifecycle guards active through push/rollback notifications.
                    self._reservation = owner
        finally:
            self._reservation = owner

    @contextmanager
    def _replay(self):
        self._busy = True
        self.changed.emit()
        try:
            yield
        finally:
            self._busy = False
            self.changed.emit()

    def execute(self, command):
        self._check_idle(allow_transaction=True)
        with self._replay():
            if self._transaction is None:
                self._stack.push(command)
            else:
                command.redo()
                self._transaction.append(command)

    def undo(self):
        self._check_idle()
        if self.can_undo:
            with self._replay():
                self._stack.undo()

    def redo(self):
        self._check_idle()
        if self.can_redo:
            with self._replay():
                self._stack.redo()

    def clear(self):
        """Declare a loaded/generated document to be the new baseline."""
        self._check_idle()
        with self._replay():
            self._stack.clear()

    @contextmanager
    def transaction(self, title):
        self._check_idle()
        commands = self._transaction = []
        self.changed.emit()
        try:
            yield
        except BaseException:
            with self._replay():
                for command in reversed(commands):
                    command.undo()
            raise
        else:
            if commands:
                with self._replay():
                    self._stack.push(_AppliedGroup(title, commands))
        finally:
            self._transaction = None
            self.changed.emit()
