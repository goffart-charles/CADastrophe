"""E3 real mouse events and native OCCT presentation; not a simulated canvas."""
from dataclasses import replace
from math import hypot
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from cadastrophe.geometry import Ray, Vec3
from cadastrophe.ui.themes import apply_theme, LIGHT, DARK
from test_sketch_mode import system, plane, camera_state


def begin(system, name="XY"):
    window = system[0]
    window.tree_selection_changed([plane(window.viewport.document, name).id])
    assert window.command_registry.execute("cadastrophe.sketch.new")
    return window.tool_sessions.editor


def screen(system, point):
    window, _, model, _ = system
    session, vp = window.tool_sessions.editor, window.viewport
    world = session.workplane.to_world(*point)
    x, y = vp.view.Convert(*world.tuple())
    return QPoint(round(x / vp.devicePixelRatioF()), round(y / vp.devicePixelRatioF()))


def choose(system, tool):
    assert system[0].command_registry.execute("cadastrophe.sketch." + tool)


def click(system, point):
    window = system[0]
    position = screen(system, point)
    expected = window.tool_sessions.editor._project(position.x(), position.y())
    QTest.mouseClick(window.viewport, Qt.MouseButton.LeftButton, pos=position)
    return expected


def move(system, point):
    vp = system[0].viewport
    position = QPointF(screen(system, point))
    # QTest.mouseMove delegates to the OS cursor and can omit hover events on
    # a non-input Windows desktop. Inject the same real Qt event as the smoke.
    QApplication.sendEvent(vp, QMouseEvent(QEvent.Type.MouseMove, position,
        QPointF(vp.mapToGlobal(position.toPoint())), Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    QApplication.processEvents()
    assert system[0].tool_sessions.editor.pointer == (position.x(), position.y())


def entities(system):
    window = system[0]
    return window.viewport.document.objects[window.tool_sessions.editor.object_id].sketch.entities


@pytest.mark.parametrize("name", ["XY", "XZ", "YZ"])
@pytest.mark.parametrize("tool,count", [("line", 1), ("rectangle", 4), ("circle", 1), ("arc", 1)])
def test_mouse_draws_each_primitive_on_each_plane_one_undo_redo(system, name, tool, count):
    window, commands, model, editor = system
    session = begin(system, name)
    vp, document = window.viewport, window.viewport.document
    pose, history = camera_state(vp), document.history.count
    choose(system, tool)
    first = click(system, (-20, -15))
    move(system, (20, 15))
    assert not entities(system) and document.history.count == history
    assert session.renderer.entities and session.state == "drawing"
    assert any(type(entity) is not model.Point for entity, _ in session.renderer.entities.values())
    assert not session.error
    assert "mm" in session.values.text()
    second = click(system, (20, 15))
    if tool == "arc":
        assert not entities(system) and len(session.draft.anchors) == 2
        move(system, (-30, 25))
        click(system, (-30, 25))
    saved = entities(system)
    assert len(saved) == count and document.history.count == history + 1
    assert camera_state(vp) == pose and not vp.selection.items()
    if tool == "line":
        assert saved[0].start == first and saved[0].end == second
    elif tool == "circle":
        assert saved[0].center == first
        assert saved[0].radius == pytest.approx(hypot(second.u-first.u, second.v-first.v))
    elif tool == "arc":
        assert type(saved[0]) is model.Arc and 0 < saved[0].sweep < 2*3.141593
    for entity in saved:
        assert vp.document_adapter.features[session.object_id][1].entities[entity.id][0] == entity
    vp.setFocus()
    QTest.keyClick(vp, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert not entities(system) and not session.draft.anchors
    QTest.keyClick(vp, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert entities(system) == saved and window.tool_sessions.editing and camera_state(vp) == pose


def test_chained_lines_escape_and_switching_tools_never_commit_a_ghost(system):
    window = system[0]
    session = begin(system)
    choose(system, "line")
    first = click(system, (-30, -25))
    second = click(system, (-5, -10))
    third = click(system, (20, 15))
    saved = entities(system)
    assert len(saved) == 2 and saved[0].end == saved[1].start == second
    assert saved[0].start == first and saved[1].end == third
    history = window.viewport.document.history.count
    for point in ((25, 20), (30, 22), (35, 25)):
        move(system, point)
    handles = [ais for _, ais in session.renderer.entities.values()]
    assert window.viewport.document.history.count == history
    QTest.keyClick(window.viewport, Qt.Key.Key_Escape)
    assert session.draft.tool is None and not session.renderer.entities
    assert not window.command_registry.action("cadastrophe.sketch.line").isChecked()
    assert all(not window.viewport.context.IsDisplayed(ais) for ais in handles)
    assert entities(system) == saved and session.state == "editing" and window.tool_sessions.editing
    choose(system, "arc")
    click(system, (0, 0))
    click(system, (15, 0))
    choose(system, "rectangle")
    assert not session.draft.anchors and entities(system) == saved
    assert window.command_registry.action("cadastrophe.sketch.rectangle").isChecked()
    assert not window.command_registry.action("cadastrophe.sketch.arc").isChecked()


@pytest.mark.parametrize("tool", ["line", "rectangle", "circle", "arc"])
def test_pending_primitive_survives_native_zoom_pan_and_resize_without_anchor_drift(system, tool):
    window = system[0]
    session = begin(system)
    choose(system, tool)
    click(system, (-20, -15))
    if tool == "arc":
        click(system, (15, -15))
    anchors = session.draft.anchors
    move(system, (20, 20))
    vp, document = window.viewport, window.viewport.document
    pose = camera_state(vp)
    direction = vp.view.Camera().Direction().Coord()
    # Pan is native even while a primitive has accepted anchors.
    start = screen(system, (5, 5))
    QTest.mousePress(vp, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.ControlModifier, start)
    QTest.mouseMove(vp, start + QPoint(35, 20))
    QTest.mouseRelease(vp, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.ControlModifier, start + QPoint(35, 20))
    position = QPointF(vp.width() / 2, vp.height() / 2)
    QApplication.sendEvent(vp, QWheelEvent(position, QPointF(vp.mapToGlobal(position.toPoint())),
        QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False))
    assert session.draft.anchors == anchors and not entities(system) and document.history.count == 1
    assert vp.view.Camera().Direction().Coord() == direction
    assert tuple(vp.view.Camera().Up().Coord()) == pose[6:9]
    window.resize(1100, 720)
    QApplication.processEvents()
    move(system, (25, 20))
    expected = session._project(*session.pointer)
    assert session.draft.current == expected and session.draft.anchors == anchors
    actual = click(system, (25, 20))
    saved = entities(system)
    assert saved and document.history.count == 2
    if tool == "line":
        assert saved[0].start == anchors[0] and saved[0].end == actual
    assert vp.cursor().shape() == Qt.CursorShape.CrossCursor


def test_drag_is_not_a_click_and_invalid_ray_or_degenerate_shape_never_mutates(system, monkeypatch):
    window, commands, model, editor = system
    session = begin(system)
    choose(system, "circle")
    position = screen(system, (0, 0))
    QTest.mousePress(window.viewport, Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseMove(window.viewport, position + QPoint(40, 30))
    QTest.mouseRelease(window.viewport, Qt.MouseButton.LeftButton, pos=position + QPoint(40, 30))
    assert not session.draft.anchors and not entities(system)
    surface = session.surface
    original = surface.screen_ray
    monkeypatch.setattr(surface, "screen_ray", lambda *_: Ray(Vec3(0, 0, 10), Vec3(1, 0, 0)))
    click(system, (0, 0))
    assert not session.draft.anchors and not entities(system)
    monkeypatch.setattr(surface, "screen_ray", original)
    click(system, (0, 0))
    click(system, (0, 0))
    assert not entities(system) and session.error and len(session.draft.anchors) == 1
    click(system, (15, 0))
    assert len(entities(system)) == 1 and not session.error


def test_commit_failure_keeps_accepted_anchors_and_retry_has_one_undo(system, monkeypatch):
    window, commands, model, editor = system
    session = begin(system)
    choose(system, "rectangle")
    click(system, (-20, -10))
    anchors = session.draft.anchors
    original = editor.add_entities
    def fail(*_, **__):
        raise RuntimeError("Échec documentaire simulé")
    monkeypatch.setattr(editor, "add_entities", fail)
    click(system, (20, 10))
    assert not entities(system) and session.draft.anchors == anchors
    assert window.viewport.document.history.count == 1 and "non validé" in session.error
    monkeypatch.setattr(editor, "add_entities", original)
    click(system, (20, 10))
    assert len(entities(system)) == 4 and window.viewport.document.history.count == 2


def test_save_reopen_excludes_transients_and_quit_disconnects_input(system, tmp_path):
    window = system[0]
    session = begin(system, "YZ")
    choose(system, "circle")
    click(system, (0, 0))
    click(system, (15, 0))
    saved = entities(system)
    choose(system, "arc")
    click(system, (0, 20))
    move(system, (15, 20))
    path = tmp_path / "drawn.cada"
    assert window.save_document(path=path) and session.draft.anchors
    surface, renderer = session.surface, session.renderer
    assert window.command_registry.execute("cadastrophe.sketch.finish")
    assert surface.closed and not renderer.entities and session.state == "idle"
    window.viewport.edit_clicked.emit(400, 300)
    assert len(window.viewport.document.objects[session.object_id].sketch.entities) == 1
    window.confirm_discard = lambda doc: True
    window.close_document()
    doc = window.open_document(path)
    QApplication.processEvents()
    window.tree_selection_changed([session.object_id])
    assert window.command_registry.execute("cadastrophe.sketch.edit")
    assert entities(system) == saved and not window.tool_sessions.editor.draft.anchors
    assert not doc.is_modified


def test_partial_attach_error_rolls_back_sketch_and_releases_all_input(system, monkeypatch):
    window, commands, model, editor = system
    pose = camera_state(window.viewport)
    original = editor.SketchEditSession.attach
    captured = []
    def fail(session, surface):
        original(session, surface)
        captured.append(surface)
        raise RuntimeError("Panne après raccord souris")
    monkeypatch.setattr(editor.SketchEditSession, "attach", fail)
    window.tree_selection_changed([plane(window.viewport.document).id])
    window.command_registry.execute("cadastrophe.sketch.new")
    assert not window.tool_sessions.active and window.viewport.document.history.count == 0
    assert captured[0].closed and not captured[0]._connections
    assert camera_state(window.viewport) == pose and not window.viewport.selection.suspended


def test_feedback_uses_document_display_units_but_entity_parameters_stay_mm(system):
    window = system[0]
    window.apply_preferences(replace(window.preferences, length_unit="in", decimals=4))
    session = begin(system)
    choose(system, "circle")
    click(system, (0, 0))
    move(system, (25.4, 0))
    assert "in" in session.values.text() and "mm" not in session.values.text()
    expected = session.draft.candidate(session.draft.current)[0]
    click(system, (25.4, 0))
    assert entities(system)[0] == expected and entities(system)[0].radius == pytest.approx(25.4, abs=.2)


@pytest.mark.parametrize("operation", ["switch", "undo_creation", "hide"])
def test_pending_tool_is_disposed_before_document_or_sketch_disappears(system, operation):
    window = system[0]
    vp, doc = window.viewport, window.viewport.document
    pose = camera_state(vp)
    session = begin(system)
    pose = camera_state(vp)
    choose(system, "arc")
    click(system, (0, 0))
    click(system, (20, 0))
    move(system, (15, 10))
    surface, renderer = session.surface, session.renderer
    if operation == "switch":
        window.create_document()
    elif operation == "undo_creation":
        window.command_registry.execute("edit.undo")
    else:
        doc.set_visible(session.object_id, False)
    assert not window.tool_sessions.active and surface.closed and not renderer.entities
    assert session.state == "idle" and not vp.selection.suspended and camera_state(vp) == pose
    vp.edit_clicked.emit(200, 200)
    if session.object_id in doc.objects:
        assert not doc.objects[session.object_id].sketch.entities


@pytest.mark.parametrize("theme", [LIGHT, DARK])
def test_drawn_scene_and_pending_preview_native_capture_for_review(system, theme):
    window = system[0]
    for obj in tuple(window.viewport.document.objects.values()):
        if obj.shape is not None and obj.plane is None:
            window.viewport.document.set_visible(obj.id, False)
    session = begin(system)
    apply_theme(QApplication.instance(), theme)
    for tool, points in (("line", ((-50, -30), (-20, -30))),
                         ("rectangle", ((-10, -30), (20, -10))),
                         ("circle", ((35, 15), (45, 15))),
                         ("arc", ((-30, 15), (-18, 15), (-30, 27)))):
        choose(system, tool)
        for point in points:
            click(system, point)
    assert len(entities(system)) == 7
    window.resize(1200, 780)
    QApplication.processEvents()
    window.viewport.camera.fit()
    choose(system, "line")
    click(system, (0, 15))
    move(system, (15, 20))
    QTest.qWait(50)  # Let queued theme/layout requests settle before visual QA.
    move(system, (15, 20))
    window.viewport.view.Redraw()
    root = Path(__file__).resolve().parents[1] / "artifacts"
    suffix = "light" if theme is LIGHT else "dark"
    assert window.grab().save(str(root / f"e3-shell-{suffix}.png"))
    assert window.viewport.view.Dump(str(root / f"e3-viewport-{suffix}.png"))
    assert session.values.text() and session.renderer.entities
    assert any(type(entity).__name__ == "Segment" for entity, _ in session.renderer.entities.values())
    note = next(label for label in window.tool_host.panel.findChildren(QLabel)
                if label.text().startswith("Échap : quitter l’outil"))
    assert not note.isVisible()
    session.help_button.setChecked(True)
    QApplication.processEvents()
    assert note.isVisible()
    assert note.height() >= note.minimumSizeHint().height()
    assert note.width() >= note.minimumSizeHint().width()
    apply_theme(QApplication.instance(), LIGHT)
