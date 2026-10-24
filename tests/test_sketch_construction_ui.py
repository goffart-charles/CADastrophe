"""Real split-button selection, dashed native geometry and reversible conversion."""
from pathlib import Path
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton, QStyle, QStyleOptionToolButton
from OCC.Core.Aspect import Aspect_TOL_DASH

from test_sketch_drawing_ui import system, begin, choose, click, move, screen, entities
from test_extrusion_tool import extrusion_system, rectangle, start
from test_primitives import volume


@pytest.mark.parametrize("index,tool,theme", [(0, "line", "light"), (1, "construction_line", "light"),
                                            (1, "construction_line", "dark")])
def test_line_menu_drawing_preview_selection_conversion_and_delete(system, tmp_path, index, tool, theme):
    window = system[0]
    window.apply_preferences(replace(window.preferences, theme=theme))
    session = begin(system)
    buttons = {}
    for key in ("line", "arc"):
        action = window.command_registry.action("cadastrophe.sketch." + key)
        button = next(b for b in window.command_manager.findChildren(QToolButton) if b.defaultAction() is action)
        buttons[key] = button
        option = QStyleOptionToolButton()
        button.initStyleOption(option)
        arrow = button.style().subControlRect(QStyle.ComplexControl.CC_ToolButton, option,
                                              QStyle.SubControl.SC_ToolButtonMenu, button)
        assert button.width() >= 46 and button.iconSize().width() >= 18
        assert arrow.width() >= 14 and button.width() - arrow.width() >= 30
    button = buttons["line"]
    menu = button.menu()
    assert [a.text() for a in menu.actions()] == ["Ligne", "Ligne de construction"]
    option = QStyleOptionToolButton()
    button.initStyleOption(option)
    arrow = button.style().subControlRect(QStyle.ComplexControl.CC_ToolButton, option,
                                          QStyle.SubControl.SC_ToolButtonMenu, button)
    observed = []
    def select():
        observed.append(menu.isVisible())
        QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(menu.actions()[index]).center())
    timer = QTimer(menu)
    timer.setSingleShot(True)
    timer.timeout.connect(menu.close)
    timer.start(1000)
    QTimer.singleShot(50, menu, select)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=arrow.center())
    timer.stop()
    assert observed == [True] and session.draft.tool == tool
    click(system, (-30, -20))
    move(system, (30, -20))
    preview = [e for e, _ in session.renderer.entities.values() if type(e) is system[2].Segment]
    assert preview and preview[0].construction == bool(index)
    click(system, (30, -20))
    line, = entities(system)
    assert line.construction == bool(index)
    if index:
        ais = session.interaction.base.entities[line.id][1]
        assert ais.Attributes().WireAspect().Aspect().Type() == Aspect_TOL_DASH
    choose(system, "select")
    QTest.mouseClick(window.viewport, Qt.MouseButton.LeftButton, pos=screen(system, (0, -20)))
    assert session.interaction.selected == (line.id,)
    QApplication.processEvents()
    panel = session.entity_panel
    assert panel.construction.isVisible() and panel.construction.isEnabled()
    if index:
        Path("artifacts").mkdir(exist_ok=True)
        assert window.viewport.view.Dump(f"artifacts/construction-line-viewport-{theme}.png")
        assert window.grab().save(f"artifacts/construction-line-editor-{theme}.png")
    panel.fields["bu"][0].set_internal_value(45)
    panel.apply.click()
    edited, = entities(system)
    assert not session.error and edited.end.u == pytest.approx(45)
    assert edited.id == line.id and edited.construction == line.construction
    line = edited
    constraints = session.document.objects[session.object_id].sketch.constraints
    count = session.document.history.count
    panel.construction.click()
    converted, = entities(system)
    assert converted.construction != line.construction and converted.id == line.id
    assert session.document.history.count == count + 1
    assert session.document.objects[session.object_id].sketch.constraints == constraints
    assert window.command_registry.execute("edit.undo")
    assert entities(system) == (line,)
    assert window.command_registry.execute("edit.redo")
    assert entities(system) == (converted,)
    assert window.save_document(path=tmp_path / "construction-ui.cada")
    assert window.command_registry.execute("object.delete")
    assert not entities(system)
    assert window.command_registry.execute("edit.undo")
    assert entities(system) == (converted,)


def test_construction_crossing_keeps_one_extrusion_region_and_volume(extrusion_system, tmp_path):
    from cadastrophe.core.feature_objects import BodyObject
    from cadastrophe.core.body_history import body_result
    from cadastrophe.core.feature_model import ResultStatus
    from time import monotonic
    window, commands, model = extrusion_system
    obj = rectangle(extrusion_system)
    axis = model.Segment(model.Vec2(-10, 30), model.Vec2(110, 30), construction=True)
    commands.add_entity(window.viewport.document, obj.id, axis)
    obj = window.viewport.document.objects[obj.id]
    tool = start(window, obj)
    assert len(tool.regions) == 1
    depth = tool.depth.internal_value
    assert window.tool_sessions.commit(), window.tool_sessions.message
    body, = (obj for obj in window.viewport.document.objects.values() if isinstance(obj, BodyObject))
    assert volume(body.shape) == pytest.approx(100*60*depth)
    path = tmp_path / "construction-extrusion.cada"
    assert window.save_document(path=path)
    assert window.close_document()
    reopened = window.open_document(path)
    assert reopened.objects[obj.id].sketch.entity(axis.id).construction
    assert volume(reopened.objects[body.id].shape) == pytest.approx(100*60*depth)
    # Converting the entity used to identify the selected contour must preserve
    # the feature graph and expose a failed rebuild, rather than reject the edit.
    sketch = reopened.objects[obj.id].sketch
    boundary = min(sketch.profile_entities, key=lambda e: e.id)
    def convert_boundary():
        QTest.qWait(100)  # Newly opened native view and action predicates settle.
        reopened.set_visible(obj.id, True)
        window.tree_selection_changed([obj.id])
        assert window.command_registry.execute("cadastrophe.sketch.edit")
        session = window.tool_sessions.editor
        session.interaction.set_selected((boundary.id,))
        session.entity_panel.construction.click()
        assert not session.error, session.error
        assert window.command_registry.execute("cadastrophe.sketch.finish")
        # The coalescing timer can be restarted by queued editor notifications.
        # Wait for its real result under load, without forcing evaluation.
        deadline = monotonic()+5
        while body_result(reopened, body.id).status is ResultStatus.DIRTY and monotonic() < deadline:
            QTest.qWait(10)
    convert_boundary()
    assert body_result(reopened, body.id).status is ResultStatus.ERROR
    assert reopened.objects[body.id].shape is None
    assert reopened.objects[body.id].features == body.features
    assert window.save_document(path=path)
    assert window.close_document()
    reopened = window.open_document(path)
    assert reopened.objects[body.id].shape is None
    convert_boundary()
    assert body_result(reopened, body.id).status is ResultStatus.READY
    assert reopened.objects[body.id].features == body.features
    assert volume(reopened.objects[body.id].shape) == pytest.approx(100*60*depth)
