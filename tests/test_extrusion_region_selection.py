"""Native 3D clicks select shared-edge regions; no region list is exposed."""
from dataclasses import replace
from importlib import import_module
from pathlib import Path

import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from PySide6.QtWidgets import QListWidget
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from cadastrophe.core.feature_objects import BodyObject
from cadastrophe.geometry import Ray
from cadastrophe.core.persistence import load_document, save_document
from test_extrusion_tool import extrusion_system, start
from test_extrusion_interaction import click_world, orient
from test_boss_interaction import hover_world
from test_primitives import volume


def source(system, kind):
    w, commands, m = system
    doc = w.viewport.document
    plane = next(obj for obj in doc.objects.values() if obj.plane and obj.plane.name == "XY")
    sketch = commands.create_sketch(doc, plane.id)
    def rectangle(x, y, width, height):
        points = tuple(m.Vec2(*p) for p in ((x,y),(x+width,y),(x+width,y+height),(x,y+height)))
        return tuple(m.Segment(p, points[(i+1)%4]) for i,p in enumerate(points))
    if kind == "divider":
        entities = (*rectangle(0, 0, 40, 20), m.Segment(m.Vec2(20, 0), m.Vec2(20, 20)))
    elif kind == "partial":
        entities = (*rectangle(0, 0, 20, 20), *rectangle(20, 5, 20, 10))
    else:
        entities = (*rectangle(0, 0, 20, 10), *rectangle(20, 0, 20, 10))
        if kind == "single":
            entities = entities[:-1]
    commands.add_entities(doc, sketch.id, entities, title="Rectangles adjacents")
    doc.history.clear()
    return doc.objects[sketch.id]


@pytest.mark.parametrize("kind", ["double", "single", "divider", "partial"])
def test_shared_rectangles_are_hovered_clicked_toggled_and_committed(extrusion_system, tmp_path, monkeypatch, kind):
    w, _, _ = extrusion_system
    sketch = source(extrusion_system, kind)
    doc, frame = w.viewport.document, sketch.sketch.support.frame
    orient(w, frame)
    before = dict(doc.objects)
    tool = start(w, sketch)
    assert not tool.editor.findChildren(QListWidget)
    assert len(tool.regions) == 2, tool._profile_error
    assert not tool.selected_regions
    # Test the mathematical shared boundary directly: a rasterized pixel can
    # project just to either side of the line, so use its exact screen ray.
    with monkeypatch.context() as patch:
        patch.setattr(tool.surface, "screen_ray", lambda *_: Ray(frame.to_world(20, 7)+frame.normal*100, frame.normal*-1))
        assert tool._region_at(0, 0) is None
    hover_world(w, frame, 10, 7)
    left = tool._hover_region
    assert left is not None
    click_world(w, frame, 10, 7)
    assert tool.selected_regions == (left,)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    left_area = 400 if kind in ("divider", "partial") else 200
    right_area = 400 if kind == "divider" else 200
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(left_area*20)
    hover_world(w, frame, 30, 7)
    right = tool._hover_region
    assert right is not None and right != left
    click_world(w, frame, 30, 7)
    assert tool.selected_regions == (left, right)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx((left_area+right_area)*20)
    click_world(w, frame, 10, 7)
    assert tool.selected_regions == (right,)
    w.tool_sessions.update_preview()
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(right_area*20)
    assert dict(doc.objects) == before
    if kind == "double":
        root = Path("artifacts")
        root.mkdir(exist_ok=True)
        assert w.tool_host.panel.grab().save(str(root/"shared-regions-panel.png"))
        w.viewport.view.Redraw()
        assert w.viewport.view.Dump(str(root/"shared-regions-preview.png"))
    assert w.tool_sessions.commit()
    bodies = [obj for obj in doc.objects.values() if isinstance(obj, BodyObject)]
    assert len(bodies) == 1 and volume(bodies[0].shape) == pytest.approx(right_area*20)
    assert BRepCheck_Analyzer(bodies[0].shape).IsValid()
    feature = doc.objects[bodies[0].features[0]]
    assert feature.parameters.contours == (right,)
    assert doc.objects[sketch.id].sketch == sketch.sketch and doc.history.count == 1
    doc.history.undo()
    assert set(doc.objects) == set(before)
    doc.history.redo()
    path = tmp_path / "shared-regions.cada"
    save_document(doc, path, registry=w.object_types)
    loaded = load_document(path, registry=w.object_types)
    assert loaded.objects[feature.id].parameters.contours == (right,)
    assert volume(loaded.objects[bodies[0].id].shape) == pytest.approx(right_area*20)
    # The selected cell keeps its identity when all horizontal dimensions grow.
    original = doc.objects[sketch.id]
    entities = tuple(replace(e, start=replace(e.start, u=e.start.u*2), end=replace(e.end, u=e.end.u*2))
                     for e in original.sketch.entities)
    doc.replace_feature(replace(original, sketch=replace(original.sketch, entities=entities)))
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert volume(doc.objects[bodies[0].id].shape) == pytest.approx(2*right_area*20)


def test_select_both_shared_regions_and_cancel_or_commit_is_atomic(extrusion_system):
    w, _, _ = extrusion_system
    sketch = source(extrusion_system, "double")
    doc, frame = w.viewport.document, sketch.sketch.support.frame
    orient(w, frame)
    before = dict(doc.objects), doc.history.index
    for commit in (False, True):
        tool = start(w, sketch)
        click_world(w, frame, 10, 5)
        click_world(w, frame, 30, 5)
        assert len(tool.selected_regions) == 2
        if not commit:
            assert w.tool_sessions.cancel()
            assert (dict(doc.objects), doc.history.index) == before
        else:
            assert w.tool_sessions.commit()
    bodies = [obj for obj in doc.objects.values() if isinstance(obj, BodyObject)]
    assert len(bodies) == 2 and doc.history.count == 1
    assert sum(volume(body.shape) for body in bodies) == pytest.approx(8000)
    doc.history.undo()
    assert set(doc.objects) == set(before[0])


def test_draw_two_adjacent_rectangles_then_select_in_3d_without_leaving_sketch_first(extrusion_system):
    from test_sketch_drawing_ui import begin, choose, screen
    from test_sketch_editing_ui import mouse_move
    w, commands, model = extrusion_system
    editor = import_module(w.plugins._record("cadastrophe.sketch").namespace+".editor")
    system = w, commands, model, editor
    session = begin(system)
    key, frame = session.object_id, session.workplane
    choose(system, "rectangle")
    for first, last in (((-20,-10),(0,10)), ((0,-10),(20,10))):
        QTest.mousePress(w.viewport, Qt.MouseButton.LeftButton, pos=screen(system, first))
        mouse_move(system, screen(system, last))
        QTest.mouseRelease(w.viewport, Qt.MouseButton.LeftButton, pos=screen(system, last))
    doc = w.viewport.document
    assert len(doc.objects[key].sketch.entities) == 8
    original = doc.objects[key].sketch
    # Snapping may adjust the drawn corners. Pick the interiors of the actual
    # rectangles, rather than assuming the unsnapped mouse coordinates survived.
    def center(index):
        first, opposite = original.entities[index].start, original.entities[index+2].start
        return (first.u+opposite.u)/2, (first.v+opposite.v)/2
    left_center, right_center = center(0), center(4)
    assert w.command_registry.execute("modeling.extrude")
    tool = w.tool_sessions.tool
    assert len(tool.regions) == 2, tool._profile_error
    assert not tool.editor.findChildren(QListWidget)
    QTest.qWait(100)  # Allow the native viewport to resize after the panel transition.
    click_world(w, frame, *left_center)
    assert len(tool.selected_regions) == 1, (tool.target, tool._pick_error)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    click_world(w, frame, *right_center)
    assert len(tool.selected_regions) == 2
    assert w.tool_sessions.commit()
    assert doc.objects[key].sketch == original
    assert len([obj for obj in doc.objects.values() if isinstance(obj, BodyObject)]) == 2


@pytest.mark.parametrize("kind,points", [
    ("cross", ((-5, -5), (5, -5), (-5, 5), (5, 5))),
    ("overlap_rectangles", ((5, 5), (15, 7), (25, 10))),
    ("overlap_circles", ((-5, 0), (5, 0), (15, 0))),
    ("circle_diameter", ((0, 5), (0, -5))),
    ("shared_arc", ((0, 5), (0, 15))),
    ("nested", ((0, 0), (15, 0), (25, 0))),
])
def test_general_cells_can_be_clicked_toggled_committed_and_reopened(extrusion_system, tmp_path, kind, points):
    from test_general_profile_regions import network
    w, commands, m = extrusion_system
    doc = w.viewport.document
    plane = next(o for o in doc.objects.values() if o.plane and o.plane.name == "XY")
    sketch = commands.create_sketch(doc, plane.id)
    curves, areas = network(kind)
    def convert(e):
        def point(p):
            return m.Vec2(p.u, p.v)
        if e.kind == "segment":
            return m.Segment(point(e.start), point(e.end), id=e.id)
        if e.kind == "arc":
            return m.Arc(point(e.center), e.radius, e.start_angle, e.sweep, id=e.id)
        return m.Circle(point(e.center), e.radius, id=e.id)
    commands.add_entities(doc, sketch.id, tuple(map(convert, curves)), title="Réseau de zones fermées")
    doc.history.clear()
    original = doc.objects[sketch.id]
    frame = original.sketch.support.frame
    orient(w, frame)
    tool = start(w, original)
    assert not tool.editor.findChildren(QListWidget)
    assert len(tool.regions) == len(points), tool._profile_error
    faces = tool.profiles.to_region_faces(doc, sketch.id)
    selected = set()
    for point in points:
        hover_world(w, frame, *point)
        key = tool._hover_region
        assert key is not None and key not in selected, (kind, point, tool._pick_error)
        click_world(w, frame, *point)
        assert tool.selected_regions == (key,)
        w.tool_sessions.update_preview()
        assert w.tool_sessions.can_commit, w.tool_sessions.message
        area = next(r.area for r in faces.regions if r.id == key)
        assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(area*20)
        click_world(w, frame, *point)
        assert not tool.selected_regions
        selected.add(key)
    for point in points:
        click_world(w, frame, *point)
    assert set(tool.selected_regions) == selected
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(sum(areas)*20)
    if kind == "overlap_circles":
        assert w.viewport.view.Dump(str(Path("artifacts")/"general-regions-circles.png"))
    assert w.tool_sessions.commit()
    assert doc.objects[sketch.id].sketch == original.sketch
    bodies = [o for o in doc.objects.values() if isinstance(o, BodyObject)]
    assert len(bodies) == len(points) and doc.history.count == 1
    for body in bodies:
        assert BRepCheck_Analyzer(body.shape, True).IsValid()
    path = tmp_path / "general-regions.cada"
    save_document(doc, path, registry=w.object_types)
    loaded = load_document(path, registry=w.object_types)
    assert sum(volume(loaded.objects[b.id].shape) for b in bodies) == pytest.approx(sum(areas)*20)
    doc.history.undo()
    assert not any(isinstance(o, BodyObject) for o in doc.objects.values())
    doc.history.redo()
    assert sum(volume(doc.objects[b.id].shape) for b in bodies) == pytest.approx(sum(areas)*20)
    def resize(e):
        def move(p):
            return m.Vec2(p.u*2, p.v*2)
        if e.kind == "segment":
            return replace(e, start=move(e.start), end=move(e.end))
        return replace(e, center=move(e.center), radius=e.radius*2)
    resized = replace(original, sketch=replace(original.sketch, entities=tuple(map(resize, original.sketch.entities))))
    doc.replace_feature(resized)
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert sum(volume(doc.objects[b.id].shape) for b in bodies) == pytest.approx(sum(areas)*80)
