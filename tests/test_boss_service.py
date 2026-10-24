"""Read-only X5 service and stale-result guard with isolated plugins."""
from importlib import import_module
from uuid import uuid4

import pytest
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox

from cadastrophe.document import SceneObject
from cadastrophe.scene import new_document
from test_extrusion_service import setup, snapshot
from test_plugins import harness, plugin_root


def test_boss_service_is_detached_and_targets_one_document(harness):
    h = harness
    doc, sketch, _, _, _ = setup(h)
    base = SceneObject("Base", BRepPrimAPI_MakeBox(100., 60., 20.).Shape(), (.5, .6, .7))
    doc.add(base)
    service = h.services.service("cadastrophe.modeling.extrusions")
    before = snapshot(doc)
    result = service.build_boss(doc, sketch.id, base.id, 30)
    assert result.success, result.message
    assert result.volume == pytest.approx(180000)
    assert snapshot(doc) == before and type(doc.objects[base.id]) is SceneObject
    other = h.documents.add(new_document())
    assert not service.build_boss(other, sketch.id, base.id, 30).success
    for target in (None, str(uuid4()), next(iter(doc.objects)), sketch.id):
        result = service.build_boss(doc, sketch.id, target, 30)
        assert not result.success and result.shape is None
    assert snapshot(doc) == before


def test_boss_service_discards_result_when_boolean_inputs_change(harness, monkeypatch):
    h = harness
    doc, sketch, _, _, records = setup(h)
    base = SceneObject("Base", BRepPrimAPI_MakeBox(100., 60., 20.).Shape(), (.5, .6, .7))
    doc.add(base)
    service = h.services.service("cadastrophe.modeling.extrusions")
    module = import_module(records["cadastrophe.modeling"].namespace + ".extrusion_service")
    actual = module.fuse_boss
    def change_document(*args, **kwargs):
        result = actual(*args, **kwargs)
        doc.rename(base.id, "Base modifiée pendant le calcul")
        return result
    monkeypatch.setattr(module, "fuse_boss", change_document)
    result = service.build_boss(doc, sketch.id, base.id, 30)
    assert not result.success and result.shape is None
    assert result.diagnostics[0].code == "stale_input"
    assert doc.objects[base.id].shape is base.shape
