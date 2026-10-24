"""Real plugin action, point selection, detached preview and historic editing."""
from dataclasses import replace
from math import pi

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QToolButton
from OCC.Core.BRepCheck import BRepCheck_Analyzer

from cadastrophe.core.feature_objects import HoleFeature
from cadastrophe.geometry import Vec3
from test_extrusion_tool import extrusion_system
from test_boss_tool import base_body, face_sketch
from test_feature_edit import edit
from test_primitives import volume


def positions(system, body, *, face=False):
    w, commands, m = system
    doc = w.viewport.document
    if face:
        obj = face_sketch(system, body)
        # Reuse the existing support tool, preserving its captured producer.
        point = m.Point(m.Vec2(*obj.sketch.support.frame.to_local(Vec3(50, 30, 20))[:2]))
        obj = replace(obj, sketch=replace(obj.sketch, entities=(point,)))
        doc.update_construction((obj,))
        return obj
    xy = next(o for o in doc.objects.values() if o.plane and o.plane.name == "XY")
    obj = commands.create_sketch(doc, xy.id)
    commands.add_entities(doc, obj.id, (m.Point(m.Vec2(20, 20)), m.Point(m.Vec2(70, 20))), title="Points")
    return doc.objects[obj.id]


@pytest.mark.parametrize("form", ("simple", "counterbore", "countersink"))
def test_holes_ui_ribbon_atomic_preview_edit_cancel_undo_reopen(extrusion_system, tmp_path, form):
    w, _, _ = extrusion_system
    doc = w.viewport.document
    body = base_body(extrusion_system)
    sketch = positions(extrusion_system, body)
    doc.history.clear()
    before = dict(doc.objects)
    w.tree_selection_changed([sketch.id])
    action = w.command_registry.action("modeling.hole_wizard")
    button = next(b for b in w.command_manager.findChildren(QToolButton) if b.defaultAction() is action)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    tool = w.tool_sessions.tool
    assert tool is not None and tool.body.currentData() == body.id
    tool.body.setCurrentIndex(tool.body.findData(body.id))
    tool.form.setCurrentIndex(tool.form.findData(form))
    tool.all_points.setChecked(False)
    tool.positions.item(0).setCheckState(Qt.CheckState.Checked)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    preview_volume = volume(w.tool_sessions.preview_object.shape)
    assert dict(doc.objects) == before and doc.history.count == 0
    assert BRepCheck_Analyzer(w.tool_sessions.preview_object.shape).IsValid()
    assert w.tool_sessions.commit(), w.tool_sessions.message
    feature, = (o for o in doc.objects.values() if isinstance(o, HoleFeature))
    assert feature.parameters.form == form and len(feature.parameters.positions) == 1
    assert feature.id in w.tree_items and doc.history.count == 1
    assert volume(doc.objects[body.id].shape) == pytest.approx(preview_volume)
    tool = edit(w, feature.id, double_click=True)
    tool.fields["diameter"].setValue(7.)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.cancel()
    assert doc.objects[feature.id] == feature
    tool = edit(w, feature.id)
    tool.fields["diameter"].setValue(8.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert doc.objects[feature.id].parameters.diameter == 8.
    doc.history.undo()
    assert doc.objects[feature.id].parameters == feature.parameters
    doc.history.redo()
    path = tmp_path / (form + ".cada")
    expected = volume(doc.objects[body.id].shape)
    assert w.save_document(path=path)
    assert w.close_document()
    restored = w.open_document(path)
    assert restored.objects[feature.id].id == feature.id
    assert volume(restored.objects[body.id].shape) == pytest.approx(expected)


def test_holes_ui_face_support_through_all_reverse_and_invalid_atomic(extrusion_system):
    w, _, _ = extrusion_system
    doc = w.viewport.document
    body = base_body(extrusion_system)
    sketch = positions(extrusion_system, body, face=True)
    doc.history.clear()
    before = dict(doc.objects)
    w.tree_selection_changed([sketch.id])
    assert w.command_registry.execute("modeling.hole_wizard")
    tool = w.tool_sessions.tool
    tool.body.setCurrentIndex(tool.body.findData(body.id))
    tool.condition.setCurrentIndex(tool.condition.findData("through_all"))
    w.tool_sessions.update_preview()
    assert not w.tool_sessions.can_commit  # Points away from the upper face.
    tool.reverse.setChecked(True)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(volume(body.shape) - pi*9*20)
    assert dict(doc.objects) == before and doc.history.count == 0
    assert w.tool_sessions.commit(), w.tool_sessions.message
    doc.history.undo()
    assert set(doc.objects) == set(before)
    assert doc.objects[body.id].features == before[body.id].features
    assert volume(doc.objects[body.id].shape) == pytest.approx(volume(body.shape))
    assert doc.objects[sketch.id].sketch == sketch.sketch
    doc.history.redo()
    assert BRepCheck_Analyzer(doc.objects[body.id].shape).IsValid()


def test_holes_ui_missing_point_repair_panel_and_equation_roundtrip(extrusion_system, tmp_path):
    from cadastrophe.core.operation_model import GeometryReference
    from cadastrophe.core.expressions import Variable, Expression
    from test_reference_repair import begin, row, apply, flush
    w, _, _ = extrusion_system
    doc = w.viewport.document
    body = base_body(extrusion_system)
    sketch = positions(extrusion_system, body)
    doc.update_construction(variables=(Variable("Diametre", Expression("value", "=8")),))
    w.tree_selection_changed([sketch.id])
    assert w.command_registry.execute("modeling.hole_wizard")
    tool = w.tool_sessions.tool
    tool.body.setCurrentIndex(tool.body.findData(body.id))
    tool.all_points.setChecked(False)
    tool.positions.item(0).setCheckState(Qt.CheckState.Checked)
    field = tool.fields["diameter"]
    field.lineEdit().setText("=Diametre")
    field.lineEdit().textEdited.emit("=Diametre")
    assert w.tool_sessions.commit(), w.tool_sessions.message
    feature, = (o for o in doc.objects.values() if isinstance(o, HoleFeature))
    assert feature.parameters.diameter == 8. and feature.expressions
    first, replacement = sketch.sketch.entities
    changed = replace(sketch, sketch=sketch.sketch.without(first.id))
    doc.update_construction((changed,), allow_errors=True)
    flush(w)
    repair = begin(w, feature.id)
    entry = row(repair, lambda e: e.role == "point")
    ref = GeometryReference("sketch_point", sketch.id, entity_id=replacement.id, part="position")
    assert any(repair.replacement.itemData(i) == ref for i in range(repair.replacement.count()))
    apply(w, repair, entry, ref)
    assert doc.objects[feature.id] == feature
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert doc.objects[feature.id].parameters.positions == (ref,)
    assert doc.objects[feature.id].expressions == feature.expressions
    assert BRepCheck_Analyzer(doc.objects[body.id].shape).IsValid()
    doc.history.undo()
    flush(w)
    assert doc.objects[feature.id].parameters.positions == feature.parameters.positions
    doc.history.redo()
    flush(w)
    path = tmp_path / "reparation-percage.cada"
    assert w.save_document(path=path)
    assert w.close_document()
    restored = w.open_document(path)
    assert restored.objects[feature.id].parameters.positions == (ref,)
    assert restored.objects[feature.id].expressions == feature.expressions
