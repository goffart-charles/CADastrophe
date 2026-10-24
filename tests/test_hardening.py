"""T17 scenarios across plugin boundaries; real Qt/AIS with isolated user data."""
from dataclasses import replace
import json
from pathlib import Path
import shutil
from time import perf_counter
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.gp import gp_Pnt
from PySide6.QtCore import QMimeData, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolBar, QDockWidget

from cadastrophe.sample_scene import demo_document
from cadastrophe.app import MainWindow
from cadastrophe.core.persistence import load_document
from cadastrophe.document import SceneObject, ObjectType
from cadastrophe.logging_setup import DiagnosticSession
from cadastrophe.ui.themes import apply_theme

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    clipboard = app.clipboard()
    snapshot = QMimeData()
    original = clipboard.mimeData()
    if original is not None:
        for mime in original.formats():
            snapshot.setData(mime, original.data(mime))
    root = tmp_path / "plugins"
    for name in ("primitives", "measurements", "import_export"):
        shutil.copytree(ROOT / "plugins" / name, root / name)
    window = MainWindow(plugin_directory=root, preferences_path=tmp_path / "prefs.json",
                        recovery_directory=tmp_path / "recovery")
    window.documents.replace(window.viewport.document.id, demo_document())
    window.show()
    window.activateWindow()
    app.processEvents()
    assert QTest.qWaitForWindowActive(window, 1000)
    try:
        yield window
    finally:
        window.tool_sessions.cancel()
        for record in window.plugins.records:
            dialog = getattr(record.instance, "dialog", None)
            if dialog is not None:
                dialog.close()
                wait_until(lambda: record.instance.dialog is None)
        window.confirm_discard = lambda doc: True
        window.close()
        window.deleteLater()
        app.processEvents()
        clipboard.setMimeData(snapshot)
        apply_theme(app)


def wait_until(predicate, timeout=20000):
    for _ in range(timeout // 10):
        if predicate():
            return
        QTest.qWait(10)
    assert predicate()


def instance(window, plugin_id):
    return next(record.instance for record in window.plugins.records if record.manifest.id == "cadastrophe." + plugin_id)


def pose(viewport):
    camera = viewport.view.Camera()
    return (*camera.Eye().Coord(), *camera.Center().Coord(), *camera.Up().Coord(), camera.Scale())


def test_t17_multi_document_primitives_clipboard_measurements_step_native_recovery_and_missing_plugins(window, tmp_path):
    first, view = window.services.active_document, window.viewport
    before_pose = pose(view)
    assert window.command_registry.execute("cadastrophe.primitives.box")
    assert window.tool_sessions.preview_object is not None and not first.is_modified
    assert window.command_registry.execute("tool.commit")
    original = first.objects[first.selected_ids[0]]
    assert first.history.count == 1 and pose(view) == before_pose
    assert window.command_registry.execute("edit.copy")
    second = window.create_document()
    QApplication.processEvents()
    assert window.command_registry.execute("edit.paste")
    pasted = second.objects[second.selected_ids[0]]
    assert pasted.id != original.id and not pasted.shape.IsPartner(original.shape)
    camera = pose(window.viewport)
    assert window.command_registry.execute("cadastrophe.measurements.area")
    assert instance(window, "measurements").panel.last_result.value > 0
    assert second.history.count == 1
    assert window.recovery.snapshot_now() == 2
    assert window.command_registry.execute("cadastrophe.import_export.export_step")
    plugin = instance(window, "import_export")
    plugin.dialog.path.setText(str(tmp_path / "exchange.step"))
    plugin.dialog.start()
    wait_until(lambda: plugin.dialog is None or bool(plugin.dialog.error_message))
    assert plugin.dialog is None
    path = tmp_path / "part.cada"
    assert window.save_document(path=path)
    assert not second.is_modified and first.is_modified and pose(window.viewport) == camera
    second.history.undo()
    assert pasted.id not in second.objects and first.objects[original.id] is original
    second.history.redo()
    assert pasted.id in second.objects and not second.is_modified
    for name in ("import_export", "measurements", "primitives"):
        assert window.plugins.deactivate("cadastrophe." + name)
    assert window.close_document(second.id)
    reloaded = window.open_document(path)
    QApplication.processEvents()
    assert reloaded.objects[pasted.id].name == pasted.name and not reloaded.is_modified
    assert pasted.id in window.viewport.presentations
    assert window.services.active_document is reloaded and first.selected_ids == (original.id,)


def test_t17_failed_command_rolls_back_is_reported_and_next_command_remains_usable(window, tmp_path):
    document = window.services.active_document
    body = next(obj for obj in document.objects.values() if obj.kind is ObjectType.BODY)
    original = body.name
    def fail():
        with document.history.transaction("Failure"):
            document.rename(body.id, "partial")
            raise RuntimeError("T17 failed command")
    window.command_registry.register("test.t17.failure", "Failure", "Test", fail)
    path = tmp_path / "command.log"
    with DiagnosticSession(path) as diagnostics:
        diagnostics.notify = window.services.message.emit
        assert window.command_registry.execute("test.t17.failure")
        assert "Erreur inattendue" in window.hint.text()
        assert document.objects[body.id].name == original and not document.is_modified
        assert not document.history.blocked
        assert window.command_registry.execute("view.fit")
    text = path.read_text(encoding="utf-8")
    assert "test.t17.failure" in text and "Traceback" in text and "T17 failed command" in text


def test_t17_step_cleanup_failure_is_logged_and_never_locks_document(window, tmp_path, monkeypatch, caplog):
    assert window.command_registry.execute("cadastrophe.import_export.export_step")
    plugin = instance(window, "import_export")
    dialog = plugin.dialog
    temp = tmp_path / "leftover.step"
    temp.write_text("temporary")
    dialog._output = temp
    original_unlink = Path.unlink
    def locked(path, *args, **kwargs):
        if path == temp:
            raise PermissionError("T17 locked temporary")
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", locked)
    dialog.close()
    assert plugin.dialog is None and not window.documents.blocked
    assert "T17 locked temporary" in caplog.text and str(temp) in caplog.text
    assert window.command_registry.execute("file.new")


@pytest.mark.parametrize("problem,explanation", [
    ("corrupt", "Ouverture impossible"), ("missing_plugin", "Plugins nécessaires"),
    ("future", "Version de document non prise en charge"),
])
def test_t17_rejected_file_leaves_all_open_documents_and_histories_intact(window, tmp_path, monkeypatch,
                                                                       problem, explanation):
    first = window.services.active_document
    body = next(obj for obj in first.objects.values() if not obj.plane)
    first.rename(body.id, "Non sauvegardé")
    second = window.create_document()
    QApplication.processEvents()
    path = tmp_path / "bad.cada"
    assert window.save_document(path=path)
    with ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    if problem == "corrupt":
        path.write_bytes(b"not a zip document")
    else:
        manifest = json.loads(entries["manifest.json"])
        if problem == "future":
            manifest["schema_version"] = 999
        else:
            manifest["schema_version"] = 6
            manifest["required_plugins"] = ["fixture.absent"]
            manifest["objects"][0]["extension"] = dict(type_id="fixture.absent.part", version=1, data={})
        entries["manifest.json"] = json.dumps(manifest).encode("utf-8")
        with ZipFile(path, "w", ZIP_DEFLATED) as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
    # Use another name: an already open path intentionally activates that document.
    candidate = tmp_path / "candidate.cada"
    shutil.copy2(path, candidate)
    window.documents.activate(first.id)
    state = window.documents.documents, tuple(first.objects), first.history.index, pose(window.viewport)
    from PySide6.QtWidgets import QMessageBox
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: messages.append((title, text)))
    assert window.open_document(candidate) is None
    assert messages and explanation in " ".join(messages[0])
    assert (window.documents.documents, tuple(first.objects), first.history.index, pose(window.viewport)) == state
    assert window.services.active_document is first and first.is_modified and not second.is_modified


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_t17_hidpi(window, theme):
    assert window.screen().availableGeometry().contains(window.frameGeometry())
    window.apply_preferences(replace(window.preferences, theme=theme))
    available = window.screen().availableGeometry()
    window.resize(min(1200, round(available.width() * .95)), min(760, round(available.height() * .9)))
    QApplication.processEvents()
    viewport, document = window.viewport, window.services.active_document
    scale = viewport.devicePixelRatioF()
    assert scale > 0
    assert not window.findChildren(QToolBar) and not window.findChildren(QDockWidget)
    assert viewport.pixel(QPoint(11, 17)) == (round(11 * scale), round(17 * scale))
    viewport.camera.standard("Isométrique")
    viewport.camera.fit()
    x, y = viewport.view.Convert(-55., -35., 40.)
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=QPoint(round(x / scale), round(y / scale)))
    selected = window.services.selection_items
    assert len(selected) == 1 and selected[0].topology == "Face"
    assert document.objects[selected[0].object_id].name == "Cube"
    assert window.command_registry.execute("cadastrophe.measurements.area")
    assert instance(window, "measurements").panel.last_result.value == pytest.approx(1600)
    assert window.command_registry.execute("cadastrophe.primitives.box")
    field = window.tool_sessions.tool.dimensions["length"]
    QApplication.processEvents()
    assert field.isVisible() and field.width() > 40
    assert window.tool_sessions.cancel()
    for tab in range(4):
        window.command_manager.setCurrentIndex(tab)
        QApplication.processEvents()
        scroll = window.command_manager.currentWidget()
        assert scroll.verticalScrollBar().maximum() == 0
    token = str(scale).replace(".", "_")
    assert window.grab().save(str(ROOT / "artifacts" / f"t17-shell-{theme}-{token}.png"))
    assert viewport.view.Dump(str(ROOT / "artifacts" / f"t17-viewport-{theme}-{token}.png"))
    print(f"T17 HIDPI OK: theme={theme}, actual Qt scale={scale}, viewport={viewport.width()}x{viewport.height()}")


def test_t17_medium_scene_timings_and_navigation_without_model_mutation(window, tmp_path):
    document = window.create_document()
    QApplication.processEvents()
    shapes = [SceneObject(f"Corps {index + 1}", BRepPrimAPI_MakeBox(gp_Pnt(index % 12 * 14, index // 12 * 14, 0),
                                                                10, 10, 10).Shape(), (.6, .7, .8))
              for index in range(144)]
    times = {}
    start = perf_counter()
    document.insert_objects(shapes, title="Scène de validation")
    QApplication.processEvents()
    times["insert_and_ais_seconds"] = perf_counter() - start
    viewport = window.viewport
    viewport.camera.fit()
    viewport.view.Redraw()
    assert len(viewport.presentations) == 147
    document.select_objects([shapes[0].id])
    state = tuple(document.objects), document.history.index, document.selected_ids
    frames = []
    for _ in range(12):
        start = perf_counter()
        viewport.camera.start_rotation(150, 150)
        viewport.camera.rotate(165, 158)
        viewport.camera.end_rotation()
        viewport.view.Redraw()
        frames.append(perf_counter() - start)
    times["rotation_redraw_median_ms"] = sorted(frames)[len(frames) // 2] * 1000
    times["rotation_redraw_max_ms"] = max(frames) * 1000
    assert (tuple(document.objects), document.history.index, document.selected_ids) == state
    path = tmp_path / "medium.cada"
    start = perf_counter()
    assert window.save_document(path=path)
    times["save_seconds"] = perf_counter() - start
    start = perf_counter()
    restored = load_document(path)
    times["load_seconds"] = perf_counter() - start
    assert len(restored.objects) == 147 and path.stat().st_size > 0
    assert viewport.view.Dump(str(ROOT / "artifacts/t17-medium-scene.png"))
    report = dict(bodies=144, faces=864, qt_scale=viewport.devicePixelRatioF(), timings=times,
                  note="Local timings, CPU command plus native redraw; not an FPS or industrial benchmark")
    (ROOT / "artifacts/t17-performance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("T17 MEDIUM SCENE " + json.dumps(report))
