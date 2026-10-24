"""Tree deletion preserves sketches and replays the surviving construction."""
from math import pi

import pytest
from PySide6.QtTest import QTest

from cadastrophe.core.body_history import body_result
from cadastrophe.core.feature_model import ResultStatus
from cadastrophe.core.persistence import save_document
from test_extrusion_tool import extrusion_system
from test_boss_tool import base_body, face_sketch
from test_cut_tool import start_cut
from test_parametric_rebuild import two_extrusions, edge_operation
from test_primitives import volume


def flush(w):
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()


def delete(w, key):
    w.tree_selection_changed([key])
    assert w.command_registry.execute("object.delete")
    flush(w)


def test_remove_cut_from_tree_rebuilds_fillet_keeps_sketch_and_undo(extrusion_system):
    w, _, _ = extrusion_system
    body = base_body(extrusion_system)
    source = face_sketch(extrusion_system, body)
    start_cut(w, source).depth.setValue(5.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    doc = w.viewport.document
    cut = doc.objects[body.id].features[-1]
    fillet = edge_operation(w, body.id, "fillet", 0., 0., 2.)
    old = volume(doc.objects[body.id].shape)
    count = doc.history.count
    delete(w, cut)
    assert doc.history.count == count+1 and cut not in doc.objects
    assert source.id in doc.objects and doc.objects[source.id].visible
    assert doc.objects[body.id].features == (body.features[0], fillet)
    assert body_result(doc, body.id).status is ResultStatus.READY
    assert volume(doc.objects[body.id].shape) == pytest.approx(old+125*pi)
    doc.history.undo()
    flush(w)
    assert cut in doc.objects and volume(doc.objects[body.id].shape) == pytest.approx(old)
    doc.history.redo()
    flush(w)
    assert cut not in doc.objects and source.id in doc.objects


@pytest.mark.parametrize("merge", [True, False])
def test_delete_initial_extrusion_preserves_all_sketches_and_roundtrips(extrusion_system, tmp_path, merge):
    w, _, _ = extrusion_system
    body_id, source_id = two_extrusions(extrusion_system, merge=merge)
    doc = w.viewport.document
    first = doc.objects[body_id].features[0]
    sketches = {key: obj.sketch for key, obj in doc.objects.items() if hasattr(obj, "sketch")}
    ids = set(doc.objects)
    delete(w, first)
    assert all(key in doc.objects and doc.objects[key].sketch == sketch for key, sketch in sketches.items())
    assert all(not getattr(obj, "features", ()) for obj in doc.objects.values())
    assert body_id not in doc.objects and doc.objects[source_id].reference_error(doc)
    path = tmp_path / "removed-base.cada"
    save_document(doc, path, registry=w.object_types)
    doc.history.undo()
    flush(w)
    assert set(doc.objects) == ids and body_result(doc, body_id).status is ResultStatus.READY
    doc.history.redo()
    flush(w)
    assert body_id not in doc.objects
    assert w.close_document() and w.open_document(path) is not None
    QTest.qWait(100)
    assert all(key in w.viewport.document.objects for key in sketches)


def test_remove_boss_removes_explicit_consumers_but_keeps_their_sketches(extrusion_system):
    w, _, _ = extrusion_system
    body_id, boss_sketch = two_extrusions(extrusion_system)
    doc = w.viewport.document
    first, boss = doc.objects[body_id].features
    source = face_sketch(extrusion_system, doc.objects[body_id], height=30.)
    start_cut(w, source).depth.setValue(3.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    cut = doc.objects[body_id].features[-1]
    delete(w, boss)
    assert boss not in doc.objects and cut not in doc.objects
    assert boss_sketch in doc.objects and source.id in doc.objects
    assert doc.objects[body_id].features == (first,)
    assert volume(doc.objects[body_id].shape) == pytest.approx(120000.)
    assert doc.objects[source.id].reference_error(doc)
