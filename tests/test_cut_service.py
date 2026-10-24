"""X6 provider contracts with real isolated plugins and native solids."""
from importlib import import_module
from math import pi

import pytest
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox

from cadastrophe.document import SceneObject
from test_extrusion_service import setup, snapshot
from test_plugins import harness, plugin_root


def cut_setup(h):
    doc, initial, commands, model, records = setup(h)
    source = commands.create_sketch(doc, initial.sketch.support.plane_id)
    commands.add_entity(doc, source.id, model.Circle(model.Vec2(50, 30), 5))
    base = SceneObject("Base", BRepPrimAPI_MakeBox(100., 60., 20.).Shape(), (.5, .6, .7))
    doc.add(base)
    return doc, doc.objects[source.id], base, h.services.service("cadastrophe.modeling.extrusions"), records


def test_read_only_cut_and_through_all_follow_current_target(harness):
    doc, source, base, service, _ = cut_setup(harness)
    before = snapshot(doc)
    result = service.build_cut(doc, source.id, base.id, 5)
    assert result.success and result.volume == pytest.approx(120000-125*pi)
    assert snapshot(doc) == before
    for height in (20., 120.):
        doc.replace_shape(base.id, BRepPrimAPI_MakeBox(100., 60., height).Shape())
        before = snapshot(doc)
        result = service.build_cut(doc, source.id, base.id, None, end_condition="through_all")
        assert result.success, result.message
        assert result.volume == pytest.approx((6000-25*pi)*height)
        assert snapshot(doc) == before


@pytest.mark.parametrize("options", [dict(depth=0), dict(depth=-1), dict(depth=float("nan")),
    dict(depth=None), dict(depth=5, reversed=1), dict(depth=5, tolerance=0),
    dict(depth=5, end_condition="through_all"), dict(depth=None, end_condition="unknown")])
def test_invalid_cut_parameters_never_mutate_or_return_a_shape(harness, options):
    doc, source, base, service, _ = cut_setup(harness)
    before = snapshot(doc)
    result = service.build_cut(doc, source.id, base.id, **options)
    assert not result.success and result.shape is None and result.diagnostics
    assert snapshot(doc) == before


def test_cut_service_rejects_stale_boolean_result(harness, monkeypatch):
    doc, source, base, service, records = cut_setup(harness)
    module = import_module(records["cadastrophe.modeling"].namespace + ".cut_service")
    native = module.cut_solid
    def change(*args, **kwargs):
        result = native(*args, **kwargs)
        doc.rename(source.id, "Modifiée pendant le calcul")
        return result
    monkeypatch.setattr(module, "cut_solid", change)
    result = service.build_cut(doc, source.id, base.id, 5)
    assert not result.success and result.shape is None and result.diagnostics[0].code == "stale_input"
    assert doc.objects[base.id].shape is base.shape
