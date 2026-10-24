"""Real native helical geometry and persistent thread construction contracts."""
from dataclasses import replace
from math import pi
from uuid import uuid4

import pytest
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Cylinder
from OCC.Core.TopoDS import topods

from cadastrophe.core.thread_model import ThreadParameters, thread_choices, apply_thread_choice
from cadastrophe.core.thread_geometry import thread_tools_steps
from cadastrophe.core.native_naming import static_topology, capture, subshapes, NamingTrace
from cadastrophe.core.operation_model import GeometryReference
from cadastrophe.core.operation_effects import apply_effects, VolumeEffect, checked_solids
from cadastrophe.core.feature_model import Operation
from cadastrophe.core.computation import complete
from cadastrophe.core.mass_properties import precise_volume
from test_primitives import volume

NATIVE_TEST_COST = 6


def support(internal=False, diameter=6., pitch=1.):
    from cadastrophe.core.fastener_catalog import metric_threads
    drill = next(drill for d, p, drill, _ in metric_threads() if d == diameter and p == pitch)
    shape = BRepPrimAPI_MakeCylinder(diameter/2, 10.).Shape()
    if internal:
        shape = checked_solids(BRepAlgoAPI_Cut(BRepPrimAPI_MakeCylinder(diameter, 10.).Shape(),
                               BRepPrimAPI_MakeCylinder(drill/2, 10.).Shape()).Shape())[0]
    topology = static_topology(shape)
    face = next(f for f in subshapes(shape, "Face")
                if (a := BRepAdaptor_Surface(topods.Face(f))).GetType() == GeomAbs_Cylinder
                and a.Cylinder().Radius() == (drill/2 if internal else diameter/2))
    ref = GeometryReference("face", str(uuid4()), topology=capture(shape, face, "Face", topology))
    p = ThreadParameters(ref, internal=internal)
    p = apply_thread_choice(p, next(c for c in thread_choices(internal) if c.nominal == diameter and c.pitch == pitch))
    return shape, topology, p


@pytest.mark.parametrize("internal", (False, True))
@pytest.mark.parametrize("left", (False, True))
def test_thread_real_native_helical_volume(internal, left):
    shape, topology, p = support(internal)
    p = replace(p, left_handed=left)
    trace = NamingTrace("thread")
    tools = complete(thread_tools_steps(shape, p, topology=topology, trace=trace))
    assert len(tools) == 1
    result = apply_effects(shape, (VolumeEffect(Operation.CUT, tools[0], trace.topology),), trace=trace)
    assert BRepCheck_Analyzer(result, True).IsValid()
    assert 0 < volume(result) < volume(shape)
    assert any(":thread:0:flank" in key for key, _ in trace.topology.faces), [[key for key, _ in t.faces] for _, t in trace.known]


def modeled(internal=False, **changes):
    shape, topology, p = support(internal)
    p = replace(p, **changes)
    trace = NamingTrace("thread")
    tools = complete(thread_tools_steps(shape, p, topology=topology, trace=trace))
    effects = tuple(VolumeEffect(Operation.CUT, s, next(t for old, t in reversed(trace.known) if old.IsSame(s))) for s in tools)
    return shape, p, apply_effects(shape, effects, trace=trace), trace


@pytest.mark.parametrize("internal", (False, True))
@pytest.mark.parametrize("options", ({"starts": 2}, {"reversed": True, "offset": 1., "phase": 37.},
                                     {"lead_in": 1., "lead_out": 1.}, {"clearance": .03},
                                     {"root_radius": 0., "root_flat": .1, "angle": 55., "depth": .5}))
def test_thread_custom_geometry_limits_and_validity(internal, options):
    original, p, result, trace = modeled(internal, length=3., **options)
    assert BRepCheck_Analyzer(result, True).IsValid()
    assert 0 < precise_volume(result) < precise_volume(original)
    assert all(":untracked:" not in key for key, _ in trace.topology.faces if key.startswith("thread:"))


def material(shape, r, theta, z):
    from math import cos, sin
    from OCC.Core.gp import gp_Pnt
    from OCC.Core.BRepClass3d import BRepClass3d_SolidClassifier
    from OCC.Core.TopAbs import TopAbs_IN
    return BRepClass3d_SolidClassifier(shape, gp_Pnt(r*cos(theta), r*sin(theta), z), 1e-7).State() == TopAbs_IN


def test_thread_precise_volume_conservation_for_trimmed_helical_faces():
    from cadastrophe.core.native_boolean import build_boolean
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Common
    original, topology, p = support(False)
    p = replace(p, length=3.)
    tool, = complete(thread_tools_steps(original, p, topology=topology))
    solids = (original, build_boolean(BRepAlgoAPI_Cut, original, tool).Shape(), build_boolean(BRepAlgoAPI_Common, original, tool).Shape())
    masses = tuple(precise_volume(s) for s in solids)
    assert abs(masses[0] - masses[1] - masses[2]) < masses[0]*1e-8
    from cadastrophe.core.cut import cut_solid
    result = cut_solid(original, tool)
    assert result.success, result.message
    assert result.removed_volume == pytest.approx(masses[2], abs=masses[0]*1e-8)


@pytest.mark.parametrize("internal", (False, True))
@pytest.mark.parametrize("left", (False, True))
def test_thread_pitch_handedness_axial_section_and_partial_length(internal, left):
    original, p, result, _ = modeled(internal, length=3., left_handed=left)
    radius = 3 - p.depth / 2
    # A quarter revolution must advance a quarter pitch, with the chosen hand.
    z = 1. + (-.25 if left else .25) + (0 if internal else .5)
    assert not material(result, radius, pi/2, z)
    assert material(result, radius, pi/2, z+.5)
    assert material(result, radius, pi/2, z+1.) == material(result, radius, pi/2, z)
    assert material(result, radius, pi/2, 8.) == material(original, radius, pi/2, 8.)


def test_thread_mating_pair_native_intersection_with_custom_radial_clearance():
    from cadastrophe.core.native_boolean import build_boolean
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Common
    _, _, screw, _ = modeled(False, length=10., clearance=.03)
    _, _, nut, _ = modeled(True, length=10., clearance=.03)
    common = build_boolean(BRepAlgoAPI_Common, screw, nut)
    assert common.IsDone()
    assert abs(precise_volume(common.Shape())) < 1e-6


@pytest.mark.parametrize("internal", (False, True))
@pytest.mark.parametrize("designation", ((3., .5), (4., .7), (5., .8), (6., 1.), (8., 1.25),
                                         (10., 1.5), (12., 1.75), (8., 1.), (10., 1.), (10., 1.25), (12., 1.5)))
def test_thread_metric_catalogue_sizes_generate_valid_real_solids(internal, designation):
    shape, topology, p = support(internal, *designation)
    p = replace(p, length=2*p.pitch)
    trace = NamingTrace("metric")
    tools = complete(thread_tools_steps(shape, p, topology=topology, trace=trace))
    result = apply_effects(shape, tuple(VolumeEffect(Operation.CUT, s) for s in tools), trace=trace)
    assert p.catalog_active and BRepCheck_Analyzer(result, True).IsValid()
    assert 0 < precise_volume(result) < precise_volume(shape)
    assert p.crest == pytest.approx(p.pitch*(.25 if internal else .125), abs=1e-12)


@pytest.mark.parametrize("internal", (False, True))
def test_thread_mesh_export_retains_helical_surface_and_measured_volume(internal):
    from plugins.import_export.mesh import triangulate, MeshOptions
    from test_mesh_exports import signed_volume
    from plugins.measurements.geometry import measure
    _, _, shape, _ = modeled(internal, length=3.)
    mesh = triangulate(shape, MeshOptions("high"))
    mesh.validate_closed()
    assert len(mesh.triangles) > 100
    exported_volume = signed_volume(tuple(tuple(mesh.vertices[i] for i in triangle) for triangle in mesh.triangles))
    assert exported_volume == pytest.approx(precise_volume(shape), rel=.003)
    assert measure("volume", (shape,)).value == pytest.approx(precise_volume(shape), rel=1e-10)


@pytest.mark.parametrize("changes", ({"pitch": 0.}, {"starts": 0}, {"starts": 1.5}, {"depth": 3.},
                                     {"length": -1.}, {"root_flat": .2}, {"angle": 180.},
                                     {"root_radius": 1.}, {"lead_in": 4., "lead_out": 4.}))
def test_thread_rejects_incoherent_parameters(changes):
    _, _, p = support()
    with pytest.raises(ValueError):
        replace(p, **changes)


def thread_document(internal=False, **changes):
    from test_parametric_operations import system
    from cadastrophe.core.feature_objects import BodyObject, ThreadFeature
    from cadastrophe.document import ObjectType
    from cadastrophe.core.feature_model import BodyStateRef
    from cadastrophe.core.body_history import preview_construction
    shape, topology, p = support(internal)
    doc, service = system()
    body = BodyObject("Écrou" if internal else "Vis", shape, (.6, .65, .7), source_kind="static", source_shape=shape)
    doc.add(body)
    p = replace(p, face=replace(p.face, object_id=body.id), length=3., **changes)
    feature = ThreadFeature("Taraudage" if internal else "Filetage", None, body.color, kind=ObjectType.FEATURE,
                            body_id=body.id, operation=Operation.CUT, input_state=BodyStateRef(body.id), parameters=p)
    new_body = replace(body, features=(feature.id,))
    candidate = {**doc.objects, body.id: new_body, feature.id: feature}
    results, prefixes = preview_construction(doc, candidate, (body.id,), service.evaluator)
    doc.update_construction((new_body, feature), results=results, prefixes=prefixes)
    doc.equation_evaluator = service.evaluator
    return doc, service, body, feature


def add_thread(doc, service, body, state, producer, *, internal=False, length=3.):
    from cadastrophe.core.feature_objects import ThreadFeature
    from cadastrophe.document import ObjectType
    from cadastrophe.core.feature_model import BodyStateRef
    from cadastrophe.core.body_history import preview_construction
    face = next(f for f in subshapes(state.shape, "Face")
                if (a := BRepAdaptor_Surface(topods.Face(f))).GetType() == GeomAbs_Cylinder
                and abs(a.Cylinder().Radius() - (2.5 if internal else 3.)) < 1e-7)
    ref = GeometryReference("face", body.id, producer, capture(state.shape, face, "Face", state.topology))
    p = apply_thread_choice(ThreadParameters(ref, internal=internal), next(c for c in thread_choices(internal) if c.nominal == 6))
    feature = ThreadFeature("Filetage", None, body.color, kind=ObjectType.FEATURE, body_id=body.id,
                            operation=Operation.CUT, input_state=BodyStateRef(body.id, producer), parameters=replace(p, length=length))
    body = replace(doc.objects[body.id], features=(*doc.objects[body.id].features, feature.id))
    candidate = {**doc.objects, body.id: body, feature.id: feature}
    results, prefixes = preview_construction(doc, candidate, (body.id,), service.evaluator)
    doc.update_construction((body, feature), results=results, prefixes=prefixes)
    return feature, results[body.id]


def test_thread_parametric_support_rebuild_lost_reference_repair_and_cancellation():
    from test_parametric_operations import system, profile, append
    from cadastrophe.core.feature_model import ExtrusionParameters, ResultStatus
    from cadastrophe.core.body_history import preview_construction, preview_construction_steps
    from cadastrophe.core.reference_repair import reference_entries, entry_issue, replacement_objects
    from cadastrophe.core.topology_refs import TopologyReference
    doc, service = system()
    sketch = profile(doc, circle=(0, 0, 3))
    body, base, state = append(doc, service, sketch, parameters=ExtrusionParameters(depth=10.))
    feature, state = add_thread(doc, service, body, state, base.id)
    initial = precise_volume(state.shape)
    labels = {key for key, _ in state.topology.faces if ":thread:" in key}
    changed = replace(base, parameters=replace(base.parameters, depth=12.))
    candidate = {**doc.objects, base.id: changed}
    results, prefixes = preview_construction(doc, candidate, (body.id,), service.evaluator)
    doc.update_construction((changed,), results=results, prefixes=prefixes)
    assert precise_volume(doc.objects[body.id].shape) == pytest.approx(initial + 18*pi, abs=1e-5)
    assert labels <= {key for key, _ in results[body.id].topology.faces}
    entry, = reference_entries(doc, feature.id)
    assert entry.role == "face" and not entry_issue(doc, entry, service._profiles)
    bad = replace(feature, parameters=replace(feature.parameters,
        face=replace(feature.parameters.face, topology=TopologyReference("Face", labels=("missing",)))))
    candidate = {**doc.objects, feature.id: bad}
    results, prefixes = preview_construction(doc, candidate, (body.id,), service.evaluator, strict=False)
    assert results[body.id].status is ResultStatus.ERROR and results[body.id].shape is None
    doc.update_construction((bad,), results=results, prefixes=prefixes)
    broken, = reference_entries(doc, feature.id)
    assert entry_issue(doc, broken, service._profiles)
    fixed, = replacement_objects(doc, broken, feature.parameters.face, service._profiles)
    candidate[feature.id] = fixed
    results, prefixes = preview_construction(doc, candidate, (body.id,), service.evaluator)
    doc.update_construction((fixed,), results=results, prefixes=prefixes)
    snapshot = dict(doc.objects), doc.history.count
    candidate = {**doc.objects, feature.id: replace(fixed, parameters=replace(fixed.parameters, length=5.))}
    steps = preview_construction_steps(doc, candidate, (body.id,), service.evaluator)
    next(steps)
    next(steps)
    steps.close()
    assert (dict(doc.objects), doc.history.count) == snapshot
    future = replace(fixed, parameters=replace(fixed.parameters, face=replace(fixed.parameters.face, producer_id=fixed.id)))
    with pytest.raises(ValueError):
        preview_construction(doc, {**doc.objects, fixed.id: future}, (body.id,), service.evaluator)


def test_thread_replay_transforms_only_its_cut_volume_into_another_hole():
    from test_holes import setup_holes, hole
    from cadastrophe.core.hole_model import HoleParameters
    from cadastrophe.core.operation_model import ReplayParameters
    from test_parametric_operations import append
    from cadastrophe.core.feature_model import ResultStatus
    doc, service, body, _, positions = setup_holes(points=((15, 15), (30, 15)))
    holes, state = hole(doc, service, body, positions, HoleParameters(diameter=5.))
    feature, threaded = add_thread(doc, service, body, state, holes.id, internal=True)
    original = 48000 - 2*pi*2.5**2*10
    removed = original - precise_volume(threaded.shape)
    _, motif, result = append(doc, service, body=body, operation=Operation.LINEAR_PATTERN,
        replay=ReplayParameters((feature.id,), GeometryReference("axis", values=(0, 0, 0, 1, 0, 0)), count=2, spacing=15.))
    assert result.status is ResultStatus.READY, result.message
    assert precise_volume(result.shape) == pytest.approx(original - 2*removed, abs=1e-5)
    assert any(":instance:" in key and ":thread:" in key for key, _ in result.topology.faces)


def test_thread_external_clearance_preserves_nearby_material_of_the_same_body():
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCC.Core.gp import gp_Ax2, gp_Pnt, gp_Dir
    from cadastrophe.core.native_boolean import build_boolean
    base = BRepPrimAPI_MakeBox(12., 10., 1.).Shape()
    shaft = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(3, 4, 0), gp_Dir(0, 0, 1)), 3., 10.).Shape()
    neighbour = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(8, 4, 0), gp_Dir(0, 0, 1)), 1., 10.).Shape()
    shape, = checked_solids(build_boolean(BRepAlgoAPI_Fuse, base, shaft).Shape())
    shape, = checked_solids(build_boolean(BRepAlgoAPI_Fuse, shape, neighbour).Shape())
    topology = static_topology(shape)
    face = next(f for f in subshapes(shape, "Face") if (a := BRepAdaptor_Surface(topods.Face(f))).GetType() == GeomAbs_Cylinder
                and a.Cylinder().Radius() == 3.)
    ref = GeometryReference("face", str(uuid4()), topology=capture(shape, face, "Face", topology))
    p = ThreadParameters(ref, length=3., clearance=.05)
    trace = NamingTrace("thread")
    tools = complete(thread_tools_steps(shape, p, topology=topology, trace=trace))
    result = apply_effects(shape, tuple(VolumeEffect(Operation.CUT, tool) for tool in tools), trace=trace)
    from OCC.Core.BRepClass3d import BRepClass3d_SolidClassifier
    from OCC.Core.TopAbs import TopAbs_IN
    assert BRepClass3d_SolidClassifier(result, gp_Pnt(8, 4, 2), 1e-7).State() == TopAbs_IN


@pytest.mark.parametrize("internal", (False, True))
def test_thread_history_equations_configurations_copy_save_and_export(internal, tmp_path):
    import os
    from pathlib import Path
    from cadastrophe.core.persistence import save_document, load_document
    from cadastrophe.core.clipboard import encode_objects, decode_objects
    from cadastrophe.core.feature_activation import set_enabled
    from cadastrophe.core.expressions import Expression
    from cadastrophe.core.parameters import set_expression
    from cadastrophe.core.configurations import create, activate, set_override
    from examples.parametric_example import registry
    doc, service, body, feature = thread_document(internal)
    doc.history.clear()
    initial = precise_volume(doc.objects[body.id].shape)
    doc.update_construction((set_expression(feature, "length", Expression("length", "=4")),))
    assert precise_volume(doc.objects[body.id].shape) < initial
    doc.history.undo()
    assert precise_volume(doc.objects[body.id].shape) == pytest.approx(initial, rel=1e-9)
    doc.history.redo()
    default = doc.active_configuration.id
    variant = create(doc, "Filetage court")
    set_override(doc, variant, feature.id + ":length", 2.)
    activate(doc, variant)
    assert doc.objects[feature.id].parameters.length == 2.
    assert precise_volume(doc.objects[body.id].shape) > initial
    types = registry(service)
    copy = decode_objects(encode_objects(doc, (body.id,), types), types)
    copied = copy.document.objects[copy.roots[0]]
    assert copied.id != body.id and precise_volume(copied.shape) == pytest.approx(precise_volume(doc.objects[body.id].shape))
    path = tmp_path / "filetage.cada"
    save_document(doc, path, registry=types)
    restored = load_document(path, registry=types)
    assert restored.objects[feature.id].parameters == doc.objects[feature.id].parameters
    assert precise_volume(restored.objects[body.id].shape) == pytest.approx(precise_volume(doc.objects[body.id].shape))
    from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_Reader, STEPControl_AsIs
    from OCC.Core.IFSelect import IFSelect_RetDone
    export = tmp_path / "filetage.step"
    writer = STEPControl_Writer()
    assert writer.Transfer(restored.objects[body.id].shape, STEPControl_AsIs) == IFSelect_RetDone
    assert writer.Write(str(export)) == IFSelect_RetDone
    reader = STEPControl_Reader()
    assert reader.ReadFile(str(export)) == IFSelect_RetDone
    reader.TransferRoots()
    assert BRepCheck_Analyzer(reader.OneShape(), True).IsValid()
    assert precise_volume(reader.OneShape()) == pytest.approx(precise_volume(restored.objects[body.id].shape), rel=1e-6)
    root = os.environ.get("CADASTROPHE_FASTENER_EXAMPLES")
    if root:
        target = Path(root) / "filetages"
        target.mkdir(parents=True, exist_ok=True)
        save_document(doc, target / ("taraudage-metrique.cada" if internal else "vis-metrique.cada"), registry=types)
    activate(doc, default)
    set_enabled(doc, (feature.id,), False, service.evaluator)
    assert precise_volume(doc.objects[body.id].shape) == pytest.approx(precise_volume(body.source_shape))
    set_enabled(doc, (feature.id,), True, service.evaluator)
    assert precise_volume(doc.objects[body.id].shape) < initial
