"""Search the live registry and launch its real Qt/CAD actions from the keyboard."""
from dataclasses import replace
from pathlib import Path
import shutil

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow, QDialog, QLineEdit

from cadastrophe.core.commands import CommandRegistry, ShortcutConflictError
from cadastrophe.ui.command_palette import CommandPalette, PALETTE_COMMAND
from cadastrophe.ui.themes import LIGHT, DARK, apply_theme


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def registry(app):
    owner = QMainWindow()
    registry = CommandRegistry(owner)
    registry.register("view.fit", "&Ajuster", "Affichage", lambda: None, shortcuts=("F",))
    registry.register("view.iso", "Isométrique", "Affichage", lambda: None, shortcuts=("Ctrl+7", "0"))
    registry.register("fixture.plugin.box", "Boîte", "Extensions", lambda: None)
    registry.register("view.edges", "Ombré avec arêtes", "Affichage", lambda: None)
    registry.register("edit.redo", "Rétablir — Créer la boîte", "Édition", lambda: None, enabled=False)
    registry.register("modeling.revolve", "Révolution · à venir", "Fonctions", reason="Modélisation à venir.")
    yield registry
    owner.deleteLater()


@pytest.mark.parametrize("query,expected", [
    ("BOITE", {"fixture.plugin.box", "edit.redo"}),
    ("ombre aretes", {"view.edges"}),
    ("extension boite", {"fixture.plugin.box"}),
    ("fixture.plugin", {"fixture.plugin.box"}),
    ("Ctrl+7", {"view.iso"}),
    ("revolution", {"modeling.revolve"}),
    ("  edition  retablir ", {"edit.redo"}),
    ("introuvable", set()),
])
def test_search_normalizes_accents_case_spaces_and_matches_all_terms(registry, query, expected):
    assert {command.id for command in registry.search(query)} == expected


def test_search_ranks_labels_and_reads_live_metadata_without_changing_actions(registry):
    assert registry.search("boite")[0].id == "fixture.plugin.box"
    before = tuple(registry)
    assert set(registry.search()) == set(before)
    registry.action("fixture.plugin.box").setText("Sphère")
    assert not registry.search("extension boite")
    assert registry.search("sphere")[0].id == "fixture.plugin.box"
    registry.set_shortcuts("view.fit", ("Ctrl+Alt+F",))
    assert registry.search("Ctrl+Alt+F")[0].id == "view.fit"
    assert tuple(registry) == before  # metadata changes never create second actions


@pytest.fixture
def window(app, tmp_path):
    from cadastrophe.sample_scene import demo_document
    from cadastrophe.app import MainWindow
    root = tmp_path / "plugins"
    shutil.copytree(Path(__file__).resolve().parents[1] / "plugins/primitives", root / "primitives")
    window = MainWindow(plugin_directory=root)
    window.documents.replace(window.viewport.document.id, demo_document())
    window.show()
    window.activateWindow()
    app.processEvents()
    assert QTest.qWaitForWindowActive(window, 1000)
    try:
        yield window
    finally:
        if window.command_palette is not None:
            window.command_palette.reject()
        window.tool_sessions.cancel()
        window.confirm_discard = lambda document: True
        window.close()
        window.deleteLater()
        app.processEvents()
        apply_theme(app, LIGHT)


def open_palette(window, from_widget=None, modifiers=Qt.KeyboardModifier.ControlModifier):
    widget = from_widget or window.viewport or window.empty_workspace
    widget.setFocus()
    QApplication.processEvents()
    QTest.keyClick(widget, Qt.Key.Key_K, modifiers)
    QTest.keyRelease(widget, Qt.Key.Key_Control)
    if modifiers & Qt.KeyboardModifier.AltModifier:
        QTest.keyRelease(widget, Qt.Key.Key_Alt)
    QApplication.processEvents()
    palette = window.command_palette
    assert isinstance(palette, CommandPalette) and palette.isVisible()
    assert QApplication.activeModalWidget() is palette and palette.search.hasFocus()
    return palette


def result_rows(palette):
    return {palette.results.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole): palette.results.topLevelItem(i)
            for i in range(palette.results.topLevelItemCount())}


def pose(viewport):
    camera = viewport.view.Camera()
    return (*camera.Eye().Coord(), *camera.Center().Coord(), *camera.Up().Coord(), camera.Scale())


def test_ctrl_k_then_enter_runs_existing_command_once_and_palette_is_reused(window):
    registry = window.command_registry
    assert registry.command(PALETTE_COMMAND).shortcuts == ("Ctrl+K",)
    calls = []
    registry.action("file.new").triggered.connect(lambda: calls.append(True))
    count = len(window.documents.documents)
    original = window.viewport
    original_pose = pose(original)
    palette = open_palette(window)
    assert PALETTE_COMMAND not in result_rows(palette)
    QTest.keyClicks(palette.search, "nouveau")
    assert palette.selected_id() == "file.new"
    row = palette.results.currentItem()
    assert row.text(2) == "Ctrl+N" and row.text(3) == "Disponible"
    QTest.keyClick(palette.search, Qt.Key.Key_Return)
    QApplication.processEvents()
    assert not palette.isVisible() and QApplication.activeModalWidget() is None
    assert len(window.documents.documents) == count + 1 and calls == [True]
    assert pose(original) == original_pose and not original.document.is_modified
    assert open_palette(window) is palette and not palette.search.text()
    assert len(window.findChildren(CommandPalette)) == 1
    QTest.keyClick(palette.search, Qt.Key.Key_Escape)
    assert not palette.isVisible()


def test_plugin_primitive_launches_from_palette_without_enter_committing_it(window):
    document = window.viewport.document
    camera = pose(window.viewport)
    palette = open_palette(window)
    QTest.keyClicks(palette.search, "boite")
    assert palette.selected_id() == "cadastrophe.primitives.box"
    QTest.keyClick(palette.search, Qt.Key.Key_Return)
    assert window.tool_sessions.active and not palette.isVisible()
    assert window.tool_host.overlay is not None and not document.is_modified
    assert document.history.count == 0 and pose(window.viewport) == camera
    assert window.command_registry.execute("tool.commit")
    assert document.history.count == 1
    document.history.undo()
    assert not document.is_modified


def test_palette_escape_during_tool_does_not_cancel_and_disabled_commands_cannot_run(window):
    assert window.command_registry.execute("cadastrophe.primitives.box")
    tool = window.tool_sessions.tool
    document = window.viewport.document
    preview = window.tool_host.overlay
    palette = open_palette(window, window.tool_sessions.tool.dimensions["length"])
    QTest.keyClicks(palette.search, "nouveau")
    assert palette.selected_id() == "file.new" and not palette.run_button.isEnabled()
    assert palette.results.currentItem().text(3) == "Indisponible"
    count = len(window.documents.documents)
    QTest.keyClick(palette.search, Qt.Key.Key_Return)
    assert palette.isVisible() and len(window.documents.documents) == count
    QTest.keyClick(palette.search, Qt.Key.Key_Escape)
    assert not palette.isVisible() and window.tool_sessions.tool is tool
    assert window.tool_host.overlay is preview and document.history.count == 0
    assert window.command_registry.execute("tool.commit")
    assert document.history.count == 1


def test_future_command_and_empty_search_results_cannot_execute(window):
    palette = open_palette(window)
    QTest.keyClicks(palette.search, "ajout par extrusion")
    assert palette.results.topLevelItemCount() == 1
    assert palette.results.currentItem().text(3) == "À venir"
    assert not palette.run_button.isEnabled() and "venir" in palette.description.text()
    QTest.keyClick(palette.search, Qt.Key.Key_Return)
    assert palette.isVisible() and not window.viewport.document.is_modified
    palette.search.setText("aucune_commandexyz")
    assert palette.results.topLevelItemCount() == 0 and not palette.run_button.isEnabled()
    assert "Aucune commande" in palette.count.text()
    QTest.keyClick(palette.search, Qt.Key.Key_Return)
    assert palette.isVisible()


def test_live_registry_changes_update_labels_shortcuts_availability_and_removed_actions(window):
    registry = window.command_registry
    palette = open_palette(window)
    palette.search.setText("fixture.dynamic")
    assert not result_rows(palette)
    calls = []
    action = registry.register("fixture.dynamic", "Dynamique", "Extensions", lambda: calls.append(True),
                               shortcuts=("Ctrl+Alt+D",))
    QApplication.processEvents()
    assert set(result_rows(palette)) == {"fixture.dynamic"}
    assert palette.results.currentItem().text(2) == "Ctrl+Alt+D"
    action.setText("Libellé modifié")
    registry.set_shortcuts("fixture.dynamic", ("Ctrl+Alt+G",))
    action.setEnabled(False)
    QApplication.processEvents()
    assert palette.results.currentItem().text(0) == "Libellé modifié"
    assert palette.results.currentItem().text(2) == "Ctrl+Alt+G"
    assert not palette.run_button.isEnabled()
    palette.accept()
    assert calls == [] and palette.isVisible()
    action.setEnabled(True)
    # Exercise the final check without delivering the queued UI refresh first.
    action.setEnabled(False)
    palette.accept()
    assert calls == [] and palette.isVisible()
    registry.unregister("fixture.dynamic")
    palette.accept()  # stale id must not fall through to a different command
    assert calls == [] and not result_rows(palette)
    QApplication.processEvents()


def test_plugin_unload_and_reload_refresh_the_open_palette(window):
    palette = open_palette(window)
    palette.search.setText("cadastrophe.primitives")
    assert len(result_rows(palette)) == 4
    assert window.plugins.deactivate("cadastrophe.primitives")
    QApplication.processEvents()
    assert not result_rows(palette)
    assert window.plugins.activate("cadastrophe.primitives")
    QApplication.processEvents()
    assert len(result_rows(palette)) == 4
    assert palette.run_button.isEnabled()


def test_keyboard_navigation_preserves_search_focus_and_double_click_executes_once(window):
    calls = []
    for index in range(3):
        window.command_registry.register(f"fixture.choice{index}", f"Choix {index}", "Test",
                                          lambda key=index: calls.append(key))
    palette = open_palette(window)
    palette.search.setText("fixture.choice")
    assert palette.selected_id() == "fixture.choice0"
    QTest.keyClick(palette.search, Qt.Key.Key_Down)
    assert palette.selected_id() == "fixture.choice1" and palette.search.hasFocus()
    QTest.keyClick(palette.search, Qt.Key.Key_PageDown)
    assert palette.selected_id() == "fixture.choice2"
    QTest.keyClick(palette.search, Qt.Key.Key_Up)
    assert palette.selected_id() == "fixture.choice1"
    row = palette.results.currentItem()
    position = palette.results.visualItemRect(row).center()
    QTest.mouseClick(palette.results.viewport(), Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseDClick(palette.results.viewport(), Qt.MouseButton.LeftButton,
                     pos=position)
    assert calls == [1] and not palette.isVisible()


def test_panel_toggle_uses_existing_action_and_displays_checked_state(window):
    palette = open_palette(window)
    palette.search.setText("window.quick_access")
    assert "coché" in palette.results.currentItem().text(3)
    before = window.command_registry.action("window.quick_access").isChecked()
    QTest.mouseClick(palette.run_button, Qt.MouseButton.LeftButton)
    assert window.command_registry.action("window.quick_access").isChecked() is not before
    assert window.quick_access.isHidden() == before
    palette = open_palette(window)
    palette.search.setText("window.quick_access")
    assert "non coché" in palette.results.currentItem().text(3)


def test_document_mutation_from_palette_remains_one_undo_step_and_camera_stays_fixed(window):
    document = window.viewport.document
    body = next(obj for obj in document.objects.values() if not obj.plane)
    document.select_objects([body.id])
    camera = pose(window.viewport)
    palette = open_palette(window)
    palette.search.setText("object.hide")
    assert palette.selected_id() == "object.hide" and palette.run_button.isEnabled()
    QTest.keyClick(palette.results, Qt.Key.Key_Return)
    assert not document.objects[body.id].visible and document.history.count == 1
    assert pose(window.viewport) == camera
    document.history.undo()
    assert document.objects[body.id].visible and not document.is_modified
    assert pose(window.viewport) == camera


def test_dialog_launch_from_palette_and_escape_focus_do_not_change_document(window):
    from cadastrophe.ui.preferences_dialog import PreferencesDialog
    field = window.manager_pane.tree_filter
    field.setText("Cube")
    document = window.viewport.document
    camera = pose(window.viewport)
    palette = open_palette(window, field)
    palette.search.setText("preferences")
    observed = []

    def close_preferences():
        dialog = QApplication.activeModalWidget()
        observed.append(isinstance(dialog, PreferencesDialog))
        if isinstance(dialog, QDialog):
            dialog.reject()

    QTimer.singleShot(0, close_preferences)
    QTest.keyClick(palette.search, Qt.Key.Key_Return)
    assert observed == [True] and not palette.isVisible()
    assert field.text() == "Cube" and not document.is_modified and pose(window.viewport) == camera
    palette = open_palette(window, field)
    count = len(window.documents.documents)
    QTest.keyClick(palette.search, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    QTest.keyRelease(palette.search, Qt.Key.Key_Control)
    assert len(window.documents.documents) == count
    QTest.keyClicks(palette.search, "FZ")  # these letters must remain text, never view shortcuts
    QTest.keyClick(palette.search, Qt.Key.Key_Escape)
    QApplication.processEvents()
    assert field.hasFocus() and field.text() == "Cube"
    assert pose(window.viewport) == camera and document.history.count == 0


def test_palette_without_document_and_rebound_launch_shortcut(window):
    window.apply_preferences(replace(window.preferences, shortcuts={PALETTE_COMMAND: ["Ctrl+Alt+K"]}))
    with pytest.raises(ShortcutConflictError):
        window.command_registry.set_shortcuts("view.fit", ("Ctrl+Alt+K",))
    assert window.close_document()
    assert window.viewport is None
    palette = open_palette(window, modifiers=Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
    palette.search.setText("enregistrer")
    assert not palette.run_button.isEnabled()
    palette.search.setText("nouveau")
    QTest.keyClick(palette.search, Qt.Key.Key_Return)
    assert window.viewport is not None and not window.viewport.document.is_modified


@pytest.mark.parametrize("theme,tokens", [("light", LIGHT), ("dark", DARK)])
def test_palette_inherits_both_themes_and_produces_native_qt_capture(window, theme, tokens):
    window.apply_preferences(replace(window.preferences, theme=theme))
    palette = open_palette(window)
    palette.search.setText("revolution")
    QApplication.processEvents()
    row = palette.results.currentItem()
    assert row.text(3) == "À venir" and row.foreground(0).color().name() == tokens.disabled
    from PySide6.QtGui import QPalette
    assert palette.palette().color(QPalette.ColorRole.Window).name() == tokens.window
    artifacts = Path(__file__).resolve().parents[1] / "artifacts"
    artifacts.mkdir(exist_ok=True)
    assert palette.grab().save(str(artifacts / f"t13-command-palette-{theme}.png"))
