"""Real native documents, tab switching, active services and discard dialogs."""
import gc
from pathlib import Path
from weakref import ref

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QToolButton
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox

from cadastrophe.app import MainWindow
from cadastrophe.core.history import HistoryBusyError
from cadastrophe.document import SceneObject
from cadastrophe.sample_scene import demo_document


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.documents.replace(window.viewport.document.id, demo_document())
    window.show()
    window.activateWindow()
    app.processEvents()
    assert window.viewport.camera and not window.viewport._failed
    try:
        yield window
    finally:
        # Only cleanup is allowed to discard unconditionally. Individual tests
        # below exercise the production confirmation, including its modal loop.
        window.confirm_discard = lambda document: True
        window.close()
        window.deleteLater()
        app.processEvents()


def activate(window, index):
    QTest.mouseClick(window.document_tabs, Qt.MouseButton.LeftButton,
                    pos=window.document_tabs.tabRect(index).center())
    QApplication.processEvents()


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


def respond_to_dialog(window, button=None, *, escape=False):
    observed = {}

    def respond():
        dialog = QApplication.activeModalWidget()
        observed["dialog"] = isinstance(dialog, QMessageBox)
        if not isinstance(dialog, QMessageBox):
            # Report failure after the loop; do not throw out of a Qt callback.
            for box in window.findChildren(QMessageBox):
                box.done(QMessageBox.StandardButton.Cancel)
            return
        observed["text"] = dialog.text()
        observed["buttons"] = dialog.standardButtons()
        observed["default"] = dialog.defaultButton() is dialog.button(QMessageBox.StandardButton.Cancel)
        if escape:
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
        else:
            dialog.button(button).click()

    QTimer.singleShot(0, respond)
    return observed


def test_new_shortcut_and_quick_access_create_real_tabs_with_only_reference_planes(window):
    first = window.viewport.document
    press(window.viewport, Qt.Key.Key_N)
    assert window.document_tabs.count() == 2
    second = window.documents.active_document
    assert second is window.viewport.document and second.name == "Pièce1"
    assert len(second.objects) == 3 and all(obj.plane and obj.visible for obj in second.objects.values())
    assert not second.is_modified and not second.history.can_undo
    assert not set(first.objects).intersection(second.objects)
    action = window.command_registry.action("file.new")
    button = next(button for button in window.quick_access.findChildren(QToolButton) if button.defaultAction() is action)
    button.click()
    QApplication.processEvents()
    assert window.document_tabs.count() == 3 and window.viewport.document.name == "Pièce2"
    assert window.view_tools.parentWidget() is window.viewport and window.view_tools.isVisible()


def test_tabs_keep_camera_render_and_true_face_selection_per_document(window):
    first_view = window.viewport
    first = first_view.document
    px, py = first_view.view.Convert(-55., -35., 40.)
    scale = first_view.devicePixelRatioF()
    QTest.mouseClick(first_view, Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier, QPoint(round(px / scale), round(py / scale)))
    face = first_view.selection.items()[0]
    assert face.topology == "Face"
    first_view.camera.pan(15, -12)
    window.command_registry.execute("view.render.wireframe")
    original_pose = pose(first_view)
    second = window.documents.add(demo_document("Deuxième"))
    QApplication.processEvents()
    second_view = window.viewport
    assert second_view is not first_view and second_view.camera
    second.select_objects([body(second).id])
    window.command_registry.execute("view.standard.front")
    other_pose = pose(second_view)
    activate(window, 0)
    assert window.viewport is first_view and pose(first_view) == original_pose
    restored = first_view.selection.items()[0]
    assert restored.topology == "Face" and restored.shape.IsSame(face.shape)
    assert first.selected_ids == (face.object_id,) and window.tree_items[face.object_id].isSelected()
    assert window.command_registry.action("view.render.wireframe").isChecked()
    assert window.view_tools.parentWidget() is first_view
    Path("artifacts").mkdir(exist_ok=True)
    first_view.view.Redraw()
    assert first_view.view.Dump("artifacts/t05-first-viewport.png")
    activate(window, 1)
    assert window.viewport is second_view and pose(second_view) == other_pose
    assert second_view.selection.items()[0].topology == "Objet"
    assert window.command_registry.action("view.render.edges").isChecked()
    assert second.selected_ids == (body(second).id,)
    second_view.view.Redraw()
    assert second_view.view.Dump("artifacts/t05-second-viewport.png")
    assert window.grab().save("artifacts/t05-documents.png")


def test_active_services_and_shortcuts_cannot_mutate_another_tab(window):
    first = window.viewport.document
    first_view = window.viewport
    first_obj = body(first)
    first.rename(first_obj.id, "Premier renommé")
    second = window.documents.add(demo_document("Deuxième"))
    QApplication.processEvents()
    second_obj = body(second)
    second.select_objects([second_obj.id])
    window.command_registry.execute("object.visibility")
    assert not second.objects[second_obj.id].visible and first.objects[first_obj.id].visible
    assert second.is_modified and first.is_modified
    assert window.document_tabs.tabText(0).endswith(" *") and window.document_tabs.tabText(1).endswith(" *")
    press(window.tree, Qt.Key.Key_Z)
    assert second.objects[second_obj.id].visible and not second.is_modified
    assert window.isWindowModified() is False
    activate(window, 0)
    assert window.isWindowModified() and window.command_registry.action("edit.undo").isEnabled()
    press(window.viewport, Qt.Key.Key_Z)
    assert first.objects[first_obj.id].name == first_obj.name and not first.is_modified
    # Mutating an inactive document updates its own view and tab, not the active tree.
    second.rename(second_obj.id, "Deuxième renommé")
    assert set(window.tree_items) == set(first.objects) == set(first_view.presentations)
    assert second_obj.id not in window.tree_items
    assert window.command_registry.action("edit.redo").isEnabled()
    assert window.document_tabs.tabText(1).endswith(" *")
    activate(window, 1)
    assert window.tree_items[second_obj.id].text(0) == "Deuxième renommé"
    press(window.viewport, Qt.Key.Key_Z)
    assert second.objects[second_obj.id].name == second_obj.name


def test_plane_mode_and_hidden_selection_remain_local_when_switching_tabs(window):
    first_view = window.viewport
    first = first_view.document
    plane = next(obj for obj in first.objects.values() if obj.plane)
    first.select_objects([plane.id])
    window.command_registry.execute("view.normal_to_plane")
    original_pose = pose(first_view)
    window.create_document()
    QApplication.processEvents()
    assert window.viewport.workplane is None and not window.viewport.document.selected_ids
    activate(window, 0)
    assert window.viewport.workplane is plane.plane and pose(first_view) == original_pose
    assert first.selected_ids == (plane.id,) and window.normal_action.isEnabled()
    assert first_view.context.IsDisplayed(first_view.local_axes)
    window.command_registry.execute("view.cancel")
    assert first_view.workplane is None


def test_real_discard_dialog_defaults_to_cancel_and_escape_preserves_document(window):
    document = window.viewport.document
    obj = body(document)
    document.rename(obj.id, "À conserver")
    original_view = window.viewport
    original_pose = pose(original_view)
    observed = respond_to_dialog(window, escape=True)
    assert not window.close_document()
    assert observed["dialog"] and observed["default"]
    assert observed["buttons"] == QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel
    assert document.name in observed["text"]
    assert window.viewport is original_view and pose(original_view) == original_pose
    assert document.objects[obj.id].name == "À conserver" and document.history.can_undo
    assert window.document_tabs.count() == 1 and window.isVisible()


def test_real_discard_closes_document_releases_native_view_and_allows_new_from_empty(window):
    document = window.viewport.document
    viewport = window.viewport
    document.rename(body(document).id, "Temporaire")
    observed = respond_to_dialog(window, QMessageBox.StandardButton.Discard)
    assert window.close_document()
    assert observed["dialog"] and viewport._native_closed
    assert viewport.context is None and viewport.display is None
    assert window.documents.documents == () and window.viewport is None
    assert window.document_tabs.count() == 0 and not window.tree_items
    assert window.empty_workspace.isVisible() and not window.view_tools.isVisible()
    for command_id in ("file.close", "edit.undo", "view.fit", "object.delete"):
        assert not window.command_registry.action(command_id).isEnabled()
    assert window.command_registry.action("file.new").isEnabled()
    press(window.empty_workspace, Qt.Key.Key_N)
    assert window.document_tabs.count() == 1 and window.viewport.document.name == "Pièce1"
    assert window.viewport.camera and window.view_tools.isVisible()


def test_clean_tab_close_button_requires_no_confirmation_and_selects_neighbor(window, monkeypatch):
    first = window.viewport.document
    second = window.create_document()
    third = window.create_document()
    QApplication.processEvents()
    monkeypatch.setattr(window, "confirm_discard", lambda document: pytest.fail("Clean document must not prompt"))
    activate(window, 1)
    from PySide6.QtWidgets import QTabBar
    close = (window.document_tabs.tabButton(1, QTabBar.ButtonPosition.RightSide)
             or window.document_tabs.tabButton(1, QTabBar.ButtonPosition.LeftSide))
    assert close is not None
    close.click()
    QApplication.processEvents()
    assert window.documents.documents == (first, third) and window.documents.active_document is third
    assert second.id not in window._views
    assert window.tree_adapter.document is third and window._history is third.history


def test_closing_inactive_dirty_tab_prompts_for_that_document_and_keeps_active_view(window):
    first = window.viewport.document
    obj = body(first)
    first.rename(obj.id, "Temporaire")
    second = window.create_document()
    QApplication.processEvents()
    active_view = window.viewport
    observed = respond_to_dialog(window, QMessageBox.StandardButton.Discard)
    assert window.close_document(first.id)
    assert first.name in observed["text"] and window.viewport is active_view
    assert window.documents.documents == (second,) and window.tree_adapter.document is second
    first.rename(obj.id, "Après fermeture")
    assert not window.isWindowModified() and window.document_tabs.count() == 1


def test_cancel_application_close_keeps_every_document_and_view_intact(window, monkeypatch):
    first = window.viewport.document
    first.rename(body(first).id, "A")
    second = window.documents.add(demo_document("Deuxième"))
    QApplication.processEvents()
    second.rename(body(second).id, "B")
    views = tuple(window._views.values())
    answers = iter((True, False))
    asked = []

    def decide(document):
        asked.append(document)
        return next(answers)

    monkeypatch.setattr(window, "confirm_discard", decide)
    assert not window.close()
    assert asked == [first, second] and window.documents.documents == (first, second)
    assert window.isVisible() and all(not view._native_closed for view in views)
    assert all(view.context is not None for view in views)
    assert first.is_modified and second.is_modified and second.history.can_undo
    monkeypatch.setattr(window, "confirm_discard", lambda document: True)
    assert window.close()
    assert not window.documents.documents and all(view._native_closed for view in views)


def test_transaction_guards_all_tabs_and_recovers_after_inactive_rollback(window):
    first = window.viewport.document
    second = window.create_document()
    QApplication.processEvents()
    with pytest.raises(ValueError):
        with first.history.transaction("Lot inactif"):
            first.rename(body(first).id, "Temporaire")
            assert not window.document_tabs.isEnabled()
            for command_id in ("file.new", "file.close", "object.rename", "edit.undo"):
                assert not window.command_registry.action(command_id).isEnabled()
            assert window.create_document() is None and not window.close_document(first.id)
            assert not window.close()
            with pytest.raises(HistoryBusyError):
                window.documents.activate(first.id)
            raise ValueError("Rollback")
    assert window.document_tabs.isEnabled() and not first.is_modified
    assert window.documents.documents == (first, second) and window.documents.active_document is second


def test_startup_opens_only_a_blank_part_without_demo_commands(tmp_path):
    app = QApplication.instance() or QApplication([])
    owner = MainWindow(preferences_path=tmp_path/'preferences.json',
                       recovery_directory=tmp_path/'recovery')
    try:
        owner.show()
        app.processEvents()
        document = owner.documents.active_document
        assert document is owner.viewport.document and document.name == 'Pièce'
        assert len(document.objects) == 3
        assert all(obj.plane and obj.visible for obj in document.objects.values())
        assert not document.is_modified and not document.history.can_undo
        assert owner.document_tabs.count() == 1
        assert owner.viewport.camera and not owner.viewport._failed
        assert 'démonstration' not in owner.windowTitle().lower()
        assert all(command.id != 'scene.reset' for command in owner.command_registry)
    finally:
        owner.close()
        owner.deleteLater()
        app.processEvents()


def test_closed_documents_and_native_views_can_be_collected(window):
    document = window.viewport.document
    document_ref = ref(document)
    viewport_ref = ref(window.viewport)
    assert window.close_document()
    del document
    QApplication.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    assert viewport_ref() is None and document_ref() is None


def test_manager_replacement_updates_native_view_and_active_services(window):
    previous = window.viewport.document
    viewport = window.viewport
    replacement = demo_document("Remplacement explicite")
    window.documents.replace(previous.id, replacement)
    assert window.viewport is viewport and viewport.document is replacement
    assert window.tree_adapter.document is replacement and window._history is replacement.history
    assert set(viewport.presentations) == set(window.tree_items) == set(replacement.objects)
    assert set(previous.objects).isdisjoint(viewport.presentations)
    assert window.document_tabs.tabText(0) == replacement.name
