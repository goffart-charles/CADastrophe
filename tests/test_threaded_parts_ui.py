"""Native editing of threads inside complete machined and hollowed parts."""
import pytest

from cadastrophe.core.mass_properties import precise_volume
from cadastrophe.core.persistence import save_document
from examples.threaded_parts import PARTS, build_threaded_part
from test_extrusion_tool import extrusion_system
from test_threaded_parts import certify

NATIVE_TEST_COST = 30


@pytest.mark.parametrize("key", PARTS)
def test_threaded_parts_native_edit_cancel_commit_undo_and_reopen(key, extrusion_system, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from test_feature_edit import edit
    from test_interactive_computation import wait_for
    model = build_threaded_part(key)
    path = tmp_path/"piece.cada"
    save_document(model.document, path, registry=model.codecs)
    w, _, _ = extrusion_system
    errors = []
    w.tool_sessions.error.connect(errors.append)
    doc = w.open_document(path)
    wait_for(lambda: not w.computations.busy, timeout=60)
    QTest.qWait(100)
    wait_for(lambda: w.services.can_start_tool, timeout=60)
    certify(model, doc)
    before = dict(doc.objects), doc.history.count
    def shorten(tool):
        field = tool.fields["length"]
        field.setFocus()
        field.lineEdit().selectAll()
        text = field.locale().toString(2*doc.objects[model.thread_id].parameters.pitch, 'f', 2)
        QTest.keyClicks(field.lineEdit(), text)
        QTest.keyClick(field, Qt.Key.Key_Tab)
        assert field.internal_value == pytest.approx(2*doc.objects[model.thread_id].parameters.pitch), field.lineEdit().text()
    tool = edit(w, model.thread_id, double_click=True)
    shorten(tool)
    w.tool_sessions.update_preview(interactive=True)
    wait_for(lambda: w.tool_sessions.can_commit, timeout=60)
    w.tool_sessions.cancel()
    QTest.qWait(10)
    assert (dict(doc.objects), doc.history.count) == before
    tool = edit(w, model.thread_id)
    shorten(tool)
    w.tool_sessions.update_preview(interactive=True)
    wait_for(lambda: w.tool_sessions.can_commit, timeout=60)
    assert tool._objects[0].parameters.length == pytest.approx(2*doc.objects[model.thread_id].parameters.pitch)
    assert w.tool_sessions.commit_interactive(), errors or w.tool_sessions.message
    wait_for(lambda: w.tool_sessions.tool is None, timeout=60)
    assert precise_volume(doc.objects[model.body_id].shape) > precise_volume(before[0][model.body_id].shape)
    doc.history.undo()
    certify(model, doc)
    doc.history.redo()
    changed = precise_volume(doc.objects[model.body_id].shape)
    assert doc.objects[model.thread_id].parameters.length == pytest.approx(2*doc.objects[model.thread_id].parameters.pitch)
    assert w.save_document(path=path) and w.close_document()
    reopened = w.open_document(path)
    wait_for(lambda: not w.computations.busy, timeout=60)
    assert precise_volume(reopened.objects[model.body_id].shape) == pytest.approx(changed, abs=5e-5)
