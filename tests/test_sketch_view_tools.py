"""Native gestures for dimensions, relation glyphs, body references and empty edits."""
from importlib import import_module
from pathlib import Path
from dataclasses import replace
from math import hypot

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox, QToolButton, QApplication

from test_sketch_mode import system, plane
from test_sketch_drawing_ui import begin, choose, click, move, screen
from test_sketch_editing_ui import mouse_move


def accept_value(session, value):
    tools = session.view_tools
    assert tools.dialog is not None, (session.error, tools.refs, tools.dimension_state.phase)
    tools.value_editor.set_internal_value(value)
    buttons = tools.dialog.findChild(QDialogButtonBox)
    QTest.mouseClick(buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert tools.dialog is None, session.error


@pytest.mark.parametrize('name', ['XY', 'XZ', 'YZ'])
def test_origin_is_a_native_dimension_reference_and_stays_fixed(system, name):
    session = begin(system, name)
    window, commands, model, _ = system
    point = model.Point(model.Vec2(20., 12.))
    commands.add_entity(session.document, session.object_id, point)
    choose(system, 'dimension')
    click(system, (0., 0.))
    origin = session.view_tools.refs[0]
    assert origin.entity_id == session.object_id and origin.part == 'position'
    click(system, (20., 12.))
    move(system, (12., -12.))
    click(system, (12., -12.))
    accept_value(session, 35.)
    sketch = session.document.objects[session.object_id].sketch
    assert sketch.origin.position == model.Vec2(0., 0.)
    assert any(origin in constraint.refs and constraint.value == 35. for constraint in sketch.constraints)
    assert sketch.entities[0].position.u == pytest.approx(35., abs=1e-6)
    session.document.history.undo()
    assert session.document.objects[session.object_id].sketch.entities[0].position == point.position


def test_new_empty_sketch_is_removed_on_exit_but_existing_empty_sketch_is_kept(system):
    session = begin(system)
    doc, key = session.document, session.object_id
    assert system[0].command_registry.execute("cadastrophe.sketch.finish")
    assert key not in doc.objects
    doc.history.undo()
    assert key in doc.objects
    system[0].tree_selection_changed([key])
    assert system[0].command_registry.execute("cadastrophe.sketch.edit")
    assert system[0].command_registry.execute("cadastrophe.sketch.finish")
    assert key in doc.objects


def test_abandoned_drawing_anchor_does_not_keep_a_new_empty_sketch(system):
    session = begin(system)
    doc, key = session.document, session.object_id
    choose(system, "line")
    click(system, (0, 0))
    assert session.draft.anchors and not doc.objects[key].sketch.entities
    choose(system, "finish")
    assert key not in doc.objects and not system[0].viewport.selection.suspended


@pytest.mark.parametrize("name", ["XY", "XZ", "YZ"])
def test_viewport_smart_dimension_place_edit_drag_undo_and_native_objects(system, name):
    session = begin(system, name)
    window, commands, model, editor = system
    line = model.Segment(model.Vec2(-25, 0), model.Vec2(15, 0))
    commands.add_entity(session.document, session.object_id, line)
    session.document.history.clear()
    choose(system, "dimension")
    assert session.view_tools.mode == "dimension"
    click(system, (-5, 0))
    move(system, (-5, -8))
    assert session.view_tools.preview.items and not session.document.objects[session.object_id].sketch.constraints
    assert session.document.history.count == 0
    click(system, (-5, -8))
    accept_value(session, 60)
    changed = session.document.objects[session.object_id].sketch
    c = changed.constraints[0]
    assert c.kind == "length" and c.label_position is not None
    assert hypot(changed.entities[0].end.u-changed.entities[0].start.u,
                 changed.entities[0].end.v-changed.entities[0].start.v) == pytest.approx(60, abs=1e-7)
    from OCC.Core.PrsDim import PrsDim_LengthDimension
    annotation = session.surface.presentation.annotations.items[c.id][1]
    assert isinstance(annotation, PrsDim_LengthDimension) and annotation.IsValid()
    assert session.document.history.count == 1
    session.document.history.undo()
    assert not session.document.objects[session.object_id].sketch.constraints
    session.document.history.redo()
    assert session.document.objects[session.object_id].sketch == changed
    choose(system, "select")
    pos = annotation.GetTextPosition()
    from cadastrophe.geometry import Vec3
    uv = session.workplane.to_local(Vec3(*pos.Coord()))
    QTest.mouseDClick(window.viewport, Qt.MouseButton.LeftButton, pos=screen(system, uv[:2]))
    assert session.view_tools.dialog is not None
    accept_value(session, 75)
    assert session.document.objects[session.object_id].sketch.constraints[0].value == 75
    annotation = session.surface.presentation.annotations.items[c.id][1]
    uv = session.workplane.to_local(Vec3(*annotation.GetTextPosition().Coord()))
    start, end = screen(system, uv[:2]), screen(system, (-5, -25))
    original = session.document.objects[session.object_id].sketch
    QTest.mousePress(window.viewport, Qt.MouseButton.LeftButton, pos=start)
    assert session.view_tools.drag is not None
    mouse_move(system, end)
    assert session.document.objects[session.object_id].sketch == original
    QTest.mouseRelease(window.viewport, Qt.MouseButton.LeftButton, pos=end)
    after = session.document.objects[session.object_id].sketch
    assert after.constraints[0].label_position != original.constraints[0].label_position
    assert after.entities == original.entities
    session.document.history.undo()
    assert session.document.objects[session.object_id].sketch == original
    glyph = session.surface.presentation.annotations.items[c.id][1]
    uv = session.workplane.to_local(Vec3(*glyph.GetTextPosition().Coord()))
    QTest.mouseClick(window.viewport, Qt.MouseButton.LeftButton, pos=screen(system, uv[:2]))
    QTest.keyClick(window.viewport, Qt.Key.Key_Delete)
    result = session.document.objects[session.object_id].sketch
    assert not result.constraints and result.entities == original.entities
    session.document.history.undo()
    assert session.document.objects[session.object_id].sketch == original


def test_circle_uses_diameter_and_escape_cancels_placement_without_history(system):
    session = begin(system)
    _, commands, model, _ = system
    circle = model.Circle(model.Vec2(0, 0), 15)
    commands.add_entity(session.document, session.object_id, circle)
    session.document.history.clear()
    choose(system, "dimension")
    click(system, (15, 0))
    move(system, (28, 12))
    assert session.view_tools.preview.items
    QTest.keyClick(system[0].viewport, Qt.Key.Key_Escape)
    assert not session.view_tools.preview.items and session.view_tools.mode == "dimension"
    QTest.keyClick(system[0].viewport, Qt.Key.Key_Escape)
    assert session.view_tools.mode is None
    assert session.document.history.count == 0
    choose(system, "dimension")
    click(system, (15, 0))
    click(system, (28, 12))
    accept_value(session, 50)
    sketch = session.document.objects[session.object_id].sketch
    assert sketch.constraints[0].kind == "diameter"
    assert sketch.entities[0].radius == pytest.approx(25)


def test_contextual_relation_button_creates_native_green_glyph_and_one_undo(system):
    session = begin(system)
    _, commands, model, _ = system
    line = model.Segment(model.Vec2(-20, -10), model.Vec2(20, 0))
    commands.add_entity(session.document, session.object_id, line)
    session.document.history.clear()
    click(system, (0, -5))
    tools = session.view_tools
    assert tools.toolbar.isVisible() and tools.available() == ("horizontal", "vertical")
    button = next(b for b in tools.toolbar.findChildren(QToolButton) if b.accessibleName() == "Relation Horizontal")
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    sketch = session.document.objects[session.object_id].sketch
    assert sketch.entities[0].start.v == pytest.approx(sketch.entities[0].end.v, abs=1e-8)
    assert sketch.constraints[0].kind == "horizontal" and session.document.history.count == 1
    from OCC.Core.AIS import AIS_MultipleConnectedInteractive
    glyph = session.surface.presentation.annotations.items[sketch.constraints[0].id][1]
    assert isinstance(glyph, AIS_MultipleConnectedInteractive)
    second = model.Segment(model.Vec2(-20, 15), model.Vec2(20, 15))
    commands.add_entity(session.document, session.object_id, second)
    session.document.history.clear()
    choose(system, "dimension")
    click(system, (0, 15))
    assert len(tools.refs) == 2
    click(system, (0, 25))
    accept_value(session, 30)
    result = session.document.objects[session.object_id].sketch
    first, last = result.entities
    assert first.start.v == pytest.approx(first.end.v, abs=1e-7)
    assert last.start.v == pytest.approx(last.end.v, abs=1e-7)
    assert abs(first.start.v-last.start.v) == pytest.approx(30, abs=1e-7)
    assert {c.kind for c in result.constraints} == {"horizontal", "parallel", "line_distance"}
    assert session.document.history.count == 1


def test_contextual_toolbar_buttons_remain_visible_when_switching_lines(system):
    session = begin(system)
    _, commands, model, _ = system
    lines = (model.Segment(model.Vec2(-20,-10), model.Vec2(20,-10)),
             model.Segment(model.Vec2(-20,15), model.Vec2(20,15)))
    commands.add_entities(session.document, session.object_id, lines, title="Deux lignes")
    for position in ((0,-10),(0,15),(0,-10),(0,15)):
        click(system, position)
        QApplication.processEvents()
        tools = session.view_tools
        assert tools.toolbar.isVisible()
        buttons = [tools.row.itemAt(i).widget() for i in range(tools.row.count())]
        assert len(buttons) == 3
        assert all(button.isVisible() and button.width() > 10 for button in buttons)
        assert tools.toolbar.width() >= sum(button.width() for button in buttons)
    horizontal = next(b for b in buttons if b.accessibleName() == "Relation Horizontal")
    QTest.mouseClick(horizontal, Qt.MouseButton.LeftButton)
    assert session.document.objects[session.object_id].sketch.constraints[-1].refs[0].entity_id == lines[1].id


@pytest.mark.parametrize("edge", [False, True, "overlap"])
@pytest.mark.parametrize("height", [880, 920])
def test_select_solid_vertex_or_edge_for_persistent_projected_relation(system, edge, height):
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from cadastrophe.plugin_api import SceneObject, ObjectType
    window, commands, model, _ = system
    window.resize(window.width(), height)
    QApplication.processEvents()
    overlap, edge = edge == "overlap", edge is True
    doc = window.viewport.document
    doc.remove_objects([obj.id for obj in doc.objects.values() if obj.kind is ObjectType.BODY])
    body = SceneObject("Bloc", BRepPrimAPI_MakeBox(20, 20, 10).Shape(), (.6, .6, .65))
    doc.add(body)
    session = begin(system)
    source = model.Point(model.Vec2(20, 20) if overlap else model.Vec2(-10, -10))
    commands.add_entity(doc, session.object_id, source)
    doc.history.clear()
    choose(system, "relations")
    click(system, (source.position.u, source.position.v))
    modifiers = Qt.KeyboardModifier.ControlModifier | (Qt.KeyboardModifier.AltModifier if overlap else Qt.KeyboardModifier.NoModifier)
    QTest.mouseClick(window.viewport, Qt.MouseButton.LeftButton, modifiers,
                     screen(system, (10, 0) if edge else (20, 20)))
    tools = session.view_tools
    assert len(tools.refs) == 2 and tools.external
    assert len(doc.objects[session.object_id].sketch.external) == 0 and doc.history.count == 0
    kind = "point_on_line" if edge else "coincident"
    assert kind in tools.available()
    tools.add_relation(kind)
    assert not session.error, session.error
    sketch = doc.objects[session.object_id].sketch
    assert len(sketch.external) == 1 and sketch.external[0].body_id == body.id
    assert sketch.external[0].topology == ("Edge" if edge else "Vertex")
    assert sketch.entities[0].position.v == pytest.approx(0 if edge else 20, abs=1e-7)
    assert doc.objects[body.id] is body and doc.history.count == 1
    doc.history.undo()
    assert not doc.objects[session.object_id].sketch.external
    assert doc.objects[session.object_id].sketch.entities == (source,)


@pytest.mark.parametrize("alignment,kind,value", [("auto", "horizontal_distance", 30), ("vertical", "vertical_distance", 25), ("aligned", "distance", 40)])
def test_smart_dimension_two_points_uses_chosen_orientation(system, alignment, kind, value):
    session = begin(system)
    _, commands, model, _ = system
    points = (model.Point(model.Vec2(-10, 0)), model.Point(model.Vec2(10, 10)))
    commands.add_entities(session.document, session.object_id, points, title="Points")
    session.document.history.clear()
    choose(system, "dimension")
    panel = session.constraint_panel
    panel.axis.setCurrentIndex(panel.axis.findData(alignment))
    click(system, (-10, 0))
    click(system, (10, 10))
    move(system, (0, -20))
    click(system, (0, -20))
    accept_value(session, value)
    sketch = session.document.objects[session.object_id].sketch
    assert sketch.constraints[0].kind == kind
    assert sketch.constraints[0].value == value and session.document.history.count == 1


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_solidworks_style_panel_and_native_dimension_visuals(system, theme):
    from cadastrophe.plugin_api import ObjectType
    window, commands, model, editor = system
    window.apply_preferences(replace(window.preferences, theme=theme))
    doc = window.viewport.document
    doc.remove_objects([obj.id for obj in doc.objects.values() if obj.kind is ObjectType.BODY])
    session = begin(system)
    c = import_module(editor.__package__ + ".constraints")
    a,b,d,e = model.Vec2(-30,-20), model.Vec2(30,-20), model.Vec2(30,20), model.Vec2(-30,20)
    edges = (model.Segment(a,b), model.Segment(b,d), model.Segment(d,e), model.Segment(e,a))
    commands.add_entities(doc, session.object_id, edges, title="Rectangle")
    constraints = [c.Constraint("horizontal" if i%2 == 0 else "vertical", (c.Reference(edge.id),)) for i, edge in enumerate(edges)]
    constraints += [c.Constraint("length", (c.Reference(edges[0].id),), 60, label_position=model.Vec2(0,-32)),
                    c.Constraint("length", (c.Reference(edges[1].id),), 40, label_position=model.Vec2(42,0))]
    commands.set_constraints(doc, session.object_id, constraints, validate_sketch=session.validate_sketch)
    session.interaction.set_selected((edges[0].id,))
    choose(system, "relations")
    QTest.keyClick(window.viewport, Qt.Key.Key_F)
    window.viewport.view.Camera().SetScale(window.viewport.view.Camera().Scale()*1.45)
    window.viewport.invalidate_view()
    QApplication.processEvents()
    QTest.qWait(20)
    p = session.constraint_panel
    assert p.selected_list.count() == 1
    buttons = p.context_widget.findChildren(QToolButton)
    assert len(buttons) == 1 and buttons[0].accessibleName() == "Ajouter Vertical"
    root = Path(__file__).resolve().parents[1] / "artifacts"
    assert p.grab().save(str(root / f"sketch-viewport-panel-{theme}.png"))
    assert window.grab().save(str(root / f"sketch-viewport-shell-{theme}.png"))
    assert window.viewport.view.Dump(str(root / f"sketch-viewport-dimensions-{theme}.png"))
