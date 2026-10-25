"""Native ribbon, preset fields, cooperative preview and one thread transaction."""
from dataclasses import replace

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QToolButton

from cadastrophe.document import SceneObject
from cadastrophe.core.feature_objects import ThreadFeature, HoleFeature
from cadastrophe.core.mass_properties import precise_volume
from test_extrusion_tool import extrusion_system
from test_holes_ui import positions
from test_boss_tool import base_body
from test_threads import support
from test_interactive_computation import wait_for


@pytest.mark.parametrize('selection', ('none', 'body'))
def test_thread_ui_starts_empty_picks_actual_hole_wall_and_accepts_decimal_keys(extrusion_system, selection):
    from math import cos, sin, pi
    from PySide6.QtCore import QEvent, QPoint, QPointF
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.GeomAbs import GeomAbs_Cylinder
    from OCC.Core.TopoDS import topods
    w, _, _ = extrusion_system
    doc = w.viewport.document
    body = base_body(extrusion_system)
    sketch = positions(extrusion_system, body)
    w.tree_selection_changed([sketch.id])
    assert w.command_registry.execute('modeling.hole_wizard')
    hole = w.tool_sessions.tool
    hole.fields['diameter'].set_internal_value(5.)
    hole.condition.setCurrentIndex(hole.condition.findData('through_all'))
    assert w.tool_sessions.commit(), w.tool_sessions.message
    w.services.select_objects(())
    if selection == 'body':
        w.services.select_objects((body.id,))
    w.viewport.view.SetProj(.25, .2, 1.)
    w.viewport.view.FitAll(.05, False)
    w.viewport.view.Redraw()
    assert w.command_registry.execute('modeling.thread')
    tool = w.tool_sessions.tool
    assert tool.support.currentData() is None and tool.face_ref is None
    QTest.mouseClick(tool.face_field.edit, Qt.MouseButton.LeftButton)
    assert w.viewport.hasFocus()
    wait_for(lambda: not w.computations.busy)
    assert not w.tool_sessions.can_commit
    point = None
    for angle in (i*pi/8 for i in range(16)):
        for z in (19., 18., 19.5):
            px, py = w.viewport.view.Convert(20+2.5*cos(angle), 20+2.5*sin(angle), z)
            scale = w.viewport.devicePixelRatioF()
            x, y = round(px/scale), round(py/scale)
            item = tool.surface.pick_face(x, y)
            if item is not None:
                surface = BRepAdaptor_Surface(topods.Face(item.shape))
                if surface.GetType() == GeomAbs_Cylinder and abs(surface.Cylinder().Radius()-2.5) < 1e-6:
                    point = QPoint(x, y)
                    break
        if point is not None:
            break
    assert point is not None, 'La paroi cylindrique doit être visible dans le trou.'
    # A private Windows desktop does not receive OS cursor moves from QTest.
    QApplication.sendEvent(w.viewport, QMouseEvent(QEvent.Type.MouseMove, QPointF(point),
        QPointF(w.viewport.mapToGlobal(point)), Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    assert any(w.viewport.context.IsDisplayed(ais) for ais in tool.surface._feedback)
    QApplication.sendEvent(w.viewport, QEvent(QEvent.Type.Leave))
    assert not tool.surface._feedback
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=point)
    assert tool.face_ref is not None and tool.internal
    assert tool._supports[tool.face_ref][3]
    for key, text, value in (('pitch', '0.75', .75), ('length', '2,25', 2.25)):
        field = tool.fields[key]
        field.setFocus()
        field.lineEdit().selectAll()
        QTest.keyClicks(field.lineEdit(), text)
        QTest.keyClick(field, Qt.Key.Key_Tab)
        assert field.internal_value == pytest.approx(value), field.lineEdit().text()
    # Restore the metric pitch; test its geometry independently of custom-profile limits.
    tool.fields['pitch'].set_internal_value(1.)
    w.tool_sessions.update_preview(interactive=True)
    wait_for(lambda: w.tool_sessions.can_commit, timeout=60)
    assert w.tool_sessions.commit_interactive(), w.tool_sessions.message
    wait_for(lambda: w.tool_sessions.tool is None, timeout=60)
    thread = next(obj for obj in doc.objects.values() if isinstance(obj, ThreadFeature))
    assert thread.parameters.internal and thread.parameters.length == pytest.approx(2.25)


def test_fastener_ui_metric_tap_drill_preset_custom_cancel_and_reopen(extrusion_system, tmp_path):
    w, _, _ = extrusion_system
    doc = w.viewport.document
    body = base_body(extrusion_system)
    sketch = positions(extrusion_system, body)
    doc.history.clear()
    w.tree_selection_changed([sketch.id])
    assert w.command_registry.execute("modeling.hole_wizard")
    tool = w.tool_sessions.tool
    assert tool.body.currentData() == body.id
    tool.catalog_family.setCurrentIndex(tool.catalog_family.findData("tap_drill"))
    index = next(i for i in range(tool.catalog_size.count()) if tool.catalog_size.itemData(i).nominal == 6)
    tool.catalog_size.setCurrentIndex(index)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert tool.fields["diameter"].internal_value == 5.
    assert w.tool_sessions.commit(), w.tool_sessions.message
    feature = next(o for o in doc.objects.values() if isinstance(o, HoleFeature))
    assert feature.parameters.catalog_active and doc.history.count == 1
    from test_feature_edit import edit
    tool = edit(w, feature.id)
    tool.fields["diameter"].setValue(5.2)
    w.tool_sessions.update_preview()
    assert "personnalisées" in tool.catalog_info.text()
    w.tool_sessions.cancel()
    assert doc.objects[feature.id].parameters.catalog_active
    path = tmp_path / "avant-trous.cada"
    assert w.save_document(path=path)
    assert w.close_document()
    restored = w.open_document(path)
    assert restored.objects[feature.id].parameters == feature.parameters


@pytest.mark.parametrize("internal", (False, True))
def test_thread_ui_native_action_atomic_cooperative_preview_edit_and_reopen(extrusion_system, tmp_path, internal):
    w, _, _ = extrusion_system
    doc = w.viewport.document
    shape, _, _ = support(internal)
    body = SceneObject("Écrou" if internal else "Tige de vis", shape, (.6, .7, .8))
    doc.add(body)
    doc.history.clear()
    before = dict(doc.objects)
    action = w.command_registry.action("modeling.thread")
    button = next(b for b in w.command_manager.findChildren(QToolButton) if b.defaultAction() is action)
    beats = []
    timer = QTimer()
    timer.setInterval(1)
    timer.timeout.connect(lambda: beats.append(1))
    timer.start()
    try:
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        tool = w.tool_sessions.tool
        assert tool is not None
        index = next(i for i in range(1, tool.support.count())
                     if tool.support.itemData(i).object_id == body.id
                     and tool._supports[tool.support.itemData(i)][3] == internal)
        tool.support.setCurrentIndex(index)
        tool.fields["length"].set_internal_value(3.)
        w.tool_sessions.update_preview(interactive=True)
        wait_for(lambda: w.tool_sessions.can_commit, timeout=30)
        assert dict(doc.objects) == before and doc.history.count == 0 and beats
        preview = precise_volume(w.tool_sessions.preview_object.shape)
        assert w.tool_sessions.commit(), w.tool_sessions.message
        wait_for(lambda: w.tool_sessions.tool is None, timeout=30)
    finally:
        timer.stop()
    feature = next(o for o in doc.objects.values() if isinstance(o, ThreadFeature))
    assert feature.parameters.internal == internal and feature.parameters.catalog_active
    assert doc.history.count == 1 and feature.id in w.tree_items
    assert precise_volume(doc.objects[body.id].shape) == pytest.approx(preview)
    doc.history.undo()
    assert feature.id not in doc.objects and type(doc.objects[body.id]) is SceneObject
    doc.history.redo()
    from test_feature_edit import edit
    tool = edit(w, feature.id)
    tool.fields["length"].set_internal_value(4.)
    w.tool_sessions.update_preview(interactive=True)
    wait_for(lambda: w.tool_sessions.can_commit, timeout=30)
    w.tool_sessions.cancel()
    assert doc.objects[feature.id].parameters.length == 3.
    path = tmp_path / "filetage.cada"
    assert w.save_document(path=path)
    assert w.close_document()
    restored = w.open_document(path)
    wait_for(lambda: restored is not None and not w.computations.busy, timeout=30)
    assert restored.objects[feature.id].parameters == feature.parameters
    assert precise_volume(restored.objects[body.id].shape) == pytest.approx(preview)


def test_thread_ui_cancel_in_progress_and_replace_parameters_without_partial_publication(extrusion_system):
    w, _, _ = extrusion_system
    doc = w.viewport.document
    shape, _, _ = support()
    body = SceneObject("Vis", shape, (.6, .7, .8))
    doc.add(body)
    doc.history.clear()
    snapshot = dict(doc.objects)
    assert w.command_registry.execute("modeling.thread")
    tool = w.tool_sessions.tool
    assert tool.support.currentData() is None
    tool.support.setCurrentIndex(1)
    tool.fields["length"].set_internal_value(3.)
    w.tool_sessions.update_preview(interactive=True)
    assert w.tool_sessions.cancel()
    QTest.qWait(20)
    assert dict(doc.objects) == snapshot and doc.history.count == 0 and not doc.history.blocked
    assert w.command_registry.execute("modeling.thread")
    tool = w.tool_sessions.tool
    tool.support.setCurrentIndex(1)
    tool.fields["length"].set_internal_value(3.)
    w.tool_sessions.update_preview(interactive=True)
    tool.fields["length"].set_internal_value(4.)
    w.tool_sessions.update_preview(interactive=True)
    wait_for(lambda: w.tool_sessions.can_commit, timeout=30)
    assert dict(doc.objects) == snapshot
    assert w.tool_sessions.commit_interactive(), w.tool_sessions.message
    wait_for(lambda: w.tool_sessions.tool is None, timeout=30)
    feature = next(o for o in doc.objects.values() if isinstance(o, ThreadFeature))
    assert feature.parameters.length == 4. and doc.history.count == 1
