"""Native reference corpus: recipes, geometric oracles, UI editing and timings."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from time import perf_counter
from zipfile import ZipFile

import pytest
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.BRepClass3d import BRepClass3d_SolidClassifier
from OCC.Core.TopAbs import TopAbs_IN, TopAbs_OUT
from OCC.Core.gp import gp_Pnt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cadastrophe.core.body_history import body_result, evaluate_body, result_at
from cadastrophe.core.feature_model import BodyStateRef, ResultStatus
from cadastrophe.core.feature_objects import SketchSourceObject
from cadastrophe.core.operation_effects import checked_solids
from cadastrophe.core.persistence import load_document, save_document
from examples.parametric_example import independent_copy, mass, registry
from examples.reference_parts import PARTS, build_reference, set_depth
from plugins.sketch import commands
from test_extrusion_tool import extrusion_system
from test_feature_edit import edit


def timed(callback):
    start = perf_counter()
    value = callback()
    return value, perf_counter()-start


def certify(document, keys, spec, depth):
    body = document.objects[keys["body"]]
    state = body_result(document, body.id)
    assert state.status is ResultStatus.READY, state.message
    assert len(checked_solids(state.shape)) == 1
    assert len(body.features) == spec.feature_count
    for key in body.features:
        prefix = result_at(document, BodyStateRef(body.id, key))
        assert prefix.status is ResultStatus.READY, prefix.message
        assert len(checked_solids(prefix.shape)) == 1
    assert mass(state.shape) == pytest.approx(spec.expected_volume(depth), abs=1e-4)
    bounds = Bnd_Box()
    brepbndlib.AddOptimal(state.shape, bounds, False, False)
    assert bounds.Get() == pytest.approx(spec.expected_bounds(depth), abs=1e-6)
    for point, inside in spec.probes(depth):
        classifier = BRepClass3d_SolidClassifier(state.shape, gp_Pnt(*point), 1e-7)
        assert classifier.State() == (TopAbs_IN if inside else TopAbs_OUT), point
    for obj in document.objects.values():
        if isinstance(obj, SketchSourceObject):
            assert not obj.reference_error(document), obj.name
    if "top_plane" in keys:
        assert document.objects[keys["top_plane"]].plane.origin.z == pytest.approx(depth)


def recipe_only(path, body_id):
    with ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        body = next(obj for obj in manifest["objects"] if obj["id"] == body_id)
        assert body["brep"] is None and body["resources"] == {}


@pytest.mark.parametrize("spec", PARTS, ids=lambda spec: spec.key)
def test_reference_part_rebuild_roundtrip_and_native_editor(spec, extrusion_system, tmp_path):
    output = Path(os.environ.get("CADASTROPHE_REFERENCE_OUTPUT", tmp_path))
    output.mkdir(parents=True, exist_ok=True)
    model, build_seconds = timed(lambda: build_reference(spec))
    keys, doc = model.handles, model.document
    ids, order = set(doc.objects), model.body.features
    certify(doc, keys, spec, spec.initial_depth)
    codecs = registry(model.service)
    baseline = output/f"{spec.key}.cada"
    variant_path = output/f"{spec.key}_variante.cada"
    _, save_seconds = timed(lambda: save_document(doc, baseline, registry=codecs))
    restored, open_seconds = timed(lambda: load_document(baseline, registry=codecs))
    assert restored.id == doc.id and set(restored.objects) == ids
    assert restored.objects[keys["body"]].features == order
    certify(restored, keys, spec, spec.initial_depth)
    recipe_only(baseline, keys["body"])
    _, cold_seconds = timed(lambda: evaluate_body(doc, keys["body"], model.service.evaluator(doc), reuse=False))
    certify(doc, keys, spec, spec.initial_depth)

    variant = independent_copy(model)
    def change():
        set_depth(variant, spec, spec.variant_depth)
        variant.rebuild()
    _, changed_seconds = timed(change)
    certify(variant.document, keys, spec, spec.variant_depth)
    assert set(variant.document.objects) == ids and variant.body.features == order
    assert variant.document.history.count == 1
    variant.document.history.undo()
    variant.rebuild()
    certify(variant.document, keys, spec, spec.initial_depth)
    variant.document.history.redo()
    variant.rebuild()
    certify(variant.document, keys, spec, spec.variant_depth)
    _, variant_save_seconds = timed(lambda: save_document(variant.document, variant_path, registry=codecs))
    loaded, variant_open_seconds = timed(lambda: load_document(variant_path, registry=codecs))
    assert loaded.id != doc.id and set(loaded.objects) == ids
    assert loaded.objects[keys["body"]].features == order
    certify(loaded, keys, spec, spec.variant_depth)
    recipe_only(variant_path, keys["body"])

    # A broken upstream contour must report ERROR/BLOCKED and clear the final
    # shape. Undo restores the original entities, references and full result.
    source = doc.objects[doc.objects[keys["base"]].sketch_id]
    commands.remove_entity(doc, source.id, source.sketch.entities[0].id)
    failure = evaluate_body(doc, keys["body"], model.service.evaluator(doc), reuse=True)
    assert failure.status is ResultStatus.BLOCKED and failure.message
    assert doc.objects[keys["body"]].shape is None
    assert result_at(doc, BodyStateRef(keys["body"], keys["base"])).status is ResultStatus.ERROR
    doc.history.undo()
    model.rebuild()
    certify(doc, keys, spec, spec.initial_depth)
    assert set(doc.objects) == ids and model.body.features == order

    # Load both documents with the real isolated plugins and native WGL view.
    w, _, _ = extrusion_system
    native_open_seconds = {}
    for path, depth in ((baseline, spec.initial_depth), (variant_path, spec.variant_depth)):
        opened, elapsed = timed(lambda: w.open_document(path))
        assert opened is not None
        QTest.qWait(100)
        w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
        certify(opened, keys, spec, depth)
        assert set(opened.objects) == ids
        assert opened.objects[keys["body"]].features == order
        assert keys["body"] in w.viewport.presentations
        w.viewport.view.SetProj(1., -1., 1.)
        w.viewport.frame_objects((keys["body"],))
        QApplication.processEvents()
        assert w.viewport.view.Dump(str(path.with_suffix(".png")))
        native_open_seconds[path.stem] = elapsed
    assert len({d.id for d in w.documents.documents}) == len(w.documents.documents)
    assert {doc.id, variant.document.id} <= {d.id for d in w.documents.documents}
    opened = w.viewport.document
    rebuilder = w.plugins._record("cadastrophe.modeling").instance.rebuilder
    session = edit(w, keys[spec.edited_feature])
    session.depth.set_internal_value(spec.variant_depth+2)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    rebuilder.flush()
    certify(opened, keys, spec, spec.variant_depth+2)
    assert w.command_registry.execute("edit.undo")
    rebuilder.flush()
    certify(opened, keys, spec, spec.variant_depth)
    assert w.command_registry.execute("edit.redo")
    rebuilder.flush()
    certify(opened, keys, spec, spec.variant_depth+2)
    edited_path = tmp_path/f"{spec.key}_edition.cada"
    assert w.save_document(path=edited_path)
    assert w.close_document()
    edited = w.open_document(edited_path)
    assert edited is not None
    assert edited is not opened
    certify(edited, keys, spec, spec.variant_depth+2)
    assert set(edited.objects) == ids and edited.objects[keys["body"]].features == order

    (output/f"{spec.key}_validation.json").write_text(json.dumps({
        "status": "passed", "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "native_engine": "OCCT", "part": spec.key, "features": spec.feature_count,
        "baseline": baseline.name, "variant": variant_path.name,
        "volume_mm3": {"baseline": spec.expected_volume(spec.initial_depth),
                       "variant": spec.expected_volume(spec.variant_depth)},
        "seconds": {"build": build_seconds, "save": save_seconds, "open_recipes": open_seconds,
                    "rebuild_no_cache": cold_seconds, "edit_and_rebuild": changed_seconds,
                    "variant_save": variant_save_seconds, "variant_open_recipes": variant_open_seconds,
                    "open_native_document": native_open_seconds},
        "checks": ["one_closed_valid_solid_at_each_prefix", "analytic_volume", "bounds", "material_probes",
                   "moving_face_supports", "stable_ids_and_order", "recipe_only_roundtrip",
                   "upstream_error_and_blocked_consumers", "undo_recovers_error",
                   "native_editor_commit_undo_redo", "native_edited_save_reopen"],
    }, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
