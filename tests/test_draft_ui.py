"""Real Qt/OCCT draft picking, historical references and atomic tool lifecycle."""
from dataclasses import replace
from math import radians, tan

import pytest
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from cadastrophe.document import SceneObject
from cadastrophe.core.body_history import body_result
from cadastrophe.core.feature_model import ResultStatus
from cadastrophe.core.feature_objects import DraftFeature
from cadastrophe.core.operation_model import GeometryReference as Ref
from cadastrophe.core.persistence import save_document
from cadastrophe.core.topology_refs import TopologyReference
from examples.parametric_example import registry
from test_primitives import volume, pose
from test_parametric_operations_ui import screen
from test_feature_edit import edit
from test_draft import draft_chain
from test_extrusion_tool import extrusion_system


def iso_view(w):
    from time import monotonic
    deadline = monotonic()+5
    while not hasattr(w.viewport, "view") and monotonic() < deadline:
        QTest.qWait(10)
    assert hasattr(w.viewport, "view")
    w.viewport.view.SetProj(1., 1., 1.)
    w.viewport.view.FitAll()
    w.viewport.view.ZFitAll()
    w.viewport.view.Redraw()
    QApplication.processEvents()


@pytest.mark.parametrize("reverse", [False, True])
def test_draft_native_face_click_preview_commit_edit_and_reopen(extrusion_system, tmp_path, reverse):
    from zipfile import ZipFile
    import json
    w, _, _ = extrusion_system
    doc = w.viewport.document
    block = SceneObject("Bloc à dépouiller", BRepPrimAPI_MakeBox(30., 20., 10.).Shape(), (.4, .6, .8))
    doc.add(block)
    doc.history.clear()
    iso_view(w)
    camera = pose(w.viewport)
    action = w.command_registry.action("modeling.draft")
    button = next(b for b in w.command_manager.findChildren(QToolButton) if b.defaultAction() is action)
    assert button.isEnabled()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    tool = w.tool_sessions.tool
    assert tool is not None and not w.tool_sessions.can_commit
    before = dict(doc.objects)
    face_point = screen(w, (30., 10., 5.))
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=face_point)
    tool.angle.setValue(5)
    tool.reverse.setChecked(reverse)
    w.tool_sessions.update_preview()
    assert len(tool.face_references) == 1 and w.tool_sessions.can_commit, w.tool_sessions.message
    expected = 6000+(-1 if not reverse else 1)*1000*tan(radians(5))
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(expected)
    assert dict(doc.objects) == before and doc.history.count == 0
    assert pose(w.viewport) == camera
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=face_point)
    w.tool_sessions.update_preview()
    assert not tool.face_references and not w.tool_sessions.can_commit
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=face_point)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    draft, = (o for o in doc.objects.values() if isinstance(o, DraftFeature))
    assert doc.history.count == 1 and draft.id in w.tree_items
    assert volume(doc.objects[block.id].shape) == pytest.approx(expected)
    doc.history.undo()
    assert type(doc.objects[block.id]) is SceneObject
    doc.history.redo()
    ids = set(doc.objects)
    tool = edit(w, draft.id, double_click=True)
    tool.angle.setValue(8)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    edited_volume = 6000+(-1 if not reverse else 1)*1000*tan(radians(8))
    assert volume(doc.objects[block.id].shape) == pytest.approx(edited_volume)
    assert set(doc.objects) == ids and doc.objects[block.id].features == (draft.id,)
    doc.history.undo()
    assert volume(doc.objects[block.id].shape) == pytest.approx(expected)
    doc.history.redo()
    path = tmp_path/"depouille_native.cada"
    assert w.save_document(path=path)
    with ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        record = next(o for o in manifest["objects"] if o["id"] == draft.id)
        assert record["brep"] is None and record["extension"]["version"] == 3
        assert record["extension"]["data"]["enabled"] is True
    assert w.close_document()
    loaded = w.open_document(path)
    assert loaded is not doc and set(loaded.objects) == ids
    assert volume(loaded.objects[block.id].shape) == pytest.approx(edited_volume)
    assert loaded.objects[draft.id].parameters.reversed is reverse


def test_draft_historical_neutral_face_and_direction_are_picked_from_source(extrusion_system, tmp_path):
    doc, service, body_id, base, draft = draft_chain()
    path = tmp_path/"depouille_historique.cada"
    save_document(doc, path, registry=registry(service))
    w, _, _ = extrusion_system
    loaded = w.open_document(path)
    iso_view(w)
    tool = edit(w, draft.id)
    # The preview hides final AIS; both picks must hit the upstream source.
    QTest.mouseClick(tool.neutral.field.edit, Qt.MouseButton.LeftButton)
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=screen(w, (15., 10., 10.)))
    assert tool.neutral.reference().kind == "face"
    assert tool.neutral.reference().producer_id == base.id
    QTest.mouseClick(tool.direction.field.edit, Qt.MouseButton.LeftButton)
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=screen(w, (30., 20., 5.)))
    assert tool.direction.reference().kind == "edge", (tool._pick_error, w.tool_sessions.message, tool.active_reference)
    assert tool.direction.reference().producer_id == base.id
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert volume(loaded.objects[body_id].shape) == pytest.approx(6000+1000*tan(radians(5)))
    assert loaded.objects[draft.id].id == draft.id


def test_draft_invalid_preview_cancel_and_explicit_reference_repair(extrusion_system, tmp_path):
    doc, service, body_id, _, draft = draft_chain()
    path = tmp_path/"depouille_reparation.cada"
    save_document(doc, path, registry=registry(service))
    w, _, _ = extrusion_system
    loaded = w.open_document(path)
    iso_view(w)
    ids = set(loaded.objects)
    tool = edit(w, draft.id)
    tool.angle.setValue(89)
    w.tool_sessions.update_preview()
    assert not w.tool_sessions.can_commit and "Dépouille impossible" in w.tool_sessions.message
    assert w.tool_sessions.cancel()
    assert not loaded.history.count and set(loaded.objects) == ids
    original = loaded.objects[draft.id]
    missing = replace(original.parameters.faces[0], topology=TopologyReference("Face", ("absent",)))
    loaded.replace_feature(replace(original, parameters=replace(original.parameters, faces=(missing,))))
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert body_result(loaded, body_id).status is ResultStatus.ERROR
    tool = edit(w, draft.id)
    assert not w.tool_sessions.can_commit
    tool.clear_faces()
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=screen(w, (30., 10., 5.)))
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert set(loaded.objects) == ids and volume(loaded.objects[body_id].shape) == pytest.approx(6000-1000*tan(radians(5)))
    tool = edit(w, draft.id)
    tool.direction.set_reference(Ref("axis", values=(0, 0, 0, 1, 0, 0)))
    w.tool_sessions.update_preview()
    assert not w.tool_sessions.can_commit and "parallèle" in w.tool_sessions.message
    assert w.tool_sessions.cancel()
