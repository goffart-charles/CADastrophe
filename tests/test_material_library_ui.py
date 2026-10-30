"""Native material selection, custom creation, persistence and project snapshots."""
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cadastrophe.app import MainWindow
from cadastrophe.core.materials import Material
from cadastrophe.core.persistence import load_document
from cadastrophe.document import ObjectType
from test_measurements_ui import window, panel, settle, PREFIX


def test_material_library_preset_applies_to_multiple_bodies_and_undo(window):
    document = window.viewport.document
    bodies = [obj for obj in document.objects.values() if obj.kind is ObjectType.BODY][:2]
    assert len(bodies) == 2
    document.select_objects([obj.id for obj in bodies])
    window.command_registry.execute(PREFIX+'material')
    widget = panel(window)
    settle(window)
    widget.material_choice.setCurrentIndex(widget.material_choice.findText('Aluminium'))
    assert widget.material_name.text() == 'Aluminium' and widget.density.value() == 2700
    assert all(document.objects[obj.id].material is None for obj in bodies)
    history_count = document.history.count
    widget.scroll_area.ensureWidgetVisible(widget.apply_material)
    QApplication.processEvents()
    assert widget.scroll_area.viewport().rect().contains(
        widget.apply_material.mapTo(widget.scroll_area.viewport(), widget.apply_material.rect().center()))
    QTest.mouseClick(widget.apply_material, Qt.MouseButton.LeftButton)
    settle(window)
    assert document.history.count == history_count + 1
    assert all(document.objects[obj.id].material == Material('Aluminium', 2700) for obj in bodies)
    assert widget.last_result.value > 0
    document.history.undo()
    settle(window)
    assert all(document.objects[obj.id].material is None for obj in bodies)
    document.history.redo()
    settle(window)
    assert widget.material_choice.currentText() == 'Aluminium'
    widget.density.setValue(2800)
    assert widget.material_choice.currentIndex() == 0
    assert all(document.objects[obj.id].material.density_kg_m3 == 2700 for obj in bodies)


def test_material_library_create_reuse_after_restart_and_project_snapshot(window, tmp_path):
    document = window.viewport.document
    obj = next(obj for obj in document.objects.values() if obj.name == 'Cube')
    document.select_objects([obj.id])
    window.command_registry.execute(PREFIX+'material')
    widget = panel(window)
    settle(window)
    widget.material_name.setText('Résine personnelle')
    widget.density.setValue(1250.5)
    original_history = document.history.count
    widget.scroll_area.ensureWidgetVisible(widget.save_material)
    QTest.mouseClick(widget.save_material, Qt.MouseButton.LeftButton)
    assert 'enregistré' in widget.material_feedback.text()
    assert widget.material_choice.currentText() == 'Résine personnelle — personnel'
    assert document.history.count == original_history and document.objects[obj.id].material is None
    QTest.mouseClick(widget.save_material, Qt.MouseButton.LeftButton)
    assert 'déjà ce nom' in widget.material_feedback.text()
    assert len(window.services.material_library.custom_materials) == 1
    widget.scroll_area.ensureWidgetVisible(widget.apply_material)
    QTest.mouseClick(widget.apply_material, Qt.MouseButton.LeftButton)
    settle(window)
    material = Material('Résine personnelle', 1250.5)
    assert document.objects[obj.id].material == material
    project = tmp_path/'matériau.cada'
    assert window.save_document(path=project)
    second = MainWindow(plugin_directory=tmp_path/'plugins', preferences_path=tmp_path/'preferences.json')
    try:
        second.show()
        QApplication.processEvents()
        second.command_registry.execute(PREFIX+'material')
        reopened_panel = panel(second)
        index = reopened_panel.material_choice.findText('Résine personnelle — personnel')
        assert index > 0
        reopened_panel.material_choice.setCurrentIndex(index)
        assert reopened_panel.density.value() == 1250.5
        reopened_panel.density.setValue(1300)
        assert reopened_panel.material_choice.currentIndex() == 0
        assert document.objects[obj.id].material == material
    finally:
        second.confirm_discard = lambda document: True
        second.close()
        second.deleteLater()
        QApplication.processEvents()
    window.services.material_library.path.unlink()
    assert load_document(project).objects[obj.id].material == material


def test_material_library_can_be_created_without_body_selection(window):
    window.viewport.document.select_objects([])
    window.command_registry.execute(PREFIX+'material')
    widget = panel(window)
    settle(window)
    widget.material_name.setText('Mousse')
    widget.density.setValue(45)
    assert widget.save_material.isEnabled() and not widget.apply_material.isEnabled()
    QTest.mouseClick(widget.save_material, Qt.MouseButton.LeftButton)
    assert window.services.material_library.custom_materials == (Material('Mousse', 45),)
