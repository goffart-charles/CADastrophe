"""Native construction curves and their parametric sweep consumers."""
from dataclasses import replace
from math import cos, sin, radians, pi, sqrt

import pytest
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.GProp import GProp_GProps

from cadastrophe.core.curve_model import CurveParameters, CurveObject
from cadastrophe.core.curve_geometry import curve_edge
from cadastrophe.core.body_history import evaluate_body, preview_construction, affected_bodies
from cadastrophe.core.feature_model import ResultStatus
from cadastrophe.core.operation_model import GeometryReference, SweepParameters
from cadastrophe.core.persistence import save_document, load_document
from cadastrophe.document import ObjectType
from cadastrophe.geometry import Vec3
from cadastrophe.scene import new_document
from plugins.curves.codec import CODEC
from test_parametric_operations import system, plane, profile
from test_sweep import swept
from examples.parametric_example import registry
from test_primitives import volume


def export_example(document, types, name):
    import os
    from pathlib import Path
    root = os.environ.get('CADASTROPHE_ISSUES_EXAMPLES')
    if root:
        path = Path(root)/'courbes'/(name+'.cada')
        path.parent.mkdir(parents=True, exist_ok=True)
        save_document(document, path, registry=types)


def curve(document, kind='helix', **options):
    parameters = CurveParameters(kind=kind, plane_id=plane(document).id if kind != 'points' else None, **options)
    obj = CurveObject('Courbe', None, (.3, .5, .8), kind=ObjectType.FEATURE, parameters=parameters)
    document.update_construction((obj,))
    return obj


@pytest.mark.parametrize('angle,clockwise', [(0., False), (0., True), (7., False), (-7., True)])
def test_helix_endpoints_native_validity_and_analytic_length(angle, clockwise):
    document = new_document()
    obj = curve(document, radius=10., pitch=8., turns=2.25, conical_angle=angle, clockwise=clockwise, start_angle=20.)
    shape = curve_edge(document, obj)
    assert BRepCheck_Analyzer(shape, True).IsValid()
    native = BRepAdaptor_Curve(shape)
    first, last = native.Value(native.FirstParameter()), native.Value(native.LastParameter())
    assert first.Coord() == pytest.approx((10*cos(radians(20)), 10*sin(radians(20)), 0.), abs=1e-6)
    assert last.Z() == pytest.approx(18., abs=1e-6)
    from plugins.modeling.extrusion_service import Extrusions
    from plugins.sketch.profile_service import SketchProfiles
    types = registry(Extrusions(SketchProfiles()))
    types.register('cadastrophe.curves', CODEC)
    export_example(document, types, f'helice_{angle:g}_{"horaire" if clockwise else "directe"}')
    if angle == 0:
        props = GProp_GProps()
        brepgprop.LinearProperties(shape, props)
        assert props.Mass() == pytest.approx(2.25*sqrt((2*pi*10)**2+8**2), rel=1e-6)


def test_spiral_and_3d_points_are_smooth_native_edges():
    document = new_document()
    obj = curve(document, kind='spiral', radius=4., radial_pitch=3., turns=2.)
    native = BRepAdaptor_Curve(curve_edge(document, obj))
    assert native.Value(native.FirstParameter()).Coord() == pytest.approx((4., 0., 0.), abs=1e-6)
    assert native.Value(native.LastParameter()).Coord() == pytest.approx((10., 0., 0.), abs=1e-6)
    from plugins.modeling.extrusion_service import Extrusions
    from plugins.sketch.profile_service import SketchProfiles
    types = registry(Extrusions(SketchProfiles()))
    types.register('cadastrophe.curves', CODEC)
    export_example(document, types, 'spirale')
    document.remove(obj.id)
    points = (Vec3(0., 0., 0.), Vec3(5., 2., 10.), Vec3(12., 6., 20.))
    obj = curve(document, kind='points', points=points)
    edge = curve_edge(document, obj)
    assert BRepCheck_Analyzer(edge, True).IsValid()
    native = BRepAdaptor_Curve(edge)
    assert native.Value(native.FirstParameter()).Coord() == pytest.approx(points[0].tuple())
    assert native.Value(native.LastParameter()).Coord() == pytest.approx(points[-1].tuple())
    export_example(document, types, 'courbe_3d_par_points')


def test_curve_follows_its_exact_datum_and_refuses_a_missing_support():
    from test_parametric_operations import datum
    from cadastrophe.core.operation_model import PlaneParameters
    document = new_document()
    support = datum(document, PlaneParameters('offset', (GeometryReference('plane', plane(document).id),), distance=5.))
    obj = CurveObject('Hélice sur plan', None, (.2, .5, .8),
        kind=ObjectType.FEATURE, parameters=CurveParameters(plane_id=support.id))
    document.update_construction((obj,))
    native = BRepAdaptor_Curve(curve_edge(document, obj))
    assert native.Value(native.FirstParameter()).Z() == pytest.approx(5.)
    changed = replace(support, parameters=replace(support.parameters, distance=15.))
    document.update_construction((changed,))
    native = BRepAdaptor_Curve(curve_edge(document, obj))
    assert native.Value(native.FirstParameter()).Z() == pytest.approx(15.)
    document.remove(support.id)
    assert obj.id not in document.objects
    with pytest.raises(ValueError, match='absent'):
        curve_edge(document, obj)
    document.history.undo()
    assert not document.objects[obj.id].reference_error(document)


def test_helix_sweep_rebuild_roundtrip_undo_and_missing_reference(tmp_path):
    document, service = system()
    path = curve(document, radius=10., pitch=8., turns=2.)
    sketch = profile(document, circle=(10., 0., 1.))
    parameters = SweepParameters((GeometryReference('curve', path.id),))
    body, feature, result = swept(document, service, sketch, None, parameters=parameters)
    assert result.status is ResultStatus.READY, result.message
    baseline = volume(result.shape)
    assert BRepCheck_Analyzer(result.shape, True).IsValid()
    from cadastrophe.core.materials import Material
    material = Material('Résine', 1250.)
    from cadastrophe.core.body_history import result_signature, body_result
    signature = result_signature(document.objects, body.id)
    document.set_materials({body.id: material})
    assert result_signature(document.objects, body.id) == signature
    types = registry(service)
    types.register('cadastrophe.curves', CODEC)
    export_example(document, types, 'ressort_avant')
    changed = replace(path, parameters=replace(path.parameters, turns=3.))
    candidate = {**document.objects, path.id: changed}
    results, prefixes = preview_construction(document, candidate, affected_bodies(candidate, {path.id}), service.evaluator)
    assert document.objects[path.id].parameters.turns == 2.
    document.update_construction((changed,), results=results, prefixes=prefixes)
    assert body_result(document, body.id).status is ResultStatus.READY
    actual = evaluate_body(document, body.id, service.evaluator(document), reuse=True)
    assert actual.status is ResultStatus.READY and volume(actual.shape) > baseline
    assert document.objects[body.id].material == material
    document.history.undo()
    assert document.objects[path.id].parameters.turns == 2.
    old = evaluate_body(document, body.id, service.evaluator(document), reuse=True)
    assert old.status is ResultStatus.READY and volume(old.shape) == pytest.approx(baseline, rel=1e-7)
    document.history.redo()
    saved = tmp_path/'ressort.cada'
    save_document(document, saved, registry=types)
    restored = load_document(saved, registry=types)
    assert restored.objects[path.id].parameters.turns == 3.
    assert restored.objects[feature.id].parameters.path[0].object_id == path.id
    assert volume(restored.objects[body.id].shape) == pytest.approx(volume(actual.shape), rel=1e-6)
    assert evaluate_body(document, body.id, service.evaluator(document), reuse=True).status is ResultStatus.READY
    export_example(document, types, 'ressort_apres')
    from cadastrophe.core.clipboard import encode_objects, decode_objects
    copied = decode_objects(encode_objects(document, [body.id], types), types)
    copied_body = copied.document.objects[copied.roots[0]]
    copied_curve, = [obj for obj in copied if isinstance(obj, CurveObject)]
    assert copied_curve.id != path.id and copied_curve.parameters.plane_id != path.parameters.plane_id
    assert copied_body.material == document.objects[body.id].material
    assert volume(copied_body.shape) == pytest.approx(volume(actual.shape), rel=1e-6)
    document.remove(path.id)
    assert path.id not in document.objects and feature.id not in document.objects
    document.history.undo()
    assert path.id in document.objects and feature.id in document.objects


def test_helix_preview_calculates_once_and_measures_previous_double_pass(monkeypatch):
    import json
    from pathlib import Path
    from time import perf_counter
    from types import SimpleNamespace
    from cadastrophe.core.feature_objects import BodyObject, SweepFeature
    from cadastrophe.core.feature_model import Operation, SketchInput
    from plugins.modeling.recipe_session import RecipePreview
    import cadastrophe.core.sweep as sweep

    document, service = system()
    path = curve(document, radius=10., pitch=8., turns=8.)
    sketch = profile(document, circle=(10., 0., 1.))
    body = BodyObject("Ressort", None, (.7, .7, .8))
    feature = SweepFeature("Balayage", None, body.color, kind=ObjectType.FEATURE, body_id=body.id,
        operation=Operation.NEW_BODY, source=SketchInput(sketch.id),
        parameters=SweepParameters((GeometryReference("curve", path.id),)))
    body = replace(body, features=(feature.id,))
    candidate = {**document.objects, body.id: body, feature.id: feature}
    calls = []
    native = sweep.sweep_face
    def counted(*args, **kwargs):
        calls.append(True)
        return native(*args, **kwargs)
    monkeypatch.setattr(sweep, "sweep_face", counted)
    start = perf_counter()
    before, _ = preview_construction(document, candidate, (body.id,), service.evaluator, stop_at=feature.id)
    preview_construction(document, candidate, (body.id,), service.evaluator, strict=False)
    old_time = perf_counter() - start
    assert len(calls) == 2
    calls.clear()
    session = RecipePreview()
    session.initialize(SimpleNamespace(active_document=document), service)
    start = perf_counter()
    session.calculate((body, feature), feature)
    new_time = perf_counter() - start
    assert len(calls) == 1
    assert volume(session._current.shape) == pytest.approx(volume(before[body.id].shape), rel=1e-8)
    assert BRepCheck_Analyzer(session._current.shape, True).IsValid()
    assert body.id not in document.objects
    artifact = Path(__file__).resolve().parents[1] / "artifacts" / "helix-preview.json"
    artifact.parent.mkdir(exist_ok=True)
    artifact.write_text(json.dumps({"turns": 8, "before_evaluations": 2, "after_evaluations": 1,
                                   "before_seconds": old_time, "after_seconds": new_time}, indent=2), encoding="utf-8")
