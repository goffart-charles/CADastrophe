"""User paths: discoverable materials, inferred hole target and repairable threads."""
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox

from cadastrophe.document import SceneObject
from cadastrophe.core.body_history import body_result
from cadastrophe.core.feature_model import ResultStatus
from cadastrophe.core.feature_objects import BodyObject, ThreadFeature
from cadastrophe.core.materials import Material
from test_extrusion_tool import extrusion_system
from test_measurements_ui import window
from test_boss_tool import base_body
from test_holes_ui import positions
from test_interactive_computation import wait_for


def test_preview_progress_does_not_take_focus_from_numeric_entry():
    from PySide6.QtWidgets import QApplication, QMainWindow
    from cadastrophe.ui.numeric_input import DecimalSpinBox
    from cadastrophe.ui.computation import Computation
    app = QApplication.instance() or QApplication([])
    owner = QMainWindow()
    field = DecimalSpinBox()
    field.setDecimals(3)
    field.setKeyboardTracking(False)
    owner.setCentralWidget(field)
    owner.show()
    owner.activateWindow()
    app.processEvents()
    assert QTest.qWaitForWindowActive(owner, 1000)
    field.setFocus()
    field.lineEdit().selectAll()
    QTest.keyClicks(field.lineEdit(), '2')
    task = Computation(owner, iter(()), 'Aperçu en cours', preserve_focus=True)
    task.timer.stop()
    try:
        task.dialog.show()
        app.processEvents()
        assert task.dialog.isVisible() and field.hasFocus()
        QTest.keyClicks(field.lineEdit(), '.25')
        field.interpretText()
        assert field.cleanText().replace(',', '.') == '2.25'
        assert field.value() == pytest.approx(2.25)
    finally:
        task.cancel()
        owner.close()
        owner.deleteLater()
        app.processEvents()


def test_applied_material_visible_in_body_properties_tree_and_after_undo(window):
    w = window
    doc = w.viewport.document
    body = next(obj for obj in doc.objects.values() if obj.name == 'Cube')
    w.services.select_objects((body.id,))
    w.command_registry.execute('object.properties')
    assert 'Matériau : non affecté' in w.manager_pane.selection_detail.text()
    doc.set_materials({body.id: Material('Acier', 7850.)})
    assert 'Matériau : Acier' in w.manager_pane.selection_detail.text()
    assert '7850' in w.manager_pane.selection_detail.text()
    assert 'Acier' in w.tree_adapter.items[body.id].toolTip(0)
    doc.history.undo()
    assert 'Matériau : non affecté' in w.manager_pane.selection_detail.text()
    doc.history.redo()
    assert 'Matériau : Acier' in w.manager_pane.selection_detail.text()


def test_hole_preselects_unique_body_before_choosing_points(extrusion_system):
    w, _, _ = extrusion_system
    body = base_body(extrusion_system)
    w.services.select_objects(())
    assert w.command_registry.execute('modeling.hole_wizard')
    assert w.tool_sessions.tool.source.currentData() is None
    assert w.tool_sessions.tool.body.currentData() == body.id


def test_hole_accepts_click_on_a_points_sketch_in_the_native_view(extrusion_system):
    from PySide6.QtCore import QPoint
    w, _, _ = extrusion_system
    body = base_body(extrusion_system)
    sketch = positions(extrusion_system, body, face=True)
    w.services.select_objects(())
    w.viewport.view.SetProj(0., 0., 1.)
    w.viewport.frame_objects((body.id,))
    assert w.command_registry.execute('modeling.hole_wizard')
    tool = w.tool_sessions.tool
    assert tool.source.currentData() is None
    QTest.mouseClick(tool.sketch_pick.edit, Qt.MouseButton.LeftButton)
    assert w.viewport.hasFocus()
    x, y = w.viewport.view.Convert(50., 30., 20.)
    scale = w.viewport.devicePixelRatioF()
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=QPoint(round(x/scale), round(y/scale)))
    assert tool.source.currentData() == sketch.id
    assert tool.body.currentData() == body.id
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message


def test_hole_accepts_body_click_when_the_sketch_target_is_ambiguous(extrusion_system):
    from PySide6.QtCore import QPoint
    w, _, _ = extrusion_system
    body = base_body(extrusion_system)
    sketch = positions(extrusion_system, body)
    w.viewport.document.add(SceneObject('Autre corps', BRepPrimAPI_MakeBox(5., 6., 7.).Shape(), (.5, .5, .5)))
    w.services.select_objects((sketch.id,))
    w.viewport.view.SetProj(0., 0., 1.)
    w.viewport.frame_objects((body.id,))
    assert w.command_registry.execute('modeling.hole_wizard')
    tool = w.tool_sessions.tool
    assert tool.body.currentData() is None and tool.pick_target == 'body'
    x, y = w.viewport.view.Convert(40., 30., 20.)
    scale = w.viewport.devicePixelRatioF()
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=QPoint(round(x/scale), round(y/scale)))
    assert tool.body.currentData() == body.id
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message


@pytest.mark.parametrize('separator', ('.', ','))
def test_thread_edit_keeps_slow_decimal_typing_while_preview_runs(extrusion_system, separator):
    from test_threads import support
    w, _, _ = extrusion_system
    doc = w.viewport.document
    shape, _, _ = support(True)
    doc.add(SceneObject('Taraudage à éditer', shape, (.6, .7, .8)))
    assert w.command_registry.execute('modeling.thread')
    tool = w.tool_sessions.tool
    tool.support.setCurrentIndex(1)
    tool.fields['length'].set_internal_value(3.)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert w.command_registry.execute('cadastrophe.modeling.edit_feature')
    field = w.tool_sessions.tool.fields['length']
    field.setFocus()
    field.lineEdit().selectAll()
    typed = ''
    for character in '2'+separator+'25':
        QTest.keyClicks(field.lineEdit(), character)
        typed += character
        QTest.qWait(220)
        assert field.hasFocus()
        assert field.cleanText().replace(',', '.') == typed.replace(',', '.')
    QTest.keyClick(field, Qt.Key.Key_Tab)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert w.tool_sessions.commit(), w.tool_sessions.message
    thread = next(obj for obj in doc.objects.values() if isinstance(obj, ThreadFeature))
    assert thread.parameters.length == pytest.approx(2.25)
    assert BRepCheck_Analyzer(doc.objects[thread.body_id].shape).IsValid()


@pytest.mark.parametrize('internal', (False, True))
def test_thread_honors_an_actually_preselected_cylindrical_face(extrusion_system, internal):
    from math import cos, sin, pi
    from PySide6.QtCore import QPoint
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.GeomAbs import GeomAbs_Cylinder
    from OCC.Core.TopoDS import topods
    from test_threads import support
    w, _, _ = extrusion_system
    shape, _, _ = support(internal)
    body = SceneObject('Trou' if internal else 'Tige', shape, (.6, .7, .8))
    w.viewport.document.add(body)
    w.viewport.view.SetProj(1., 0., 1.)
    w.viewport.frame_objects((body.id,))
    w.viewport.view.Redraw()
    selected = None
    for angle in (i*pi/8 for i in range(16)):
        for z in (5., 8., 9.):
            radius = 2.5 if internal else 3.
            x, y = w.viewport.view.Convert(radius*cos(angle), radius*sin(angle), z)
            ratio = w.viewport.devicePixelRatioF()
            QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=QPoint(round(x/ratio), round(y/ratio)))
            items = w.services.selection_items
            if len(items) == 1 and items[0].topology == 'Face':
                surface = BRepAdaptor_Surface(topods.Face(items[0].shape))
                if surface.GetType() == GeomAbs_Cylinder and abs(surface.Cylinder().Radius()-radius) < 1e-6:
                    selected = items[0]
                    break
        if selected is not None:
            break
    assert selected is not None
    assert w.command_registry.execute('modeling.thread')
    tool = w.tool_sessions.tool
    assert tool.face_ref is not None and tool.face_ref.object_id == body.id
    assert tool.internal == internal


@pytest.mark.parametrize('editing', (False, True))
def test_hole_prefers_actual_sketch_support_with_multiple_bodies(extrusion_system, editing):
    w, _, _ = extrusion_system
    body = base_body(extrusion_system)
    doc = w.viewport.document
    other = SceneObject('Autre corps', BRepPrimAPI_MakeBox(5., 6., 7.).Shape(), (.5, .5, .5))
    doc.add(other)
    sketch = positions(extrusion_system, body, face=True)
    w.services.select_objects((sketch.id,))
    if editing:
        assert w.command_registry.execute('cadastrophe.sketch.edit')
        assert w.tool_sessions.editing
    assert w.command_registry.execute('modeling.hole_wizard')
    assert w.tool_sessions.tool.body.currentData() == body.id


def test_hole_keeps_ambiguous_target_empty_and_follows_associative_plane(extrusion_system):
    from cadastrophe.core.operation_model import GeometryReference, PlaneParameters
    from test_parametric_operations import datum
    w, commands, m = extrusion_system
    body = base_body(extrusion_system)
    doc = w.viewport.document
    doc.add(SceneObject('Autre corps', BRepPrimAPI_MakeBox(5., 6., 7.).Shape(), (.5, .5, .5)))
    sketch = positions(extrusion_system, body)
    w.services.select_objects((sketch.id,))
    assert w.command_registry.execute('modeling.hole_wizard')
    assert w.tool_sessions.tool.body.currentData() is None
    w.tool_sessions.cancel()
    attached = positions(extrusion_system, body, face=True)
    support = attached.sketch.support
    ref = GeometryReference('face', body.id, support.producer_id, support.reference)
    plane = datum(doc, PlaneParameters('offset', (ref,), distance=2.))
    points = commands.create_sketch(doc, plane.id)
    commands.add_entities(doc, points.id, (m.Point(m.Vec2(5., 5.)),), title='Position')
    w.services.select_objects((points.id,))
    assert w.command_registry.execute('modeling.hole_wizard')
    assert w.tool_sessions.tool.body.currentData() == body.id


def threaded_cylinder(system):
    w, commands, m = system
    doc = w.viewport.document
    xy = next(obj for obj in doc.objects.values() if obj.plane and obj.plane.name == 'XY')
    sketch = commands.create_sketch(doc, xy.id)
    commands.add_entities(doc, sketch.id, (m.Circle(m.Vec2(0., 0.), 2.5),), title='Cercle Ø5')
    w.services.select_objects((sketch.id,))
    assert w.command_registry.execute('modeling.extrude')
    w.tool_sessions.tool.depth.set_internal_value(10.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    body = next(obj for obj in doc.objects.values() if isinstance(obj, BodyObject))
    w.services.select_objects(())
    assert w.command_registry.execute('modeling.thread')
    tool = w.tool_sessions.tool
    assert tool.face_ref is None
    tool.support.setCurrentIndex(1)
    tool.fields['length'].set_internal_value(2.5)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.commit(), w.tool_sessions.message
    wait_for(lambda: not w.computations.busy)
    thread = next(obj for obj in doc.objects.values() if isinstance(obj, ThreadFeature))
    sketch = doc.objects[sketch.id]
    circle, = sketch.sketch.entities
    doc.update_construction((replace(sketch, sketch=replace(sketch.sketch,
        entities=(replace(circle, radius=2.75),))),), allow_errors=True)
    w.plugins._record('cadastrophe.modeling').instance.rebuilder.flush()
    assert body_result(doc, body.id).status is ResultStatus.ERROR
    return body.id, thread.id


def tree_popup(w, key, monkeypatch):
    row = w.tree_adapter.items[key]
    w.tree.expandAll()
    w.tree.scrollToItem(row)
    menus = []
    monkeypatch.setattr(w, '_show_context_menu', lambda menu, pos: menus.append(menu))
    point = w.tree.visualItemRect(row).center()
    QTest.mouseClick(w.tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
    w.tree_menu(point)
    assert menus
    return menus[-1]


def test_body_error_menu_targets_its_recipes_without_a_visible_final_solid(extrusion_system, monkeypatch):
    w, _, _ = extrusion_system
    doc = w.viewport.document
    body_id, thread_id = threaded_cylinder(extrusion_system)
    assert doc.objects[body_id].shape is None
    menu = tree_popup(w, body_id, monkeypatch)
    def actions(popup):
        for action in popup.actions():
            yield action
            if action.menu():
                yield from actions(action.menu())
    command = 'cadastrophe.modeling.history_before'
    back = next(a for a in actions(menu) if a.data() == (thread_id, command))
    assert back.isEnabled()
    back.trigger()
    assert doc.history_context.position.feature_id == thread_id
    assert w.viewport.presentations.get(body_id) is not None
    assert w.command_registry.execute('cadastrophe.modeling.history_end')
    menu = tree_popup(w, body_id, monkeypatch)
    command = 'cadastrophe.modeling.edit_feature'
    modify = next(a for a in actions(menu) if a.data() == (thread_id, command))
    assert modify.isEnabled()
    modify.trigger()
    assert w.tool_sessions.tool.feature.id == thread_id
    assert w.tool_sessions.tool.adapt_diameter.isEnabled()


def test_thread_diameter_error_remains_editable_and_history_accessible(extrusion_system, monkeypatch, tmp_path):
    w, _, _ = extrusion_system
    doc = w.viewport.document
    body_id, thread_id = threaded_cylinder(extrusion_system)
    menu = tree_popup(w, thread_id, monkeypatch)
    for suffix in ('edit_feature', 'history_before', 'history_after'):
        action = w.command_registry.action('cadastrophe.modeling.'+suffix)
        assert action in menu.actions() and action.isEnabled(), (suffix, w.services.busy, doc.selected_ids)
    assert w.command_registry.execute('cadastrophe.modeling.history_before')
    assert doc.history_context.position.feature_id == thread_id
    assert w.viewport.presentations.get(body_id) is not None
    assert w.command_registry.execute('cadastrophe.modeling.history_end')
    assert w.command_registry.execute('cadastrophe.modeling.edit_feature')
    tool = w.tool_sessions.tool
    assert tool.adapt_diameter.isVisible() and tool.adapt_diameter.isEnabled()
    QTest.mouseClick(tool.adapt_diameter, Qt.MouseButton.LeftButton)
    assert tool.fields['diameter'].internal_value == pytest.approx(5.5)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert body_result(doc, body_id).status is ResultStatus.READY
    assert BRepCheck_Analyzer(doc.objects[body_id].shape).IsValid()
    doc.history.undo()
    assert body_result(doc, body_id).status is ResultStatus.ERROR
    doc.history.redo()
    assert body_result(doc, body_id).status is ResultStatus.READY
    path = tmp_path/'vis-réparée.cada'
    assert w.save_document(path=path)
    assert w.close_document() and w.open_document(path) is not None
    assert body_result(w.viewport.document, body_id).status is ResultStatus.READY
