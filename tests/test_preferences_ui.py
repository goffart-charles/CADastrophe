"""Native preference application, actual geometry units and simulated restarts."""
from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.GProp import GProp_GProps
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from cadastrophe.sample_scene import demo_document
from cadastrophe.app import MainWindow
from cadastrophe.core.commands import ShortcutConflictError
from cadastrophe.core.preferences import Preferences
from cadastrophe.ui.preferences_dialog import PreferencesDialog
from cadastrophe.ui.themes import LIGHT, DARK, apply_theme, theme_tokens


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "plugins"
    shutil.copytree(Path(__file__).resolve().parents[1] / "plugins/primitives", root / "primitives")
    window = MainWindow(plugin_directory=root, preferences_path=tmp_path / "preferences.json")
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


def pose(viewport):
    camera = viewport.view.Camera()
    return (*camera.Eye().Coord(), *camera.Center().Coord(), *camera.Up().Coord(), camera.Scale())


def test_dark_preferences_apply_to_all_views_without_editing_documents_or_camera_and_restart(window):
    first = window.viewport.document
    first_view = window.viewport
    obj = next(obj for obj in first.objects.values() if not obj.plane)
    first.select_objects([obj.id])
    x, y = first_view.view.Convert(-55., -55., 20.)
    from PySide6.QtCore import QPoint
    scale = first_view.devicePixelRatioF()
    QTest.mouseClick(first_view, Qt.MouseButton.LeftButton, pos=QPoint(round(x / scale), round(y / scale)))
    face = first_view.selection.items()[0]
    assert face.topology == "Face"
    first_pose = pose(first_view)
    second = window.create_document()
    QTest.qWait(100)
    second_pose = pose(window.viewport)
    settings = replace(window.preferences, theme="dark", length_unit="cm", decimals=4,
                       rotation_speed=1.5, zoom_speed=2., invert_zoom=True, selection="#f08040")
    window.apply_preferences(settings)
    assert theme_tokens() == DARK and window.units.text() == "cm"
    assert first.objects[obj.id] is obj and not first.is_modified and not second.is_modified
    assert first.history.count == second.history.count == 0
    assert pose(first_view) == first_pose and pose(window.viewport) == second_pose
    assert first_view.preferences == window.viewport.preferences == settings
    assert first_view.camera.rotation_speed == 1.5 and window.viewport.camera.zoom_speed == 2.
    assert first.selected_ids == (obj.id,)
    assert first_view.selection.items()[0].shape.IsSame(face.shape)
    window.documents.activate(first.id)
    assert window.tree_items[obj.id].foreground(0).color().name() == DARK.text
    path = window.preference_store.path
    window.command_manager.setCurrentIndex(2)
    window.manager_pane.setCurrentIndex(3)
    available = window.screen().availableGeometry().size()
    frame = window.frameGeometry().size() - window.size()
    window.resize(min(1280, available.width() - frame.width() - 8),
                  min(800, available.height() - frame.height() - 8))
    window.splitter.setSizes([320, 960])
    window.command_registry.action("window.quick_access").setChecked(False)
    QApplication.processEvents()
    sizes = window.splitter.sizes()
    saved_size = window.size()  # Windows may constrain 800 requested pixels to the work area.
    assert window.close()
    other = MainWindow(plugin_directory=window.plugins.directory, preferences_path=path)
    try:
        other.show()
        QTest.qWait(100)
        assert other.preferences == settings and other.units.text() == "cm"
        assert other.command_manager.currentIndex() == 2 and other.manager_pane.currentIndex() == 3
        assert not other.command_registry.action("window.quick_access").isChecked()
        assert other.size() == saved_size
        assert other.splitter.sizes() == pytest.approx(sizes, abs=2)
        assert other.command_registry.execute("window.reset_layout")
        assert other.command_registry.action("window.quick_access").isChecked()
    finally:
        other.close()
        other.deleteLater()


def test_shortcut_preferences_conflict_is_atomic_then_real_keyboard_and_restart_work(window):
    previous = window.preferences
    with pytest.raises(ShortcutConflictError):
        window.apply_preferences(replace(previous, theme="dark", shortcuts={"view.fit": ["Z"]}))
    assert window.preferences == previous and theme_tokens() == LIGHT
    assert not window.preference_store.path.exists()
    settings = replace(previous, shortcuts={"view.fit": ["Ctrl+Alt+F"]})
    window.apply_preferences(settings)
    calls = []
    window.command_registry.action("view.fit").triggered.connect(lambda: calls.append(True))
    window.viewport.setFocus()
    QTest.keyClick(window.viewport, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
    QTest.keyRelease(window.viewport, Qt.Key.Key_Control)
    QTest.keyRelease(window.viewport, Qt.Key.Key_Alt)
    assert calls == [True] and window.viewport.document.history.count == 0
    assert window.close()
    other = MainWindow(plugin_directory=window.plugins.directory, preferences_path=window.preference_store.path)
    try:
        assert other.command_registry.command("view.fit").shortcuts == ("Ctrl+Alt+F",)
    finally:
        other.close()
        other.deleteLater()


@pytest.mark.parametrize("unit,display_length,expected_mm", [("cm", 2.54, 25.4), ("m", .0254, 25.4), ("in", 1., 25.4)])
def test_primitive_saisie_converts_units_and_native_file_remains_mm(window, tmp_path, unit, display_length, expected_mm):
    window.apply_preferences(replace(window.preferences, length_unit=unit))
    doc = window.create_document()
    QTest.qWait(100)
    assert window.command_registry.execute("cadastrophe.primitives.box")
    tool = window.tool_sessions.tool
    assert tool.dimensions["width"].internal_value == 30. and tool.origin[0].internal_value == 0.
    assert tool.dimensions["length"].suffix() == " " + unit
    tool.dimensions["length"].setValue(display_length)
    with pytest.raises(ValueError):
        window.apply_preferences(replace(window.preferences, length_unit="mm"))
    assert window.command_registry.execute("tool.commit")
    obj = next(obj for obj in doc.objects.values() if not obj.plane)
    properties = GProp_GProps()
    brepgprop.VolumeProperties(obj.shape, properties)
    assert properties.Mass() == pytest.approx(expected_mm * 30 * 25)
    assert doc.length_unit == "mm" and doc.history.count == 1
    path = tmp_path / "units.cada"
    assert window.save_document(path=path)
    from zipfile import ZipFile
    with ZipFile(path) as archive:
        assert json.loads(archive.read("manifest.json"))["document"]["length_unit"] == "mm"


def test_preferences_dialog_cancel_save_and_validation_use_real_shared_action(window):
    observed = []

    def cancel():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, PreferencesDialog)
        dialog.theme.setCurrentIndex(1)
        dialog.reject()
        observed.append("cancel")

    QTimer.singleShot(0, cancel)
    assert window.command_registry.execute("settings.preferences")
    assert window.preferences == Preferences() and not window.preference_store.path.exists()

    def save():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, PreferencesDialog)
        dialog.theme.setCurrentIndex(1)
        dialog.colors["selection"].setText("bad")
        dialog.accept()
        assert dialog.isVisible() and dialog.error.text()
        dialog.colors["selection"].setText("#ff940f")
        dialog.accept()
        observed.append("save")

    QTimer.singleShot(0, save)
    assert window.command_registry.execute("settings.preferences")
    assert observed == ["cancel", "save"] and window.preferences.theme == "dark"
    assert theme_tokens() == DARK and window.preference_store.path.exists()
    output = Path("artifacts")
    output.mkdir(exist_ok=True)
    assert window.grab().save(str(output / "t11-dark-shell.png"))
    assert window.viewport.view.Dump(str(output / "t11-dark-viewport.png"))


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_numeric_arrows_and_live_tree_colors_remain_usable_after_theme_switch(window, theme):
    from PySide6.QtCore import QPoint

    window.apply_preferences(replace(window.preferences, theme=theme))
    obj = next(obj for obj in window.viewport.document.objects.values() if not obj.plane)
    assert window.tree_items[obj.id].foreground(0).color().name() == (DARK if theme == "dark" else LIGHT).text
    assert window.command_registry.execute("cadastrophe.primitives.box")
    field = window.tool_sessions.tool.dimensions["length"]
    QApplication.processEvents()
    previous = field.value()
    QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=QPoint(field.width() - 9, 5))
    assert field.value() > previous
    QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=QPoint(field.width() - 9, field.height() - 5))
    assert field.value() == previous
    output = Path("artifacts")
    output.mkdir(exist_ok=True)
    assert window.grab().save(str(output / f"t11-{theme}-parameters.png"))
    assert window.tool_sessions.cancel()


def test_navigation_sensitivity_and_wheel_inversion_change_real_camera_only(window):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent

    viewport = window.viewport
    viewport.camera.start_rotation(400, 300)
    viewport.camera.rotate(440, 320)
    viewport.camera.end_rotation()
    normal = pose(viewport)
    viewport.camera.standard("Isométrique")
    viewport.camera.fit()
    window.apply_preferences(replace(window.preferences, rotation_speed=2.))
    viewport.camera.start_rotation(400, 300)
    viewport.camera.rotate(420, 310)
    viewport.camera.end_rotation()
    assert pose(viewport) == pytest.approx(normal, abs=1e-9)

    def wheel():
        position = QPointF(viewport.width() / 2, viewport.height() / 2)
        QApplication.sendEvent(viewport, QWheelEvent(position, QPointF(viewport.mapToGlobal(position.toPoint())),
            QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False))

    wheel()
    smaller = viewport.view.Camera().Scale()
    window.apply_preferences(replace(window.preferences, invert_zoom=True))
    wheel()
    assert viewport.view.Camera().Scale() > smaller
    assert not viewport.document.is_modified and viewport.document.history.count == 0
