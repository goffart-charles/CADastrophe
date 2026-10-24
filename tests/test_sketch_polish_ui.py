"""Native sketch colors and independent orbit axes, after E5 and before E6."""
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_sRGB

from test_sketch_mode import system, create, enter, camera_state
from test_sketch_drawing_ui import begin, click, move
from test_sketch_face_navigation_ui import navigation_move


def rgb(ais):
    color = Quantity_Color()
    ais.Color(color)
    return color.Values(Quantity_TOC_sRGB)


def test_edit_entry_clears_native_selected_sketch_curve(system):
    from test_sketch_interaction_polish import clean_scene
    clean_scene(system)
    window, commands, model, _ = system
    session = begin(system)
    segment = model.Segment(model.Vec2(-30, -20), model.Vec2(30, -20))
    commands.add_entity(session.document, session.object_id, segment)
    key = session.object_id
    assert window.command_registry.execute("cadastrophe.sketch.finish")
    vp = window.viewport
    for obj in tuple(vp.document.objects.values()):
        if obj.plane is not None:
            vp.document.set_visible(obj.id, False)
    x, y = vp.view.Convert(0., -20., 0.)
    point = QPoint(round(x/vp.devicePixelRatioF()), round(y/vp.devicePixelRatioF()))
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=point)
    presenter = vp.document_adapter.features[key][1]
    ais = presenter.entities[segment.id][1]
    assert vp.document.selected_ids == (key,) and vp.context.NbSelected() == 1
    vp.context.InitSelected()
    assert vp.context.SelectedInteractive() == ais
    assert window.command_registry.execute("cadastrophe.sketch.edit")
    assert not vp.context.IsSelected(ais)
    assert not vp.context.IsHilighted(ais)
    assert window.tool_sessions.editor.interaction.selected == ()
    expected = (0., 0., 1.) if window.preferences.theme == "light" else (.42, .62, 1.)
    assert rgb(ais) == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize("plane", ("XY", "XZ", "YZ"))
def test_spline_mouse_preview_commit_undo_and_reload(system, tmp_path, plane):
    from test_sketch_drawing_ui import choose
    from PySide6.QtCore import Qt
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    session = begin(system, plane)
    window, _, model, _ = system
    doc, key = session.document, session.object_id
    count = doc.history.count
    choose(system, "spline")
    click(system, (-20, -15))
    for point in ((-10, 20), (10, -10), (25, 15)):
        move(system, point)
        assert session.renderer.entities and not session.error
        click(system, point)
    QTest.keyClick(window.viewport, Qt.Key.Key_Return)
    assert not session.error
    entity, = doc.objects[key].sketch.entities
    assert type(entity) is model.Spline and not entity.weights
    from importlib import import_module
    native = import_module(model.__package__+".presentation")
    assert BRepCheck_Analyzer(native.entity_shape(doc.objects[key].sketch, entity)).IsValid()
    assert doc.history.count == count+1
    doc.history.undo()
    assert not doc.objects[key].sketch.entities
    doc.history.redo()
    snapshot = doc.objects[key].sketch
    path = tmp_path/"spline.cada"
    assert window.save_document(path=path) and window.close_document()
    assert window.open_document(path).objects[key].sketch == snapshot


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_native_sketch_colors_for_inactive_editing_selected_hover_and_theme_refresh(system, theme):
    window = system[0]
    vp = window.viewport
    window.apply_preferences(replace(window.preferences, theme=theme))
    for obj in tuple(vp.document.objects.values()):
        if obj.plane is None and obj.shape is not None:
            vp.document.set_visible(obj.id, False)
    obj = create(system, populated=True)
    presenter = vp.document_adapter.features[obj.id][1]
    segment = obj.sketch.entities[1]
    ais = presenter.entities[segment.id][1]
    palette = {"light": {"editing": (0, 0, 1), "inactive": (.5, .5, .5), "selected": (0, .65, 1)},
               "dark": {"editing": (.42, .62, 1), "inactive": (.66, .70, .76), "selected": (.1, .8, 1)}}
    assert rgb(ais) == pytest.approx(palette[theme]["inactive"], abs=1e-6)
    previous, count = camera_state(vp), vp.document.history.count
    enter(system, obj)
    previous = camera_state(vp)
    assert rgb(ais) == pytest.approx(palette[theme]["editing"], abs=1e-6)
    click(system, (0, -20))
    assert window.tool_sessions.editor.interaction.selected == (segment.id,)
    assert rgb(ais) == pytest.approx(palette[theme]["selected"], abs=1e-6)
    move(system, (25, 15))
    circle_ais = presenter.entities[obj.sketch.entities[2].id][1]
    assert vp.context.HasDetected() and vp.context.DetectedInteractive() == circle_ais
    assert circle_ais.DynamicHilightAttributes().Color().Values(Quantity_TOC_sRGB) == pytest.approx((1, .5, 0), abs=1e-6)
    root = Path(__file__).resolve().parents[1] / "artifacts"
    vp.view.Redraw()
    assert vp.view.Dump(str(root / f"sketch-colors-{theme}.png"))
    assert window.command_registry.execute("cadastrophe.sketch.finish")
    assert camera_state(vp) == previous and vp.document.history.count == count
    window.tree_selection_changed([])  # Exit restores the selected sketch from the tree.
    other = "dark" if theme == "light" else "light"
    window.apply_preferences(replace(window.preferences, theme=other))
    # The AIS object is reused; only its native style changes at theme switch.
    assert presenter.entities[segment.id][1] == ais
    assert rgb(ais) == pytest.approx(palette[other]["inactive"], abs=1e-6)
    assert vp.document.history.count == count


@pytest.mark.parametrize("plane", ["XY", "XZ", "YZ"])
@pytest.mark.parametrize("delta", [(65, 0), (0, 65)])
def test_each_mouse_axis_tilts_sketch_from_normal_and_ctrl8_restores_alignment(system, plane, delta):
    window = system[0]
    vp = window.viewport
    previous = camera_state(vp)
    session = begin(system, plane)
    frame = session.workplane
    count = session.document.history.count
    direction = vp.view.Camera().Direction()
    assert direction.Coord() == pytest.approx((frame.normal*-1).tuple())
    start = QPoint(vp.width()//2, vp.height()//2)
    end = start + QPoint(*delta)
    QTest.mousePress(vp, Qt.MouseButton.MiddleButton, pos=start)
    navigation_move(vp, end)
    assert vp.view.Camera().Direction().Dot(direction) < .999
    QTest.mouseRelease(vp, Qt.MouseButton.MiddleButton, pos=end)
    assert window.command_registry.execute("view.normal_to_plane")
    assert vp.view.Camera().Direction().Coord() == pytest.approx((frame.normal*-1).tuple())
    assert vp.view.Camera().Center().Coord() == pytest.approx(frame.origin.tuple())
    assert vp.view.Camera().Up().Coord() == pytest.approx(frame.v.tuple())
    assert session.document.history.count == count
    previous = camera_state(vp)
    assert window.command_registry.execute("cadastrophe.sketch.finish")
    assert camera_state(vp) == previous
