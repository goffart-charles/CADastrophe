"""File actions and native reload through real document/view services."""
from pathlib import Path
import shutil

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QToolButton
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.GProp import GProp_GProps

from cadastrophe.app import MainWindow
from cadastrophe.core.persistence import DocumentFileError, load_document, save_document
from cadastrophe.sample_scene import demo_document


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.documents.replace(window.viewport.document.id, demo_document())
    window.show()
    window.activateWindow()
    app.processEvents()
    try:
        yield window
    finally:
        window.confirm_discard = lambda document: True
        window.close()
        window.deleteLater()
        app.processEvents()


def body(document):
    return next(obj for obj in document.objects.values() if not obj.plane)


def pose(viewport):
    camera = viewport.view.Camera()
    return (*camera.Eye().Coord(), *camera.Center().Coord(), *camera.Up().Coord(), camera.Scale())


def press(widget, key, modifiers=Qt.KeyboardModifier.ControlModifier):
    widget.setFocus()
    QApplication.processEvents()
    QTest.keyClick(widget, key, modifiers)
    QApplication.processEvents()


def test_shortcuts_save_save_as_and_open_real_scene_without_moving_camera(window, tmp_path, monkeypatch):
    document = window.viewport.document
    viewport = window.viewport
    obj = body(document)
    document.rename(obj.id, "Pièce précise")
    document.select_objects([obj.id])
    original_pose = pose(viewport)
    target = tmp_path / "premier.cada"
    monkeypatch.setattr(window, "choose_save_path", lambda document: target)
    press(window.tree, Qt.Key.Key_S)
    assert document.file_path == target and not document.is_modified
    assert pose(viewport) == original_pose and document.selected_ids == (obj.id,)
    assert window.document_tabs.tabText(0) == target.name and not window.isWindowModified()
    assert window.tree.topLevelItem(0).text(0) == target.name + " · mm"
    assert str(target) in window.document_tabs.tabToolTip(0)
    first_bytes = target.read_bytes()
    document.rename(obj.id, "Version suivante")
    copy = tmp_path / "copie.cada"
    monkeypatch.setattr(window, "choose_save_path", lambda document: copy)
    press(window.viewport, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert document.file_path == copy and target.read_bytes() == first_bytes
    assert load_document(copy).objects[obj.id].name == "Version suivante"
    assert pose(viewport) == original_pose
    assert window.close_document() and window.viewport is None
    monkeypatch.setattr(window, "choose_open_path", lambda: copy)
    press(window.empty_workspace, Qt.Key.Key_O)
    loaded = window.viewport.document
    assert loaded.id == document.id and loaded.file_path == copy
    assert loaded.objects[obj.id].name == "Version suivante" and not loaded.is_modified
    assert set(window.tree_items) == set(window.viewport.presentations) == set(loaded.objects)
    assert not loaded.selected_ids and loaded.history.count == 0
    window.viewport.camera.standard("Isométrique")
    window.viewport.camera.fit()
    px, py = window.viewport.view.Convert(-55., -35., 40.)
    scale = window.viewport.devicePixelRatioF()
    QTest.mouseClick(window.viewport, Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier, QPoint(round(px / scale), round(py / scale)))
    selected = window.viewport.selection.items()[0]
    assert selected.topology == "Face" and selected.object_id == obj.id
    before_save = pose(window.viewport)
    assert window.save_document()
    assert window.viewport.selection.items()[0].shape.IsSame(selected.shape)
    assert pose(window.viewport) == before_save
    props = GProp_GProps()
    brepgprop.VolumeProperties(loaded.objects[obj.id].shape, props)
    assert props.Mass() == pytest.approx(40**3)
    Path("artifacts").mkdir(exist_ok=True)
    window.viewport.view.Redraw()
    assert window.viewport.view.Dump("artifacts/t06-reopened-viewport.png")


def test_save_uses_current_path_and_preserves_history_around_saved_revision(window, tmp_path, monkeypatch):
    document = window.viewport.document
    obj = body(document)
    target = tmp_path / "actuel.cada"
    assert window.save_document(path=target)
    monkeypatch.setattr(window, "choose_save_path", lambda document: pytest.fail("Save must reuse the file path"))
    document.rename(obj.id, "Sauvegardé")
    assert window.command_registry.execute("file.save")
    assert load_document(target).objects[obj.id].name == "Sauvegardé"
    press(window.viewport, Qt.Key.Key_Z)
    assert document.is_modified and window.isWindowModified()
    assert window.document_tabs.tabText(0).endswith(" *")
    press(window.viewport, Qt.Key.Key_Y)
    assert not document.is_modified and not window.isWindowModified()


def test_reopening_same_path_activates_existing_document_without_discarding_unsaved_edits(window, tmp_path):
    first = window.viewport.document
    path = tmp_path / "déjà ouvert.cada"
    assert window.save_document(path=path)
    first.rename(body(first).id, "Non enregistré")
    second = window.create_document()
    QApplication.processEvents()
    assert window.open_document(path) is first
    assert window.documents.documents == (first, second) and window.viewport.document is first
    assert first.is_modified and body(first).name == "Non enregistré"


def test_duplicate_file_identity_and_overwriting_another_open_document_are_refused(window, tmp_path, monkeypatch):
    first = window.viewport.document
    path, copy = tmp_path / "original.cada", tmp_path / "copie.cada"
    assert window.save_document(path=path)
    shutil.copyfile(path, copy)
    errors = []
    monkeypatch.setattr(window, "report_file_error", lambda title, error: errors.append(str(error)))
    assert window.open_document(copy) is None and len(window.documents.documents) == 1
    assert "même identifiant" in errors[-1]
    second = window.create_document()
    QApplication.processEvents()
    previous = path.read_bytes()
    assert not window.save_document(path=path)
    assert "autre document" in errors[-1]
    assert path.read_bytes() == previous and second.file_path is None and first.file_path == path


def test_save_cancel_and_failure_preserve_path_and_modified_status(window, tmp_path, monkeypatch):
    document = window.viewport.document
    path = tmp_path / "original.cada"
    assert window.save_document(path=path)
    document.rename(body(document).id, "Non enregistré")
    monkeypatch.setattr(window, "choose_save_path", lambda document: None)
    before = path.read_bytes()
    assert not window.save_document(save_as=True)
    assert document.file_path == path and document.is_modified and path.read_bytes() == before
    errors = []
    monkeypatch.setattr(window, "report_file_error", lambda title, error: errors.append(error))
    assert not window.save_document(path=tmp_path / "absent" / "file.cada")
    assert errors and document.file_path == path and document.is_modified
    assert path.read_bytes() == before


def test_invalid_open_and_cancel_leave_all_views_histories_and_selection_intact(window, tmp_path, monkeypatch):
    document = window.viewport.document
    viewport = window.viewport
    obj = body(document)
    document.rename(obj.id, "Conservé")
    document.select_objects([obj.id])
    original_pose = pose(viewport)
    bad = tmp_path / "corrompu.cada"
    bad.write_bytes(b"broken")
    observed = []

    def dismiss():
        box = QApplication.activeModalWidget()
        observed.append(isinstance(box, QMessageBox))
        if isinstance(box, QMessageBox):
            observed.append(box.text())
            box.accept()

    QTimer.singleShot(0, dismiss)
    assert window.open_document(bad) is None
    assert observed[0] and "Lecture impossible" in observed[1]
    monkeypatch.setattr(window, "choose_open_path", lambda: None)
    assert window.open_document() is None
    assert window.documents.documents == (document,) and window.viewport is viewport
    assert pose(viewport) == original_pose and document.selected_ids == (obj.id,) and document.history.can_undo


def test_save_on_close_writes_the_requested_inactive_document(window, tmp_path, monkeypatch):
    first = window.viewport.document
    obj = body(first)
    first.rename(obj.id, "Avant fermeture")
    second = window.create_document()
    QApplication.processEvents()
    active_view = window.viewport
    path = tmp_path / "premier.cada"
    monkeypatch.setattr(window, "choose_save_path", lambda document: path if document is first else pytest.fail("Wrong document"))

    def choose_save():
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, QMessageBox):
            dialog.button(QMessageBox.StandardButton.Save).click()

    QTimer.singleShot(0, choose_save)
    assert window.close_document(first.id)
    assert load_document(path).objects[obj.id].name == "Avant fermeture"
    assert window.documents.documents == (second,) and window.viewport is active_view


@pytest.mark.parametrize("failure", ["cancel", "disk"])
def test_save_on_close_cancel_or_failure_keeps_document_open(window, tmp_path, monkeypatch, failure):
    document = window.viewport.document
    document.rename(body(document).id, "À conserver")
    errors = []
    monkeypatch.setattr(window, "report_file_error", lambda title, error: errors.append(error))
    monkeypatch.setattr(window, "choose_save_path", lambda document:
                        None if failure == "cancel" else tmp_path / "absent" / "failure.cada")

    def choose_save():
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, QMessageBox):
            dialog.button(QMessageBox.StandardButton.Save).click()

    QTimer.singleShot(0, choose_save)
    assert not window.close_document()
    assert window.viewport.document is document and document.is_modified
    assert not window.viewport._native_closed and document.file_path is None
    assert bool(errors) == (failure == "disk")


def test_real_save_dialog_adds_default_suffix_and_open_dialog_selects_file(window, tmp_path, monkeypatch):
    # Drive Qt's real fallback picker; native preference is checked separately.
    create = window.file_dialogs.create_dialog
    def qt_dialog(*args,**kwargs):
        dialog = create(*args,**kwargs)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog)
        return dialog
    monkeypatch.setattr(window.file_dialogs,"create_dialog",qt_dialog)
    target = tmp_path / "choisi.cada"
    observed = []

    def accept_save():
        dialog = QApplication.activeModalWidget()
        observed.append(isinstance(dialog, QFileDialog))
        if isinstance(dialog, QFileDialog):
            dialog.setDirectory(str(tmp_path))
            dialog.selectFile("choisi")
            dialog.accept()

    QTimer.singleShot(0, accept_save)
    assert window.save_document()
    assert observed == [True] and window.viewport.document.file_path == target
    assert target.is_file()
    assert window.close_document()

    def accept_open():
        dialog = QApplication.activeModalWidget()
        observed.append(isinstance(dialog, QFileDialog))
        if isinstance(dialog, QFileDialog):
            dialog.setDirectory(str(tmp_path))
            dialog.selectFile(str(target))
            dialog.accept()

    QTimer.singleShot(0, accept_open)
    assert window.open_document().file_path == target
    # The file dialog's modal loop defers the newly exposed native viewport's
    # first paint. Give Qt its normal event-loop turns before checking OCCT.
    for _ in range(50):
        if window.viewport.camera is not None:
            break
        QTest.qWait(20)
    assert observed == [True, True] and window.viewport.camera


def test_file_actions_are_guarded_during_inactive_transaction_and_available_when_empty(window, tmp_path):
    first = window.viewport.document
    second = window.create_document()
    QApplication.processEvents()
    with first.history.transaction("En cours"):
        for command_id in ("file.open", "file.save", "file.save_as"):
            assert not window.command_registry.execute(command_id)
        assert not window.save_document(path=tmp_path / "blocked.cada")
        assert window.open_document(tmp_path / "blocked.cada") is None
    assert not (tmp_path / "blocked.cada").exists()
    assert window.close_document() and window.close_document()
    assert window.command_registry.action("file.open").isEnabled()
    assert not window.command_registry.action("file.save").isEnabled()
    assert not window.command_registry.action("file.save_as").isEnabled()
