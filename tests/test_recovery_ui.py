"""Qt recovery flow, preferences, lifecycle cancellation and preview isolation."""
from dataclasses import replace
from pathlib import Path
import shutil

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from cadastrophe.app import MainWindow
from cadastrophe.core.documents import DocumentManager
from cadastrophe.core.object_types import ObjectTypeRegistry
from cadastrophe.core.recovery import RecoveryService
from cadastrophe.core.persistence import save_document, load_document
from cadastrophe.sample_scene import demo_document
from cadastrophe.ui.preferences_dialog import PreferencesDialog
from cadastrophe.ui.recovery_dialog import RecoveryDialog
from cadastrophe.ui.themes import LIGHT, DARK, apply_theme


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "plugins"
    shutil.copytree(Path(__file__).resolve().parents[1] / "plugins/primitives", root / "primitives")
    window = MainWindow(plugin_directory=root, preferences_path=tmp_path / "preferences.json",
                        recovery_directory=tmp_path / "recovery")
    window.documents.replace(window.viewport.document.id, demo_document())
    window.show()
    window.activateWindow()
    app.processEvents()
    try:
        yield window
    finally:
        window.tool_sessions.cancel()
        window.confirm_discard = lambda document: True
        window.close()
        window.deleteLater()
        app.processEvents()
        apply_theme(app, LIGHT)


def body(document):
    return next(obj for obj in document.objects.values() if not obj.plane)


def pose(window):
    camera = window.viewport.view.Camera()
    return (*camera.Eye().Coord(), *camera.Center().Coord(), *camera.Up().Coord(), camera.Scale())


def abandoned(window, tmp_path):
    documents = DocumentManager()
    document = documents.add(demo_document("Pièce avant crash"))
    original = tmp_path / "original.cada"
    save_document(document, original)
    document.rename(body(document).id, "Donnée récupérée")
    service = RecoveryService(documents, ObjectTypeRegistry(), window.recovery.directory)
    service.configure(True, 5)
    assert service.snapshot_now() == 1
    entry = service._current[document.id]
    service.shutdown()
    return entry, original


def test_preferences_save_cancel_restart_and_timer_reconfiguration_use_real_dialog(window):
    previous = window.preferences

    def cancel():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, PreferencesDialog)
        dialog.autosave_enabled.setChecked(False)
        dialog.autosave_interval_minutes.setValue(12)
        dialog.reject()

    QTimer.singleShot(0, cancel)
    assert window.command_registry.execute("settings.preferences")
    assert window.preferences == previous and window.recovery.timer.isActive()

    def save():
        dialog = QApplication.activeModalWidget()
        dialog.autosave_enabled.setChecked(False)
        assert not dialog.autosave_interval_minutes.isEnabled()
        dialog.autosave_interval_minutes.setValue(12)
        QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Save), Qt.MouseButton.LeftButton)

    QTimer.singleShot(0, save)
    assert window.command_registry.execute("settings.preferences")
    assert not window.preferences.autosave_enabled and not window.recovery.timer.isActive()
    assert window.recovery.timer.interval() == 12 * 60_000
    path = window.preference_store.path
    root, directory = window.plugins.directory, window.recovery.directory
    assert window.close()
    other = MainWindow(plugin_directory=root, preferences_path=path, recovery_directory=directory)
    try:
        assert not other.preferences.autosave_enabled and other.preferences.autosave_interval_minutes == 12
        assert not other.recovery.timer.isActive()
        other.apply_preferences(replace(other.preferences, autosave_enabled=True, autosave_interval_minutes=1))
        assert other.recovery.timer.isActive() and other.recovery.timer.interval() == 60_000
    finally:
        other.close()
        other.deleteLater()


def test_active_tool_and_preview_are_excluded_then_committed_geometry_is_recoverable(window):
    document = window.viewport.document
    obj = body(document)
    document.rename(obj.id, "Modification avant outil")
    assert window.recovery.snapshot_now() == 1
    entry = window.recovery._current[document.id]
    old_bytes = entry.snapshot_path.read_bytes()
    original_count, camera = len(document.objects), pose(window)
    assert window.command_registry.execute("cadastrophe.primitives.box")
    assert window.recovery.snapshot_now() == 0
    assert entry.snapshot_path.read_bytes() == old_bytes
    assert not window.command_registry.action("file.recover").isEnabled()
    window.tool_sessions.cancel()
    assert len(document.objects) == original_count and pose(window) == camera
    assert window.command_registry.execute("cadastrophe.primitives.box")
    assert window.command_registry.execute("tool.commit")
    assert window.recovery.snapshot_now() == 1
    loaded = load_document(window.recovery._current[document.id].snapshot_path)
    assert len(loaded.objects) == original_count + 1 and len(document.objects) == original_count + 1
    assert pose(window) == camera and document.history.count == 2


def test_recovery_dialog_later_preserves_then_restore_opens_unsaved_tab_without_overwrite(window, tmp_path):
    entry, original = abandoned(window, tmp_path)
    old_bytes = original.read_bytes()
    original_document, camera = window.viewport.document, pose(window)
    observed = []

    def later():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, RecoveryDialog)
        assert dialog.list.topLevelItemCount() == 1 and dialog.selected() == entry
        observed.append(True)
        QTest.keyClick(dialog, Qt.Key.Key_Escape)

    QTimer.singleShot(0, later)
    window.manage_recovery(only_if_available=True)  # same path as normal startup
    assert observed == [True] and entry.record_path.exists() and entry.snapshot_path.exists()
    assert window.viewport.document is original_document and pose(window) == camera

    def restore():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, RecoveryDialog)
        QTest.mouseClick(dialog.restore_button, Qt.MouseButton.LeftButton)

    QTimer.singleShot(0, restore)
    assert window.command_registry.execute("file.recover")
    recovered = window.viewport.document
    assert recovered.id == entry.document_id and recovered.file_path is None and recovered.is_modified
    assert body(recovered).name == "Donnée récupérée"
    assert "*" in window.document_tabs.tabText(window.document_tabs.currentIndex())
    assert recovered.history.count == 0 and original.read_bytes() == old_bytes
    assert entry.snapshot_path.exists()  # retain the backup until save or normal close
    assert window.save_document(path=tmp_path / "choisi.cada")
    assert not entry.snapshot_path.exists() and not entry.record_path.exists()
    assert original.read_bytes() == old_bytes


def test_corrupt_restore_reports_inside_dialog_and_only_explicit_discard_deletes_copy(window, tmp_path):
    entry, original = abandoned(window, tmp_path)
    entry.snapshot_path.write_bytes(b"broken snapshot")
    count = len(window.documents.documents)
    dialog = RecoveryDialog(window.recovery, window)
    try:
        dialog.show()
        QTest.mouseClick(dialog.restore_button, Qt.MouseButton.LeftButton)
        assert "corrompue" in dialog.error.text() and len(window.documents.documents) == count
        assert entry.record_path.exists() and original.exists()
        QTest.mouseClick(dialog.delete_button, Qt.MouseButton.LeftButton)
        assert not entry.record_path.exists() and not entry.snapshot_path.exists() and original.exists()
        assert dialog.list.topLevelItemCount() == 0 and not dialog.restore_button.isEnabled()
    finally:
        dialog.reject()
        dialog.deleteLater()


def test_cancelled_close_and_failed_save_preserve_snapshots_then_normal_close_cleans(window, tmp_path, monkeypatch):
    document = window.viewport.document
    document.rename(body(document).id, "Non enregistré")
    assert window.recovery.snapshot_now() == 1
    entry = window.recovery._current[document.id]
    window.confirm_discard = lambda document: False
    assert not window.close_document() and entry.snapshot_path.exists()
    assert not window.close() and entry.snapshot_path.exists()
    failures = []
    monkeypatch.setattr(window, "report_file_error", lambda title, error: failures.append(str(error)))
    assert not window.save_document(path=tmp_path / "missing/fail.cada")
    assert failures and document.is_modified and entry.snapshot_path.exists()
    window.confirm_discard = lambda document: True
    assert window.close_document()
    assert not entry.snapshot_path.exists() and not entry.record_path.exists()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_recovery_dialog_uses_theme_and_captures_native_controls(window, tmp_path, theme):
    abandoned(window, tmp_path)
    window.apply_preferences(replace(window.preferences, theme=theme))
    dialog = RecoveryDialog(window.recovery, window)
    try:
        dialog.show()
        QApplication.processEvents()
        assert dialog.restore_button.isEnabled()
        from PySide6.QtGui import QPalette
        assert dialog.palette().color(QPalette.ColorRole.Window).name() == (DARK if theme == "dark" else LIGHT).window
        path = Path(__file__).resolve().parents[1] / "artifacts" / f"t14-recovery-{theme}.png"
        assert dialog.grab().save(str(path))
    finally:
        dialog.reject()
        dialog.deleteLater()
