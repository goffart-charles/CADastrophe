"""Owned, coalesced reconstruction after document commands, never during replay."""
import logging

from PySide6.QtCore import QTimer

from cadastrophe.core.body_history import body_result, evaluate_body, evaluate_body_steps, result_signature
from cadastrophe.core.feature_model import ResultStatus
from cadastrophe.core.feature_objects import BodyObject

log = logging.getLogger(__name__)


class Rebuilder:
    def __init__(self, services, extrusions):
        self.services, self.extrusions = services, extrusions
        self._connections = {}
        self._closed = self._working = False
        self.threshold = 32
        self._task = None
        self._cancelled = {}
        self._timer = QTimer(services)
        self._timer.setSingleShot(True)
        self._timer.setInterval(75)
        self._timeout = lambda: self.flush(interactive=True)
        self._timer.timeout.connect(self._timeout)

    def sync(self):
        if self._closed:
            return
        documents = set(self.services.documents)
        document_ids = {document.id for document in documents}
        self._cancelled = {key: signature for key, signature in self._cancelled.items() if key[0] in document_ids}
        for document in set(self._connections) - documents:
            document.equation_evaluator = None
            document.history_context.evaluator = None
            for signal, callback in self._connections.pop(document):
                signal.disconnect(callback)
        for document in documents - set(self._connections):
            document.equation_evaluator = self.extrusions.evaluator
            document.history_context.evaluator = self.extrusions.evaluator
            connections = ((document.history.changed, self.request),
                           (document.body_result_changed, self.request),
                           (document.history_context.changed, self.services.state_changed.emit))
            for signal, callback in connections:
                signal.connect(callback)
            self._connections[document] = connections
        self.request()

    def request(self, *_):
        if not self._closed and not self._working:
            self._timer.start()

    def flush(self, *, interactive=False):
        self._timer.stop()
        if self._closed or self._working or self.services.computation_busy:
            return
        self._working = True
        try:
            for document in tuple(self._connections):
                # A later history release requests a new pass. Never spin while
                # a tool holds a reservation or a transaction is in progress.
                if document not in self._connections or document.history.blocked:
                    continue
                adapter = self.extrusions.evaluator(document)
                for body in tuple(document.objects.values()):
                    if document not in self._connections or document.history.blocked:
                        break  # A publication callback may close the document/start an owned tool.
                    if (isinstance(body, BodyObject) and body.features
                            and body_result(document, body.id).status is ResultStatus.DIRTY):
                        snapshot = result_signature(document.objects, body.id)
                        if self._cancelled.get((document.id, body.id)) == snapshot:
                            continue
                        self._cancelled.pop((document.id, body.id), None)
                        from cadastrophe.core.computation import cooperative_required
                        cooperative = (len(body.features) >= self.threshold or interactive
                                       and cooperative_required({key: document.objects[key] for key in body.features}))
                        if cooperative and self.services.computation_available:
                            self._start(document, body, adapter)
                            return
                        try:
                            evaluate_body(document, body.id, adapter, reuse=True)
                        except Exception:
                            log.exception("Reconstruction abandonnée : %s", body.id)
                from .datums import refresh_datums
                refresh_datums(document)
        finally:
            if self._task is None:
                self._working = False

    def retry(self):
        self._cancelled.clear()
        self.request()

    def _start(self, document, body, adapter):
        steps = evaluate_body_steps(document, body.id, adapter, reuse=True)
        def finish():
            self._task = None
            self._working = False
            self.request()
        def completed(_):
            from .datums import refresh_datums
            refresh_datums(document)
            finish()
        def failed(error):
            self.services.report_problem(f"Reconstruction interrompue : {error}")
            self._cancelled[(document.id, body.id)] = result_signature(document.objects, body.id)
            finish()
        def cancelled():
            self._cancelled[(document.id, body.id)] = result_signature(document.objects, body.id)
            self.services.message.emit("Recalcul annulé · F5 pour réessayer.")
            finish()
        self._task = self.services.run_computation(steps, f"Recalcul · {body.name}",
                                                  completed=completed, failed=failed, cancelled=cancelled)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._task is not None:
            self._task.cancel()
        self._timer.stop()
        self._timer.timeout.disconnect(self._timeout)
        self._timer.deleteLater()
        for connections in self._connections.values():
            for signal, callback in connections:
                signal.disconnect(callback)
        for document in self._connections:
            document.equation_evaluator = None
            document.history_context.evaluator = None
            document._body_evaluation_cache.clear()
        self._connections.clear()
        self._cancelled.clear()
