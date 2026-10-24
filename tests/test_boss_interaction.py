"""Boss interaction, with actual OCCT rendering on the private test desktop."""
from importlib import import_module
from math import pi
from pathlib import Path

import pytest
from OCC.Core.AIS import AIS_ColoredShape
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.Graphic3d import Graphic3d_ZLayerId_Default
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB, Quantity_TOC_sRGB
from OCC.Core.gp import gp_Pnt
from PySide6.QtCore import QPoint, QPointF, QEvent, Qt
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cadastrophe.core.body_history import body_result
from cadastrophe.core.feature_model import Operation, ResultStatus
from cadastrophe.core.feature_objects import BodyObject
from cadastrophe.core.persistence import load_document, save_document
from cadastrophe.document import SceneObject
from cadastrophe.geometry import Vec3
from cadastrophe.presentation import PresentationSurface
from test_extrusion_tool import extrusion_system, rectangle, start
from test_boss_tool import base_body, face_sketch
from test_extrusion_interaction import click_world, orient
from test_primitives import volume


def hover_world(w, frame, u, v):
    x, y = PresentationSurface(w.viewport).project(frame.to_world(u, v))
    point = QPointF(x, y)
    QApplication.sendEvent(w.viewport, QMouseEvent(QEvent.Type.MouseMove, point,
        QPointF(w.viewport.mapToGlobal(point.toPoint())), Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))


def test_multiple_regions_native_hover_toggle_and_atomic_roundtrip(extrusion_system, tmp_path):
    w, _, _ = extrusion_system
    sketch = rectangle(extrusion_system, hole=True, multiple=True)
    doc, frame = w.viewport.document, sketch.sketch.support.frame
    orient(w, frame)
    before = dict(doc.objects)
    tool = start(w, sketch)
    assert tool.merge.isChecked() and not tool.selected_regions
    hover_world(w, frame, 20, 20)
    assert tool._hover_region is not None and len(tool.surface._feedback) == 1
    assert dict(doc.objects) == before and doc.history.count == 0
    click_world(w, frame, 20, 20)
    hover_world(w, frame, 200, 0)
    assert len(tool.surface._feedback) == 2
    click_world(w, frame, 200, 0)
    assert len(tool.selected_regions) == 2
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert len(w.tool_host.overlay.presentations) == 2
    assert "2 nouveau" in tool.operation.text()
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(120000)
    click_world(w, frame, 200, 0)  # Toggle out, then back in, without Ctrl.
    assert len(tool.selected_regions) == 1
    click_world(w, frame, 200, 0)
    assert w.tool_sessions.commit()
    bodies = [o for o in doc.objects.values() if isinstance(o, BodyObject)]
    assert len(bodies) == 2 and doc.history.count == 1
    ids = set(doc.objects)
    assert all(BRepCheck_Analyzer(o.shape).IsValid() for o in bodies)
    assert sum(volume(o.shape) for o in bodies) == pytest.approx(120000)
    assert all(len(doc.objects[o.features[-1]].parameters.contours) == 1 for o in bodies)
    doc.history.undo()
    assert set(doc.objects) == set(before) and doc.objects[sketch.id].visible
    doc.history.redo()
    assert set(doc.objects) == ids and all(doc.objects[o.id].shape is o.shape for o in bodies)
    path = tmp_path / "multi-region.cada"
    save_document(doc, path, registry=w.object_types)
    loaded = load_document(path, registry=w.object_types)
    assert all(body_result(loaded, o.id).status is ResultStatus.READY for o in bodies)
    assert sum(volume(loaded.objects[o.id].shape) for o in bodies) == pytest.approx(120000)
    assert len(w.tree_adapter.rows[sketch.id]) == 2


@pytest.mark.parametrize("disjoint", [False, True])
def test_multiple_face_regions_fuse_or_create_announced_extra_body(extrusion_system, tmp_path, disjoint):
    w, commands, m = extrusion_system
    doc, vp = w.viewport.document, w.viewport
    parent = base_body(extrusion_system)
    sketch = face_sketch(extrusion_system, parent)
    frame = sketch.sketch.support.frame
    u, v, _ = frame.to_local(Vec3(300 if disjoint else 75, 30, 20))
    commands.add_entity(doc, sketch.id, m.Circle(m.Vec2(u, v), 5))
    sketch = doc.objects[sketch.id]
    doc.history.clear()
    tool = start(w, sketch)
    tool._set_regions(tuple(outer.id for outer, _ in tool.regions))
    tool.depth.setValue(10)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert len(tool._result.parts) == (2 if disjoint else 1)
    assert tool.preview_hidden_ids == (parent.id,)
    overlay = w.tool_host.overlay.presentation
    assert isinstance(overlay, AIS_ColoredShape) and overlay.ZLayer() == Graphic3d_ZLayerId_Default
    assert overlay.Transparency() == 0
    assert tool.preview_parts[0][2]
    assert not vp.context.IsDisplayed(vp.presentations[parent.id])
    color = Quantity_Color()
    vp.presentations[parent.id].Color(color)
    assert color.Values(Quantity_TOC_RGB) == pytest.approx(parent.color)
    if not disjoint:
        assert w.command_registry.execute("view.fit")
        vp.view.Redraw()
        Path("artifacts").mkdir(exist_ok=True)
        assert vp.view.Dump("artifacts/post-x5-preview.png")
    assert w.tool_sessions.commit()
    bodies = [o for o in doc.objects.values() if isinstance(o, BodyObject)]
    assert len(bodies) == (2 if disjoint else 1)
    feature = doc.objects[doc.objects[parent.id].features[-1]]
    assert feature.operation is Operation.BOSS
    assert len(feature.parameters.contours) == (1 if disjoint else 2)
    assert sum(volume(o.shape) for o in bodies) == pytest.approx(120000+500*pi)
    assert all(body_result(doc, o.id).status is ResultStatus.READY for o in bodies)
    ids = set(doc.objects)
    doc.history.undo()
    assert doc.objects[parent.id].shape is parent.shape
    doc.history.redo()
    assert set(doc.objects) == ids and all(doc.objects[o.id].shape is o.shape for o in bodies)
    path = tmp_path / "multi-boss.cada"
    save_document(doc, path, registry=w.object_types)
    loaded = load_document(path, registry=w.object_types)
    assert all(body_result(loaded, o.id).status is ResultStatus.READY for o in bodies)
    assert sum(volume(loaded.objects[o.id].shape) for o in bodies) == pytest.approx(120000+500*pi)


def test_hover_highlights_entire_sketch_and_cleans_on_leave(extrusion_system):
    w, _, _ = extrusion_system
    sketch = rectangle(extrusion_system)
    doc, frame = w.viewport.document, sketch.sketch.support.frame
    orient(w, frame)
    doc.select_objects(())
    tool = start(w)
    hover_world(w, frame, 50, 0)
    presenter = w.viewport.document_adapter.features[sketch.id][1]
    module = import_module(presenter.__class__.__module__)
    assert presenter.hover and tool.source.value is None
    for _, ais in presenter.entities.values():
        color = Quantity_Color()
        ais.Color(color)
        assert color.Values(Quantity_TOC_sRGB) == pytest.approx(module.HOVER)
    hover_world(w, frame, 150, 100)
    assert not presenter.hover and doc.selected_ids == ()
    assert w.tool_sessions.cancel()
    assert w.viewport.tool_hover_handler is None


def test_automatic_fusion_requires_choice_for_multiple_compatible_bodies(extrusion_system):
    w, _, _ = extrusion_system
    doc = w.viewport.document
    parent = base_body(extrusion_system, static=True)
    other = SceneObject("Autre cible", BRepPrimAPI_MakeBox(gp_Pnt(50, 0, 0), 100., 60., 20.).Shape(), (.2, .4, .6))
    doc.add(other)
    sketch = rectangle(extrusion_system)
    tool = start(w, sketch)
    tool.depth.setValue(30)
    w.tool_sessions.update_preview()
    assert not w.tool_sessions.can_commit and "Plusieurs corps" in w.tool_sessions.message
    tool.choose_body(parent.id)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit and tool.preview_hidden_ids == (parent.id,)
    tool.activate("body")
    w.tool_sessions.update_preview()
    orient(w, sketch.sketch.support.frame)
    hover_world(w, sketch.sketch.support.frame, 25, 30)
    assert w.viewport.context.HasDetected()
    assert w.viewport.context.DetectedInteractive() == w.viewport.presentations[parent.id]
    tool.choose_body(parent.id)
    assert w.tool_sessions.commit()
    assert doc.objects[other.id] is other
    assert volume(doc.objects[parent.id].shape) == pytest.approx(180000)


def test_extrusion_can_start_directly_from_sketch_editor(extrusion_system):
    w, _, _ = extrusion_system
    sketch = rectangle(extrusion_system)
    doc = w.viewport.document
    w.tree_selection_changed([sketch.id])
    assert w.command_registry.execute("cadastrophe.sketch.edit")
    assert w.tool_sessions.editing
    assert w.command_registry.action("cadastrophe.modeling.extrude").isEnabled()
    assert w.command_registry.action("modeling.extrude").isEnabled()
    assert w.command_registry.action("modeling.cut_extrude").isEnabled()
    assert w.command_registry.execute("modeling.extrude")
    assert not w.tool_sessions.editing and w.tool_sessions.tool.source.value == sketch.id
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert w.viewport.editing_id is None and doc.history.count == 0
    assert w.tool_sessions.commit()
    body = next(o for o in doc.objects.values() if isinstance(o, BodyObject))
    assert volume(body.shape) == pytest.approx(120000) and doc.history.count == 1
    doc.history.undo()
    assert body.id not in doc.objects and doc.objects[sketch.id].visible


def test_preview_is_occluded_by_an_opaque_body(extrusion_system, tmp_path):
    w, _, _ = extrusion_system
    doc, vp = w.viewport.document, w.viewport
    sketch = rectangle(extrusion_system)
    cover = SceneObject("Cache opaque", BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 30), 100., 60., 5.).Shape(), (.2, .5, .7))
    doc.add(cover)
    orient(w, sketch.sketch.support.frame)
    def sample(name):
        vp.view.Redraw()
        path = tmp_path / name
        assert vp.view.Dump(str(path))
        image = QImage(str(path))
        x, y = PresentationSurface(vp).project(Vec3(50, 30, 35))
        return image.pixelColor(round(x), round(y)).getRgb()
    before = sample("before.png")
    tool = start(w, sketch)
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert w.tool_host.overlay.presentation.ZLayer() == Graphic3d_ZLayerId_Default
    assert sample("after.png") == before
    assert doc.objects[cover.id] is cover
    assert w.tool_sessions.cancel()


@pytest.mark.parametrize("static", [False, True])
def test_multi_body_commit_failure_restores_every_result(extrusion_system, monkeypatch, static):
    w, commands, m = extrusion_system
    doc = w.viewport.document
    parent = base_body(extrusion_system, static)
    sketch = face_sketch(extrusion_system, parent)
    u, v, _ = sketch.sketch.support.frame.to_local(Vec3(300, 30, 20))
    commands.add_entity(doc, sketch.id, m.Circle(m.Vec2(u, v), 5))
    sketch = doc.objects[sketch.id]
    doc.history.clear()
    tool = start(w, sketch)
    tool._set_regions(tuple(outer.id for outer, _ in tool.regions))
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit and len(tool._result.parts) == 2
    before_ids = set(doc.objects)
    def fail(*_):
        raise ValueError("Échec après publication des deux corps")
    monkeypatch.setattr(doc, "set_visible", fail)
    assert not w.tool_sessions.commit()
    assert set(doc.objects) == before_ids and doc.objects[parent.id].shape is parent.shape
    assert type(doc.objects[parent.id]) is type(parent) and doc.objects[sketch.id].visible
    assert doc.history.count == 0 and not doc.history.blocked
    assert w.viewport.context.IsDisplayed(w.viewport.presentations[parent.id])


def test_cancel_after_sketch_handoff_preserves_valid_sketch(extrusion_system):
    w, _, _ = extrusion_system
    sketch = rectangle(extrusion_system)
    doc = w.viewport.document
    w.tree_selection_changed([sketch.id])
    assert w.command_registry.execute("cadastrophe.sketch.edit")
    assert w.command_registry.execute("cadastrophe.modeling.extrude")
    assert w.tool_sessions.cancel()
    assert doc.objects[sketch.id].sketch == sketch.sketch and doc.objects[sketch.id].visible
    assert not w.tool_sessions.active and not doc.history.blocked and doc.history.count == 0
    assert doc.selected_ids == (sketch.id,)
    assert w.command_registry.execute("cadastrophe.sketch.edit")
    assert w.tool_sessions.editing
