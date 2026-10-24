"""X6 interactive cuts, file restoration and opaque additive preview regression."""
from dataclasses import replace
from math import pi
from pathlib import Path

import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.gp import gp_Pnt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest

from cadastrophe.core.body_history import body_result, result_at
from cadastrophe.core.feature_model import Operation, ResultStatus, BodyStateRef
from cadastrophe.core.feature_objects import BodyObject
from cadastrophe.core.persistence import save_document, load_document
from cadastrophe.document import SceneObject
from cadastrophe.geometry import Vec3, WorkPlane
from cadastrophe.presentation import PresentationSurface
from test_extrusion_tool import extrusion_system, rectangle, start
from test_boss_tool import base_body, face_sketch
from test_extrusion_interaction import orient
from test_primitives import volume, pose


def start_cut(w, sketch=None):
    if sketch is not None:
        w.tree_selection_changed([sketch.id])
    assert w.command_registry.execute("modeling.cut_extrude")
    assert w.tool_sessions.tool is not None and w.tool_sessions.tool.is_cut
    return w.tool_sessions.tool


def pixel_at(vp, point, path):
    vp.view.Redraw()
    assert vp.view.Dump(str(path))
    image = QImage(str(path))
    x, y = PresentationSurface(vp).project(point)
    return image.pixelColor(round(x), round(y)).getRgb()


def test_additive_preview_cannot_show_through_its_own_opaque_base(extrusion_system, tmp_path):
    w, _, _ = extrusion_system
    parent = base_body(extrusion_system)
    sketch = face_sketch(extrusion_system, parent)
    vp, doc = w.viewport, w.viewport.document
    frame = WorkPlane("Vue dessous", Vec3(50, 30, 0), Vec3(1, 0, 0), Vec3(0, -1, 0))
    orient(w, frame)
    pixel_at(vp, Vec3(50, 30, 0), tmp_path / "base-before.png")
    tool = start(w, sketch)
    assert w.tool_sessions.can_commit
    assert not vp.context.IsDisplayed(vp.presentations[parent.id])
    assert parent.id in w.tool_host.overlay.hidden
    assert w.tool_host.overlay.presentation.Transparency() == 0
    expected = pixel_at(vp, Vec3(50, 30, 0), tmp_path / "base-after.png")
    assert max(expected[:3])-min(expected[:3]) < 15  # Base remains gray, never yellow.
    tool.depth.setValue(80)
    w.tool_sessions.update_preview()
    assert pixel_at(vp, Vec3(50, 30, 0), tmp_path / "base-deeper.png") == expected
    assert doc.objects[parent.id].shape is parent.shape
    assert w.tool_sessions.cancel()


@pytest.mark.parametrize("static", [False, True])
@pytest.mark.parametrize("through", [False, True])
def test_cut_face_blind_and_through_all_atomic_history_and_reopening(extrusion_system, tmp_path, static, through):
    w, _, _ = extrusion_system
    doc, vp = w.viewport.document, w.viewport
    parent = base_body(extrusion_system, static)
    other = SceneObject("Autre corps", BRepPrimAPI_MakeBox(gp_Pnt(200, 0, 0), 10., 10., 10.).Shape(), (.2, .4, .7))
    doc.add(other)
    sketch = face_sketch(extrusion_system, parent)
    doc.history.clear()
    before_ids, camera = set(doc.objects), pose(vp)
    tool = start_cut(w, sketch)
    assert tool.body.value == parent.id and tool.reverse.isChecked()
    assert tool.depth.internal_value == 5
    if through:
        tool.condition.setCurrentIndex(1)
        tool.depth.lineEdit().setText("valeur ignorée car désactivée")
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    removed = 25*pi*(20 if through else 5)
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(120000-removed)
    assert not vp.context.IsDisplayed(vp.presentations[parent.id])
    assert vp.context.IsDisplayed(vp.presentations[other.id])
    assert doc.objects[parent.id] is parent and doc.history.count == 0
    assert pose(vp) == camera
    assert w.tool_host.overlay.presentation.Transparency() == 0
    if not static and through:
        vp.camera.normal_to(sketch.sketch.support.frame)
        assert w.command_registry.execute("view.fit")
        x, y = PresentationSurface(vp).project(Vec3(50, 30, 20))
        tool.hovered(x, y)
        assert tool._hover_region is not None
        assert all(ais.DisplayMode() == 0 for ais in tool.surface._feedback)
        vp.view.Redraw()
        Path("artifacts").mkdir(exist_ok=True)
        assert vp.view.Dump("artifacts/x6-cut-preview.png")
        hole = pixel_at(vp, Vec3(50, 30, 20), tmp_path / "hole.png")
        base = pixel_at(vp, Vec3(20, 30, 20), tmp_path / "surface.png")
        assert sum(hole[:3]) > sum(base[:3])+100  # Open hole, no filled selection sheet.
    assert w.tool_sessions.commit()
    body = doc.objects[parent.id]
    feature = doc.objects[body.features[-1]]
    assert feature.operation is Operation.CUT and feature.body_id == parent.id
    assert feature.parameters.end_condition == ("through_all" if through else "blind")
    assert feature.parameters.depth == (None if through else 5)
    assert feature.parameters.reversed and not feature.parameters.merge_result
    before_ref = BodyStateRef(parent.id, None if static else parent.features[-1])
    assert feature.source.supports == (before_ref,)
    assert result_at(doc, before_ref).shape is parent.shape
    assert body.name == parent.name and body.color == parent.color
    assert BRepCheck_Analyzer(body.shape).IsValid()
    assert volume(body.shape) == pytest.approx(120000-removed)
    assert doc.history.count == 1 and not doc.objects[sketch.id].visible
    assert doc.objects[other.id] is other
    for _ in range(3):
        doc.history.undo()
        assert set(doc.objects) == before_ids and doc.objects[parent.id].shape is parent.shape
        assert type(doc.objects[parent.id]) is type(parent)
        assert doc.objects[sketch.id].visible and doc.objects[other.id] is other
        doc.history.redo()
        assert doc.objects[parent.id].shape is body.shape
        assert result_at(doc, before_ref).shape is parent.shape
    path = tmp_path / "cut.cada"
    save_document(doc, path, registry=w.object_types)
    loaded = load_document(path, registry=w.object_types)
    assert not loaded.is_modified and loaded.history.count == 0
    assert body_result(loaded, parent.id).status is ResultStatus.READY, body_result(loaded, parent.id).message
    assert volume(loaded.objects[parent.id].shape) == pytest.approx(120000-removed)
    assert loaded.objects[feature.id].parameters == feature.parameters
    assert loaded.objects[parent.id].features == body.features
    assert volume(loaded.objects[other.id].shape) == pytest.approx(1000)


def test_cut_direction_failure_and_cancel_leave_original_intact(extrusion_system):
    w, _, _ = extrusion_system
    doc, vp = w.viewport.document, w.viewport
    parent = base_body(extrusion_system)
    sketch = face_sketch(extrusion_system, parent)
    w.tree_selection_changed([sketch.id])
    before = dict(doc.objects), doc.history.index, doc.selected_ids
    tool = start_cut(w, sketch)
    assert w.tool_sessions.can_commit
    tool.reverse.setChecked(False)
    w.tool_sessions.update_preview()
    assert not w.tool_sessions.can_commit and "matière" in w.tool_sessions.message
    assert not w.tool_sessions.commit()
    assert vp.context.IsDisplayed(vp.presentations[parent.id])
    tool.condition.setCurrentIndex(1)
    w.tool_sessions.update_preview()
    assert not w.tool_sessions.can_commit and "direction" in w.tool_sessions.message
    tool.reverse.setChecked(True)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit
    assert w.tool_sessions.cancel()
    assert (dict(doc.objects), doc.history.index, doc.selected_ids) == before
    assert vp.context.IsDisplayed(vp.presentations[parent.id])


def test_cut_requires_target_and_can_start_inside_sketch(extrusion_system):
    w, commands, m = extrusion_system
    doc = w.viewport.document
    parent = base_body(extrusion_system, static=True)
    other = SceneObject("Autre cible", BRepPrimAPI_MakeBox(gp_Pnt(200, 0, 0), 10., 10., 10.).Shape(), (.2, .4, .7))
    doc.add(other)
    plane = next(obj for obj in doc.objects.values() if obj.plane and obj.plane.name == "XY")
    sketch = commands.create_sketch(doc, plane.id)
    commands.add_entity(doc, sketch.id, m.Circle(m.Vec2(50, 30), 5))
    doc.history.clear()
    w.tree_selection_changed([sketch.id])
    assert w.command_registry.execute("cadastrophe.sketch.edit")
    assert w.command_registry.action("modeling.cut_extrude").isEnabled()
    tool = start_cut(w)
    assert not w.tool_sessions.editing and tool.source.value == sketch.id
    assert not w.tool_sessions.can_commit and "explicitement" in w.tool_sessions.message
    tool.choose_body(parent.id)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert w.tool_sessions.commit()
    assert doc.objects[other.id] is other
    assert volume(doc.objects[parent.id].shape) == pytest.approx(120000-125*pi)


def test_through_all_recomputes_from_modified_base_on_explicit_evaluation(extrusion_system):
    w, commands, m = extrusion_system
    doc = w.viewport.document
    parent = base_body(extrusion_system)
    plane = next(obj for obj in doc.objects.values() if obj.plane and obj.plane.name == "XY")
    sketch = commands.create_sketch(doc, plane.id)
    commands.add_entity(doc, sketch.id, m.Circle(m.Vec2(50, 30), 5))
    tool = start_cut(w, doc.objects[sketch.id])
    tool.condition.setCurrentIndex(1)
    assert w.tool_sessions.commit()
    initial = doc.objects[parent.features[0]]
    doc.update_construction((replace(initial, parameters=replace(initial.parameters, depth=80)),))
    assert doc.objects[parent.id].shape is None  # Rebuild is deferred until after the command.
    service = w.services.service("cadastrophe.modeling.extrusions")
    result = w.services.service("cadastrophe.modeling.history").evaluate(doc, parent.id, service.evaluator(doc))
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx((6000-25*pi)*80)
    assert doc.objects[doc.objects[parent.id].features[-1]].parameters.depth is None


@pytest.mark.parametrize("static", [False, True])
def test_cut_failed_publication_restores_previous_body(extrusion_system, monkeypatch, static):
    w, _, _ = extrusion_system
    doc = w.viewport.document
    parent = base_body(extrusion_system, static)
    sketch = face_sketch(extrusion_system, parent)
    doc.history.clear()
    start_cut(w, sketch)
    before = set(doc.objects)
    def fail(*_):
        raise ValueError("Échec après publication de la coupe")
    monkeypatch.setattr(doc, "set_visible", fail)
    assert not w.tool_sessions.commit()
    assert not w.tool_sessions.active and not doc.history.blocked
    assert set(doc.objects) == before and doc.objects[parent.id].shape is parent.shape
    assert type(doc.objects[parent.id]) is type(parent) and doc.objects[sketch.id].visible
    assert doc.history.count == 0
    assert w.viewport.context.IsDisplayed(w.viewport.presentations[parent.id])


def test_two_selected_regions_make_two_holes_in_one_cut(extrusion_system, tmp_path):
    w, commands, m = extrusion_system
    doc = w.viewport.document
    parent = base_body(extrusion_system)
    sketch = face_sketch(extrusion_system, parent)
    u, v, _ = sketch.sketch.support.frame.to_local(Vec3(75, 30, 20))
    commands.add_entity(doc, sketch.id, m.Circle(m.Vec2(u, v), 5))
    tool = start_cut(w, doc.objects[sketch.id])
    tool._set_regions(tuple(outer.id for outer, _ in tool.regions))
    tool.condition.setCurrentIndex(1)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    body = doc.objects[parent.id]
    feature = doc.objects[body.features[-1]]
    assert len(feature.parameters.contours) == 2
    assert volume(body.shape) == pytest.approx(120000-1000*pi)
    path = tmp_path / "two-holes.cada"
    save_document(doc, path, registry=w.object_types)
    loaded = load_document(path, registry=w.object_types)
    assert body_result(loaded, parent.id).status is ResultStatus.READY
    assert volume(loaded.objects[parent.id].shape) == pytest.approx(120000-1000*pi)


def test_split_cut_refused_and_depth_correction_recovers_in_same_session(extrusion_system):
    w, commands, m = extrusion_system
    doc = w.viewport.document
    parent = base_body(extrusion_system)
    sketch = face_sketch(extrusion_system, parent)
    commands.remove_entity(doc, sketch.id, doc.objects[sketch.id].sketch.entities[0].id)
    frame = sketch.sketch.support.frame
    corners = [frame.to_local(Vec3(x, y, 20)) for x, y in ((45, -10), (55, -10), (55, 70), (45, 70))]
    points = [m.Vec2(u, v) for u, v, _ in corners]
    commands.add_entities(doc, sketch.id, tuple(m.Segment(p, points[(i+1)%4]) for i, p in enumerate(points)), title="Profil rainure")
    doc.history.clear()
    tool = start_cut(w, doc.objects[sketch.id])
    tool.depth.setValue(30)
    w.tool_sessions.update_preview()
    assert not w.tool_sessions.can_commit and "plusieurs solides" in w.tool_sessions.message
    assert not w.tool_sessions.commit() and doc.objects[parent.id].shape is parent.shape
    assert doc.history.count == 0
    tool.depth.setValue(5)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert volume(doc.objects[parent.id].shape) == pytest.approx(117000)
