"""Native app integration: plugin UI, active documents, persistence and startup choices."""
import json
from pathlib import Path
import shutil
import sys

import pytest
from PySide6.QtCore import Qt, QTimer, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox

from cadastrophe.sample_scene import demo_document
from cadastrophe.app import MainWindow
from cadastrophe.ui.plugin_dialog import PluginDialog

SAMPLE = Path(__file__).parent / "fixtures/plugins/sample"


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "plugins"
    shutil.copytree(SAMPLE, root / "sample")
    window = MainWindow(plugin_directory=root)
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


def body(doc):
    return next(obj for obj in doc.objects.values() if not obj.plane)


def pose(viewport):
    camera = viewport.view.Camera()
    return (*camera.Eye().Coord(), *camera.Center().Coord(), *camera.Up().Coord(), camera.Scale())


def test_ribbon_and_shortcut_share_action_without_listing_it_in_extensions(window):
    registry = window.command_registry
    action = registry.action("fixture.sample.rename")
    assert action not in window.plugins_menu.actions() and window.manager_pane.count() == 5
    button = next(button for button in window.command_manager.findChildren(QToolButton) if button.defaultAction() is action)
    doc = window.viewport.document
    obj = body(doc)
    doc.select_objects([obj.id])
    original_pose = pose(window.viewport)
    for invoke in (action.trigger, button.click,
                   lambda: QTest.keyClick(window.viewport, Qt.Key.Key_P, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
        window.viewport.setFocus()
        QApplication.processEvents()
        invoke()
        assert doc.objects[obj.id].name == "Nom depuis le plugin" and doc.history.count == 1
        assert registry.execute("edit.undo") and doc.objects[obj.id].name == obj.name
    assert pose(window.viewport) == original_pose


def test_plugin_services_and_panel_follow_active_documents_and_disable_during_transactions(window):
    first = window.viewport.document
    first_obj = body(first)
    first.select_objects([first_obj.id])
    instance = window.plugins.records[0].instance
    assert instance.label.text() == first.display_name
    second = window.create_document()
    QApplication.processEvents()
    assert window.services.active_document is second and instance.label.text() == second.display_name
    assert not window.command_registry.action("fixture.sample.rename").isEnabled()
    plane = next(iter(second.objects.values()))
    second.select_objects([plane.id])
    assert window.command_registry.execute("fixture.sample.rename")
    assert second.objects[plane.id].name == "Nom depuis le plugin" and first.objects[first_obj.id].name == first_obj.name
    with first.history.transaction("Transaction inactive"):
        assert not window.command_registry.execute("fixture.sample.rename")
        assert not window.plugins.deactivate("fixture.sample")
    assert window.command_registry.action("fixture.sample.rename").isEnabled()
    window.documents.activate(first.id)
    assert instance.label.text() == first.display_name and first.selected_ids == (first_obj.id,)


def test_native_plugin_object_saves_reopens_and_prevents_unload_while_undo_keeps_it(window, tmp_path):
    cls = window.object_types.binding("fixture.sample.tagged").codec.object_class
    doc = window.viewport.document
    obj = cls("Type plugin", BRepPrimAPI_MakeBox(15, 25, 35).Shape(), (0.5, 0.6, 0.7), tag="Métadonnée")
    doc.add(obj)
    assert obj.id in window.tree_items and obj.id in window.viewport.presentations
    path = tmp_path / "plugin.cada"
    assert window.save_document(path=path)
    assert not window.plugins.deactivate("fixture.sample")
    doc.remove(obj.id)
    assert not window.plugins.deactivate("fixture.sample")
    doc.history.undo()
    assert type(doc.objects[obj.id]) is cls and doc.objects[obj.id].tag == "Métadonnée"
    assert window.close_document()
    assert window.plugins.deactivate("fixture.sample")
    assert window.plugins.activate("fixture.sample")
    reopened = window.open_document(path)
    QApplication.processEvents()
    restored = reopened.objects[obj.id]
    assert restored.tag == "Métadonnée" and type(restored) is window.object_types.binding("fixture.sample.tagged").codec.object_class
    assert window.viewport.context.IsDisplayed(window.viewport.presentations[obj.id])
    assert window.close_document()
    assert window.plugins.deactivate("fixture.sample")
    errors = []
    window.report_file_error = lambda title, error: errors.append(str(error))
    assert window.open_document(path) is None and "Plugins nécessaires" in errors[-1]
    assert not window.documents.documents


def test_settings_dialog_persists_choices_without_unloading_live_contributions(window):
    observed = []

    def choose_disabled():
        dialog = QApplication.activeModalWidget()
        observed.append(isinstance(dialog, PluginDialog))
        if isinstance(dialog, PluginDialog):
            dialog.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
            dialog.save()

    QTimer.singleShot(0, choose_disabled)
    assert window.command_registry.execute("plugins.manage")
    assert observed == [True] and window.plugins.records[0].state == "active"
    config = json.loads((window.plugins.directory / "settings.json").read_text())
    assert config == {"schema_version": 1, "enabled": {"fixture.sample": False}}
    # Another application instance models the next startup, sharing no modules or registrations.
    other = MainWindow(plugin_directory=window.plugins.directory)
    try:
        assert other.plugins.records[0].state == "disabled"
        assert all(command.id != "fixture.sample.rename" for command in other.command_registry)
        assert other.manager_pane.count() == 4
    finally:
        other.close()
        other.deleteLater()


def test_unload_removes_all_widget_contributions_and_shutdown_forgets_modules(window):
    record = window.plugins.records[0]
    namespace = record.namespace
    assert window.plugins.deactivate("fixture.sample")
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert window.manager_pane.count() == 4
    assert not any(button.defaultAction() and button.defaultAction().objectName().startswith("fixture.sample.")
                   for button in window.command_manager.findChildren(QToolButton))
    assert window.plugins.activate("fixture.sample")
    namespace = record.namespace
    assert window.close()
    assert not any(name == namespace or name.startswith(namespace + ".") for name in sys.modules)


def test_incompatible_plugin_is_visible_in_diagnostics_and_does_not_break_native_startup(tmp_path):
    root = tmp_path / "plugins"
    shutil.copytree(SAMPLE, root / "sample")
    path = root / "sample/plugin.json"
    manifest = json.loads(path.read_text())
    manifest["api_version"] = 99
    path.write_text(json.dumps(manifest))
    app = QApplication.instance() or QApplication([])
    window = MainWindow(plugin_directory=root)
    try:
        window.show()
        app.processEvents()
        assert window.viewport.camera and not window.viewport._failed
        assert window.plugins.records[0].state == "incompatible"
        assert not any(command.id.startswith("fixture.sample.") for command in window.command_registry)
        dialog = PluginDialog(window.plugins, window)
        assert "Incompatible" in dialog.table.item(0, 3).text() and "99" in dialog.table.item(0, 3).text()
        dialog.deleteLater()
    finally:
        window.close()
        window.deleteLater()
