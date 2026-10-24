"""Owned application diagnostics; no changes to document or navigation state."""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading

from PySide6.QtCore import QtMsgType, qInstallMessageHandler, qVersion

log = logging.getLogger("cadastrophe.diagnostics")


def default_log_path():
    from .core.preferences import default_preferences_path
    # Separate files avoid Windows rollover conflicts between running instances.
    return default_preferences_path().parent / "logs" / f"cadastrophe-{os.getpid()}.log"


class DiagnosticSession:
    """Install and restore Python/thread/Qt hooks and only our own log handlers."""
    def __init__(self, path, *, max_bytes=2 * 1024 * 1024, backups=2):
        self.path = Path(path)
        self.max_bytes, self.backups = max_bytes, backups
        self.notify = None
        self.handlers = []

    def __enter__(self):
        self.logger = logging.getLogger("cadastrophe")
        self._level = self.logger.level
        self.logger.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s %(levelname)s pid=%(process)d %(threadName)s %(name)s: %(message)s")
        console = logging.StreamHandler()
        self.handlers.append(console)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handlers.append(RotatingFileHandler(self.path, maxBytes=self.max_bytes,
                                                    backupCount=self.backups, encoding="utf-8"))
        except OSError as exc:
            self.file_error = str(exc)
        else:
            self.file_error = ""
        for handler in self.handlers:
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
        self._python_hook, self._thread_hook = sys.excepthook, threading.excepthook
        sys.excepthook, threading.excepthook = self._uncaught, self._thread_exception
        self._qt_hook = qInstallMessageHandler(self._qt_message)
        log.info("Session démarrée ; Python %s ; Qt %s ; journal %s", sys.version.split()[0], qVersion(), self.path)
        if self.file_error:
            log.warning("Journal fichier indisponible, console conservée : %s", self.file_error)
        return self

    def _uncaught(self, exception_type, exception, traceback):
        if issubclass(exception_type, (KeyboardInterrupt, SystemExit)):
            self._python_hook(exception_type, exception, traceback)
            return
        self._report(exception_type, exception, traceback, "Exception Python non interceptée")

    def _thread_exception(self, args):
        if args.exc_type is not SystemExit:
            self._report(args.exc_type, args.exc_value, args.exc_traceback,
                         f"Exception du thread {args.thread.name if args.thread else '?'}")

    def _report(self, exception_type, exception, traceback, title):
        log.error(title, exc_info=(exception_type, exception, traceback))
        if self.notify is not None:
            location = f"Journal : {self.path}" if not self.file_error else "Détails dans la console."
            try:
                self.notify(f"Erreur inattendue ({exception_type.__name__}). {location}")
            except Exception:
                log.exception("Notification d’erreur impossible")

    @staticmethod
    def _qt_message(kind, context, message):
        level = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO,
                 QtMsgType.QtWarningMsg: logging.WARNING, QtMsgType.QtCriticalMsg: logging.ERROR,
                 QtMsgType.QtFatalMsg: logging.CRITICAL}.get(kind, logging.WARNING)
        logging.getLogger("cadastrophe.qt").log(level, "%s [%s %s:%s]", message, context.category,
                                            context.file or "Qt", context.line)

    def __exit__(self, exception_type, exception, traceback):
        if exception_type is not None:
            log.error("Arrêt de session sur exception", exc_info=(exception_type, exception, traceback))
        else:
            log.info("Session terminée")
        self.notify = None
        sys.excepthook, threading.excepthook = self._python_hook, self._thread_hook
        qInstallMessageHandler(self._qt_hook)
        for handler in reversed(self.handlers):
            self.logger.removeHandler(handler)
            handler.close()
        self.logger.setLevel(self._level)
