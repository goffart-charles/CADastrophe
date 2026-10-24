"""STEP worker, real AIS/document integration, cancellation and file protection."""
from dataclasses import replace
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.GProp import GProp_GProps
from PySide6.QtCore import QProcess, QTimer, Qt
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton, QMessageBox

from cadastrophe.sample_scene import demo_document
from cadastrophe.app import MainWindow
from cadastrophe.core.persistence import save_document, load_document
from cadastrophe.document import SceneObject, ObjectType
from cadastrophe.ui.themes import LIGHT, apply_theme

PLUGIN_ID = "cadastrophe.import_export"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "plugins"
    shutil.copytree(ROOT / "plugins/import_export", root / "import_export")
    window = MainWindow(plugin_directory=root)
    window.documents.replace(window.viewport.document.id, demo_document())
    window.show()
    window.activateWindow()
    app.processEvents()
    assert QTest.qWaitForWindowActive(window, 1000)
    try:
        yield window
    finally:
        instance = plugin(window)
        if instance.dialog is not None:
            instance.dialog.close()
            wait_until(lambda: instance.dialog is None)
        window.confirm_discard = lambda doc: True
        window.close()
        window.deleteLater()
        app.processEvents()
        apply_theme(app, LIGHT)


def plugin(window):
    record = next(record for record in window.plugins.records if record.manifest.id == PLUGIN_ID)
    assert record.state == "active", record.error
    return record.instance


def wait_until(predicate, timeout=20000):
    for _ in range(timeout // 10):
        if predicate():
            return
        QTest.qWait(10)
    assert predicate(), "STEP did not settle"


def open_dialog(window, mode):
    assert window.command_registry.execute(PLUGIN_ID + "." + mode + "_step")
    dialog = plugin(window).dialog
    assert dialog is not None and dialog.isVisible()
    return dialog


def run_dialog(window, mode, path, *, unit="mm", all_bodies=False):
    dialog = open_dialog(window, mode)
    dialog.path.setText(str(path))
    if mode == "export":
        dialog.unit.setCurrentIndex(dialog.unit.findData(unit))
        if all_bodies:
            dialog.scope.setCurrentIndex(0)
    dialog.start_button.click()
    wait_until(lambda: plugin(window).dialog is None or bool(dialog.error_message))
    assert plugin(window).dialog is None, dialog.error_message


def mass(shape):
    props = GProp_GProps()
    brepgprop.VolumeProperties(shape, props)
    return props.Mass()


def pose(viewport):
    camera = viewport.view.Camera()
    return (*camera.Eye().Coord(), *camera.Center().Coord(), camera.Scale())


def test_real_worker_export_import_ais_selection_undo_and_native_save_without_plugin(window, tmp_path):
    first = window.services.active_document
    box = SceneObject("Référence", BRepPrimAPI_MakeBox(25.4, 50.8, 76.2).Shape(), (.6, .7, .8))
    first.add(box)
    first.select_objects([box.id])
    state = first.history.count, first.history.index, first.is_modified, pose(window.viewport)
    source = tmp_path / "pièce pouces.step"
    messages = []
    window.services.message.connect(messages.append)
    run_dialog(window, "export", source, unit="in")
    assert source.is_file() and "INCH" in source.read_text(encoding="ascii")
    shutil.copy2(source, ROOT / "artifacts/t16-reference-in.step")
    assert (first.history.count, first.history.index, first.is_modified, pose(window.viewport)) == state
    second = window.create_document()
    QApplication.processEvents()
    before_camera = pose(window.viewport)
    run_dialog(window, "import", source)
    imported = [obj for obj in second.objects.values() if obj.kind is ObjectType.BODY]
    assert len(imported) == 1
    obj = imported[0]
    assert obj.id in second.selected_ids and obj.id != box.id
    assert mass(obj.shape) == pytest.approx(25.4 * 50.8 * 76.2)
    assert obj.id in window.viewport.presentations
    assert [item.object_id for item in window.services.selection_items] == [obj.id]
    assert pose(window.viewport) == before_camera
    assert second.history.count == 1 and second.history.undo_text == "Importer STEP"
    second.history.undo()
    assert obj.id not in second.objects and not second.is_modified
    second.history.redo()
    assert second.selected_ids == (obj.id,)
    native = tmp_path / "importé.cada"
    save_document(second, native, registry=window.object_types)
    shutil.copy2(native, ROOT / "artifacts/t16-imported.cada")
    window.viewport.camera.fit()
    assert window.viewport.view.Dump(str(ROOT / "artifacts/t16-imported.png"))
    assert window.plugins.deactivate(PLUGIN_ID)
    restored = load_document(native)
    assert restored.objects[obj.id].name == obj.name
    assert mass(restored.objects[obj.id].shape) == pytest.approx(mass(obj.shape))
    assert window.plugins.activate(PLUGIN_ID)
    again = tmp_path / "again.step"
    run_dialog(window, "export", again, unit="cm")
    third = window.create_document()
    QApplication.processEvents()
    run_dialog(window, "import", again)
    final = next(obj for obj in third.objects.values() if obj.kind is ObjectType.BODY)
    assert mass(final.shape) == pytest.approx(mass(obj.shape))
    assert len(messages) == 4 and "→ mm" in messages[-1]


def test_actions_shared_in_extensions_ribbon_and_palette_and_lifecycle_is_guarded(window):
    doc = window.services.active_document
    for key in (PLUGIN_ID+".import_step",PLUGIN_ID+".export"):
        action = window.command_registry.action(key)
        assert action not in window.plugins_menu.actions() and action.isEnabled()
        assert len([button for button in window.command_manager.findChildren(QToolButton)
                    if button.defaultAction() is action]) == 1
        assert action in [command.action for command in window.command_registry.search(key)]
    file_action = window.command_registry.action("file.export")
    file_menu = window.menuBar().actions()[0].menu()
    assert file_action in file_menu.actions() and file_action.isEnabled()
    dialog = open_dialog(window, "import")
    assert window.documents.blocked and not doc.is_modified
    assert not window.command_registry.execute("file.new")
    assert not window.command_registry.execute(PLUGIN_ID + ".import_step")
    assert not window.plugins.deactivate(PLUGIN_ID)
    window.close()
    assert window.isVisible() and not doc.is_modified
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert plugin(window).dialog is None and not window.documents.blocked
    assert window.plugins.deactivate(PLUGIN_ID)
    assert not file_action.isEnabled()
    assert not any(command.id.startswith(PLUGIN_ID) for command in window.command_registry)
    assert window.plugins.activate(PLUGIN_ID)
    assert file_action.isEnabled()


def test_invalid_input_retry_and_cancel_preserve_clean_marker_redo_and_selection(window, tmp_path):
    doc = window.services.active_document
    body = next(obj for obj in doc.objects.values() if obj.kind is ObjectType.BODY)
    doc.select_objects([body.id])
    doc.rename(body.id, "temp")
    doc.history.undo()
    state = tuple(doc.objects), doc.history.count, doc.history.index, doc.selected_ids, doc.is_modified
    bad = tmp_path / "bad.step"
    bad.write_text("incorrect")
    dialog = open_dialog(window, "import")
    dialog.path.setText(str(bad))
    dialog.start()
    wait_until(lambda: bool(dialog.error_message))
    assert "Lecture STEP" in dialog.error_message and dialog.start_button.isEnabled()
    assert dialog._temporary is None
    dialog.path.setText(str(tmp_path / "missing.step"))
    dialog.start()
    assert "absent" in dialog.error_message
    dialog.close()
    assert (tuple(doc.objects), doc.history.count, doc.history.index, doc.selected_ids, doc.is_modified) == state
    assert doc.history.can_redo and not window.documents.blocked


def test_cancel_running_process_keeps_destination_and_releases_reservation(window, tmp_path, monkeypatch):
    target = tmp_path / "existing.step"
    target.write_bytes(b"original")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    dialog = open_dialog(window, "export")
    dialog.path.setText(str(target))
    ticks = []
    timer = QTimer()
    timer.setInterval(1)
    timer.timeout.connect(lambda: ticks.append(1))
    # Force a deterministic worker delay; no window and no manual timing race.
    worker = next(record.path for record in window.plugins.records if record.manifest.id == PLUGIN_ID) / "worker.py"
    worker.write_text("import time\ntime.sleep(10)\n", encoding="utf-8")
    dialog.start()
    timer.start()
    wait_until(lambda: dialog.process.state() == QProcess.ProcessState.Running)
    QTest.qWait(60)
    assert ticks and target.read_bytes() == b"original"
    assert not window.command_registry.execute("file.save")
    directory = Path(dialog._temporary.name)
    output = dialog._output
    dialog.cancel_button.click()
    wait_until(lambda: plugin(window).dialog is None)
    timer.stop()
    assert target.read_bytes() == b"original" and not directory.exists() and not output.exists()
    assert not window.documents.blocked


@pytest.mark.parametrize("failure", ["crash", "reported", "start"])
def test_worker_failures_preserve_existing_export_and_allow_retry(window, tmp_path, monkeypatch, failure):
    target = tmp_path / "protected.step"
    target.write_bytes(b"original")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    dialog = open_dialog(window, "export")
    dialog.path.setText(str(target))
    module = sys.modules[dialog.__class__.__module__]
    worker = next(record.path for record in window.plugins.records if record.manifest.id == PLUGIN_ID) / "worker.py"
    original = worker.read_text(encoding="utf-8")
    if failure == "start":
        monkeypatch.setattr(module, "sys", SimpleNamespace(executable=str(tmp_path / "missing.exe")))
    elif failure == "crash":
        worker.write_text("import os\nos._exit(3)\n", encoding="utf-8")
    else:
        worker.write_text("import sys\nfrom pathlib import Path\n"
                          "(Path(sys.argv[2]) / 'result.json').write_text('{\"error\":\"test error\"}')\n"
                          "raise SystemExit(1)\n", encoding="utf-8")
    dialog.start()
    wait_until(lambda: bool(dialog.error_message))
    assert target.read_bytes() == b"original" and dialog._temporary is None and dialog._output is None
    assert dialog.start_button.isEnabled() and window.documents.blocked
    worker.write_text(original, encoding="utf-8")
    monkeypatch.setattr(module, "sys", sys)
    dialog.start()
    wait_until(lambda: plugin(window).dialog is None or bool(dialog.error_message))
    assert plugin(window).dialog is None, dialog.error_message
    assert target.read_bytes().startswith(b"ISO-10303-21;") and not window.documents.blocked


def test_cancel_after_first_decoded_root_leaves_no_partial_objects_or_history(window, tmp_path):
    target = tmp_path / "multiple.step"
    run_dialog(window, "export", target, all_bodies=True)
    document = window.services.active_document
    state = tuple(document.objects), document.history.count, document.history.index, document.selected_ids, document.is_modified
    dialog = open_dialog(window, "import")
    dialog.path.setText(str(target))
    dialog.timer.timeout.disconnect()
    def decode_one_then_cancel():
        dialog._stage_next()
        assert dialog._index == 1 and len(dialog._imported) == 1
        dialog.close()
    dialog.timer.timeout.connect(decode_one_then_cancel)
    dialog.start()
    wait_until(lambda: plugin(window).dialog is None)
    assert (tuple(document.objects), document.history.count, document.history.index,
            document.selected_ids, document.is_modified) == state
    assert not window.documents.blocked


def test_export_scope_excludes_planes_includes_hidden_bodies_and_empty_selection_is_explained(window, tmp_path):
    document = window.services.active_document
    body = next(obj for obj in document.objects.values() if obj.kind is ObjectType.BODY)
    plane = next(obj for obj in document.objects.values() if obj.plane)
    document.set_visible(body.id, False)
    document.select_objects([body.id, plane.id])
    target = tmp_path / "one-hidden.step"
    run_dialog(window, "export", target)
    new_doc = window.create_document()
    QApplication.processEvents()
    run_dialog(window, "import", target)
    assert len([obj for obj in new_doc.objects.values() if obj.kind is ObjectType.BODY]) == 1
    dialog = open_dialog(window, "export")
    new_doc.select_objects([])
    dialog.scope.setCurrentIndex(1)
    dialog.path.setText(str(tmp_path / "empty.step"))
    dialog.start()
    assert "Aucun corps" in dialog.error_message and not (tmp_path / "empty.step").exists()
    dialog.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_dialog_themes_and_original_icons(window, theme):
    window.apply_preferences(replace(window.preferences, theme=theme))
    dialog = open_dialog(window, "export")
    QApplication.processEvents()
    assert dialog.grab().save(str(ROOT / "artifacts" / f"t16-step-{theme}.png"))
    for name in ("import", "export"):
        assert QSvgRenderer(str(ROOT / "plugins/import_export/icons" / f"{name}.svg")).isValid()
    dialog.close()
