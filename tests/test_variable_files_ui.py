"""Shared-file actions through the real equations dialog and plugin evaluator."""
from dataclasses import replace
import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cadastrophe.core.expressions import Expression, Variable
from cadastrophe.core.variable_files import export_variables
from cadastrophe.ui.equations import EquationsDialog
from test_extrusion_tool import extrusion_system, rectangle, start
from test_variable_files import part, edit_file
from test_primitives import volume


def test_link_reload_unlink_and_read_only_are_native_atomic_actions(extrusion_system, tmp_path, monkeypatch):
    w, _, _ = extrusion_system
    sketch = rectangle(extrusion_system)
    start(w, sketch)
    assert w.tool_sessions.commit()
    document = w.viewport.document
    body = next(o for o in document.objects.values() if getattr(o, 'features', ()))
    variable = Variable('epaisseur', Expression('value', '=10'))
    document.set_variables((variable,))
    document.set_equation(body.features[0], 'depth', '=epaisseur')
    baseline = volume(document.objects[body.id].shape)
    other, _, _, _, _ = part(20.)
    path = tmp_path/'variables.txt'
    export_variables(other, path)
    dialog = EquationsDialog(document, w)
    dialog.show()
    QApplication.processEvents()
    monkeypatch.setattr(dialog.file_dialogs, 'choose', lambda *args, **kwargs: path)
    try:
        count = document.history.count
        QTest.mouseClick(dialog.link_button, Qt.MouseButton.LeftButton)
        assert not dialog.message.text(), dialog.message.text()
        assert document.history.count == count+1 and document.variable_source is not None
        assert document.variables[0].id == variable.id
        assert volume(document.objects[body.id].shape) == pytest.approx(baseline*2)
        assert str(path) in dialog.source_label.text()
        edit_file(path, lambda data: data['variables'][0]['expression'].update(text='=30'))
        QTest.mouseClick(dialog.reload_button, Qt.MouseButton.LeftButton)
        assert not dialog.message.text(), dialog.message.text()
        assert volume(document.objects[body.id].shape) == pytest.approx(baseline*3)
        document.history.undo()
        assert volume(document.objects[body.id].shape) == pytest.approx(baseline*2)
        document.history.redo()
        path.unlink()
        before = (dict(document.objects), document.variable_source, document.history.count)
        QTest.mouseClick(dialog.reload_button, Qt.MouseButton.LeftButton)
        assert 'Lecture' in dialog.message.text()
        assert before == (dict(document.objects), document.variable_source, document.history.count)
        QTest.mouseClick(dialog.unlink_button, Qt.MouseButton.LeftButton)
        assert document.variable_source is None and not dialog.reload_button.isEnabled()
        document.history.undo()
        dialog.setReadOnly(True)
        assert all(not button.isEnabled() for button in (dialog.link_button, dialog.export_button, dialog.reload_button, dialog.unlink_button))
    finally:
        dialog.close()
        dialog.deleteLater()


def test_export_button_writes_and_links_a_text_file(extrusion_system, tmp_path, monkeypatch):
    w = extrusion_system[0]
    document = w.viewport.document
    document.set_variables((Variable('largeur', Expression('value', '=25')),))
    dialog = EquationsDialog(document, w)
    target = tmp_path/'dimensions partagées.txt'
    monkeypatch.setattr(dialog.file_dialogs, 'choose', lambda *args, **kwargs: target)
    try:
        QTest.mouseClick(dialog.export_button, Qt.MouseButton.LeftButton)
        assert not dialog.message.text(), dialog.message.text()
        assert document.variable_source.path == str(target)
        assert json.loads(target.read_text(encoding='utf8'))['variables'][0]['name'] == 'largeur'
        document.history.undo()
        assert document.variable_source is None and target.exists()
    finally:
        dialog.close()
        dialog.deleteLater()
