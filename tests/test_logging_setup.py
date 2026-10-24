"""Real hook delivery, bounded logs and fallback without filesystem assumptions."""
import logging
from pathlib import Path
import sys
import threading

import pytest
from PySide6.QtCore import qInstallMessageHandler, qWarning
from PySide6.QtWidgets import QApplication, QPushButton

from cadastrophe.logging_setup import DiagnosticSession, default_log_path


def test_utf8_and_rotation_keep_existing_handlers_and_restore_levels_and_hooks(tmp_path):
    logger = logging.getLogger("cadastrophe")
    before = tuple(logger.handlers), logger.level, sys.excepthook, threading.excepthook
    path = tmp_path / "logs" / "session.log"
    with DiagnosticSession(path, max_bytes=1024, backups=2):
        for index in range(40):
            logger.info("Pièce éçà %d : %s", index, "x" * 150)
        logger.warning("Dernier message éçà")
    files = list(path.parent.glob("session.log*"))
    assert len(files) == 3 and all(file.stat().st_size <= 1024 for file in files)
    content = "".join(file.read_text(encoding="utf-8") for file in files)
    assert "Dernier message éçà" in content and "Session terminée" in content and "pid=" in content
    assert (tuple(logger.handlers), logger.level, sys.excepthook, threading.excepthook) == before


def test_real_qt_slot_and_thread_exceptions_are_logged_notified_and_do_not_stop_other_slots(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "exceptions.log"
    messages, successes = [], []
    button = QPushButton()
    def broken():
        raise ValueError("slot failure éçà")
    button.clicked.connect(broken)
    with DiagnosticSession(path) as session:
        session.notify = messages.append
        button.click()  # PySide invokes sys.excepthook itself.
        thread = threading.Thread(target=broken, name="T17-worker")
        thread.start()
        thread.join(timeout=3)
        assert not thread.is_alive()
        button.clicked.disconnect(broken)
        button.clicked.connect(lambda: successes.append(True))
        button.click()
    button.deleteLater()
    app.processEvents()
    content = path.read_text(encoding="utf-8")
    assert content.count("ValueError: slot failure éçà") == 2 and "Traceback" in content
    assert "T17-worker" in content and len(messages) == 2 and successes == [True]
    assert all(str(path) in message for message in messages)


def test_qt_warnings_reach_log_and_previous_handler_is_restored(tmp_path):
    delivered = []
    previous = qInstallMessageHandler(lambda kind, context, message: delivered.append(message))
    try:
        path = tmp_path / "qt.log"
        with DiagnosticSession(path):
            qWarning("T17 Qt warning")
        assert "T17 Qt warning" in path.read_text(encoding="utf-8") and not delivered
        qWarning("previous handler restored")
        assert delivered == ["previous handler restored"]
    finally:
        qInstallMessageHandler(previous)


def test_unwritable_log_location_falls_back_to_console_and_keeps_exception_notification(tmp_path, capsys):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("keep")
    with DiagnosticSession(blocked / "app.log") as session:
        messages = []
        session.notify = messages.append
        assert session.file_error
        try:
            raise RuntimeError("fallback failure")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
    assert blocked.read_text() == "keep" and "console" in messages[0]
    assert "fallback failure" in capsys.readouterr().err


def test_startup_failure_is_logged_and_original_exception_propagates(tmp_path):
    path = tmp_path / "startup.log"
    with pytest.raises(ValueError, match="startup failure"):
        with DiagnosticSession(path):
            raise ValueError("startup failure")
    assert "ValueError: startup failure" in path.read_text(encoding="utf-8")


def test_default_log_uses_user_configuration_parent_and_never_working_directory(tmp_path, monkeypatch):
    from cadastrophe.core import preferences
    monkeypatch.setattr(preferences, "default_preferences_path", lambda: tmp_path / "config" / "preferences.json")
    path = default_log_path()
    assert path.parent == tmp_path / "config" / "logs" and path.name.startswith("cadastrophe-")


def test_notification_failure_is_logged_without_recursing_and_interrupt_uses_original_hook(tmp_path, monkeypatch):
    interrupted = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: interrupted.append(args[0]))
    path = tmp_path / "notify.log"
    with DiagnosticSession(path) as session:
        def broken_notification(message):
            raise RuntimeError("notification failure")
        session.notify = broken_notification
        session._uncaught(ValueError, ValueError("original failure"), None)
        session._uncaught(KeyboardInterrupt, KeyboardInterrupt(), None)
    text = path.read_text(encoding="utf-8")
    assert "original failure" in text and "notification failure" in text
    assert interrupted == [KeyboardInterrupt]
