"""X3 via actual isolated plugins, native geometry, document and undo contracts."""
from importlib import import_module
from math import pi
from pathlib import Path
import shutil

import pytest
from OCC.Core.TopAbs import TopAbs_SOLID

from cadastrophe.core.body_history import body_result
from cadastrophe.core.feature_model import Operation, ResultStatus
from cadastrophe.core.persistence import load_document, save_document
from cadastrophe.core.presentations import PresentationRegistry
from cadastrophe.scene import new_document
from test_modeling_history import construction
from test_plugins import harness, plugin_root


def setup(h, *, hole=False, multiple=False):
    h.manager.host.presentations = PresentationRegistry()
    root = Path(__file__).resolve().parents[1] / "plugins"
    for name in ("sketch", "modeling"):
        shutil.copytree(root / name, h.root / name, ignore=shutil.ignore_patterns("__pycache__"))
    h.manager.startup()
    records = {record.manifest.id: record for record in h.manager.records}
    assert all(record.state == "active" for record in records.values())
    namespace = records["cadastrophe.sketch"].namespace
    commands, model = (import_module(namespace + "." + name) for name in ("commands", "model"))
    doc = h.documents.add(new_document())
    obj = commands.create_sketch(doc, next(iter(doc.objects)))
    corners = tuple(model.Vec2(*point) for point in ((0, 0), (100, 0), (100, 60), (0, 60)))
    curves = tuple(model.Segment(point, corners[(index+1)%4]) for index, point in enumerate(corners))
    if hole:
        curves += (model.Circle(model.Vec2(50, 30), 10),)
    if multiple:
        curves += (model.Circle(model.Vec2(200, 0), 10),)
    commands.add_entities(doc, obj.id, curves, title="Profil")
    return doc, doc.objects[obj.id], commands, model, records


def snapshot(doc):
    return dict(doc.objects), doc.history.count, doc.history.index, doc.is_modified, doc.selected_ids


@pytest.mark.parametrize("hole", [False, True])
def test_public_native_service_leaves_document_history_and_x1_body_unchanged(harness, tmp_path, hole):
    h = harness
    doc, sketch, commands, model, _ = setup(h, hole=hole)
    body, _ = construction(doc, sketch, (Operation.NEW_BODY,))
    doc.select_objects((sketch.id,))
    service = h.services.service("cadastrophe.modeling.extrusions")
    before = snapshot(doc)
    result = service.build(doc, sketch.id, 20)
    expected = (6000 - (100*pi if hole else 0))*20
    assert result.success and result.shape.ShapeType() == TopAbs_SOLID
    assert result.volume == pytest.approx(expected)
    assert snapshot(doc) == before
    assert doc.objects[body.id].shape is None and body_result(doc, body.id).status is ResultStatus.DIRTY
    assert [command.id for command in h.registry if command.id.startswith("cadastrophe.modeling.")] == [
        "cadastrophe.modeling.extrude", "cadastrophe.modeling.cut_extrude",
        "cadastrophe.modeling.history_before", "cadastrophe.modeling.history_after", "cadastrophe.modeling.history_end",
        "cadastrophe.modeling.disable_features", "cadastrophe.modeling.enable_features",
        "cadastrophe.modeling.repair_references", "cadastrophe.modeling.select_parents",
        "cadastrophe.modeling.select_consumers", "cadastrophe.modeling.select_error_sources", "cadastrophe.modeling.edit_feature",
        "cadastrophe.modeling.hole_wizard", "cadastrophe.modeling.thread",
        "cadastrophe.modeling.shell", "cadastrophe.modeling.draft", "cadastrophe.modeling.edit_plane", "cadastrophe.modeling.reference_plane", "cadastrophe.modeling.revolve",
        "cadastrophe.modeling.cut_revolve", "cadastrophe.modeling.sweep", "cadastrophe.modeling.cut_sweep", "cadastrophe.modeling.loft", "cadastrophe.modeling.cut_loft", "cadastrophe.modeling.mirror", "cadastrophe.modeling.linear_pattern",
        "cadastrophe.modeling.circular_pattern",
        "cadastrophe.modeling.fillet", "cadastrophe.modeling.chamfer", "cadastrophe.modeling.rebuild"]
    # A future ToolSession can request its preview under its existing reservation.
    owner = object()
    doc.history.reserve(owner)
    try:
        assert service.build(doc, sketch.id, 20, reversed=True).volume == pytest.approx(expected)
    finally:
        doc.history.release(owner)
    assert snapshot(doc) == before
    path = tmp_path / "x3.cada"
    save_document(doc, path, registry=h.types)
    loaded = load_document(path, registry=h.types)
    assert service.build(loaded, sketch.id, 20).volume == pytest.approx(expected)
    assert not loaded.is_modified and loaded.history.count == 0
    # Domain edits and undo/redo still use the existing stack, not preview state.
    point = model.Point(model.Vec2(5, 5))
    commands.add_entity(doc, sketch.id, point)
    doc.history.undo()
    assert service.build(doc, sketch.id, 20).volume == pytest.approx(expected)
    doc.history.redo()
    assert doc.objects[sketch.id].sketch.entities[-1] == point
    assert service.build(doc, sketch.id, 20).volume == pytest.approx(expected)


def test_multiple_regions_require_explicit_choice_and_inner_disk_is_selectable(harness):
    h = harness
    doc, sketch, _, _, _ = setup(h, hole=True, multiple=True)
    service = h.services.service("cadastrophe.modeling.extrusions")
    before = snapshot(doc)
    failed = service.build(doc, sketch.id, 20)
    assert not failed.success and failed.shape is None
    assert failed.diagnostics[0].code == "multiple_regions" and len(failed.diagnostics[0].region_ids) == 2
    report = h.services.service("cadastrophe.sketch.profiles").analyze(doc, sketch.id)
    outer, holes = next((outer, holes) for outer, holes in report.regions if holes)
    result = service.build(doc, sketch.id, 20, contour_ids=(outer.id,))
    assert result.success and result.volume == pytest.approx((6000-100*pi)*20)
    other_id = next(c.id for c, _ in report.regions if c.id != outer.id)
    assert service.build(doc, sketch.id, 20, contour_ids=(other_id,)).volume == pytest.approx(2000*pi)
    disk = service.build(doc, sketch.id, 20, contour_ids=(holes[0].id,))
    assert disk.success and disk.volume == pytest.approx(2000*pi)
    assert snapshot(doc) == before


def test_profile_failures_and_invalid_parameters_never_return_previous_shape(harness):
    h = harness
    doc, sketch, commands, model, _ = setup(h)
    service = h.services.service("cadastrophe.modeling.extrusions")
    assert service.build(doc, sketch.id, 20).success
    for options in ({"depth": 0}, {"depth": -1}, {"depth": 1e-9}, {"depth": float("inf")},
                    {"depth": 20, "reversed": 1}, {"depth": 20, "tolerance": 0},
                    {"depth": 20, "tolerance": 10**1000}):
        result = service.build(doc, sketch.id, **options)
        assert not result.success and result.shape is result.volume is None
    key = sketch.sketch.entities[-1].id
    commands.remove_entity(doc, sketch.id, key)
    result = service.build(doc, sketch.id, 20)
    assert not result.success and result.diagnostics[0].code == "profile_error"
    assert "ouvert" in result.message
    doc.history.undo()
    assert service.build(doc, sketch.id, 20).success
    plane_id = sketch.sketch.support.plane_id
    doc.replace_shape(plane_id, doc.objects[plane_id].shape)
    result = service.build(doc, sketch.id, 20)
    assert not result.success and "non résolu" in result.message
    other = h.documents.add(new_document())
    assert not service.build(other, sketch.id, 20).success


def test_service_drops_result_if_document_changes_during_native_calculation(harness, monkeypatch):
    h = harness
    doc, sketch, _, _, records = setup(h)
    service = h.services.service("cadastrophe.modeling.extrusions")
    module = import_module(records["cadastrophe.modeling"].namespace + ".extrusion_service")
    actual = module.extrude_face
    def change_document(*args, **kwargs):
        result = actual(*args, **kwargs)
        doc.rename(sketch.id, "Modifiée pendant le calcul")
        return result
    monkeypatch.setattr(module, "extrude_face", change_document)
    result = service.build(doc, sketch.id, 20)
    assert not result.success and result.shape is None and result.diagnostics[0].code == "stale_input"


def test_service_ownership_cleanup_and_closed_provider_survive_plugin_unload(harness):
    h = harness
    doc, sketch, _, _, _ = setup(h)
    service = h.services.service("cadastrophe.modeling.extrusions")
    assert service.build(doc, sketch.id, 20).success
    assert not h.manager.deactivate("cadastrophe.sketch")
    h.documents.remove(doc.id)
    assert h.manager.deactivate("cadastrophe.modeling")
    with pytest.raises(LookupError):
        h.services.service("cadastrophe.modeling.extrusions")
    with pytest.raises(RuntimeError, match="fermé"):
        service.build(doc, sketch.id, 20)
    assert h.manager.deactivate("cadastrophe.sketch")
