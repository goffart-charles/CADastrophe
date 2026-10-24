"""Cooperative Qt jobs: one native operation per event turn, no event pumping."""
from PySide6.QtCore import QObject, QTimer, Signal, Qt
from PySide6.QtWidgets import QProgressDialog


class Computation(QObject):
    progressed = Signal(int, int, str)
    finished = Signal(str)

    def __init__(self, parent, steps, title, *, completed=None, failed=None, cancelled=None):
        super().__init__(parent)
        self.steps = iter(steps)
        self.completed, self.failed, self.cancelled = completed, failed, cancelled
        self.outcome = "running"
        self._advancing = self._cancel_requested = False
        self.dialog = QProgressDialog(title, "Annuler", 0, 0, parent)
        self.dialog.setWindowTitle(title)
        self.dialog.setWindowModality(Qt.WindowModality.NonModal)
        self.dialog.setMinimumDuration(500)
        self.dialog.setAutoClose(False)
        self.dialog.setAutoReset(False)
        self.dialog.canceled.connect(self.cancel)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.advance)
        self.timer.start(0)

    def advance(self):
        if self.outcome != "running":
            return
        try:
            self._advancing = True
            try:
                current, total, label = next(self.steps)
            finally:
                self._advancing = False
            if self._cancel_requested:
                self.cancel()
                return
            self.dialog.setRange(0, max(current+1, total))
            self.dialog.setValue(current)
            self.dialog.setLabelText(label)
            self.progressed.emit(current, total, label)
        except StopIteration as result:
            self.finish("completed", self.completed, result.value)
            return
        except (Exception, SystemExit) as error:
            self.finish("failed", self.failed, error)
            return
        if self.outcome == "running":
            self.timer.start(0)

    def cancel(self):
        if self._advancing:
            self._cancel_requested = True
            return
        self.finish("cancelled", self.cancelled)

    def finish(self, outcome, callback, *args):
        if self.outcome != "running":
            return
        self.outcome = outcome
        self.timer.stop()
        try:
            close = getattr(self.steps, "close", None)
            if close is not None:
                close()
        except (Exception, SystemExit) as error:
            self.outcome = outcome = "failed"
            callback, args = self.failed, (error,)
        finally:
            self.steps = iter(())
            self.dialog.close()
            self.dialog.deleteLater()
            self.completed = self.failed = self.cancelled = None
        try:
            if callback is not None:
                callback(*args)
        finally:
            self.finished.emit(outcome)
            self.deleteLater()


class Computations(QObject):
    changed = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.window = parent
        self.active = None

    @property
    def busy(self):
        return self.active is not None

    def start(self, steps, title, **callbacks):
        if self.busy:
            close = getattr(steps, "close", None)
            if close is not None:
                close()
            raise ValueError("Un calcul est déjà en cours.")
        self.active = task = Computation(self.window, steps, title, **callbacks)
        task.finished.connect(self._finished)
        self.changed.emit()
        return task

    def _finished(self, _):
        if self.active is self.sender():
            self.active = None
            self.changed.emit()

    def cancel(self):
        if self.active is not None:
            self.active.cancel()
