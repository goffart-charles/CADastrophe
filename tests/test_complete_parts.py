"""Whole delivered parts: geometric oracles, shared variants and real rebuilds."""
from dataclasses import replace
from math import pi
from pathlib import Path
import os

import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepClass3d import BRepClass3d_SolidClassifier
from OCC.Core.TopAbs import TopAbs_IN, TopAbs_OUT
from OCC.Core.gp import gp_Pnt

from cadastrophe.document import ObjectType
from cadastrophe.core.body_history import body_result, result_at, evaluate_body, evaluate_body_steps
from cadastrophe.core.configurations import activate, validate_all
from cadastrophe.core.feature_model import BodyStateRef, ResultStatus
from cadastrophe.core.feature_objects import BodyObject
from cadastrophe.core.operation_effects import checked_solids
from cadastrophe.core.persistence import load_document, save_document
from examples.configured_parts import CASES, build_configured_part
from examples.parametric_example import mass
from plugins.sketch.profile_service import SketchProfiles
from test_parametric_operations import bounds
from test_extrusion_tool import extrusion_system


def certify(model, document=None):
    doc = document or model.document
    reinforced = doc.active_configuration.name == "Renforcée"
    undrilled = doc.active_configuration.name == "Sans perçages guidés"
    height = model.case.reinforced_height if reinforced else model.case.height
    diameter = 5. if reinforced else 4.
    count = model.case.count-int(reinforced)
    thickness = height if model.case.key == "equerre-fixation" else model.case.thickness
    raw = result_at(doc, BodyStateRef(model.body_id, model.original_features[-1]))
    assert raw.status is ResultStatus.READY, raw.message
    removed = 0. if undrilled else count*pi*(diameter/2)**2*thickness
    # A single entry chamfer is a cone frustum minus its bore cylinder.
    if not reinforced and not undrilled:
        removed += pi*((diameter/2)*.25**2+.25**3/3)
    expected = mass(raw.shape)-removed
    for key, amount in ((model.body_id, expected), (model.child_id, 9*pi*height/4), (model.independent_id, 210.)):
        shape = doc.objects[key].shape
        assert shape is not None and BRepCheck_Analyzer(shape, True).IsValid()
        assert len(checked_solids(shape)) == 1
        assert mass(shape) == pytest.approx(amount, rel=1e-8, abs=1e-5)
    assert sum(o.kind is ObjectType.BODY for o in doc.objects.values()) == 3
    result = body_result(doc, model.body_id)
    probe = (*model.case.center, thickness/2)
    classified = BRepClass3d_SolidClassifier(result.shape, gp_Pnt(*probe), 1e-7).State()
    assert classified == (TopAbs_IN if undrilled else TopAbs_OUT)
    if not undrilled:
        points = doc.objects[model.points_id].sketch.entities
        assert len(points) == count
        labels = {label for label, _ in result.topology.faces}
        assert all(f"{model.hole_id}:hole:{p.id}:bore:wall" in labels for p in points)
    support_z = (height if model.case.key != "bride-atlas" else 8.)
    assert bounds(doc.objects[model.child_id].shape)[2] == pytest.approx(support_z)
    assert bounds(doc.objects[model.child_id].shape)[5] == pytest.approx(support_z+height/4)
    for body in doc.objects.values():
        if isinstance(body, BodyObject):
            for fid in body.features:
                state = result_at(doc, BodyStateRef(body.id, fid))
                assert state.status is ResultStatus.READY, (doc.objects[fid].name, state.message)
    return expected


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.key)
def test_complete_parts_variants_exact_geometry_shared_producers_and_roundtrip(case, tmp_path, monkeypatch):
    model = build_configured_part(case)
    doc = model.document
    original = frozenset(doc.objects)
    support = doc.objects[model.support_id].parameters.references[0]
    independent = doc.objects[model.independent_id]
    native = type(model.service.evaluator(doc)).evaluate
    calls = []
    names = {}
    def traced(self, feature, *args):
        calls.append(feature.id)
        return native(self, feature, *args)
    monkeypatch.setattr(type(model.service.evaluator(doc)), "evaluate", traced)
    for key in (*model.configurations, *reversed(model.configurations)):
        activate(doc, key)
        certify(model)
        labels = {label for label, _ in body_result(doc, model.body_id).topology.faces}
        assert labels == names.setdefault(key, labels)
        assert frozenset(doc.objects) == original and model.original_ids <= original
        assert doc.objects[model.support_id].parameters.references[0] == support
        assert doc.objects[model.independent_id] is independent
    assert model.hole_id in calls and calls.count(model.original_features[0]) >= (case.key != "bride-atlas")
    assert not set(doc.objects[model.independent_id].features).intersection(calls)
    before = doc.configuration_state, mass(doc.objects[model.body_id].shape)
    activate(doc, model.configurations[1])
    doc.history.undo()
    assert doc.configuration_state == before[0]
    certify(model)
    doc.history.redo()
    certify(model)
    output = tmp_path / (case.key+".cada")
    save_document(doc, output, registry=model.codecs)
    loaded = load_document(output, registry=model.codecs)
    assert loaded.configuration_state == doc.configuration_state
    for key in model.configurations:
        activate(loaded, key, evaluator=model.service.evaluator)
        certify(model, loaded)
        assert {label for label, _ in body_result(loaded, model.body_id).topology.faces} == names[key]
    snapshot = dict(doc.objects), doc.configuration_state, doc.history.index
    assert not any(validate_all(doc).values())
    assert (dict(doc.objects), doc.configuration_state, doc.history.index) == snapshot
    destination = os.environ.get("CADASTROPHE_CONFIGURED_PARTS_OUTPUT")
    if destination:
        activate(doc, model.configurations[0])
        target = Path(destination) / case.key / "03-configurations.cada"
        target.parent.mkdir(parents=True, exist_ok=True)
        save_document(doc, target, registry=model.codecs)


def test_complete_parts_historical_insertion_is_common_and_keeps_finishing_reference(tmp_path):
    from cadastrophe.core.feature_model import Operation, ExtrusionParameters, SketchInput
    from cadastrophe.core.feature_objects import ExtrusionFeature
    from plugins.sketch import commands
    from plugins.sketch.model import Circle, Vec2
    model = build_configured_part(CASES[0])
    doc = model.document
    before = certify(model)
    reference = doc.objects[model.finish_id].parameters.references
    doc.history_context.evaluator = model.service.evaluator
    doc.history_context.set_position(model.finish_id, after=False)
    xy = next(o for o in doc.objects.values() if o.plane and o.plane.name == "XY")
    sketch = commands.create_sketch(doc, xy.id, name="Plot inséré avant finition")
    commands.add_entity(doc, sketch.id, Circle(Vec2(20., 25.), 2.))
    owner = doc.objects[model.body_id]
    feature = ExtrusionFeature("Plot inséré", None, owner.color, kind=ObjectType.FEATURE,
        body_id=owner.id, operation=Operation.BOSS, source=SketchInput(sketch.id),
        parameters=ExtrusionParameters(depth=12.))
    doc.update_construction((replace(owner, features=(*owner.features, feature.id)), feature))
    assert doc.objects[owner.id].features[-2:] == (feature.id, model.finish_id)
    assert mass(doc.objects[owner.id].shape) == pytest.approx(before+16*pi)
    assert doc.objects[model.finish_id].parameters.references == reference
    doc.history_context.set_position()
    doc.history.undo()
    assert mass(doc.objects[owner.id].shape) == pytest.approx(before)
    doc.history.redo()
    for key in model.configurations:
        activate(doc, key)
        assert feature.id in doc.objects[owner.id].features
        assert BRepCheck_Analyzer(doc.objects[owner.id].shape).IsValid()
        assert doc.objects[model.finish_id].parameters.references == reference
    save_document(doc, tmp_path/"insertion.cada", registry=model.codecs)
    loaded = load_document(tmp_path/"insertion.cada", registry=model.codecs)
    assert loaded.objects[owner.id].features[-2:] == (feature.id, model.finish_id)
    assert loaded.objects[model.finish_id].parameters.references == reference


def test_complete_parts_cancel_rebuild_retains_objects_prefixes_history_and_active_variant():
    from cadastrophe.core.body_history import prefix_results
    from cadastrophe.core.history import HistoryBusyError
    model = build_configured_part(CASES[1])
    doc = model.document
    activate(doc, model.configurations[1])
    snapshot = dict(doc.objects), doc.configuration_state, doc.history.index, doc.history.count
    prefixes = dict(prefix_results(doc, model.body_id))
    task = evaluate_body_steps(doc, model.body_id, model.service.evaluator(doc), reuse=False)
    assert doc.history.blocked
    with pytest.raises(HistoryBusyError):
        activate(doc, model.configurations[0])
    assert next(task)[0] == 1
    assert (dict(doc.objects), doc.configuration_state, doc.history.index, doc.history.count) == snapshot
    task.close()
    assert not doc.history.blocked
    assert dict(prefix_results(doc, model.body_id)) == prefixes
    assert (dict(doc.objects), doc.configuration_state, doc.history.index, doc.history.count) == snapshot
    result = evaluate_body(doc, model.body_id, model.service.evaluator(doc), reuse=False)
    assert result.status is ResultStatus.READY
    certify(model)


def test_complete_parts_clear_undo_publishes_whole_shared_graph_before_notifications():
    model = build_configured_part(CASES[1])
    doc = model.document
    activate(doc, model.configurations[1])
    original = frozenset(doc.objects)
    configurations = doc.configuration_state
    doc.clear()
    observations = []
    def added(key):
        observations.append((key, frozenset(doc.objects)))
    doc.object_added.connect(added)
    try:
        doc.history.undo()
    finally:
        doc.object_added.disconnect(added)
    assert {key for key, _ in observations} == original
    assert all(graph == original for _, graph in observations)
    assert doc.configuration_state == configurations
    for key in (model.body_id, model.child_id, model.independent_id):
        assert evaluate_body(doc, key, model.service.evaluator(doc), reuse=True).status is ResultStatus.READY
    certify(model)


def test_complete_parts_split_cap_refuses_ambiguity_and_explicit_repair_survives_rebuild(tmp_path):
    from cadastrophe.core.feature_model import Operation, ExtrusionParameters, SketchInput
    from cadastrophe.core.feature_objects import ExtrusionFeature
    from cadastrophe.core.native_naming import capture, resolve, subshapes
    from cadastrophe.core.operation_model import GeometryReference, PlaneParameters
    from cadastrophe.core.reference_objects import DatumPlaneObject
    from cadastrophe.core.reference_geometry import face_plane, plane_shape, resolve_plane
    from cadastrophe.core.reference_repair import reference_entries, replacement_objects, entry_issue
    from examples.parametric_example import polygon
    from plugins.sketch import commands
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps
    model = build_configured_part(CASES[0])
    doc = model.document
    before = body_result(doc, model.body_id)
    caps = []
    for face in subshapes(before.shape, "Face"):
        try:
            plane = face_plane(face)
        except ValueError:
            continue
        if plane.normal.z > .99 and abs(plane.origin.z-8.) < 1e-6:
            caps.append(face)
    assert len(caps) == 1
    old = capture(before.shape, caps[0], "Face", before.topology)
    xy = next(o for o in doc.objects.values() if o.plane and o.plane.name == "XY")
    support = DatumPlaneObject("Plan de la rainure", None, (.4,.6,.7), plane=xy.plane,
        parameters=PlaneParameters("offset", (GeometryReference("plane", xy.id),), distance=8.))
    frame = resolve_plane(doc, support)
    doc.add(replace(support, plane=frame, shape=plane_shape(frame)))
    sketch = commands.create_sketch(doc, support.id)
    commands.add_entities(doc, sketch.id, polygon(((9,24), (81,24), (81,26), (9,26))), title="Profil de la rainure")
    owner = doc.objects[model.body_id]
    slot = ExtrusionFeature("Rainure divisant le dessus", None, owner.color, kind=ObjectType.FEATURE,
        body_id=owner.id, operation=Operation.CUT, source=SketchInput(sketch.id),
        parameters=ExtrusionParameters(depth=1., reversed=True))
    doc.update_construction((replace(owner, features=(*owner.features, slot.id)), slot))
    after = body_result(doc, owner.id)
    assert BRepCheck_Analyzer(after.shape).IsValid() and len(checked_solids(after.shape)) == 1
    assert mass(after.shape) < mass(before.shape)
    with pytest.raises(ValueError, match="ambigu"):
        resolve(after.shape, old, after.topology)
    descendants = [f for f in subshapes(after.shape, "Face") if set(old.labels) <= set(after.topology.labels(f, "Face"))]
    assert len(descendants) == 2
    # An explicit face choice supplies a named neighborhood for the repair.
    properties = GProp_GProps()
    def center_y(face):
        brepgprop.SurfaceProperties(face, properties)
        return properties.CentreOfMass().Y()
    chosen = min(descendants, key=center_y)
    new = capture(after.shape, chosen, "Face", after.topology)
    assert resolve(after.shape, new, after.topology).IsSame(chosen)
    broken = DatumPlaneObject("Support à réparer après division", None, (.4,.6,.7), plane=frame,
        parameters=PlaneParameters("offset", (GeometryReference("face", owner.id, slot.id, old),), distance=0.))
    doc.update_construction((broken,), allow_errors=True)
    assert doc.objects[broken.id].reference_error_message
    entry, = reference_entries(doc, broken.id)
    repaired = GeometryReference("face", owner.id, slot.id, new)
    profiles = SketchProfiles()
    doc.update_construction(replacement_objects(doc, entry, repaired, profiles))
    assert not entry_issue(doc, reference_entries(doc, broken.id)[0], profiles)
    path = tmp_path/"face-divisee.cada"
    save_document(doc, path, registry=model.codecs)
    loaded = load_document(path, registry=model.codecs)
    for reuse in (False, True, False):
        result = evaluate_body(loaded, owner.id, model.service.evaluator(loaded), reuse=reuse)
        assert BRepCheck_Analyzer(result.shape).IsValid()
        resolved = resolve(result.shape, new, result.topology)
        assert center_y(resolved) < 24.
        with pytest.raises(ValueError, match="ambigu"):
            resolve(result.shape, old, result.topology)
    # The complementary fusion is real OCCT geometry on this complete part.
    # A face chosen on one side of the slot must not become the merged face.
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from cadastrophe.core.native_naming import NamingTrace
    filler = BRepPrimAPI_MakeBox(gp_Pnt(9.,24.,7.), 72.,2.,1.).Shape()
    maker = BRepAlgoAPI_Fuse(after.shape, filler)
    maker.SimplifyResult(True, True)
    merged = maker.Shape()
    trace = NamingTrace("remplissage", after)
    trace.operation(maker, (after.shape, filler), merged)
    assert BRepCheck_Analyzer(merged).IsValid() and len(checked_solids(merged)) == 1
    with pytest.raises(ValueError, match="perdue|ambigu|fusion"):
        resolve(merged, new, trace.topology)


def test_complete_parts_source_deactivation_hides_stale_geometry_then_repair_and_reload(extrusion_system, tmp_path):
    from cadastrophe.core.feature_activation import set_enabled
    from cadastrophe.core.body_history import last_valid_result
    from cadastrophe.core.reference_repair import reference_entries, entry_issue, replacement_objects
    from cadastrophe.core.topology_refs import TopologyReference
    from PySide6.QtTest import QTest
    model = build_configured_part(CASES[1])
    path = tmp_path/"boitier.cada"
    save_document(model.document, path, registry=model.codecs)
    w, _, _ = extrusion_system
    doc = w.open_document(path)
    QTest.qWait(150)
    assert w.viewport.camera is not None and not w.viewport._failed
    service = w.services.service("cadastrophe.modeling.extrusions")
    profiles = w.services.service("cadastrophe.sketch.profiles")
    before = last_valid_result(doc, model.body_id)
    original_support = doc.objects[model.support_id]
    independent = doc.objects[model.independent_id]
    root = model.original_features[0]
    set_enabled(doc, (root,), False, service.evaluator)
    for key in (model.body_id, model.child_id):
        assert body_result(doc, key).status in (ResultStatus.ERROR, ResultStatus.BLOCKED)
        assert doc.objects[key].shape is None
        shown = w.viewport.document_adapter.display_object(doc.objects[key])
        assert shown.shape is None
    assert last_valid_result(doc, model.body_id) is before
    assert doc.objects[model.independent_id] is independent
    doc.history.undo()
    certify(model, doc)
    # Deliberately corrupt a producer reference through the ordinary command,
    # then repair it explicitly using the original named face, never an index.
    reference = original_support.parameters.references[0]
    missing = replace(reference, topology=TopologyReference("Face", ("missing:cap",)))
    broken = replace(original_support, parameters=replace(original_support.parameters, references=(missing,)))
    doc.update_construction((broken,), allow_errors=True)
    assert doc.objects[model.child_id].shape is None
    entry, = reference_entries(doc, model.support_id)
    assert entry_issue(doc, entry, profiles)
    replacements = replacement_objects(doc, entry, reference, profiles)
    doc.update_construction(replacements, title="Réparer le support partagé")
    assert not entry_issue(doc, reference_entries(doc, model.support_id)[0], profiles)
    certify(model, doc)
    doc.history.undo()
    assert doc.objects[model.child_id].shape is None
    doc.history.redo()
    certify(model, doc)
    saved = tmp_path/"repare.cada"
    assert w.save_document(path=saved) and w.close_document()
    loaded = w.open_document(saved)
    certify(model, loaded)
    assert loaded.objects[model.support_id].parameters.references[0] == reference
    QTest.qWait(50)
