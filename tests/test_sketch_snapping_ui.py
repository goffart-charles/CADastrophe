"""E5 real native snapping/dragging, press-release rectangles and UI requests."""
from dataclasses import asdict, replace
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QFocusEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from cadastrophe.core.preferences import Preferences, PreferenceStore
from test_sketch_mode import system, camera_state
from test_sketch_drawing_ui import begin, choose, click, move, screen, entities
from test_sketch_editing_ui import mouse_move

ROOT = Path(__file__).resolve().parents[1]


def test_todo_body_label_and_native_msaa_diagnostics(system):
    from OCC.Core.Graphic3d import Graphic3d_TypeOfLimit_MaxMsaa
    window = system[0]
    view = window.viewport.view
    view.Redraw()
    assert window.tree_adapter.bodies.text(0) == "Corps"
    requested = view.RenderingParams().NbMsaaSamples
    maximum = view.Viewer().Driver().InquireLimit(Graphic3d_TypeOfLimit_MaxMsaa)
    assert requested == 4 and maximum >= requested
    native = view.View().DumpJson()
    (ROOT / "artifacts/e5-rendering-native.json").write_text(native, encoding="utf-8")
    (ROOT / "artifacts/e5-antialiasing.json").write_text(json.dumps(dict(msaa_requested=requested,
        driver_max_samples=maximum, device_pixel_ratio=window.viewport.devicePixelRatioF()), indent=2), encoding="utf-8")


@pytest.mark.parametrize("name", ["XY", "XZ", "YZ"])
@pytest.mark.parametrize("zoom", [.2, 1., 5.])
def test_real_endpoint_snap_uses_screen_pixels_at_each_zoom_and_support_and_commits_exact_geometry(system, name, zoom):
    window, commands, model, _ = system
    session = begin(system, name)
    # Keep the test point in the drawable viewport even at 5x zoom: the native
    # ViewCube now remains active during sketch editing and consumes its clicks.
    target = model.Vec2(12, 7)
    commands.add_entity(session.document, session.object_id, model.Point(target))
    vp = window.viewport
    vp.view.Camera().SetScale(vp.view.Camera().Scale()*zoom)
    vp.view.Redraw()
    pose = camera_state(vp)
    choose(system, "line")
    point = screen(system, (target.u, target.v))
    position = QPointF(point.x()+5, point.y()+2)
    assert vp.rect().contains(position.toPoint()) and not vp.cube_at(position)
    QApplication.sendEvent(vp, QMouseEvent(QEvent.Type.MouseMove, position, QPointF(vp.mapToGlobal(position.toPoint())),
        Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    assert session.snap_result.kind == "endpoint" and session.draft.current == target
    assert session.snap_renderer.marker is not None and "Extrémité" in session.values.text()
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=position.toPoint())
    assert session.draft.anchors == (target,)
    click(system, (-30, -25))
    assert entities(system)[-1].start == target and camera_state(vp) == pose
    assert session.document.history.count == 3  # sketch, reference point, line


def test_continuous_native_projection_matches_occt_pixels_and_grid_is_nonselectable(system):
    window, _, model, _ = system
    session = begin(system)
    for uv in ((0, 0), (32.7, -19.5), (-15, 29)):
        point = model.Vec2(*uv)
        x, y = session.surface.project(session.workplane.to_world(*uv))
        expected = screen(system, uv)
        assert abs(x-expected.x()) <= 1.1 and abs(y-expected.y()) <= 1.1
    session.snap_settings = replace(session.snap_settings, grid=True, inference=False, enabled=False, grid_step=10)
    choose(system, "circle")
    move(system, (30.2, 20.2))
    assert session.draft.current == model.Vec2(30, 20) and session.snap_result.kind == "grid"
    grid = session.snap_renderer.grid
    assert grid is not None and grid.IsInfinite()
    history = session.document.history.count
    document = session.document
    window.tool_sessions.end_edit()
    assert not window.viewport.context.IsDisplayed(grid) and document.history.count == history+1
    assert session.object_id not in document.objects


def test_rectangle_starts_on_press_previews_before_release_and_commits_one_batch(system):
    window, _, model, _ = system
    session = begin(system)
    choose(system, "rectangle")
    vp = window.viewport
    start, end = screen(system, (-30, -25)), screen(system, (30, 25))
    history = session.document.history.count
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=start)
    assert len(session.draft.anchors) == 1 and session.state == "drawing"
    mouse_move(system, end)
    preview = [entity for entity, _ in session.renderer.entities.values() if type(entity) is model.Segment]
    assert len(preview) == 4 and not entities(system) and session.document.history.count == history
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=end)
    assert len(entities(system)) == 4 and session.document.history.count == history+1
    saved = entities(system)
    assert saved[0].end == saved[1].start and saved[3].end == saved[0].start
    session.document.history.undo()
    assert not entities(system)
    session.document.history.redo()
    assert entities(system) == saved


def test_rectangle_focus_cancel_drops_detached_drag_and_never_commits_on_late_release(system):
    window = system[0]
    session = begin(system)
    choose(system, "rectangle")
    vp = window.viewport
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=screen(system, (-30, -20)))
    mouse_move(system, screen(system, (30, 20)))
    QApplication.sendEvent(vp, QFocusEvent(QEvent.Type.FocusOut))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=screen(system, (30, 20)))
    assert not entities(system) and not session.draft.anchors
    assert session.snap_renderer.marker is None


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_inference_guides_toggle_and_solidworks_ribbon_capture(system, theme):
    window, _, model, _ = system
    window.apply_preferences(replace(window.preferences, theme=theme))
    session = begin(system)
    choose(system, "line")
    click(system, (-30, -20))
    move(system, (30, -19.8))
    assert session.snap_result.kind == "horizontal" and session.snap_renderer.guide is not None
    assert session.draft.current.v == session.draft.anchors[0].v
    assert window.command_registry.execute("cadastrophe.sketch.snap")
    assert not session.snap_settings.enabled
    from plugins.sketch.ribbon import GROUPS, PLACEHOLDERS
    page = window.command_manager.widget(1).widget()
    groups = [page.layout().itemAt(i).widget() for i in range(page.layout().count()-1)]
    assert [group.accessibleName() for group in groups] == [title for title, _ in GROUPS]
    for key, _, _ in PLACEHOLDERS:
        command = window.command_registry.command("cadastrophe.sketch."+key)
        if key in {"dimension", "relations", "repair", "spline", "ellipse", "slot", "polygon", "fillet", "chamfer",
                   "point", "text", "trim", "offset", "mirror", "linear_pattern", "circular_pattern"}:
            assert command.implemented and command.enabled
        else:
            assert not command.implemented and not command.enabled
    window.command_manager.setCurrentIndex(1)
    QApplication.processEvents()
    assert window.grab().save(str(ROOT / "artifacts" / f"e5-ribbon-{theme}.png"))
    assert window.viewport.view.Dump(str(ROOT / "artifacts" / f"e5-inference-{theme}.png"))
    scale = str(window.viewport.devicePixelRatioF()).replace(".", "_")
    assert window.grab().save(str(ROOT / "artifacts" / f"e5-ribbon-{theme}-{scale}.png"))
    assert window.viewport.view.Dump(str(ROOT / "artifacts" / f"e5-inference-{theme}-{scale}.png"))


@pytest.mark.parametrize("version", [1, 2, 3])
def test_preferences_current_schema_and_old_version_rejection(tmp_path, version):
    path = tmp_path / "prefs.json"
    data = asdict(replace(Preferences(), theme="dark", length_unit="cm"))
    if version < 3:
        data = {key: value for key, value in data.items() if not key.startswith("sketch_")}
    if version == 1:
        del data["autosave_enabled"], data["autosave_interval_minutes"]
    path.write_text(json.dumps(dict(schema_version=version, preferences=data, layout={})), encoding="utf-8")
    store = PreferenceStore(path)
    preferences, layout = store.load()
    if version < 3:
        assert store.error and not store.writable
        assert preferences == Preferences()
        return
    assert not store.error and preferences.theme == "dark" and preferences.length_unit == "cm"
    assert preferences.sketch_snapping and not preferences.sketch_grid
    chosen = replace(preferences, sketch_snapping=False, sketch_inference=False, sketch_grid=True, sketch_grid_step=2.5)
    store.save(chosen, layout)
    assert PreferenceStore(path).load()[0] == chosen and json.loads(path.read_text())["schema_version"] == 3
