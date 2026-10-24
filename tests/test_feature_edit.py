"""X8: real tree edit sessions, detached native previews and document reopening."""
from dataclasses import replace
import json
from math import pi
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from cadastrophe.core.body_history import body_result
from cadastrophe.core.feature_model import ResultStatus
from cadastrophe.core.feature_objects import BodyObject
from cadastrophe.core.history import HistoryBusyError
from cadastrophe.core.object_types import ObjectTypeRegistry
from cadastrophe.core.persistence import save_document, load_document, DocumentFileError
from cadastrophe.core.units import DisplayUnits
from test_extrusion_tool import extrusion_system, rectangle, start
from test_boss_tool import base_body, face_sketch
from test_cut_tool import start_cut
from test_primitives import volume, pose


def edit(w, key, *, double_click=False):
    if double_click:
        w.activate_tree_object(w.tree_items[key])
    else:
        w.tree_selection_changed([key])
        assert w.command_registry.execute("cadastrophe.modeling.edit_feature")
    assert w.tool_sessions.active
    return w.tool_sessions.tool


def chain(system, static=False):
    w, _, _ = system
    parent = base_body(system, static)
    source = face_sketch(system, parent)
    tool = start(w, source)
    tool.depth.setValue(10)
    assert w.tool_sessions.commit()
    doc = w.viewport.document
    boss = doc.objects[parent.id].features[-1]
    hole = face_sketch(system, doc.objects[parent.id], center=(80, 30))
    start_cut(w, hole)
    assert w.tool_sessions.commit()
    cut = doc.objects[parent.id].features[-1]
    return parent.id, boss, cut, source, hole


@pytest.mark.parametrize("static", [False, True])
def test_edit_middle_feature_rebuilds_final_body_atomically_and_preserves_ids(extrusion_system, static):
    w, _, _ = extrusion_system
    body_id, boss_id, cut_id, source, hole = chain(extrusion_system, static)
    doc, vp = w.viewport.document, w.viewport
    feature = doc.objects[boss_id]
    doc.history.clear()
    before, camera = dict(doc.objects), pose(vp)
    original_prefixes = {key: dict(states) for key, states in doc._body_prefixes.items()}
    tool = edit(w, boss_id, double_click=True)
    assert tool.depth.internal_value == 10 and not tool.reverse.isChecked()
    tool.depth.setValue(15)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert tool.title.startswith("Ajout par extrusion")
    assert any(label.text() == "Ajout par extrusion" for label in tool.editor.findChildren(QLabel))
    from OCC.Core.AIS import AIS_ColoredShape
    from OCC.Core.Quantity import Quantity_TOC_RGB
    from cadastrophe.core.native_naming import subshapes
    shape, rgb, base_faces, _ = tool.preview_parts[0]
    assert len(base_faces) == 6 and len(subshapes(shape, "Face")) > len(base_faces)
    overlay = w.tool_host.overlay.presentation
    assert isinstance(overlay, AIS_ColoredShape)
    for face in base_faces:
        assert overlay.CustomAspects(face).ShadingAspect().Color().Values(Quantity_TOC_RGB) == pytest.approx(rgb)
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(120000+375*pi)  # Later cut is rolled back.
    assert dict(doc.objects) == before and doc.history.count == 0
    assert doc._body_prefixes == original_prefixes
    assert pose(vp) == camera
    assert not vp.context.IsDisplayed(vp.presentations[body_id])
    assert len(w.tool_host.overlay.presentations) == 1
    assert w.tool_sessions.commit()
    assert set(doc.objects) == set(before) and doc.history.count == 1
    assert doc.objects[boss_id].source == feature.source
    assert doc.objects[boss_id].id == boss_id and doc.objects[boss_id].parameters.depth == 15
    assert doc.objects[cut_id] is before[cut_id]
    assert doc.objects[source.id].sketch == before[source.id].sketch
    assert doc.objects[body_id].features == before[body_id].features
    assert doc.selected_ids == (boss_id,)
    for action, expected in ((doc.history.undo, 120000+125*pi), (doc.history.redo, 120000+250*pi)):
        action()
        assert body_result(doc, body_id).status is ResultStatus.READY
        assert volume(doc.objects[body_id].shape) == pytest.approx(expected)
        assert BRepCheck_Analyzer(doc.objects[body_id].shape).IsValid()
    assert source.id not in vp.presentations and hole.id not in vp.presentations
    assert w.tree_items[boss_id].parent() is w.tree_items[body_id]
    assert w.tree_items[source.id].parent() is w.tree_items[boss_id]


def test_creation_edit_sketch_save_close_reopen_and_edit_again(extrusion_system, tmp_path):
    w, commands, m = extrusion_system
    body_id, boss_id, cut_id, source, hole = chain(extrusion_system)
    doc = w.viewport.document
    tool = edit(w, cut_id)
    assert tool.reverse.isChecked() and tool.condition.currentData() == "blind"
    tool.condition.setCurrentIndex(1)
    assert not tool.depth.isEnabled()
    assert w.tool_sessions.commit()
    expected = 120000-250*pi  # boss +250*pi, full-depth cut -500*pi
    assert volume(doc.objects[body_id].shape) == pytest.approx(expected)
    # Use the actual existing sketch editing lifecycle and command service.
    doc.set_visible(source.id, True)
    w.tree_selection_changed([source.id])
    assert w.command_registry.execute("cadastrophe.sketch.edit") and w.tool_sessions.editing
    current = doc.objects[source.id]
    circle = current.sketch.entities[0]
    commands.replace_entity(doc, source.id, replace(circle, radius=8))
    assert w.command_registry.execute("cadastrophe.sketch.finish")
    QTest.qWait(180)
    expected = 120000+640*pi-500*pi
    assert volume(doc.objects[body_id].shape) == pytest.approx(expected)
    tool = edit(w, boss_id)
    tool.depth.setValue(12)
    assert w.tool_sessions.commit()
    expected = 120000+768*pi-500*pi
    path = tmp_path / "editable.cada"
    save_document(doc, path, registry=w.object_types)
    ids = set(doc.objects)
    assert w.close_document()
    reopened = w.open_document(path)
    QTest.qWait(100)
    assert reopened is not None and reopened is not doc
    assert set(reopened.objects) == ids and reopened.history.count == 0 and not reopened.is_modified
    assert volume(reopened.objects[body_id].shape) == pytest.approx(expected)
    assert reopened.objects[cut_id].parameters.depth is None
    assert reopened.objects[cut_id].parameters.end_condition == "through_all"
    assert reopened.objects[boss_id].source == doc.objects[boss_id].source
    tool = edit(w, cut_id, double_click=True)
    assert tool.condition.currentData() == "through_all" and not tool.depth.isEnabled()
    tool.condition.setCurrentIndex(0)
    tool.depth.setValue(3)
    assert w.tool_sessions.commit()
    assert reopened.history.count == 1
    assert volume(reopened.objects[body_id].shape) == pytest.approx(120000+768*pi-75*pi)
    reopened.history.undo()
    assert volume(reopened.objects[body_id].shape) == pytest.approx(expected)


def test_invalid_downstream_support_and_cancel_keep_live_body_and_redo(extrusion_system):
    w, _, _ = extrusion_system
    body_id, boss_id, cut_id, _, _ = chain(extrusion_system)
    doc, vp = w.viewport.document, w.viewport
    first = doc.objects[body_id].features[0]
    doc.rename(boss_id, "Nom temporaire")
    doc.history.undo()
    before = dict(doc.objects), doc.history.count, doc.history.index, doc.is_modified
    tool = edit(w, first)
    old_ais = vp.presentations[body_id]
    tool.depth.setValue(30)  # Moves the face supporting the later boss/cut.
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit  # Moving the producing face now repositions its dependent sketches.
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(180000)
    assert w.tool_sessions.active
    assert not vp.context.IsDisplayed(old_ais) and w.tool_host.overlay.presentation is not None
    for text in ("0", "abc", "-2"):
        tool.depth.lineEdit().setText(text)
        assert not w.tool_sessions.commit()
    tool.depth.setValue(20)
    tool.reverse.setChecked(True)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit  # A valid edited prefix may be committed with explicit downstream errors.
    QTest.keyClick(tool.depth, Qt.Key.Key_Escape)
    assert not w.tool_sessions.active
    assert (dict(doc.objects), doc.history.count, doc.history.index, doc.is_modified) == before
    assert doc.selected_ids == (first,) and doc.history.can_redo
    doc.history.redo()
    assert doc.objects[boss_id].name == "Nom temporaire"


def test_hidden_body_units_direction_noop_and_lifecycle_lock(extrusion_system):
    w, _, _ = extrusion_system
    other = w.viewport.document
    w.create_document()
    parent = base_body(extrusion_system)
    doc = w.viewport.document
    feature_id = parent.features[0]
    doc.rename(feature_id, "Base renommée")
    doc.set_visible(parent.id, False)
    doc.history.clear()
    w.services._display_units = DisplayUnits(unit="cm")
    tool = edit(w, feature_id)
    assert tool.depth.value() == 2 and tool.title == "Ajout par extrusion · Base renommée"
    assert w.tool_host.overlay.presentation is None
    with pytest.raises(HistoryBusyError):
        w.documents.activate(other.id)
    assert not w.close_document()
    assert not w.plugins.deactivate("cadastrophe.modeling")
    assert not w.command_registry.action("edit.undo").isEnabled()
    assert w.tool_sessions.commit()  # Unchanged parameters create no command.
    assert doc.history.count == 0
    tool = edit(w, feature_id)
    tool.depth.lineEdit().setText("3")
    tool.reverse.setChecked(True)
    assert w.tool_sessions.commit()
    assert doc.objects[feature_id].parameters.depth == 30
    assert doc.objects[feature_id].parameters.reversed
    assert volume(doc.objects[parent.id].shape) == pytest.approx(180000)
    assert not doc.objects[parent.id].visible and not w.viewport.context.IsDisplayed(w.viewport.presentations[parent.id])
    w.documents.activate(other.id)
    assert other.history.count == 0
    w.documents.activate(doc.id)
    doc.history.undo()
    assert doc.objects[feature_id].parameters.depth == 20
    tool = edit(w, feature_id)
    tool.depth.setValue(8)
    before = dict(doc.objects), doc.history.index
    w.confirm_discard = lambda document: True
    w.close()  # Application shutdown cancels pending timers/previews before closing documents.
    QTest.qWait(100)
    assert (dict(doc.objects), doc.history.index) == before
    assert not w.tool_sessions.active and not doc.history.blocked


def test_missing_plugin_unknown_fields_and_rebuild_error_are_lossless(extrusion_system, tmp_path):
    w, _, _ = extrusion_system
    parent = base_body(extrusion_system)
    doc = w.viewport.document
    path = tmp_path / "original.cada"
    save_document(doc, path, registry=w.object_types)
    original_bytes = path.read_bytes()
    with pytest.raises(DocumentFileError, match="non disponibles"):
        load_document(path, registry=ObjectTypeRegistry())
    assert path.read_bytes() == original_bytes
    with ZipFile(path) as archive:
        payloads = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(payloads["manifest.json"])
    record = next(obj for obj in manifest["objects"] if obj["id"] == parent.features[0])
    record["extension"]["data"]["parameters"]["future_option"] = 123
    payloads["manifest.json"] = json.dumps(manifest).encode()
    unknown = tmp_path / "unknown.cada"
    with ZipFile(unknown, "w", ZIP_DEFLATED) as archive:
        for name, payload in payloads.items():
            archive.writestr(name, payload)
    with pytest.raises(DocumentFileError, match="inconnus"):
        load_document(unknown, registry=w.object_types)
    assert dict(doc.objects)[parent.id].shape is parent.shape
    # A geometrically invalid recipe still loads with diagnostics and all data.
    source_id = doc.objects[parent.features[0]].source.sketch_id
    source = doc.objects[source_id]
    # Open the profile without deleting its persisted contour representative.
    # Removing the last segment used to delete that UUID in about one run in
    # four, depending on random UUID order, and fail graph validation instead.
    contour_id = doc.objects[parent.features[0]].parameters.contours[0]
    entities = tuple(replace(edge, end=replace(edge.end, u=edge.end.u-1)) if edge.id == contour_id else edge
                     for edge in source.sketch.entities)
    doc.replace_feature(replace(source, sketch=replace(source.sketch, entities=entities)))
    assert {edge.id for edge in entities} == {edge.id for edge in source.sketch.entities}
    broken = tmp_path / "broken.cada"
    save_document(doc, broken, registry=w.object_types)
    loaded = load_document(broken, registry=w.object_types)
    assert body_result(loaded, parent.id).status is ResultStatus.ERROR
    assert loaded.objects[parent.id].shape is None
    assert loaded.objects[parent.features[0]].parameters == doc.objects[parent.features[0]].parameters
    assert loaded.objects[source_id].sketch == doc.objects[source_id].sketch
    assert set(loaded.objects) == set(doc.objects)


def test_feature_tree_selection_highlights_only_existing_body_and_context_edit(extrusion_system):
    w, _, _ = extrusion_system
    parent = base_body(extrusion_system)
    doc, vp = w.viewport.document, w.viewport
    key = parent.features[0]
    ais = vp.presentations[parent.id]
    w.tree_selection_changed([key])
    assert doc.selected_ids == (key,)
    assert vp.selection.items()[0].object_id == key
    assert vp.document_adapter._construction_highlights == {parent.id: ais}
    assert key not in vp.presentations
    menu = w.object_context_menu(selection=True)
    try:
        action = w.command_registry.action("cadastrophe.modeling.edit_feature")
        assert action in menu.actions() and action.isEnabled()
    finally:
        menu.deleteLater()
    vp.selection.from_tree((parent.id,))
    assert w.tree_items[parent.id].isSelected()
    assert not w.tree_items[key].isSelected() and not vp.document_adapter._construction_highlights
    assert not w.command_registry.action("cadastrophe.modeling.edit_feature").isEnabled()


def test_edit_failure_rolls_back_and_cross_body_preview_stays_detached(extrusion_system, monkeypatch):
    w, _, _ = extrusion_system
    parent = base_body(extrusion_system)
    source = face_sketch(extrusion_system, parent)
    tool = start(w, source)
    tool.merge.setChecked(False)
    tool.depth.setValue(10)
    assert w.tool_sessions.commit()
    doc = w.viewport.document
    other = next(obj for obj in doc.objects.values() if isinstance(obj, BodyObject) and obj.id != parent.id)
    doc.history.clear()
    feature_id = parent.features[0]
    before = dict(doc.objects)
    tool = edit(w, feature_id)
    tool.reverse.setChecked(True)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit  # The edit remains detached, including downstream reconstruction errors.
    assert dict(doc.objects) == before
    w.tool_sessions.cancel()
    # Editing only the downstream body's extrusion retains its producer state.
    tool = edit(w, other.features[0])
    tool.depth.setValue(12)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert doc.objects[parent.id] is before[parent.id]
    assert volume(doc.objects[other.id].shape) == pytest.approx(250*pi)
    original_update = doc.update_construction
    def fail_after_publication(*args, **kwargs):
        original_update(*args, **kwargs)
        raise ValueError("Échec après publication")
    monkeypatch.setattr(doc, "update_construction", fail_after_publication)
    assert not w.tool_sessions.commit()
    assert not w.tool_sessions.active and doc.history.count == 0
    assert doc.objects[other.features[0]].parameters.depth == 10
    assert volume(doc.objects[other.id].shape) == pytest.approx(250*pi)
    assert doc.objects[parent.id] is before[parent.id]
    assert BRepCheck_Analyzer(doc.objects[other.id].shape).IsValid()
