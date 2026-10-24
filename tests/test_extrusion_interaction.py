"""Extrusion interaction: native picking, collectors and ownership tree."""
from dataclasses import replace
from math import pi

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox

from cadastrophe.core.feature_objects import BodyObject
from cadastrophe.presentation import PresentationSurface
from cadastrophe.ui.document_tree import ROLE
from test_extrusion_tool import extrusion_system, rectangle, start
from test_primitives import volume


def click_world(w, frame, u, v):
    x, y = PresentationSurface(w.viewport).project(frame.to_world(u, v))
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=QPoint(round(x), round(y)))


def orient(w, frame):
    w.viewport.camera.normal_to(frame)
    assert w.command_registry.execute("view.fit")
    w.viewport.view.Redraw()


def test_native_sketch_selection_outside_editor_enables_edit_and_extrude(extrusion_system):
    w, _, _ = extrusion_system
    sketch = rectangle(extrusion_system)
    doc = w.viewport.document
    frame = sketch.sketch.support.frame
    orient(w, frame)
    doc.select_objects(())
    click_world(w, frame, 50, 0)
    assert doc.selected_ids == (sketch.id,)
    assert w.services.selection_items[0].object_id == sketch.id
    assert w.services.selection_items[0].shape is None  # Whole logical sketch.
    assert w.command_registry.action("cadastrophe.sketch.edit").isEnabled()
    assert w.command_registry.action("modeling.extrude").isEnabled()
    # Entering/exiting Sketch must not retain dangling native feature owners.
    assert w.command_registry.execute("cadastrophe.sketch.edit")
    assert w.command_registry.execute("cadastrophe.sketch.finish")
    doc.select_objects(())
    click_world(w, frame, 50, 0)
    assert doc.selected_ids == (sketch.id,)


def test_collectors_pick_source_regions_and_holes_in_3d_without_document_mutation(extrusion_system):
    w, _, _ = extrusion_system
    sketch = rectangle(extrusion_system, hole=True, multiple=True)
    doc, frame = w.viewport.document, sketch.sketch.support.frame
    orient(w, frame)
    doc.select_objects(())
    before = dict(doc.objects)
    tool = start(w)
    assert not tool.editor.findChildren(QComboBox)
    QTest.mouseClick(tool.source.edit, Qt.MouseButton.LeftButton)
    click_world(w, frame, 50, 0)
    assert tool.source.value == sketch.id and tool.target == "contour"
    assert tool.region.value is None
    click_world(w, frame, 200, 0)  # Inside the second, disjoint region.
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(pi*100*20)
    click_world(w, frame, 20, 20)
    w.tool_sessions.update_preview()
    assert len(tool.selected_regions) == 2
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(6000*20)
    tool.region.clear_button.click()
    click_world(w, frame, 50, 30)  # The inner disk is now an independent cell.
    w.tool_sessions.update_preview()
    assert tool.region.value is not None and w.tool_sessions.can_commit
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(pi*100*20)
    click_world(w, frame, 50, 30)
    assert tool.region.value is None
    click_world(w, frame, 130, 30)  # Empty space never selects an arbitrary region.
    assert tool.region.value is None
    click_world(w, frame, 20, 20)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit
    assert dict(doc.objects) == before and doc.history.count == 0 and not doc.is_modified
    tool.source.clear_button.click()
    w.tool_sessions.update_preview()
    assert tool.source.value is None and tool.region.value is None and not w.tool_sessions.can_commit
    assert w.tool_sessions.cancel()
    assert w.viewport.tool_pick_handler is None and doc.selected_ids == ()


def test_tree_nests_body_feature_and_shared_sketch_aliases_with_undo(extrusion_system, tmp_path):
    w, _, _ = extrusion_system
    sketch = rectangle(extrusion_system, multiple=True)
    doc = w.viewport.document
    first = start(w, sketch)
    region_ids = tuple(outer.id for outer, _ in first.regions)
    first.choose_region(region_ids[0])
    assert w.tool_sessions.commit()
    second = start(w, doc.objects[sketch.id])
    second.choose_region(region_ids[1])
    assert w.tool_sessions.commit()
    bodies = [obj for obj in doc.objects.values() if isinstance(obj, BodyObject)]
    assert len(bodies) == 2
    adapter = w.tree_adapter
    for body in bodies:
        row = w.tree_items[body.id]
        assert row.child(0).data(0, ROLE) == body.features[0]
        assert row.child(0).child(0).data(0, ROLE) == sketch.id
    assert len(adapter.rows[sketch.id]) == 2
    doc.select_objects((sketch.id,))
    assert all(row.isSelected() for row in adapter.rows[sketch.id])
    doc.history.undo()
    assert len(adapter.rows[sketch.id]) == 1 and len([o for o in doc.objects.values() if isinstance(o, BodyObject)]) == 1
    doc.history.redo()
    assert len(adapter.rows[sketch.id]) == 2
    alias = adapter.rows[sketch.id][1]
    alias.setText(0, "Profil partagé")
    assert all(row.text(0) == "Profil partagé" for row in adapter.rows[sketch.id])
    assert len([o for o in doc.objects.values() if o.id == sketch.id]) == 1
    path = tmp_path / "shared.cada"
    assert w.save_document(path=path)
    assert w.close_document()
    loaded = w.open_document(path)
    QTest.qWait(100)
    assert loaded is not None and len(w.tree_adapter.rows[sketch.id]) == 2
    loaded.remove(bodies[0].id)
    assert len(w.tree_adapter.rows[sketch.id]) == 1
    assert w.tree_items[sketch.id].parent().data(0, ROLE) == bodies[1].features[0]


def test_extensions_menu_contains_only_manager_but_actions_remain_searchable(extrusion_system):
    w = extrusion_system[0]
    menu = next(action.menu() for action in w.menuBar().actions() if action.text() == "Extensions")
    assert len(menu.actions()) == 1
    assert "extensions" in menu.actions()[0].text().casefold()
    assert any(c.id == "cadastrophe.modeling.extrude" for c in w.command_registry.search("extrudé"))


def test_contextual_relations_hide_when_empty_and_anchor_follows_camera(extrusion_system):
    w, commands, m = extrusion_system
    sketch = rectangle(extrusion_system, hole=True)
    doc = w.viewport.document
    w.tree_selection_changed([sketch.id])
    assert w.command_registry.execute("cadastrophe.sketch.edit")
    session = w.tool_sessions.editor
    tools, surface = session.view_tools, session.surface
    namespace = __import__(tools.__module__, fromlist=["Reference"])
    Reference = namespace.Reference
    circle = next(e for e in doc.objects[sketch.id].sketch.entities if type(e) is m.Circle)
    tools.refs = [Reference(circle.id)]
    x, y = surface.project(session.workplane.to_world(60, 30))
    tools.show_toolbar(x, y)
    assert not tools.toolbar.isVisible()
    segment = doc.objects[sketch.id].sketch.entities[0]
    tools.refs = [Reference(segment.id)]
    tools.show_toolbar(*surface.project(session.workplane.to_world(50, 0)))
    assert tools.toolbar.isVisible()
    previous = tools.toolbar.pos()
    w.viewport.camera.pan(80, 30)
    session.view_changed()
    assert tools.toolbar.pos() != previous
    p = tools.toolbar_anchor
    x, y = surface.project(session.workplane.to_world(p.u, p.v))
    width, height = surface.size
    assert tools.toolbar.pos() == QPoint(max(0, min(round(x+16), width-tools.toolbar.width())),
                                        max(0, min(round(y+16), height-tools.toolbar.height())))
    tools.refs = [Reference(segment.id, "start")]
    tools.show_toolbar(*surface.project(session.workplane.to_world(0, 0)))
    tools.add_relation("fixed")
    assert len([c for c in doc.objects[sketch.id].sketch.constraints if c.kind == "fixed"]) == 1
    tools.refs = [Reference(segment.id, "start")]
    tools.add_relation("fixed")
    assert "existe déjà" in session.error
    assert len([c for c in doc.objects[sketch.id].sketch.constraints if c.kind == "fixed"]) == 1
    assert not tools.toolbar.isVisible()
