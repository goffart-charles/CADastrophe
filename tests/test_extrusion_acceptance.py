"""X9: a continuous native UI workflow, with independent topology/volume checks."""
from importlib import import_module
from dataclasses import asdict
import json
from math import pi
from pathlib import Path

import pytest
from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepClass3d import BRepClass3d_SolidClassifier
from OCC.Core.TopAbs import TopAbs_SOLID, TopAbs_SHELL, TopAbs_OUT, TopAbs_IN
from OCC.Core.TopExp import topexp
from OCC.Core.TopTools import TopTools_IndexedMapOfShape
from OCC.Core.TopoDS import topods
from OCC.Core.gp import gp_Pnt
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cadastrophe.app import MainWindow
from cadastrophe.core.body_history import body_result
from cadastrophe.core.feature_model import ResultStatus
from cadastrophe.core.feature_objects import BodyObject
from cadastrophe.geometry import REFERENCE_PLANES, Vec3
from test_extrusion_tool import extrusion_system, start, rectangle
from test_feature_edit import edit
from test_primitives import volume, pose
from test_sketch_drawing_ui import choose, click
from test_sketch_constraints_ui import draw_rectangle, add
from test_sketch_face_navigation_ui import navigation_move


def check_body(doc, key, expected):
    shape = doc.objects[key].shape
    assert body_result(doc, key).status is ResultStatus.READY
    assert shape.ShapeType() == TopAbs_SOLID and BRepCheck_Analyzer(shape, True).IsValid()
    solids, shells = TopTools_IndexedMapOfShape(), TopTools_IndexedMapOfShape()
    topexp.MapShapes(shape, TopAbs_SOLID, solids)
    topexp.MapShapes(shape, TopAbs_SHELL, shells)
    assert solids.Size() == 1 and shells.Size() > 0
    assert all(BRep_Tool.IsClosed(topods.Shell(shells.FindKey(i))) for i in range(1, shells.Size()+1))
    assert volume(shape) == pytest.approx(expected, rel=1e-8)
    return dict(volume=volume(shape), solids=solids.Size(), shells=shells.Size(), valid=True)


def face_circle(system, body_id, center, radius):
    w, _, _, _ = system
    vp, doc = w.viewport, w.viewport.document
    vp.camera.normal_to(REFERENCE_PLANES[0])
    assert w.command_registry.execute("view.fit")
    vp.view.Redraw()
    x, y = vp.view.Convert(*center)
    pos = QPoint(round(x/vp.devicePixelRatioF()), round(y/vp.devicePixelRatioF()))
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=pos)
    selected = w.services.selection_items
    assert len(selected) == 1 and selected[0].object_id == body_id and selected[0].topology == "Face"
    assert w.command_registry.execute("cadastrophe.sketch.new")
    session = w.tool_sessions.editor
    key, support = session.object_id, doc.objects[session.object_id].sketch.support
    assert support.face_index is not None and support.plane_id == body_id
    u, v, _ = support.frame.to_local(Vec3(*center))
    choose(system, "circle")
    click(system, (u, v))
    click(system, (u+radius, v))
    choose(system, "select")
    entities = doc.objects[key].sketch.entities
    assert len(entities) == 1
    assert w.command_registry.execute("cadastrophe.sketch.relations")
    constraints = import_module(session.__module__.rsplit('.', 1)[0] + '.constraints')
    add(session.constraint_panel, "radius", (constraints.Reference(entities[0].id),), radius)
    return session, key


def test_full_mouse_face_dimension_history_restart_and_reedit(extrusion_system, tmp_path):
    w, commands, model = extrusion_system
    editor = import_module(w.plugins._record("cadastrophe.sketch").namespace + ".editor")
    system = w, commands, model, editor
    doc, vp = w.viewport.document, w.viewport
    stages = {}
    session = draw_rectangle(system)
    source_id = session.object_id
    constraints = import_module(editor.__package__ + ".constraints")
    Ref = constraints.Reference
    panel = session.constraint_panel
    panel.link.click()
    assert w.command_registry.execute("cadastrophe.sketch.relations")
    edges = doc.objects[source_id].sketch.entities
    add(panel, "fixed", (Ref(edges[0].id, "start"),))
    add(panel, "length", (Ref(edges[0].id),), 100)
    width_id = doc.objects[source_id].sketch.constraints[-1].id
    add(panel, "length", (Ref(edges[1].id),), 60)
    # Handoff directly from the actual editor to the consuming ribbon command.
    assert w.command_registry.execute("modeling.extrude")
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert w.tool_sessions.commit()
    body = next(obj for obj in doc.objects.values() if isinstance(obj, BodyObject))
    stages["A_base"] = check_body(doc, body.id, 120000)
    assert len(body.features) == 1 and not doc.objects[source_id].visible
    _, boss_source = face_circle(system, body.id, (0, 10, 20), 5)
    assert w.command_registry.execute("modeling.extrude")
    tool = w.tool_sessions.tool
    tool.depth.setValue(10)
    assert w.tool_sessions.commit()
    boss_id = doc.objects[body.id].features[-1]
    stages["B_boss"] = check_body(doc, body.id, 120000+250*pi)
    _, cut_source = face_circle(system, body.id, (50, 10, 20), 10)
    assert w.command_registry.execute("modeling.cut_extrude")
    assert w.tool_sessions.commit()
    cut_id = doc.objects[body.id].features[-1]
    stages["C_pocket"] = check_body(doc, body.id, 120000-250*pi)
    # A blind pocket retains its floor; the through cut removes it.
    circle = doc.objects[cut_source].sketch.entities[0]
    world = doc.objects[cut_source].sketch.to_world(circle.center)
    floor_point = gp_Pnt(world.x, world.y, 1.)
    assert BRepClass3d_SolidClassifier(doc.objects[body.id].shape, floor_point, 1e-7).State() == TopAbs_IN
    w.activate_tree_object(w.tree_items[cut_id])
    tool = w.tool_sessions.tool
    tool.condition.setCurrentIndex(1)
    QTest.keyClick(tool.condition, Qt.Key.Key_Return)
    assert not w.tool_sessions.active
    stages["D_through"] = check_body(doc, body.id, 120000-1750*pi)
    assert BRepClass3d_SolidClassifier(doc.objects[body.id].shape, floor_point, 1e-7).State() == TopAbs_OUT
    assert doc.objects[cut_id].parameters.depth is None
    # Reopen the original Sketch and edit its actual driving dimension in Qt.
    doc.set_visible(source_id, True)
    w.tree_selection_changed([source_id])
    assert w.command_registry.execute("cadastrophe.sketch.edit")
    session = w.tool_sessions.editor
    row = next(i for i, c in enumerate(doc.objects[source_id].sketch.constraints) if c.id == width_id)
    session.constraint_panel.list.setCurrentRow(row)
    session.constraint_panel.value.set_internal_value(150)
    session.constraint_panel.value.setFocus()
    QTest.keyClick(session.constraint_panel.value, Qt.Key.Key_Return)
    assert w.command_registry.execute("cadastrophe.sketch.finish")
    QTest.qWait(180)
    stages["F_dimension"] = check_body(doc, body.id, 180000-1750*pi)
    assert width_id in {c.id for c in doc.objects[source_id].sketch.constraints}
    vp.setFocus()
    QTest.keyClick(vp, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    QTest.qWait(180)
    check_body(doc, body.id, 120000-1750*pi)
    QTest.keyClick(vp, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    QTest.qWait(180)
    stages["G_redo"] = check_body(doc, body.id, 180000-1750*pi)
    doc.set_visible(source_id, False)
    # Feature preview cancellation preserves the current native body and camera.
    before, history = dict(doc.objects), doc.history.index
    tool = edit(w, boss_id)
    camera = pose(vp)
    tool.depth.setValue(12)
    w.tool_sessions.update_preview()
    assert pose(vp) == camera and dict(doc.objects) == before
    ghost = w.tool_host.overlay.presentation
    assert ghost is not None
    origin = QPoint(vp.width()//2, vp.height()//2)
    destination = origin + QPoint(60, 35)
    QTest.mousePress(vp, Qt.MouseButton.MiddleButton, pos=origin)
    navigation_move(vp, destination)
    QTest.mouseRelease(vp, Qt.MouseButton.MiddleButton, pos=destination)
    assert pose(vp) != camera and dict(doc.objects) == before
    camera = pose(vp)
    tool.depth.setValue(14)
    w.tool_sessions.update_preview()
    assert pose(vp) == camera
    QTest.keyClick(tool.depth, Qt.Key.Key_Escape)
    assert not vp.context.IsDisplayed(ghost) and doc.history.index == history
    stages["J_cancel"] = check_body(doc, body.id, 180000-1750*pi)
    # Moving the producer repositions its sketches while preview stays detached.
    tool = edit(w, body.features[0])
    tool.depth.setValue(30)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert not tool.rebuild_warning
    assert dict(doc.objects) == before
    assert w.tool_sessions.cancel()
    stages["I_moving_support_cancelled"] = check_body(doc, body.id, 180000-1750*pi)
    # Real document closure and a new application window, with the same plugins.
    path = tmp_path / "x9-piece.cada"
    doc.rename(boss_id, "Bossage de contrôle")
    doc.set_transparencies({body.id: .15})
    assert w.save_document(path=path)
    snapshots = {key: doc.objects[key] for key in (source_id, boss_source, cut_source, boss_id, cut_id)}
    assert w.close_document()
    w.confirm_discard = lambda document: True
    w.close()
    reloaded_window = MainWindow(plugin_directory=w.plugins.directory,
        preferences_path=tmp_path / "restart-prefs.json", recovery_directory=tmp_path / "restart-recovery")
    try:
        reloaded_window.show()
        QApplication.processEvents()
        reloaded = reloaded_window.open_document(path)
        QTest.qWait(120)
        assert reloaded is not None and not reloaded.is_modified and reloaded.history.count == 0
        stages["H_reopened"] = check_body(reloaded, body.id, 180000-1750*pi)
        assert reloaded.objects[body.id].features == doc.objects[body.id].features
        assert reloaded.objects[body.id].transparency == .15
        for key, previous in snapshots.items():
            assert asdict(reloaded.objects[key]) == asdict(previous)
        assert reloaded.objects[boss_id].name == "Bossage de contrôle"
        tool = edit(reloaded_window, cut_id, double_click=True)
        assert tool.condition.currentData() == "through_all"
        tool.condition.setCurrentIndex(0)
        tool.depth.setValue(3)
        assert reloaded_window.tool_sessions.commit()
        stages["H_reedited"] = check_body(reloaded, body.id, 180000-50*pi)
        reloaded.history.undo()
        check_body(reloaded, body.id, 180000-1750*pi)
        reloaded_window.command_registry.execute("view.standard.isometric")
        reloaded_window.command_registry.execute("view.fit")
        reloaded.select_objects(())
        reloaded_window.viewport.view.Redraw()
        root = Path("artifacts")
        root.mkdir(exist_ok=True)
        assert reloaded_window.viewport.view.Dump(str(root / "x9-piece.png"))
        assert reloaded_window.grab().save(str(root / "x9-shell.png"))
        (root / "x9-volumes.json").write_text(json.dumps(stages, indent=2), encoding="utf-8")
    finally:
        reloaded_window.tool_sessions.cancel()
        reloaded_window.confirm_discard = lambda document: True
        reloaded_window.close()
        reloaded_window.deleteLater()
        QApplication.processEvents()


def test_native_preview_coalesces_changes_and_never_calculates_from_paint(extrusion_system, monkeypatch):
    w, _, _ = extrusion_system
    other = w.viewport.document
    w.create_document()
    source = rectangle(extrusion_system)
    doc = w.viewport.document
    tool = start(w, source)
    service = w.services.service("cadastrophe.modeling.extrusions")
    build, calls = service.plan, []
    def traced(*args, **kwargs):
        calls.append(args[2])
        return build(*args, **kwargs)
    monkeypatch.setattr(service, "plan", traced)
    before = dict(doc.objects), doc.history.index
    for depth in range(21, 51):
        tool.depth.setValue(depth)
    assert calls == [] and not w.tool_sessions.can_commit
    QTest.qWait(180)
    assert calls == [50]
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(300000)
    for _ in range(5):
        w.viewport.view.Redraw()
        QApplication.processEvents()
    assert calls == [50] and (dict(doc.objects), doc.history.index) == before
    tool.depth.setValue(70)
    assert w.tool_sessions.cancel()
    w.documents.activate(other.id)
    QTest.qWait(160)
    assert calls == [50] and not doc.history.blocked
    assert (dict(doc.objects), doc.history.index) == before
