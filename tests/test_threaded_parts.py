"""Whole-part qualification with analytic screw-profile and placement oracles."""
from dataclasses import replace
from math import pi, tan, radians
from pathlib import Path
import os

import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepClass3d import BRepClass3d_SolidClassifier
from OCC.Core.TopAbs import TopAbs_IN, TopAbs_OUT
from OCC.Core.gp import gp_Pnt

from cadastrophe.core.body_history import body_result, result_at
from cadastrophe.core.clipboard import encode_objects, decode_objects
from cadastrophe.core.configurations import activate
from cadastrophe.core.feature_model import BodyStateRef, ResultStatus
from cadastrophe.core.mass_properties import precise_volume
from cadastrophe.core.native_naming import resolve
from cadastrophe.core.operation_effects import checked_solids
from cadastrophe.core.persistence import save_document, load_document
from examples.threaded_parts import PARTS, build_threaded_part
from test_parametric_operations import bounds

NATIVE_TEST_COST = 100


def certify(model, doc=None):
    doc = doc or model.document
    bracket = model.key == "equerre-fixation"
    body = doc.objects[model.body_id]
    state = body_result(doc, body.id)
    assert state.status is ResultStatus.READY, state.message
    assert BRepCheck_Analyzer(state.shape, True).IsValid()
    assert len(checked_solids(state.shape)) == 1
    raw = result_at(doc, BodyStateRef(body.id, model.original_features[-1]))
    drilled = result_at(doc, BodyStateRef(body.id, model.hole_id))
    p = doc.objects[model.thread_id].parameters
    hole = doc.objects[model.hole_id].parameters
    thickness = doc.objects[model.original_features[0]].parameters.depth if bracket else 10.
    count = len(model.centers)
    old_radius = 0. if bracket else 1.5
    radius, major = hole.diameter/2, p.diameter/2
    # Integrate 2*pi*r times the axial groove fraction across the radial annulus.
    # The internal metric groove has a flat root and straight 60-degree flanks.
    # This oracle does not build a helix or use the feature's cutting volumes.
    groove = 2*pi/p.pitch * (p.root_flat*(major**2-radius**2)/2
        + 2*tan(radians(p.angle/2))*(major*(major**2-radius**2)/2-(major**3-radius**3)/3))
    drilled_volume = precise_volume(raw.shape)-count*pi*(radius**2-old_radius**2)*thickness
    assert precise_volume(drilled.shape) == pytest.approx(drilled_volume, abs=2e-5)
    expected = drilled_volume-count*groove*p.length
    if model.finish_id:
        f = doc.objects[model.finish_id].parameters
        r, outer = f.diameter/2, f.head_diameter/2
        depth = (outer-r)/tan(radians(f.angle/2))
        expected -= pi*r*r*thickness + pi*depth*(outer-r)*(outer+2*r)/3
    assert precise_volume(state.shape) == pytest.approx(expected, abs=5e-5)
    assert bounds(state.shape) == pytest.approx(bounds(raw.shape), abs=2e-6)
    for x, y in model.centers:
        assert BRepClass3d_SolidClassifier(state.shape, gp_Pnt(x, y, thickness/2), 1e-7).State() == TopAbs_OUT
        assert BRepClass3d_SolidClassifier(state.shape, gp_Pnt(x+major+.3, y, thickness-.5), 1e-7).State() == TopAbs_IN
    # The partial thread must leave the pilot wall intact below its axial limit.
    x, y = model.centers[0]
    assert BRepClass3d_SolidClassifier(state.shape, gp_Pnt(x+(radius+major)/2, y, .5), 1e-7).State() == TopAbs_IN
    assert hole.catalog_active and p.catalog_active
    turns = 2 if doc.active_configuration.name == "Taraudages courts" else 3 if bracket else 4
    assert p.length == pytest.approx(turns*p.pitch)
    assert model.original_ids <= doc.objects.keys()
    assert p.face.producer_id == model.hole_id
    assert resolve(drilled.shape, p.face.topology, drilled.topology) is not None
    for fid in body.features:
        result = result_at(doc, BodyStateRef(body.id, fid))
        assert result.status is ResultStatus.READY, (doc.objects[fid].name, result.message)
    assert any(":instance:" in label and ":thread:" in label for label, _ in state.topology.faces)
    return expected


@pytest.mark.parametrize("key", PARTS)
def test_threaded_parts_analytic_volumes_variants_copy_roundtrip_and_exports(key, tmp_path):
    model = build_threaded_part(key)
    doc = model.document
    original = frozenset(doc.objects)
    parameters = doc.objects[model.thread_id].parameters
    volumes = {}
    labels = {}
    for configuration in (*model.configurations, model.configurations[0]):
        activate(doc, configuration)
        volumes[configuration] = certify(model)
        names = {label for label, _ in body_result(doc, model.body_id).topology.faces}
        assert names == labels.setdefault(configuration, names)
        assert frozenset(doc.objects) == original
        assert doc.objects[model.thread_id].parameters.face == parameters.face
    activate(doc, model.configurations[1])
    doc.history.undo()
    assert doc.active_configuration.id == model.configurations[0]
    certify(model)
    doc.history.redo()
    certify(model)
    copy = decode_objects(encode_objects(doc, (model.body_id,), model.codecs), model.codecs)
    copied = copy.document.objects[copy.roots[0]]
    assert copied.id != model.body_id and set(copy.document.objects).isdisjoint(original)
    assert precise_volume(copied.shape) == pytest.approx(volumes[model.configurations[1]], abs=5e-5)
    assert len(copy.document.configurations) == len(doc.configurations)
    for configuration in copy.document.configurations:
        activate(copy.document, configuration.id, evaluator=model.service.evaluator)
        source = next(c for c in doc.configurations if c.name == configuration.name)
        assert precise_volume(copy.document.objects[copied.id].shape) == pytest.approx(volumes[source.id], abs=5e-5)
    path = tmp_path / "piece-taraudee.cada"
    save_document(doc, path, registry=model.codecs)
    loaded = load_document(path, registry=model.codecs)
    assert frozenset(loaded.objects) == original
    for configuration in model.configurations:
        activate(loaded, configuration, evaluator=model.service.evaluator)
        certify(model, loaded)
        assert {label for label, _ in body_result(loaded, model.body_id).topology.faces} == labels[configuration]
    from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_Reader, STEPControl_AsIs
    from OCC.Core.IFSelect import IFSelect_RetDone
    export = tmp_path / "piece.step"
    writer = STEPControl_Writer()
    shape = loaded.objects[model.body_id].shape
    assert writer.Transfer(shape, STEPControl_AsIs) == IFSelect_RetDone
    assert writer.Write(str(export)) == IFSelect_RetDone
    reader = STEPControl_Reader()
    assert reader.ReadFile(str(export)) == IFSelect_RetDone
    assert reader.TransferRoots() == 1
    assert BRepCheck_Analyzer(reader.OneShape(), True).IsValid()
    assert len(checked_solids(reader.OneShape())) == 1
    assert precise_volume(reader.OneShape()) == pytest.approx(precise_volume(shape), rel=1e-7)
    assert bounds(reader.OneShape()) == pytest.approx(bounds(shape), abs=2e-5)
    from plugins.import_export.mesh import triangulate, MeshOptions
    from test_mesh_exports import signed_volume
    mesh = triangulate(shape, MeshOptions("high"))
    mesh.validate_closed()
    triangles = tuple(tuple(mesh.vertices[i] for i in t) for t in mesh.triangles)
    assert signed_volume(triangles) == pytest.approx(precise_volume(shape), rel=.003)
    destination = os.environ.get("CADASTROPHE_THREADED_PARTS_OUTPUT")
    if destination:
        activate(doc, model.configurations[0])
        output = Path(destination)/key/"04-fixations-taraudees.cada"
        output.parent.mkdir(parents=True, exist_ok=True)
        save_document(doc, output, registry=model.codecs)


@pytest.mark.parametrize("key", PARTS)
def test_threaded_parts_invalid_support_hides_result_repair_undo_and_reload(key, tmp_path):
    from cadastrophe.core.body_history import last_valid_result
    from cadastrophe.core.reference_repair import reference_entries, entry_issue, replacement_objects
    from cadastrophe.core.topology_refs import TopologyReference
    model = build_threaded_part(key)
    doc = model.document
    expected = certify(model)
    thread = doc.objects[model.thread_id]
    previous = last_valid_result(doc, model.body_id)
    bad = replace(thread.parameters.face, topology=TopologyReference("Face", ("missing:pilot",)))
    doc.update_construction((replace(thread, parameters=replace(thread.parameters, face=bad)),), allow_errors=True)
    assert result_at(doc, BodyStateRef(model.body_id, model.thread_id)).status is ResultStatus.ERROR
    assert body_result(doc, model.body_id).status is ResultStatus.BLOCKED
    assert doc.objects[model.body_id].shape is None
    assert last_valid_result(doc, model.body_id) is previous
    for fid in model.replays:
        assert result_at(doc, BodyStateRef(model.body_id, fid)).status is ResultStatus.BLOCKED
    entry, = reference_entries(doc, model.thread_id)
    profiles = model.service._profiles
    assert entry_issue(doc, entry, profiles)
    doc.update_construction(replacement_objects(doc, entry, thread.parameters.face, profiles), title="Réparer l’avant-trou")
    assert certify(model) == pytest.approx(expected)
    doc.history.undo()
    assert doc.objects[model.body_id].shape is None
    doc.history.redo()
    certify(model)
    path = tmp_path/"reparation.cada"
    save_document(doc, path, registry=model.codecs)
    certify(model, load_document(path, registry=model.codecs))
    # A diameter that no longer fits must fail atomically, without an old result.
    hole = doc.objects[model.hole_id]
    snapshot = dict(doc.objects), doc.history.count
    with pytest.raises(ValueError):
        doc.update_construction((replace(hole, parameters=replace(hole.parameters, diameter=thread.parameters.diameter+.2)),))
    assert (dict(doc.objects), doc.history.count) == snapshot
    certify(model)
