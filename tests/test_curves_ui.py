"""Real plugin actions, native curve owners and transactional editing."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton
from OCC.Core.BRepCheck import BRepCheck_Analyzer

from cadastrophe.app import MainWindow
from cadastrophe.core.curve_model import CurveObject
from cadastrophe.core.curve_geometry import curve_edge
from test_parametric_operations_ui import screen


@pytest.fixture
def curves_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(preferences_path=tmp_path/'prefs.json', recovery_directory=tmp_path/'recovery')
    window.show()
    app.processEvents()
    window.viewport.camera_animation_duration = 0
    try:
        assert window.plugins._record('cadastrophe.curves').state == 'active'
        yield window
    finally:
        window.tool_sessions.cancel()
        window.confirm_discard = lambda document: True
        window.close()
        window.deleteLater()
        app.processEvents()


@pytest.mark.parametrize('kind', ['helix', 'spiral', 'points'])
def test_curve_ribbon_native_preview_edit_undo_and_file(curves_window, tmp_path, kind):
    w = curves_window
    document = w.viewport.document
    before = dict(document.objects)
    action = w.command_registry.action('cadastrophe.curves.'+kind)
    button, = [button for button in w.command_manager.findChildren(QToolButton) if button.defaultAction() is action]
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    tool = w.tool_sessions.tool
    assert tool.preview_parts and dict(document.objects) == before and document.history.count == 0
    assert BRepCheck_Analyzer(tool.preview_parts[0][0], True).IsValid()
    import os
    if os.environ.get('CADASTROPHE_CONTRAST_CAPTURE'):
        from pathlib import Path
        path = Path(os.environ['CADASTROPHE_CONTRAST_CAPTURE'])
        path.mkdir(parents=True, exist_ok=True)
        assert w.viewport.view.Dump(str(path / (kind+'-preview.png')))
    assert w.tool_sessions.commit(), w.tool_sessions.message
    obj, = [obj for obj in document.objects.values() if isinstance(obj, CurveObject)]
    assert obj.id in w.tree_items and document.history.count == 1
    presenter = w.viewport.document_adapter.features[obj.id][1]
    assert presenter.owns(presenter.ais) and w.viewport.context.IsDisplayed(presenter.ais)
    w.tree_selection_changed([obj.id])
    item, = w.viewport.selection.items()
    assert item.shape is not None and item.topology == 'Edge'
    w.viewport.frame_objects([obj.id])
    assert w.command_registry.execute('cadastrophe.curves.edit')
    edit = w.tool_sessions.tool
    if kind == 'points':
        edit.points.setPlainText('0;0;0\n20;0;15\n30;20;30')
    else:
        edit.turns.setValue(4.)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert document.objects[obj.id] == obj
    assert w.tool_sessions.cancel()
    assert document.objects[obj.id] == obj and document.history.count == 1
    assert w.command_registry.execute('cadastrophe.curves.edit')
    edit = w.tool_sessions.tool
    if kind == 'points':
        edit.points.setPlainText('0;0;0\n20;0;15\n30;20;30')
    else:
        edit.turns.setValue(4.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    changed = document.objects[obj.id]
    assert changed.parameters != obj.parameters and changed.id == obj.id
    document.history.undo()
    assert document.objects[obj.id].parameters == obj.parameters
    document.history.redo()
    path = tmp_path/(kind+'.cada')
    assert w.save_document(path=path)
    assert w.close_document()
    restored = w.open_document(path)
    assert restored.objects[obj.id].parameters == changed.parameters
    assert BRepCheck_Analyzer(curve_edge(restored, restored.objects[obj.id]), True).IsValid()


def test_native_curve_pick_is_a_whole_producer_reference(curves_window):
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    w = curves_window
    assert w.command_registry.execute('cadastrophe.curves.helix')
    assert w.tool_sessions.commit(), w.tool_sessions.message
    document = w.viewport.document
    obj, = [obj for obj in document.objects.values() if isinstance(obj, CurveObject)]
    from importlib import import_module
    namespace = w.plugins._record('cadastrophe.sketch').namespace
    commands = import_module(namespace+'.commands')
    model = import_module(namespace+'.model')
    sketch = commands.create_sketch(document, obj.parameters.plane_id)
    commands.add_entity(document, sketch.id, model.Circle(model.Vec2(10., 0.), 1.))
    document.set_visibilities({o.id: False for o in document.objects.values() if o.plane})
    w.viewport.frame_objects([obj.id])
    native = BRepAdaptor_Curve(curve_edge(document, obj))
    point = native.Value(native.FirstParameter()+.17*(native.LastParameter()-native.FirstParameter()))
    position = screen(w, point.Coord())
    w.tree_selection_changed([sketch.id])
    assert w.command_registry.execute('modeling.sweep')
    tool = w.tool_sessions.tool
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=position)
    assert len(tool.path_references) == 1, w.tool_sessions.message
    reference, = tool.path_references
    assert reference.kind == 'curve' and reference.object_id == obj.id
    assert reference.entity_id is None
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert w.tool_sessions.commit(), w.tool_sessions.message
    from cadastrophe.core.feature_objects import BodyObject
    from test_primitives import volume
    body, = [item for item in document.objects.values() if isinstance(item, BodyObject)]
    before = volume(body.shape)
    w.tree_selection_changed([obj.id])
    assert w.command_registry.execute('cadastrophe.curves.edit')
    w.tool_sessions.tool.turns.setValue(4.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert volume(document.objects[body.id].shape) > before
    document.history.undo()
    assert volume(document.objects[body.id].shape) == pytest.approx(before, rel=1e-6)
