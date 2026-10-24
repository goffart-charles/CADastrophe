"""Native Qt shell checks; CAD/navigation regression remains in the smoke suite."""
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QPoint, QCoreApplication, QEvent
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton, QScrollArea, QTabWidget, QToolBar, QDockWidget, QWidget

from cadastrophe.sample_scene import demo_document
from cadastrophe.app import MainWindow
from cadastrophe.ui.tool_ribbon import ToolRibbon
from cadastrophe.ui.features import FEATURE_GROUPS, FEATURE_TOOLS
from cadastrophe.ui.themes import apply_theme


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    return app


@pytest.fixture
def window(qt_app, discard_document_changes):
    window = MainWindow()
    window.documents.replace(window.viewport.document.id, demo_document())
    yield window
    window.close()
    window.deleteLater()
    qt_app.processEvents()


def test_shell_integrates_tree_and_viewport_without_toolbars_or_docks(window):
    assert window.centralWidget().isAncestorOf(window.viewport)
    assert window.manager_pane.isAncestorOf(window.tree)
    assert window.splitter.widget(0) is window.manager_pane
    assert not window.findChildren(QToolBar)
    assert not window.findChildren(QDockWidget)
    assert window.manager_pane.count() == 5  # four base pages and the measurements plugin
    assert window.tree.isHeaderHidden()
    header = window.menuWidget()
    assert header.isAncestorOf(window.menuBar())
    assert window.menuWidget() is header
    assert window.statusBar().isSizeGripEnabled()


def test_command_manager_tabs_and_default_page(window):
    manager = window.command_manager
    assert [manager.tabText(i) for i in range(manager.count())] == [
        "Fonctions", "Esquisse", "Affichage", "Évaluation"]
    assert manager.currentIndex() == 0
    assert manager.tabPosition() == QTabWidget.TabPosition.North
    assert window.manager_pane.tabPosition() == QTabWidget.TabPosition.West
    assert all(isinstance(manager.widget(i), QScrollArea) for i in range(4))


def test_embedded_menu_survives_deferred_deletion(window, qt_app):
    window.show()
    qt_app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert window.menuBar().isVisible()
    assert window.menuBar().actions()
    assert window.render_menu.actions()


def test_unavailable_tools_are_explicitly_disabled(window):
    pending = [command.action for command in window.command_registry
               if not command.implemented and command.category in ("Esquisse", "Fonctions", "Évaluation")]
    assert len(pending) == len(FEATURE_TOOLS) - 18  # Includes real threads in addition to the existing modeling tools.
    assert window.command_registry.command("modeling.thread").implemented
    assert window.command_registry.command("modeling.extrude").implemented
    assert window.command_registry.command("cadastrophe.modeling.extrude").implemented
    placeholders = [command.action for command in window.command_registry
                    if command.id.startswith("cadastrophe.sketch.") and not command.implemented]
    assert {command.id for command in window.command_registry
            if command.id.startswith("cadastrophe.sketch.") and not command.implemented} == {
                "cadastrophe.sketch.convert", "cadastrophe.sketch.move"}
    assert window.command_registry.command("cadastrophe.sketch.new").implemented
    for action in pending + placeholders:
        assert not action.isEnabled()
        assert action.toolTip()
        assert action.shortcut().isEmpty()
        buttons = [button for button in window.command_manager.findChildren(QToolButton)
                   if button.defaultAction() is action]
        if action.objectName() == "modeling.circular_pattern":
            assert not buttons
            assert any(button.menu() and action in button.menu().actions()
                       for button in window.command_manager.findChildren(QToolButton))
        else:
            assert len(buttons) == 1 and not buttons[0].isEnabled()


def test_workshop_families_show_captions_and_group_the_actions(window, qt_app):
    window.show()
    qt_app.processEvents()
    page = window.command_manager.widget(0).widget()
    groups = [page.layout().itemAt(i).widget() for i in range(page.layout().count() - 1)]
    assert [group.accessibleName() for group in groups] == [title for title, _ in FEATURE_GROUPS] + ["Courbes", "Primitives"]
    for key in ("history_before", "history_after", "history_end"):
        assert window.command_registry.command(f"cadastrophe.modeling.{key}").implemented
    for group, (_, columns) in zip(groups, FEATURE_GROUPS):
        body = group.findChild(QWidget, "groupTools")
        for index, keys in enumerate(columns):
            widget = body.layout().itemAt(index).widget()
            buttons = [widget] if isinstance(widget, QToolButton) else widget.findChildren(QToolButton)
            assert [button.defaultAction().objectName() for button in buttons] == [
                f"modeling.{key}" for key in keys]
            if len(keys) == 3:
                assert buttons[0].y() < buttons[1].y() < buttons[2].y()
                assert all(button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon
                           for button in buttons)
    window.resize(1440, 800)
    window.activateWindow()
    qt_app.processEvents()
    QTest.qWait(150)
    Path("artifacts").mkdir(exist_ok=True)
    assert window.grab().save("artifacts/workshop-features.png")


def test_all_ribbon_tools_stay_vertically_centered_at_narrow_and_wide_sizes(window, qt_app):
    window.show()
    for width in (800, 1440):
        window.resize(width, 800)
        for tab in range(window.command_manager.count()):
            window.command_manager.setCurrentIndex(tab)
            qt_app.processEvents()
            scroll = window.command_manager.currentWidget()
            content = scroll.widget()
            assert scroll.verticalScrollBar().maximum() == 0
            for index in range(content.layout().count() - 1):
                group = content.layout().itemAt(index).widget()
                body = group.findChild(QWidget, "groupTools")
                assert group.geometry().bottom() < content.height()
                for column in range(body.layout().count()):
                    tool = body.layout().itemAt(column).widget()
                    center_y = tool.mapTo(body, QPoint(0, tool.height() // 2)).y()
                    assert abs(center_y - body.height() // 2) <= 1
                    assert body.rect().contains(tool.geometry())
                    buttons = [tool] if isinstance(tool, QToolButton) else tool.findChildren(QToolButton)
                    for button in buttons:
                        if button.property("commandCompact"):
                            assert "\n" not in button.text(), button.text()


def test_action_button_uses_qt_action_without_duplicate_callback(qt_app):
    manager = ToolRibbon()
    action = QAction("Test", manager)
    calls = []
    action.triggered.connect(lambda: calls.append(True))
    button = manager.add_action(manager.add_group("Affichage", "Test"), action)
    button.click()
    assert calls == [True]
    action.setEnabled(False)
    assert not button.isEnabled()
    button.click()
    assert calls == [True]
    manager.deleteLater()


def test_view_tools_reuse_existing_actions_and_shortcuts(window):
    for label, key in (("Ajuster", "F"), ("Cadrer la sélection", "Shift+F")):
        action = next(command.action for command in window.command_registry
                      if command.label == label and key in command.shortcuts)
        assert action in window.view_tools.actions()
        assert key in [shortcut.toString() for shortcut in action.shortcuts()]
        assert any(button.defaultAction() is action
                   for button in window.view_tools.findChildren(QToolButton))
    assert window.command_registry.action("view.standard.isometric") in window.orientation_menu.actions()
    assert window.command_registry.action("file.save") in window.quick_access.actions()
    assert not window.normal_action.isEnabled()


def test_tabs_panels_and_resize_preserve_native_scene(window, qt_app):
    window.show()
    qt_app.processEvents()
    viewport = window.viewport
    assert viewport.camera and not viewport._failed
    assert len(viewport.document.objects) == 7
    camera = viewport.view.Camera()
    pose = (*camera.Eye().Coord(), *camera.Center().Coord(), *camera.Up().Coord(), camera.Scale())
    shapes = {key: obj.shape for key, obj in viewport.document.objects.items()}
    for index in range(4):
        bar = window.command_manager.tabBar()
        QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(index).center())
        qt_app.processEvents()
        assert window.command_manager.currentIndex() == index
    window.command_manager.setCurrentIndex(2)
    panel = window.manager_pane
    toggle = window.command_registry.action("window.tree")
    toggle.trigger()
    qt_app.processEvents()
    assert not panel.isVisible()
    toggle.trigger()
    qt_app.processEvents()
    assert panel.isVisible()
    for index in range(panel.count()):
        panel.setCurrentIndex(index)
        qt_app.processEvents()
    panel.setCurrentIndex(0)
    window.showMaximized()
    qt_app.processEvents()
    assert window.isMaximized()
    window.showNormal()
    qt_app.processEvents()
    for width, height in ((800, 600), (1280, 800)):
        window.resize(width, height)
        qt_app.processEvents()
        assert window.width() <= width
        assert viewport.width() > 200 and viewport.height() > 200
        assert window.command_manager.width() <= window.width()
        tools = window.view_tools
        assert tools.parentWidget() is viewport and tools.isVisible()
        assert tools.geometry().top() >= 0
        assert tools.geometry().right() <= viewport.width()
        assert viewport.width() - tools.geometry().right() <= 13
        assert viewport.height() - tools.geometry().bottom() <= 13
        ribbon = window.command_manager
        assert (ribbon.tabBar().mapTo(ribbon, QPoint()).y()
                < ribbon.currentWidget().mapTo(ribbon, QPoint()).y())
    assert pose == (*camera.Eye().Coord(), *camera.Center().Coord(), *camera.Up().Coord(), camera.Scale())
    assert all(shapes[key].IsSame(obj.shape) for key, obj in viewport.document.objects.items())
    # Qt capture documents only the shell; OCCT native dumps are tested separately.
    Path("artifacts").mkdir(exist_ok=True)
    assert window.grab().save("artifacts/shell-t01.png")
    window.activateWindow()
    qt_app.processEvents()
    QTest.qWait(150)
    assert window.grab().save("artifacts/workshop-shell.png")


def test_plane_actions_work_from_sketch_tab(window, qt_app):
    window.show()
    qt_app.processEvents()
    plane = next(obj for obj in window.viewport.document.objects.values() if obj.plane)
    window.tree.setCurrentItem(window.tree_items[plane.id])
    assert window.normal_action.isEnabled()
    window.command_manager.setCurrentIndex(1)
    qt_app.processEvents()
    button = next(button for button in window.view_tools.findChildren(QToolButton)
                  if button.defaultAction() is window.normal_action)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert window.viewport.workplane is plane.plane
    assert window.command_registry.execute("view.cancel")
    assert window.viewport.workplane is None


def test_tree_filter_preserves_selection_and_restores_branches(window, qt_app):
    window.show()
    qt_app.processEvents()
    obj = next(obj for obj in window.viewport.document.objects.values() if not obj.plane)
    window.tree.setCurrentItem(window.tree_items[obj.id])
    selected = window.selected_ids[:]
    window.manager_pane.tree_filter.setText(obj.name)
    assert not window.tree_items[obj.id].isHidden()
    assert all(item.isHidden() for key, item in window.tree_items.items() if key != obj.id)
    assert window.selected_ids == selected
    assert window.manager_pane.selection_name.text() == obj.name
    window.manager_pane.tree_filter.clear()
    assert all(not item.isHidden() for item in window.tree_items.values())


def test_overlay_buttons_dispatch_without_viewport_mouse_gestures(window, qt_app):
    window.show()
    qt_app.processEvents()
    action = window.command_registry.action("view.fit")
    calls = []
    action.triggered.connect(lambda: calls.append(True))
    button = next(button for button in window.view_tools.findChildren(QToolButton)
                  if button.defaultAction() is action)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert calls == [True]
    assert window.viewport.state.button == ""
    assert not window.viewport.window_zoom
