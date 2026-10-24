"""X5 real plugin, native preview, body history and persistence scenarios."""
from dataclasses import replace
from importlib import import_module
from math import pi
from pathlib import Path

import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.TopAbs import TopAbs_SOLID
from OCC.Core.gp import gp_Pnt
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from cadastrophe.core.body_history import body_result, result_at
from cadastrophe.core.feature_model import BodyStateRef, Operation, ResultStatus, EvaluationResult
from cadastrophe.core.feature_objects import BodyObject
from cadastrophe.core.persistence import load_document, save_document
from cadastrophe.document import SceneObject
from cadastrophe.geometry import Vec3
from cadastrophe.presentation import PresentationSurface
from test_extrusion_tool import extrusion_system, rectangle, start
from test_primitives import volume, pose


def base_body(system, static=False):
    w, _, _ = system
    doc = w.viewport.document
    if static:
        obj = SceneObject("Bloc importé", BRepPrimAPI_MakeBox(100., 60., 20.).Shape(), (.3, .5, .7))
        doc.add(obj)
        return obj
    source = rectangle(system)
    start(w, source)
    assert w.tool_sessions.commit()
    return next(obj for obj in doc.objects.values() if isinstance(obj, BodyObject))


def face_sketch(system, body, *, kind="circle", center=(50, 30), height=20):
    w, commands, m = system
    supports = import_module(w.plugins._record("cadastrophe.sketch").namespace + ".supports")
    faces = supports.face_map(body.shape)
    for index in range(1, faces.Size()+1):
        try:
            frame = supports.face_frame(faces.FindKey(index), "Face supérieure")
        except ValueError:  # Cylindrical walls are deliberately not sketch supports.
            continue
        if frame.normal.z > .99 and abs(frame.origin.z-height) < 1e-7:
            break
    else:
        raise AssertionError("Face supérieure absente")
    doc = w.viewport.document
    sketch = commands.create_sketch(doc, m.PlaneSupport(body.id, frame, body.revision, index))
    u, v, _ = frame.to_local(Vec3(*center, height))
    if kind == "circle":
        commands.add_entity(doc, sketch.id, m.Circle(m.Vec2(u, v), 5))
    else:
        points = tuple(m.Vec2(u+du, v+dv) for du, dv in ((-10, -5), (10, -5), (10, 5), (-10, 5)))
        commands.add_entities(doc, sketch.id, tuple(m.Segment(p, points[(i+1)%4]) for i, p in enumerate(points)), title="Profil")
    return doc.objects[sketch.id]


@pytest.mark.parametrize("static", [False, True])
@pytest.mark.parametrize("kind,area", [("circle", 25*pi), ("rectangle", 200)])
def test_face_boss_single_result_undo_redo_and_reopen(extrusion_system, tmp_path, static, kind, area):
    system = extrusion_system
    w, _, _ = system
    doc, vp = w.viewport.document, w.viewport
    parent = base_body(system, static)
    other = SceneObject("Autre corps", BRepPrimAPI_MakeBox(gp_Pnt(200, 0, 0), 10., 10., 10.).Shape(), (.8, .4, .3))
    doc.add(other)
    sketch = face_sketch(system, parent, kind=kind)
    doc.history.clear()
    original_ids, original_pose = set(doc.objects), pose(vp)
    tool = start(w, sketch)
    assert tool.merge.isChecked() and tool.body.value == parent.id
    tool.depth.setValue(10)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(120000+10*area)
    original_ais = vp.presentations[parent.id]
    assert not vp.context.IsDisplayed(original_ais)  # One closed opaque combined result.
    assert vp.context.IsDisplayed(vp.presentations[other.id])
    assert doc.objects[parent.id] is parent and doc.history.count == 0
    assert pose(vp) == original_pose
    if not static and kind == "circle":
        assert w.command_registry.execute("view.fit")
        vp.view.Redraw()
        Path("artifacts").mkdir(exist_ok=True)
        assert vp.view.Dump("artifacts/x5-boss-preview.png")
    assert w.tool_sessions.commit()
    body = doc.objects[parent.id]
    feature = doc.objects[body.features[-1]]
    before = BodyStateRef(body.id, None if static else parent.features[-1])
    assert feature.operation is Operation.BOSS and feature.body_id == parent.id
    assert feature.source.supports == (before,) and feature.parameters.merge_result
    assert len(body.features) == (1 if static else 2)
    assert body.source_shape is (parent.shape if static else None)
    assert body.name == parent.name and body.color == parent.color
    assert body.shape.ShapeType() == TopAbs_SOLID and BRepCheck_Analyzer(body.shape).IsValid()
    assert volume(body.shape) == pytest.approx(120000+10*area)
    assert result_at(doc, before).shape is parent.shape
    assert body_result(doc, body.id).status is ResultStatus.READY
    assert doc.history.count == 1 and not doc.objects[sketch.id].visible
    assert w.tree_items[feature.id].parent() is w.tree_items[body.id]
    assert w.tree_items[sketch.id].parent() is w.tree_items[feature.id]
    for _ in range(3):
        doc.history.undo()
        assert set(doc.objects) == original_ids and doc.objects[sketch.id].visible
        assert doc.objects[parent.id].shape is parent.shape
        assert doc.objects[other.id] is other and vp.context.IsDisplayed(vp.presentations[other.id])
        doc.history.redo()
        assert doc.objects[parent.id].shape is body.shape
        assert result_at(doc, before).shape is parent.shape
        assert doc.objects[other.id] is other
    path = tmp_path / "boss.cada"
    save_document(doc, path, registry=w.object_types)
    loaded = load_document(path, registry=w.object_types)
    restored = loaded.objects[parent.id]
    assert not loaded.is_modified and loaded.history.count == 0
    assert restored.features == body.features and restored.revision == doc.objects[parent.id].revision
    assert loaded.objects[feature.id].source == feature.source
    assert body_result(loaded, body.id).status is ResultStatus.READY, body_result(loaded, body.id).message
    assert volume(restored.shape) == pytest.approx(120000+10*area)
    assert volume(result_at(loaded, before).shape) == pytest.approx(120000)
    assert volume(loaded.objects[other.id].shape) == pytest.approx(1000)
    if not static and kind == "circle":
        assert w.command_registry.execute("view.fit")
        vp.view.Redraw()
        Path("artifacts").mkdir(exist_ok=True)
        assert vp.view.Dump("artifacts/x5-boss-viewport.png")


def test_cancel_failure_and_explicit_new_body_preserve_target(extrusion_system):
    w, _, _ = extrusion_system
    doc, vp = w.viewport.document, w.viewport
    parent = base_body(extrusion_system)
    sketch = face_sketch(extrusion_system, parent)
    doc.history.clear()
    w.tree_selection_changed([sketch.id])
    before = dict(doc.objects), doc.history.index, doc.selected_ids
    tool = start(w, sketch)
    assert w.tool_sessions.can_commit
    old_ais = vp.presentations[parent.id]
    tool.reverse.setChecked(True)
    tool.depth.setValue(10)
    w.tool_sessions.update_preview()
    assert not w.tool_sessions.can_commit and "matière" in w.tool_sessions.message
    assert not w.tool_sessions.commit()
    assert vp.context.IsDisplayed(old_ais) and w.tool_host.overlay.presentation is None
    assert w.tool_sessions.cancel()
    assert (dict(doc.objects), doc.history.index, doc.selected_ids) == before
    disjoint = face_sketch(extrusion_system, parent, center=(300, 30))
    tool = start(w, disjoint)
    assert w.tool_sessions.can_commit and "nouveau" in tool.operation.text().lower()
    assert tool.merge.isChecked() and doc.objects[parent.id].shape is parent.shape
    tool.merge.setChecked(False)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit
    assert vp.context.IsDisplayed(vp.presentations[parent.id])
    assert w.tool_sessions.commit()
    assert len([obj for obj in doc.objects.values() if isinstance(obj, BodyObject)]) == 2
    assert doc.objects[parent.id].shape is parent.shape


def test_reference_plane_auto_fuses_unique_target_and_allows_native_target_picking(extrusion_system):
    w, _, _ = extrusion_system
    doc = w.viewport.document
    parent = base_body(extrusion_system, static=True)
    other = SceneObject("Second bloc", BRepPrimAPI_MakeBox(gp_Pnt(200, 0, 0), 10., 10., 10.).Shape(), (.8, .4, .3))
    doc.add(other)
    sketch = rectangle(extrusion_system)
    tool = start(w, sketch)
    assert tool.merge.isChecked() and tool.body.value is None
    tool.depth.setValue(30)
    tool.merge.setChecked(True)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit
    assert tool.preview_hidden_ids == (parent.id,)
    QTest.mouseClick(tool.body.edit, Qt.MouseButton.LeftButton)
    w.tool_sessions.update_preview()
    assert not w.tool_sessions.can_commit and tool.target == "body"
    assert w.viewport.context.IsDisplayed(w.viewport.presentations[parent.id])
    w.viewport.camera.normal_to(sketch.sketch.support.frame)
    assert w.command_registry.execute("view.fit")
    x, y = PresentationSurface(w.viewport).project(Vec3(70, 40, 20))
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=QPoint(round(x), round(y)))
    w.tool_sessions.update_preview()
    assert tool.body.value == parent.id and w.tool_sessions.can_commit, w.tool_sessions.message
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(180000)
    # Re-arming the target shows the original for another native pick.
    QTest.mouseClick(tool.body.edit, Qt.MouseButton.LeftButton)
    w.tool_sessions.update_preview()
    assert w.viewport.context.IsDisplayed(w.viewport.presentations[parent.id])
    w.tree_selection_changed([other.id])
    w.tool_sessions.update_preview()
    assert tool.body.value == other.id and w.tool_sessions.can_commit
    assert "nouveau" in tool.operation.text().lower()
    tool.body.clear_button.click()
    assert tool.body.value is None
    assert w.tool_sessions.cancel()


def test_successive_bosses_restore_prefixes_and_allow_explicit_update_delete(extrusion_system, tmp_path):
    w, _, _ = extrusion_system
    doc = w.viewport.document
    parent = base_body(extrusion_system)
    first_source = face_sketch(extrusion_system, parent)
    tool = start(w, first_source)
    tool.depth.setValue(10)
    assert w.tool_sessions.commit()
    first = doc.objects[parent.id]
    source = face_sketch(extrusion_system, first, height=30)
    tool = start(w, source)
    tool.depth.setValue(10)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    final = doc.objects[parent.id]
    feature = doc.objects[final.features[-1]]
    assert len(final.features) == 3 and volume(final.shape) == pytest.approx(120000+500*pi)
    assert feature.source.supports == (BodyStateRef(parent.id, first.features[-1]),)
    other = SceneObject("Corps indépendant", BRepPrimAPI_MakeBox(5., 5., 5.).Shape(), (.8, .4, .3))
    doc.add(other)
    path = tmp_path / "chain.cada"
    save_document(doc, path, registry=w.object_types)
    loaded = load_document(path, registry=w.object_types)
    assert body_result(loaded, parent.id).status is ResultStatus.READY, body_result(loaded, parent.id).message
    assert volume(loaded.objects[parent.id].shape) == pytest.approx(120000+500*pi)
    assert volume(result_at(loaded, feature.source.supports[0]).shape) == pytest.approx(120000+250*pi)
    # Existing programmatic construction commands, not the X8 editing UI.
    from cadastrophe.core.body_history import prefix_results
    original = loaded.objects[parent.id].shape
    independent = loaded.objects[other.id]
    service = w.services.service("cadastrophe.modeling.extrusions")
    revised = replace(feature, parameters=replace(feature.parameters, depth=15))
    support_ref = feature.source.supports[0]
    previous = result_at(loaded, support_ref).shape
    result = service.evaluator(loaded).evaluate(revised, loaded.objects[source.id], previous, {support_ref: previous})
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(120000+625*pi)
    states = dict(prefix_results(loaded, parent.id))
    states[BodyStateRef(parent.id, feature.id)] = result
    loaded.update_construction((revised,), results={parent.id: result}, prefixes={parent.id: states})
    assert loaded.objects[parent.id].shape is result.shape
    loaded.history.undo()
    assert loaded.objects[parent.id].shape is original
    loaded.history.redo()
    assert loaded.objects[parent.id].shape is result.shape and loaded.objects[other.id] is independent
    states = prefix_results(loaded, parent.id)
    prior = states[feature.source.supports[0]]
    loaded.update_construction((replace(loaded.objects[parent.id], features=first.features),), remove=(feature.id,),
        results={parent.id: prior}, prefixes={parent.id: {ref: value for ref, value in states.items()
            if ref.producer_id in first.features or ref.producer_id is None}})
    assert volume(loaded.objects[parent.id].shape) == pytest.approx(120000+250*pi)
    loaded.history.undo()
    assert volume(loaded.objects[parent.id].shape) == pytest.approx(120000+625*pi)
    loaded.history.redo()
    assert volume(loaded.objects[parent.id].shape) == pytest.approx(120000+250*pi)
    assert loaded.objects[other.id] is independent


@pytest.mark.parametrize("static", [False, True])
def test_failed_boss_publication_rolls_back_target_and_preview(extrusion_system, monkeypatch, static):
    w, _, _ = extrusion_system
    doc = w.viewport.document
    parent = base_body(extrusion_system, static)
    sketch = face_sketch(extrusion_system, parent)
    doc.history.clear()
    tool = start(w, sketch)
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    original_ids = set(doc.objects)
    def fail(*_):
        raise ValueError("Échec de visibilité après fusion")
    monkeypatch.setattr(doc, "set_visible", fail)
    assert not w.tool_sessions.commit()
    assert not w.tool_sessions.active and not doc.history.blocked
    assert set(doc.objects) == original_ids and doc.objects[parent.id].shape is parent.shape
    assert doc.objects[sketch.id].visible and doc.history.count == 0
    assert type(doc.objects[parent.id]) is type(parent)
    assert w.viewport.context.IsDisplayed(w.viewport.presentations[parent.id])
    if not static:
        assert body_result(doc, parent.id).status is ResultStatus.READY
        assert result_at(doc, BodyStateRef(parent.id, parent.features[-1])).shape is parent.shape


def test_explicit_prefix_still_validates_the_face_and_preview_navigation(extrusion_system):
    w, _, _ = extrusion_system
    doc, vp = w.viewport.document, w.viewport
    parent = base_body(extrusion_system)
    sketch = face_sketch(extrusion_system, parent)
    profiles = w.services.service("cadastrophe.sketch.profiles")
    before = dict(doc.objects)
    wrong_prefix = BRepPrimAPI_MakeBox(100., 60., 40.).Shape()
    with pytest.raises(ValueError, match="non résolu|perdue"):
        profiles.to_faces(doc, sketch.id, support_shapes={parent.id: wrong_prefix})
    with pytest.raises(ValueError, match="complets"):
        profiles.to_faces(doc, sketch.id, support_shapes={})
    assert dict(doc.objects) == before
    tool = start(w, sketch)
    assert w.tool_sessions.can_commit
    initial = pose(vp)
    center = QPoint(vp.width()//2, vp.height()//2)
    QTest.mousePress(vp, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, center)
    QTest.mouseMove(vp, center+QPoint(45, 25), 20)
    QTest.mouseRelease(vp, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, center+QPoint(45, 25))
    assert pose(vp) != initial
    camera = pose(vp)
    tool.depth.setValue(10)
    w.tool_sessions.update_preview()
    assert pose(vp) == camera and dict(doc.objects) == before
    assert w.tool_sessions.cancel()
    assert pose(vp) == camera and vp.context.IsDisplayed(vp.presentations[parent.id])
