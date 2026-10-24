"""Native AIS entity selection, detached editing gestures and common shortcuts."""
from dataclasses import replace
from math import pi

import pytest
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QFocusEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from test_sketch_mode import system
from test_sketch_drawing_ui import begin, screen, click, move, choose
from test_sketch_mode import plane, camera_state


def seed(system, plane="XY"):
    window, commands, model, _ = system
    session = begin(system, plane)
    values = (model.Point(model.Vec2(-40, -25)),
              model.Segment(model.Vec2(-20, -20), model.Vec2(20, -20)),
              model.Circle(model.Vec2(35, 15), 10),
              model.Arc(model.Vec2(-30, 20), 15, 0, pi))
    for entity in values:
        commands.add_entity(window.viewport.document, session.object_id, entity)
    window.viewport.document.history.clear()
    return session, values


def values(system):
    session = system[0].tool_sessions.editor
    return session.document.objects[session.object_id].sketch.entities


def mouse_move(system, position, *, buttons=Qt.MouseButton.LeftButton):
    vp = system[0].viewport
    point = QPointF(position)
    QApplication.sendEvent(vp, QMouseEvent(QEvent.Type.MouseMove, point,
        QPointF(vp.mapToGlobal(point.toPoint())), Qt.MouseButton.NoButton, buttons, Qt.KeyboardModifier.NoModifier))


def drag(system, first, last):
    vp = system[0].viewport
    start, end = screen(system, first), screen(system, last)
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=start)
    mouse_move(system, end)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=end)


@pytest.mark.parametrize("plane", ["XY", "XZ", "YZ"])
def test_native_entity_picking_ctrl_selection_hover_and_property_list_sync(system, plane):
    window = system[0]
    session, original = seed(system, plane)
    move(system, (0, -20))
    assert window.viewport.context.HasDetected()
    assert session.surface.presentation.key(window.viewport.context.DetectedInteractive()) == original[1].id
    click(system, (0, -20))
    assert session.interaction.selected == (original[1].id,)
    assert len(session.interaction.handles) == 2
    QTest.mouseClick(window.viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, screen(system, (35, 25)))
    assert set(session.interaction.selected) == {original[1].id, original[2].id}
    assert len(session.entity_panel.list.selectedItems()) == 2
    assert not window.viewport.selection.items() and window.viewport.selection.suspended
    QTest.mouseClick(window.viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, screen(system, (0, -20)))
    assert session.interaction.selected == (original[2].id,)
    click(system, (70, -40))
    assert not session.interaction.selected and not session.interaction.handles
    assert values(system) == original and session.document.history.count == 0


@pytest.mark.parametrize("index,part,target", [
    (0, "position", (-35, -20)), (1, "start", (-25, -15)), (1, "end", (25, -15)),
    (2, "center", (30, 10)), (2, "radius", (55, 15)),
    (3, "center", (-25, 15)), (3, "start", (-15, 25)), (3, "end", (-45, 25)), (3, "radius", (-30, 40))])
def test_each_native_control_handle_is_one_undoable_edit(system, index, part, target):
    window = system[0]
    session, original = seed(system)
    session.interaction.set_selected((original[index].id,))
    from importlib import import_module
    editing = import_module(system[3].__package__ + ".editing")
    anchor = dict(editing.control_points(original[index]))[part]
    start, end = screen(system, (anchor.u, anchor.v)), screen(system, target)
    QTest.mousePress(window.viewport, Qt.MouseButton.LeftButton, pos=start)
    assert session.interaction.pending["handle"] == (original[index].id, part)
    expected_point = session._project(end.x(), end.y())
    # At high DPI/small windows, 5 model mm can be below Qt's logical-pixel
    # click/drag threshold. Cross it explicitly, then return to the target.
    mouse_move(system, start + QPoint(50, 50))
    mouse_move(system, end)
    assert session.state == "dragging" and values(system) == original and session.document.history.count == 0
    expected = editing.moved_handle(original[index], part, expected_point)
    QTest.mouseRelease(window.viewport, Qt.MouseButton.LeftButton, pos=end)
    assert values(system)[index] == expected and session.document.history.count == 1
    assert not session.interaction.pending and not session.surface.presentation.hidden_ids
    QTest.keyClick(window.viewport, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert values(system) == original
    QTest.keyClick(window.viewport, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert values(system)[index] == expected and session.interaction.selected == (original[index].id,)


def test_group_translation_and_escape_cancel_do_not_mutate_or_leave_ghosts(system):
    window = system[0]
    session, original = seed(system)
    session.interaction.set_selected((original[1].id, original[2].id))
    start, end = screen(system, (0, -20)), screen(system, (5, -10))
    QTest.mousePress(window.viewport, Qt.MouseButton.LeftButton, pos=start)
    mouse_move(system, end)
    assert session.state == "dragging" and len(session.surface.presentation.hidden_ids) == 2
    assert values(system) == original and session.document.history.count == 0
    QTest.keyClick(window.viewport, Qt.Key.Key_Escape)
    QTest.mouseRelease(window.viewport, Qt.MouseButton.LeftButton, pos=end)
    assert values(system) == original and session.document.history.count == 0
    assert not session.surface.presentation.hidden_ids and len(session.interaction.selected) == 2
    drag(system, (0, -20), (5, -10))
    updated = values(system)
    assert updated[0] == original[0] and updated[3] == original[3]
    assert updated[1] != original[1] and updated[2] != original[2]
    assert updated[1].start.u - original[1].start.u == pytest.approx(updated[2].center.u - original[2].center.u)
    assert updated[1].start.v - original[1].start.v == pytest.approx(updated[2].center.v - original[2].center.v)
    assert session.document.history.count == 1


def test_native_rectangle_selects_entities_not_bodies_and_drawing_stays_exclusive(system):
    window = system[0]
    session, original = seed(system)
    drag(system, (-60, -40), (60, 45))
    assert set(session.interaction.selected) == {entity.id for entity in original}
    assert not window.viewport.selection.items() and session.document.history.count == 0
    choose(system, "line")
    assert not window.command_registry.action("object.delete").isEnabled()
    click(system, (0, 0))
    move(system, (15, 10))
    assert values(system) == original and session.draft.anchors
    assert not session.surface.presentation.interactive
    assert window.command_registry.execute("cadastrophe.sketch.select")
    assert not session.draft.anchors and session.surface.presentation.interactive
    assert not session.renderer.entities and values(system) == original


class MemoryClipboard:
    """Test transport backend only. The application never falls back to memory."""
    def __init__(self):
        self.mime = None
    def mimeData(self):
        return self.mime
    def setMimeData(self, mime):
        self.mime = mime


def test_common_shortcuts_copy_paste_cut_delete_and_undo_use_entity_uuids(system):
    window = system[0]
    window.object_clipboard.clipboard = MemoryClipboard()
    session, original = seed(system)
    session.interaction.set_selected(entity.id for entity in original)
    vp = window.viewport
    vp.setFocus()
    QTest.keyClick(vp, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert session.document.history.count == 0 and window.command_registry.action("edit.paste").isEnabled()
    QTest.keyClick(vp, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    first = values(system)[4:]
    assert len(first) == 4 and not {entity.id for entity in first} & {entity.id for entity in original}
    assert session.document.history.count == 1 and len(session.interaction.selected) == 4
    QTest.keyClick(vp, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    second = values(system)[8:]
    assert second[0].position.u - first[0].position.u == 10
    assert second[0].position.v - first[0].position.v == 10
    assert not {entity.id for entity in first} & {entity.id for entity in second}
    QTest.keyClick(vp, Qt.Key.Key_X, Qt.KeyboardModifier.ControlModifier)
    assert len(values(system)) == 8 and session.document.history.count == 3
    QTest.keyClick(vp, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert values(system)[8:] == second
    session.interaction.set_selected(entity.id for entity in second)
    QTest.keyClick(vp, Qt.Key.Key_Delete)
    assert len(values(system)) == 8
    QTest.keyClick(vp, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert values(system)[8:] == second


def test_failed_system_publication_never_cuts_source_entities(system):
    window = system[0]
    class RefusingClipboard(MemoryClipboard):
        def setMimeData(self, mime): pass
    window.object_clipboard.clipboard = RefusingClipboard()
    session, original = seed(system)
    session.interaction.set_selected(entity.id for entity in original)
    assert window.command_registry.execute("edit.cut")
    assert values(system) == original and session.document.history.count == 0 and session.error


@pytest.mark.parametrize("index,key,value", [(0, "pu", 8.), (1, "bu", 25.), (2, "radius", 12.), (3, "radius", 20.)])
def test_property_manager_parameter_apply_is_buffered_undoable_and_text_keys_remain_native(system, index, key, value):
    window = system[0]
    session, original = seed(system)
    session.interaction.set_selected((original[index].id,))
    panel = session.entity_panel
    field = panel.fields[key][0]
    field.set_internal_value(value)
    assert values(system) == original
    panel.apply.click()
    changed = values(system)[index]
    assert changed != original[index] and changed.id == original[index].id and session.document.history.count == 1
    session.document.history.undo()
    assert values(system) == original


def test_enter_reads_pending_parameter_text_delete_in_field_does_not_delete_entity(system):
    window = system[0]
    session, original = seed(system)
    session.interaction.set_selected((original[0].id,))
    field = session.entity_panel.fields['pu'][0]
    field.setFocus()
    field.lineEdit().selectAll()
    QTest.keyClick(field.lineEdit(), Qt.Key.Key_Delete)
    assert values(system) == original and session.document.history.count == 0
    QTest.keyClicks(field.lineEdit(), '8' + field.locale().decimalPoint() + '125')
    QTest.keyClick(field, Qt.Key.Key_Return)
    assert values(system)[0].position.u == 8.125 and session.document.history.count == 1
    session.document.history.undo()
    assert values(system) == original


def test_invalid_parameters_and_noop_apply_never_enter_history(system):
    session, original = seed(system)
    session.interaction.set_selected((original[1].id,))
    panel = session.entity_panel
    assert panel.commit() and session.document.history.count == 0
    panel.fields['bu'][0].set_internal_value(original[1].start.u)
    panel.fields['bv'][0].set_internal_value(original[1].start.v)
    assert not panel.commit() and panel.message.text()
    assert values(system) == original and session.document.history.count == 0


def test_focus_loss_cancels_drag_restores_actions_and_preview(system):
    window = system[0]
    session, original = seed(system)
    session.interaction.set_selected((original[1].id,))
    start, end = screen(system, (0, -20)), screen(system, (8, -10))
    QTest.mousePress(window.viewport, Qt.MouseButton.LeftButton, pos=start)
    mouse_move(system, end)
    assert session.interaction.pending and session.surface.presentation.hidden_ids
    QApplication.sendEvent(window.viewport, QFocusEvent(QEvent.Type.FocusOut))
    assert not session.interaction.pending and not session.surface.presentation.hidden_ids
    assert window.command_registry.action('object.delete').isEnabled()
    QTest.mouseRelease(window.viewport, Qt.MouseButton.LeftButton, pos=end)
    assert values(system) == original and session.document.history.count == 0


def test_malformed_clipboard_or_failed_drag_commit_preserves_document(system, monkeypatch):
    window = system[0]
    window.object_clipboard.clipboard = MemoryClipboard()
    session, original = seed(system)
    from importlib import import_module
    clipboard = import_module(system[3].__package__ + '.clipboard')
    window.services.clipboard.write(clipboard.MIME_TYPE, b'{broken')
    window.update_action_states()
    assert window.command_registry.execute('edit.paste')
    assert values(system) == original and session.document.history.count == 0 and session.error
    session.interaction.set_selected((original[1].id,))
    def fail(*args, **kwargs):
        raise RuntimeError('Échec documentaire simulé')
    monkeypatch.setattr(system[3], 'replace_entities', fail)
    drag(system, (0, -20), (10, -10))
    assert values(system) == original and session.document.history.count == 0 and session.error
    assert not session.surface.presentation.hidden_ids and not session.interaction.pending


def test_copy_between_document_planes_uses_local_coordinates_new_ids_and_target_support(system):
    window = system[0]
    window.object_clipboard.clipboard = MemoryClipboard()
    source, original = seed(system)
    source.interaction.set_selected(entity.id for entity in original)
    window.command_registry.execute('edit.copy')
    source_doc = source.document
    source_id = source.object_id
    window.command_registry.execute('cadastrophe.sketch.finish')
    window.create_document()
    QApplication.processEvents()  # Initialize the destination's native WNT view.
    target = begin(system, 'YZ')
    assert target.document is not source_doc
    history = target.document.history.count
    window.command_registry.execute('edit.paste')
    pasted = values(system)
    assert len(pasted) == 4 and target.document.history.count == history + 1
    assert not {entity.id for entity in pasted} & {entity.id for entity in original}
    assert pasted[0].position == system[2].Vec2(original[0].position.u + 10, original[0].position.v + 10)
    support = target.document.objects[target.object_id].sketch.support
    assert support.plane_id == plane(target.document, 'YZ').id
    assert source_doc.objects[source_id].sketch.entities == original and source_doc.history.count == 0


def test_edit_save_reopen_removes_overlays_and_restores_3d_camera_and_selection(system, tmp_path):
    window = system[0]
    pose = camera_state(window.viewport)
    session, original = seed(system)
    pose = camera_state(window.viewport)
    session.interaction.set_selected((original[2].id,))
    session.entity_panel.fields['radius'][0].set_internal_value(17.)
    assert session.entity_panel.commit()
    saved, object_id = values(system), session.object_id
    overlays = [ais for _, ais in session.interaction.overlay.entities.values()]
    surface = session.surface
    path = tmp_path / 'edited.cada'
    assert window.save_document(path=path)
    assert window.command_registry.execute('cadastrophe.sketch.finish')
    assert surface.closed and camera_state(window.viewport) == pose and not window.viewport.selection.suspended
    assert all(not window.viewport.context.IsDisplayed(ais) for ais in overlays)
    window.confirm_discard = lambda doc: True
    window.close_document()
    window.open_document(path)
    QApplication.processEvents()
    window.tree_selection_changed([object_id])
    assert window.command_registry.execute('cadastrophe.sketch.edit')
    assert values(system) == saved and not window.tool_sessions.editor.interaction.selected
    assert window.tool_sessions.editor.entity_panel.list.count() == 4


def test_real_qt_clipboard_roundtrip_when_windows_desktop_provides_it(system):
    clipboard = QApplication.clipboard()
    snapshot = QMimeData()
    previous = clipboard.mimeData()
    if previous is not None:
        for mime in previous.formats():
            snapshot.setData(mime, previous.data(mime))
    try:
        marker = 'cadastrophe-e4-clipboard-probe'
        clipboard.setText(marker)
        QApplication.processEvents()
        if clipboard.text() != marker:
            pytest.skip('Presse-papiers Windows indisponible sur ce bureau de test (sonde texte Qt).')
        window = system[0]
        session, original = seed(system)
        session.interaction.set_selected(entity.id for entity in original)
        # Exercise the command after the editor's queued native activation and
        # layout events, as in a real keyboard/menu copy on the private desktop.
        window.viewport.setFocus()
        QApplication.processEvents()
        assert QTest.qWaitForWindowActive(window, 1000)
        window.command_registry.execute('edit.copy')
        assert not session.error
        window.command_registry.execute('edit.paste')
        assert len(values(system)) == 8 and session.document.history.count == 1
    finally:
        clipboard.setMimeData(snapshot)
