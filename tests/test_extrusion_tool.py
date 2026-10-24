"""X4: actual isolated plugins, PropertyManager, AIS and native file round-trip.

All windows run on main.py --test's private Windows desktop.
"""
from dataclasses import replace
from importlib import import_module
import json
from math import pi
from pathlib import Path
import shutil
from zipfile import ZipFile

import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.TColStd import TColStd_ListOfInteger
from OCC.Core.TopAbs import TopAbs_SOLID
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QToolButton

from cadastrophe.sample_scene import demo_document
from cadastrophe.app import MainWindow
from cadastrophe.core.body_history import body_result
from cadastrophe.core.feature_model import ResultStatus, Operation
from cadastrophe.core.feature_objects import BodyObject, ExtrusionFeature
from cadastrophe.core.history import HistoryBusyError
from cadastrophe.core.persistence import load_document, save_document
from cadastrophe.core.units import DisplayUnits
from cadastrophe.geometry import Ray, Vec3
from test_primitives import volume, pose


@pytest.fixture
def extrusion_system(tmp_path):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "plugins"
    for name in ("sketch", "modeling", "primitives"):
        shutil.copytree(Path(__file__).resolve().parents[1] / "plugins" / name, root / name,
                        ignore=shutil.ignore_patterns("__pycache__"))
    window = MainWindow(plugin_directory=root, preferences_path=tmp_path / "prefs.json",
                        recovery_directory=tmp_path / "recovery")
    window.documents.replace(window.viewport.document.id, demo_document())
    window.show()
    window.activateWindow()
    app.processEvents()
    window.viewport.camera_animation_duration = 0
    assert window.plugins._record("cadastrophe.modeling").state == "active"
    namespace = window.plugins._record("cadastrophe.sketch").namespace
    commands, model = (import_module(namespace + "." + key) for key in ("commands", "model"))
    window.create_document()
    app.processEvents()
    if window.viewport.camera is None:
        QTest.qWait(100)
    try:
        yield window, commands, model
    finally:
        window.tool_sessions.cancel()
        window.confirm_discard = lambda doc: True
        window.close()
        window.deleteLater()
        app.processEvents()


def rectangle(system, *, plane="XY", hole=False, multiple=False, open=False):
    w, commands, m = system
    doc = w.viewport.document
    support = next(obj for obj in doc.objects.values() if obj.plane and obj.plane.name == plane)
    sketch = commands.create_sketch(doc, support.id)
    corners = tuple(m.Vec2(*p) for p in ((0, 0), (100, 0), (100, 60), (0, 60)))
    entities = tuple(m.Segment(p, corners[(i+1)%4]) for i, p in enumerate(corners))
    if open:
        entities = entities[:-1]
    if hole:
        entities += (m.Circle(m.Vec2(50, 30), 10),)
    if multiple:
        entities += (m.Circle(m.Vec2(200, 0), 10),)
    commands.add_entities(doc, sketch.id, entities, title="Profil rectangle")
    doc.history.clear()
    return doc.objects[sketch.id]


def start(w, sketch=None, *, button=False):
    if sketch is not None:
        w.tree_selection_changed([sketch.id])
    QApplication.processEvents()
    if button:
        action = w.command_registry.action("modeling.extrude")
        buttons = [b for b in w.command_manager.findChildren(QToolButton) if b.defaultAction() is action]
        assert len(buttons) == 1
        buttons[0].click()
    else:
        assert w.command_registry.execute("cadastrophe.modeling.extrude")
    assert w.tool_sessions.active
    return w.tool_sessions.tool


def test_ribbon_preview_commit_one_body_atomic_undo_redo_and_native_reopen(extrusion_system, tmp_path):
    w, _, _ = extrusion_system
    sketch = rectangle(extrusion_system)
    doc, vp = w.viewport.document, w.viewport
    original_ids, original_pose = set(doc.objects), pose(vp)
    tool = start(w, sketch, button=True)
    assert tool.source.value == sketch.id and tool.depth.internal_value == 20
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert set(doc.objects) == original_ids and doc.history.count == 0 and not doc.is_modified
    overlay = w.tool_host.overlay.presentation
    modes = TColStd_ListOfInteger()
    vp.context.ActivatedModes(overlay, modes)
    assert modes.Size() == 0 and vp.context.IsDisplayed(overlay)
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(120000)
    assert pose(vp) == original_pose
    assert not w.command_registry.action("modeling.cut_extrude").isEnabled()
    with pytest.raises(HistoryBusyError):
        save_document(doc, tmp_path / "preview.cada", registry=w.object_types)
    QTest.keyClick(tool.depth, Qt.Key.Key_Return)
    assert not w.tool_sessions.active and not doc.history.blocked
    body, = (obj for obj in doc.objects.values() if isinstance(obj, BodyObject))
    feature, = (obj for obj in doc.objects.values() if isinstance(obj, ExtrusionFeature))
    assert feature.operation is Operation.NEW_BODY and feature.source.sketch_id == sketch.id
    assert feature.parameters.depth == 20 and not feature.parameters.merge_result
    assert body.features == (feature.id,) and feature.shape is None
    assert body.shape.ShapeType() == TopAbs_SOLID and BRepCheck_Analyzer(body.shape).IsValid()
    assert volume(body.shape) == pytest.approx(120000)
    assert body_result(doc, body.id).status is ResultStatus.READY
    assert not doc.objects[sketch.id].visible and doc.history.count == 1
    assert doc.selected_ids == (body.id,)
    assert body.id in vp.presentations and feature.id not in vp.presentations
    assert body.id in w.tree_items and feature.id in w.tree_items and sketch.id in w.tree_items
    assert not vp.context.IsDisplayed(overlay)
    assert pose(vp) == original_pose
    hit = vp.selection.pick_surface(Ray(Vec3(50, 30, 100), Vec3(0, 0, -1)))
    assert hit is not None
    for _ in range(3):
        assert w.command_registry.execute("edit.undo")
        assert set(doc.objects) == original_ids and doc.objects[sketch.id].visible
        assert body.id not in vp.presentations and not doc.is_modified
        assert w.command_registry.execute("edit.redo")
        assert doc.objects[body.id].shape is body.shape
        assert doc.objects[feature.id].parameters == feature.parameters
        assert not doc.objects[sketch.id].visible and body.id in vp.presentations
    output = Path("artifacts")
    output.mkdir(exist_ok=True)
    vp.view.Redraw()
    assert vp.view.Dump(str(output / "x4-extrusion-viewport.png"))
    assert w.grab().save(str(output / "x4-extrusion-result.png"))
    path = tmp_path / "extrusion.cada"
    assert w.save_document(path=path)
    with ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        record = next(record for record in manifest["objects"] if record["id"] == body.id)
        assert record["brep"] is None  # Recipe, never an opaque cached final shape.
    assert w.close_document()
    reopened = w.open_document(path)
    QTest.qWait(100)  # First native paint of the new viewport after tab replacement.
    assert reopened is not None and not reopened.is_modified and reopened.history.count == 0
    restored = reopened.objects[body.id]
    assert restored.revision == doc.objects[body.id].revision
    assert restored.features == body.features and volume(restored.shape) == pytest.approx(120000)
    assert reopened.objects[feature.id].parameters == feature.parameters
    assert body.id in w.viewport.presentations and not reopened.objects[sketch.id].visible
    # Existing Sketch contract: show the consumed sketch before editing it.
    reopened.set_visible(sketch.id, True)
    w.tree_selection_changed([sketch.id])
    assert w.command_registry.action("cadastrophe.sketch.edit").isEnabled()
    assert w.command_registry.execute("cadastrophe.sketch.edit")
    assert w.tool_sessions.editing
    assert w.command_registry.execute("cadastrophe.sketch.finish")


@pytest.mark.parametrize("cancel", ["escape", "button"])
def test_select_during_tool_cancel_restores_selection_and_redo(extrusion_system, cancel):
    w, _, _ = extrusion_system
    sketch = rectangle(extrusion_system)
    doc = w.viewport.document
    original = next(iter(doc.objects))
    doc.rename(sketch.id, "Profil renommé")
    doc.history.undo()
    w.tree_selection_changed([original])
    before = dict(doc.objects), doc.history.count, doc.history.index, doc.is_modified
    tool = start(w)
    assert not w.tool_sessions.can_commit
    w.tree_selection_changed([sketch.id])
    w.tool_sessions.update_preview()
    assert tool.source.value == sketch.id and w.tool_sessions.can_commit
    tool.depth.setValue(35)
    tool.reverse.setChecked(True)
    for _ in range(100):
        if w.tool_sessions.can_commit:
            break
        QTest.qWait(20)
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(210000)
    overlay = w.tool_host.overlay.presentation
    if cancel == "escape":
        QTest.keyClick(tool.depth, Qt.Key.Key_Escape)
    else:
        w.tool_host.panel.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
    assert not w.tool_sessions.active and w.tool_host.panel is None
    assert (dict(doc.objects), doc.history.count, doc.history.index, doc.is_modified) == before
    assert doc.selected_ids == (original,) and doc.history.can_redo
    assert not w.viewport.context.IsDisplayed(overlay)
    doc.history.redo()
    assert doc.objects[sketch.id].name == "Profil renommé"


@pytest.mark.parametrize("plane,unit,reverse", [("XY", "mm", False), ("XZ", "cm", True), ("YZ", "in", False)])
def test_units_holes_direction_pending_input_and_navigation(extrusion_system, plane, unit, reverse):
    w, _, _ = extrusion_system
    sketch = rectangle(extrusion_system, plane=plane, hole=True)
    w.services._display_units = DisplayUnits(unit=unit)
    tool = start(w, sketch)
    assert tool.depth.internal_value == 20
    # Both the surrounding region and the inner disk can now be selected.
    tool.choose_region(next(outer.id for outer, holes in tool.regions if holes))
    tool.depth.lineEdit().selectAll()
    QTest.keyClicks(tool.depth.lineEdit(), "2")
    tool.reverse.setChecked(reverse)
    w.tool_sessions.update_preview()
    depth = DisplayUnits(unit=unit).to_internal(2)
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx((6000-100*pi)*depth)
    assert Vec3(*tool._result.translation).dot(sketch.sketch.support.frame.normal) == pytest.approx(-depth if reverse else depth)
    vp, initial = w.viewport, pose(w.viewport)
    center = QPoint(vp.width()//2, vp.height()//2)
    QTest.mousePress(vp, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, center)
    QTest.mouseMove(vp, center + QPoint(45, 25), 20)
    QTest.mouseRelease(vp, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, center+QPoint(45,25))
    assert pose(vp) != initial
    camera = pose(vp)
    tool.depth.setValue(DisplayUnits(unit=unit).to_display(depth*2))
    w.tool_sessions.update_preview()
    assert pose(vp) == camera
    w.tool_host.panel.buttons.button(QDialogButtonBox.StandardButton.Ok).click()
    body, = (obj for obj in w.viewport.document.objects.values() if isinstance(obj, BodyObject))
    feature = w.viewport.document.objects[body.features[0]]
    assert feature.parameters.depth == pytest.approx(depth*2)
    assert feature.parameters.reversed is reverse
    assert volume(body.shape) == pytest.approx((6000-100*pi)*depth*2)


def test_multiple_regions_require_explicit_choice_and_invalid_values_recover(extrusion_system):
    w, _, _ = extrusion_system
    sketch = rectangle(extrusion_system, multiple=True)
    tool = start(w, sketch)
    assert not w.tool_sessions.can_commit and "région" in w.tool_sessions.message
    tool.choose_region(tool.regions[0][0].id)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit
    first_volume = volume(w.tool_sessions.preview_object.shape)
    tool.choose_region(tool.regions[1][0].id)
    w.tool_sessions.update_preview()
    assert volume(w.tool_sessions.preview_object.shape) != pytest.approx(first_volume)
    for text in ("0", "abc", "-2"):
        tool.depth.lineEdit().setText(text)  # Also exercise invalid pending/pasted text.
        w.tool_sessions.update_preview()
        assert not w.tool_sessions.commit()
        assert w.tool_sessions.active and w.viewport.document.history.count == 0
        assert w.tool_host.overlay.presentation is None
    tool.depth.setValue(10)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit
    assert w.tool_sessions.commit()
    feature, = (obj for obj in w.viewport.document.objects.values() if isinstance(obj, ExtrusionFeature))
    assert len(feature.parameters.contours) == 1


def test_open_profile_rejected_then_switch_source_and_commit_failure_rolls_back(extrusion_system, monkeypatch):
    w, _, _ = extrusion_system
    invalid = rectangle(extrusion_system, open=True)
    valid = rectangle(extrusion_system)
    doc = w.viewport.document
    tool = start(w, invalid)
    assert not w.tool_sessions.can_commit
    assert w.tool_host.overlay.presentation is None
    tool.choose_source(valid.id)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit
    before = dict(doc.objects)
    def fail(*_):
        raise ValueError("Échec simulé de visibilité")
    monkeypatch.setattr(doc, "set_visible", fail)
    assert not w.tool_sessions.commit()
    assert dict(doc.objects) == before and doc.history.count == 0 and not doc.is_modified
    assert not w.tool_sessions.active and doc.selected_ids == (invalid.id,)


def test_document_lifetime_and_plugin_cleanup_restore_shell_placeholder(extrusion_system):
    w, _, _ = extrusion_system
    other = w.viewport.document
    w.create_document()
    sketch = rectangle(extrusion_system)
    doc = w.viewport.document
    tool = start(w, sketch)
    with pytest.raises(HistoryBusyError):
        w.documents.activate(other.id)
    assert not w.close_document()
    assert w.tool_sessions.tool is tool and w.viewport.document is doc
    # Keep the established global lifecycle lock while a tool owns the document.
    assert not w.plugins.deactivate("cadastrophe.modeling")
    assert w.tool_sessions.cancel()
    assert w.plugins.deactivate("cadastrophe.modeling")
    assert not w.tool_sessions.active and not doc.history.blocked
    assert not w.command_registry.command("modeling.extrude").implemented
    assert not w.command_registry.action("modeling.extrude").isEnabled()
    assert not w.object_types._document_restorers
    assert w.plugins.activate("cadastrophe.modeling")
    assert w.command_registry.command("modeling.extrude").implemented


def test_source_edit_invalidates_before_deferred_rebuild_and_file_restoration(extrusion_system, tmp_path):
    w, commands, m = extrusion_system
    sketch = rectangle(extrusion_system)
    start(w, sketch)
    assert w.tool_sessions.commit()
    doc = w.viewport.document
    body, = (obj for obj in doc.objects.values() if isinstance(obj, BodyObject))
    commands.add_entity(doc, sketch.id, m.Point(m.Vec2(10, 10)))
    assert doc.objects[body.id].shape is None and body_result(doc, body.id).status is ResultStatus.DIRTY
    assert body.id not in w.viewport.presentations
    # Saving a dirty derived cache still persists the actual recipe, not a stale solid.
    path = tmp_path / "edited.cada"
    save_document(doc, path, registry=w.object_types)
    loaded = load_document(path, registry=w.object_types)
    assert volume(loaded.objects[body.id].shape) == pytest.approx(120000)
    assert not loaded.is_modified and loaded.history.count == 0


@pytest.mark.parametrize("static", [False, True])
def test_new_body_from_face_sketch_records_producer_and_reopens(extrusion_system, tmp_path, static):
    w, commands, m = extrusion_system
    doc = w.viewport.document
    if static:
        assert w.command_registry.execute("cadastrophe.primitives.box")
        assert w.tool_sessions.commit()
        parent, = (obj for obj in doc.objects.values() if obj.kind.value == "body")
    else:
        base = rectangle(extrusion_system)
        start(w, base)
        assert w.tool_sessions.commit()
        parent, = (obj for obj in doc.objects.values() if isinstance(obj, BodyObject))
    supports = import_module(w.plugins._record("cadastrophe.sketch").namespace + ".supports")
    faces = supports.face_map(parent.shape)
    index, frame = next((i, frame) for i in range(1, faces.Size()+1)
                        if (frame := supports.face_frame(faces.FindKey(i), "Face supérieure")).normal.z > .99)
    sketch = commands.create_sketch(doc, m.PlaneSupport(parent.id, frame, parent.revision, index))
    commands.add_entity(doc, sketch.id, m.Circle(m.Vec2(10, 10), 3))
    sketch = doc.objects[sketch.id]
    tool = start(w, sketch)
    tool.merge.setChecked(False)  # X5 defaults to a boss on a face; exercise new-body mode explicitly.
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert w.tool_sessions.commit()
    child, = (obj for obj in doc.objects.values() if isinstance(obj, BodyObject) and obj.id != parent.id)
    feature = doc.objects[child.features[0]]
    assert feature.source.supports[0].body_id == parent.id
    assert feature.source.supports[0].producer_id == (None if static else parent.features[-1])
    assert doc.objects[parent.id].shape is parent.shape
    path = tmp_path / "face-source.cada"
    save_document(doc, path, registry=w.object_types)
    loaded = load_document(path, registry=w.object_types)
    assert body_result(loaded, child.id).status is ResultStatus.READY
    assert volume(loaded.objects[child.id].shape) == pytest.approx(pi*3**2*20)
    assert not supports.face_support_status(loaded.objects[sketch.id].sketch.support, loaded.objects[parent.id])
    if not static:
        # Opening a broken upstream profile must preserve the recipes but expose
        # ERROR/BLOCKED, never a cached solid or an arbitrary replacement face.
        edge = doc.objects[base.id].sketch.entities[0]
        commands.replace_entity(doc, base.id, replace(edge, start=m.Vec2(-5, 0)))
        save_document(doc, path, registry=w.object_types)
        failed = load_document(path, registry=w.object_types)
        assert body_result(failed, parent.id).status is ResultStatus.ERROR
        assert body_result(failed, parent.id).message
        assert body_result(failed, child.id).status is ResultStatus.BLOCKED
        assert failed.objects[parent.id].shape is None and failed.objects[child.id].shape is None
        assert failed.objects[feature.id].source == feature.source
        assert not failed.is_modified and failed.history.count == 0
