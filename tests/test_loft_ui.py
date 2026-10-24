"""Loft sections picked in the real native viewport and edited by the host."""
from dataclasses import replace
from math import pi

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QToolButton

from cadastrophe.core.feature_objects import LoftFeature, BodyObject
from cadastrophe.core.feature_model import ResultStatus
from cadastrophe.core.body_history import body_result
from test_extrusion_tool import extrusion_system
from test_parametric_operations_ui import screen
from test_draft_ui import iso_view
from test_feature_edit import edit
from test_primitives import volume, pose


def inputs(system):
    from test_parametric_operations import datum, plane
    from cadastrophe.core.operation_model import PlaneParameters, GeometryReference as Ref
    w, commands, m = system
    doc = w.viewport.document
    top = datum(doc, PlaneParameters("offset", (Ref("plane", plane(doc).id),), distance=10))
    a = commands.create_sketch(doc, plane(doc).id)
    commands.add_entity(doc, a.id, m.Circle(m.Vec2(0, 0), 2))
    b = commands.create_sketch(doc, top.id)
    commands.add_entity(doc, b.id, m.Circle(m.Vec2(0, 0), 4))
    doc.history.clear()
    return doc, a, b


def add_section(tool, sketch):
    tool.section_sketch.setCurrentIndex(tool.section_sketch.findData(sketch.id))


def test_loft_native_section_click_preview_edit_undo_and_reopen(extrusion_system, tmp_path):
    w, _, _ = extrusion_system
    doc, a, b = inputs(extrusion_system)
    iso_view(w)
    camera = pose(w.viewport)
    w.tree_selection_changed([a.id])
    action = w.command_registry.action("modeling.loft")
    button = next(b for b in w.command_manager.findChildren(QToolButton) if b.defaultAction() is action)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    tool = w.tool_sessions.tool
    assert tool is not None and len(tool.sections) == 1
    assert tool.matching.itemText(tool.matching.findData("automatic")) == "Automatique"
    before = dict(doc.objects)
    assert not w.tool_sessions.can_commit
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=screen(w, (4., 0., 10.)))
    w.tool_sessions.update_preview()
    assert len(tool.sections) == 2
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(280*pi/3, abs=1e-4)
    assert dict(doc.objects) == before and doc.history.count == 0
    assert pose(w.viewport) == camera
    assert w.tool_sessions.commit(), w.tool_sessions.message
    feature, = (o for o in doc.objects.values() if isinstance(o, LoftFeature))
    body, = (o for o in doc.objects.values() if isinstance(o, BodyObject))
    ids = set(doc.objects)
    assert doc.history.count == 1
    assert w.tree_items[a.id].parent() is w.tree_items[feature.id]
    assert w.tree_items[b.id].parent() is w.tree_items[feature.id]
    assert not doc.objects[a.id].visible and not doc.objects[b.id].visible
    doc.history.undo()
    assert body.id not in doc.objects and doc.objects[a.id].visible and doc.objects[b.id].visible
    doc.history.redo()
    tool = edit(w, feature.id, double_click=True)
    tool.mode.setCurrentIndex(tool.mode.findData("ruled"))
    tool.matching.setCurrentIndex(tool.matching.findData("explicit"))
    tool.section_list.setCurrentRow(1)
    tool.move_section(-1)
    assert tool.sections[0].source.sketch_id == b.id
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert set(doc.objects) == ids
    doc.history.undo()
    doc.history.redo()
    filename = tmp_path/"lissage_native.cada"
    assert w.save_document(path=filename)
    assert w.close_document()
    loaded = w.open_document(filename)
    assert loaded is not doc and set(loaded.objects) == ids
    assert loaded.objects[feature.id].parameters.mode == "ruled"
    assert loaded.objects[feature.id].parameters.sections[0].source.sketch_id == b.id
    assert volume(loaded.objects[body.id].shape) == pytest.approx(280*pi/3, abs=1e-4)


@pytest.mark.parametrize("cut", [False, True])
def test_loft_body_target_cancel_and_atomic_commit(extrusion_system, cut):
    from cadastrophe.document import SceneObject
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.gp import gp_Pnt
    w, _, _ = extrusion_system
    doc, a, b = inputs(extrusion_system)
    block = SceneObject("Cible", BRepPrimAPI_MakeBox(gp_Pnt(-5, -5, 0 if cut else -5), 10., 10., 10. if cut else 5.).Shape(), (.4, .5, .6))
    doc.add(block)
    doc.history.clear()
    w.tree_selection_changed([a.id])
    assert w.command_registry.execute("modeling.cut_loft" if cut else "modeling.loft")
    tool = w.tool_sessions.tool
    add_section(tool, b)
    w.tool_sessions.update_preview()
    expected = (1000 if cut else 500)+(-1 if cut else 1)*280*pi/3
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(expected, abs=1e-4)
    before = dict(doc.objects)
    assert w.tool_sessions.cancel()
    assert dict(doc.objects) == before and doc.history.count == 0
    assert w.command_registry.execute("modeling.cut_loft" if cut else "modeling.loft")
    add_section(w.tool_sessions.tool, b)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert doc.objects[block.id].features
    assert volume(doc.objects[block.id].shape) == pytest.approx(expected, abs=1e-4)


def test_loft_missing_anchor_can_be_repaired_without_changing_identity(extrusion_system, tmp_path):
    from uuid import uuid4
    from test_loft import system, section_at, section, lofted, LoftParameters
    from cadastrophe.core.persistence import save_document
    from cadastrophe.core.body_history import evaluate_body
    from examples.parametric_example import registry
    doc, service = system()
    sketches = (section_at(doc, 0), section_at(doc, 10))
    body, feature, result = lofted(doc, service, sketches,
        parameters=LoftParameters(tuple(section(s, explicit=True) for s in sketches), matching="explicit"))
    sections = (feature.parameters.sections[0], replace(feature.parameters.sections[1], anchors=(str(uuid4()),)))
    doc.replace_feature(replace(feature, parameters=replace(feature.parameters, sections=sections)))
    assert evaluate_body(doc, body.id, service.evaluator(doc)).status is ResultStatus.ERROR
    filename = tmp_path/"lissage_a_reparer.cada"
    save_document(doc, filename, registry=registry(service))
    w, _, _ = extrusion_system
    loaded = w.open_document(filename)
    iso_view(w)
    ids = set(loaded.objects)
    tool = edit(w, feature.id)
    assert not w.tool_sessions.can_commit
    tool.section_list.setCurrentRow(1)
    anchor = sketches[1].sketch.entities[0].id
    tool.anchor_fields[0].setCurrentIndex(tool.anchor_fields[0].findData(anchor))
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert set(loaded.objects) == ids and loaded.objects[body.id].features == (feature.id,)
    assert volume(loaded.objects[body.id].shape) == pytest.approx(40*pi)
    loaded.history.undo()
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert body_result(loaded, body.id).status is ResultStatus.ERROR
    loaded.history.redo()
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert body_result(loaded, body.id).status is ResultStatus.READY


def test_loft_section_replacement_remove_and_new_body(extrusion_system):
    w, commands, m = extrusion_system
    doc, a, b = inputs(extrusion_system)
    replacement = commands.create_sketch(doc, b.sketch.support.plane_id)
    commands.add_entity(doc, replacement.id, m.Circle(m.Vec2(0, 0), 3))
    w.tree_selection_changed([a.id])
    assert w.command_registry.execute("modeling.loft")
    tool = w.tool_sessions.tool
    tool.merge.setChecked(False)
    add_section(tool, b)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    feature, = (o for o in doc.objects.values() if isinstance(o, LoftFeature))
    ids = set(doc.objects)
    tool = edit(w, feature.id)
    tool.section_list.setCurrentRow(1)
    tool.remove_section()
    w.tool_sessions.update_preview()
    assert not w.tool_sessions.can_commit
    add_section(tool, b)
    tool.replace_section.setChecked(True)
    w.tree_selection_changed([replacement.id])
    assert tool.sections[1].source.sketch_id == replacement.id
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert set(doc.objects) == ids and doc.objects[feature.id].parameters.sections[1].source.sketch_id == replacement.id
    assert volume(doc.objects[feature.body_id].shape) == pytest.approx(190*pi/3, abs=1e-4)
    doc.history.undo()
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert doc.objects[feature.id].parameters.sections[1].source.sketch_id == b.id


def test_loft_multi_region_section_requires_explicit_region_choice(extrusion_system):
    w, commands, m = extrusion_system
    doc, a, b = inputs(extrusion_system)
    commands.add_entity(doc, b.id, m.Circle(m.Vec2(12, 0), 2))
    w.tree_selection_changed([a.id])
    assert w.command_registry.execute("modeling.loft")
    tool = w.tool_sessions.tool
    add_section(tool, b)
    w.tool_sessions.update_preview()
    assert not w.tool_sessions.can_commit and not tool.sections[-1].contours
    tool.region.setCurrentIndex(1)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert len(tool.sections[-1].contours) == 1 and tool.anchor_fields
    before = dict(doc.objects)
    assert w.tool_sessions.cancel()
    assert dict(doc.objects) == before
