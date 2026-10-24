"""Generate a complex editable example and certify geometry, rebuild and UI."""
from dataclasses import replace
from math import cos, pi, radians, sin
import json
import os
from pathlib import Path
from zipfile import ZipFile

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from OCC.Core.BRepClass3d import BRepClass3d_SolidClassifier
from OCC.Core.TopAbs import TopAbs_IN, TopAbs_OUT
from OCC.Core.gp import gp_Pnt

from cadastrophe.core.body_history import body_result, result_at
from cadastrophe.core.feature_model import BodyStateRef, ResultStatus
from cadastrophe.core.feature_objects import BodyObject, SketchSourceObject
from cadastrophe.core.operation_effects import checked_solids
from cadastrophe.core.persistence import load_document, save_document
from examples.parametric_flange import build_demo, independent_copy, mass, registry
from plugins.sketch import commands
from test_extrusion_tool import extrusion_system
from test_feature_edit import edit
from test_workshop_identity import capture
from test_advanced_cad_tools import collect_native_tool_sessions


def solid_at(shape, point, expected):
    classifier = BRepClass3d_SolidClassifier(shape, gp_Pnt(*point), 1e-7)
    assert classifier.State() == expected, point


def check_holes(shape, *, count, flange_height, plot_height, probe=0):
    for i in range(count):
        theta = 2*pi*i/count
        for radius in (33, 45):
            x, y = radius*cos(theta), radius*sin(theta)
            for z in (4, flange_height+plot_height-1):
                solid_at(shape, (x-probe*sin(theta), y+probe*cos(theta), z), TopAbs_OUT)
                solid_at(shape, (x-3.3*sin(theta), y+3.3*cos(theta), z), TopAbs_IN)


def certify(model):
    result = model.rebuild()
    assert len(checked_solids(result.shape)) == 1
    for key in model.body.features:
        state = result_at(model.document, BodyStateRef(model.body.id, key))
        assert state.status is ResultStatus.READY, (model.document.objects[key].name, state.message)
    for obj in model.document.objects.values():
        if isinstance(obj, SketchSourceObject):
            assert not obj.reference_error(model.document), obj.name
    return mass(result.shape)


@pytest.mark.skipif(not (os.environ.get("CADASTROPHE_DEMO_OUTPUT") or os.environ.get("CADASTROPHE_COMPLEX_DEMO_TEST") == "1"),
                    reason="Démonstration longue : activer CADASTROPHE_COMPLEX_DEMO_TEST=1 ou CADASTROPHE_DEMO_OUTPUT.")
def test_atlas_demo_rebuild_roundtrip_and_real_history_editor(extrusion_system, tmp_path):
    output = Path(os.environ["CADASTROPHE_DEMO_OUTPUT"]) if os.environ.get("CADASTROPHE_DEMO_OUTPUT") else tmp_path
    output.mkdir(parents=True, exist_ok=True)
    baseline = build_demo()
    keys, document = baseline.handles, baseline.document
    original_ids = set(document.objects)
    original_order = baseline.body.features
    baseline_volume = certify(baseline)
    assert len(original_order) == 18
    check_holes(baseline.body.shape, count=6, flange_height=8, plot_height=6)
    solid_at(baseline.body.shape, (0,0,4), TopAbs_OUT)
    solid_at(baseline.body.shape, (15,0,4), TopAbs_IN)
    for x in (-67,67):
        for y in (-14,14):
            solid_at(baseline.body.shape, (x,y,4), TopAbs_OUT)
            solid_at(baseline.body.shape, (x+3.5,y,4), TopAbs_IN)
    normal_path, variant_path = output/"bride_atlas.cada", output/"bride_atlas_variante.cada"
    codecs = registry(baseline.service)
    save_document(document, normal_path, registry=codecs)
    restored = load_document(normal_path, registry=codecs)
    assert set(restored.objects) == original_ids
    assert mass(restored.objects[keys["body"]].shape) == pytest.approx(baseline_volume, abs=1e-4)
    with ZipFile(normal_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        record = next(r for r in manifest["objects"] if r["id"] == keys["body"])
        assert record["brep"] is None and record["resources"] == {}

    model = independent_copy(baseline)
    doc = model.document
    evidence = [dict(check="baseline", volume_mm3=baseline_volume, features=18, plots=12)]
    # A dimension, rather than a baked-in change to a calculated B-Rep.
    hole = doc.objects[keys["hole_sketch"]]
    commands.set_constraints(doc, hole.id, tuple(replace(c, value=5.4) if c.kind == "diameter" else c for c in hole.sketch.constraints))
    v = certify(model)
    assert v == pytest.approx(baseline_volume-12*pi*(2.7**2-2.1**2)*14, abs=1e-3)
    check_holes(model.body.shape, count=6, flange_height=8, plot_height=6, probe=2.4)
    evidence.append(dict(check="diameter_4.2_to_5.4", volume_mm3=v, all_12_holes_checked=True))

    boss = doc.objects[keys["plot"]]
    doc.replace_feature(replace(boss, parameters=replace(boss.parameters, depth=10)))
    previous, v = v, certify(model)
    assert v == pytest.approx(previous+12*pi*(4**2-2.7**2)*4, abs=1e-3)
    assert doc.objects[keys["plot_plane"]].plane.origin.z == pytest.approx(18)
    check_holes(model.body.shape, count=6, flange_height=8, plot_height=10)
    evidence.append(dict(check="plot_height_6_to_10", volume_mm3=v, support_z=18))

    features = tuple(doc.objects[keys[k]] for k in ("circular_plots", "circular_holes"))
    doc.update_construction(tuple(replace(f, parameters=replace(f.parameters, count=7)) for f in features))
    previous, v = v, certify(model)
    assert v == pytest.approx(previous+2*pi*(4**2*10-2.7**2*18), abs=1e-3)
    check_holes(model.body.shape, count=7, flange_height=8, plot_height=10)
    evidence.append(dict(check="circular_count_6_to_7", volume_mm3=v, plots=14, holes=14))

    profile = doc.objects[keys["turned_profile"]]
    def moved(point):
        return replace(point, v=12) if point.v == 8 else point
    commands.replace_entities(doc, profile.id, tuple(replace(e, start=moved(e.start), end=moved(e.end))
        for e in profile.sketch.entities if not e.construction))
    previous, v = v, certify(model)
    expected = previous+pi*(4*(62**2-24**2)-(58**2-52**2)-14*2.7**2*4-2.5**2*4/cos(radians(15)))
    assert v == pytest.approx(expected, abs=1e-3)
    assert doc.objects[keys["flange_plane"]].plane.origin.z == pytest.approx(12)
    assert doc.objects[keys["plot_plane"]].plane.origin.z == pytest.approx(22)
    check_holes(model.body.shape, count=7, flange_height=12, plot_height=10)
    evidence.append(dict(check="initial_profile_flange_8_to_12", volume_mm3=v, flange_z=12, plot_support_z=22))

    datum = doc.objects[keys["inclined_plane"]]
    doc.replace_feature(replace(datum, parameters=replace(datum.parameters, angle=22)))
    previous, v = v, certify(model)
    assert v == pytest.approx(previous-pi*2.5**2*12*(1/cos(radians(22))-1/cos(radians(15))), abs=1e-3)
    assert set(doc.objects) == original_ids and model.body.features == original_order
    evidence.append(dict(check="inclined_plane_15_to_22", volume_mm3=v, stable_object_ids=True))
    save_document(doc, variant_path, registry=registry(model.service))
    loaded = load_document(variant_path, registry=registry(model.service))
    assert loaded.id != document.id
    assert loaded.objects[keys["body"]].features == original_order
    assert mass(loaded.objects[keys["body"]].shape) == pytest.approx(v, abs=1e-4)

    # Exercise the actual isolated plugins and the ordinary history editor too.
    w, _, _ = extrusion_system
    w.resize(1500, 1000)
    for path in (normal_path, variant_path):
        opened = w.open_document(path)
        assert opened is not None
        QTest.qWait(100)  # The new document tab initializes its native viewer on show.
        w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
        result = body_result(opened, keys["body"])
        assert result.status is ResultStatus.READY, result.message
        assert len([o for o in opened.objects.values() if isinstance(o, BodyObject)]) == 1
        w.viewport.view.SetProj(1., -1., 1.)
        w.viewport.frame_objects((keys["body"],))
        opened.select_objects(())
        QApplication.processEvents()
        capture(w, path.with_suffix(".png"))
    opened = w.viewport.document
    session = edit(w, keys["plot"])
    session.depth.set_internal_value(11)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert mass(opened.objects[keys["body"]].shape) == pytest.approx(v+14*pi*(4**2-2.7**2), abs=1e-3)
    opened.history.undo()
    assert mass(opened.objects[keys["body"]].shape) == pytest.approx(v, abs=1e-4)
    evidence.append(dict(check="native_history_edit_and_undo", passed=True))
    (output/"validation.json").write_text(json.dumps(dict(
        status="passed", native_engine="OCCT", shape="one connected closed solid",
        baseline=normal_path.name, variant=variant_path.name,
        objects=len(original_ids), features=18,
        sketches=sum(isinstance(o, SketchSourceObject) for o in doc.objects.values()),
        checks=evidence), ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
