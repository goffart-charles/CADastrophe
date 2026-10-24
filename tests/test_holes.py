"""Native hole geometry, associative positions and ordinary document history."""
from dataclasses import replace
from math import pi, tan, radians
import os
from pathlib import Path

import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer

from cadastrophe.document import ObjectType
from cadastrophe.core.feature_objects import HoleFeature
from cadastrophe.core.feature_model import Operation, SketchInput, ExtrusionParameters, ResultStatus, BodyStateRef
from cadastrophe.core.hole_model import HoleParameters
from cadastrophe.core.operation_model import GeometryReference, PlaneParameters, ReplayParameters
from cadastrophe.core.body_history import evaluate_body, preview_construction, affected_bodies
from cadastrophe.core.persistence import save_document, load_document
from examples.parametric_example import registry
from plugins.modeling.operation_codec import HOLE_CODEC
from plugins.sketch import commands
from plugins.sketch.model import Point, Vec2
from plugins.sketch.patterns import add_pattern
from test_parametric_operations import system, plane, profile, append, datum
from test_primitives import volume


def setup_holes(*, reverse=False, points=((15, 15),), patterned=False):
    doc, service = system()
    base = profile(doc, ((0, 0), (60, 0), (60, 40), (0, 40)))
    body, extrusion, result = append(doc, service, base, parameters=ExtrusionParameters(depth=20.))
    assert result.status is ResultStatus.READY, result.message
    support = datum(doc, PlaneParameters("offset", (GeometryReference("plane", plane(doc).id),), distance=20.)) if reverse else plane(doc)
    positions = commands.create_sketch(doc, support.id)
    commands.add_entities(doc, positions.id, tuple(Point(Vec2(*p)) for p in points), title="Positions")
    positions = doc.objects[positions.id]
    if patterned:
        sketch = add_pattern(positions.sketch, (positions.sketch.entities[0].id,), "linear_pattern", count=3, du=15.)
        positions = replace(positions, sketch=sketch)
        doc.update_construction((positions,))
    return doc, service, body, extrusion, positions


def hole(doc, service, body, positions, parameters=None):
    body = doc.objects[body.id]
    feature = HoleFeature("Perçage", None, body.color, kind=ObjectType.FEATURE, body_id=body.id,
        operation=Operation.CUT, source=SketchInput(positions.id, positions.producer_refs),
        parameters=parameters or HoleParameters())
    candidate = {**doc.objects, body.id: replace(body, features=(*body.features, feature.id)), feature.id: feature}
    results, prefixes = preview_construction(doc, candidate, affected_bodies(candidate, (feature.id,)), service.evaluator)
    doc.update_construction((candidate[body.id], feature), results=results, prefixes=prefixes)
    return feature, results[body.id]


def removed(p, count=1, thickness=20.):
    depth = p.depth if p.end_condition == "blind" else thickness
    radius, head = p.diameter / 2, p.head_diameter / 2
    amount = pi * radius**2 * depth
    if p.form == "counterbore":
        amount += pi * (head**2 - radius**2) * p.head_depth
    if p.form == "countersink":
        height = (head - radius) / tan(radians(p.angle / 2))
        amount += pi * height * ((head**2 + head*radius + radius**2) / 3 - radius**2)
    return count * amount


@pytest.mark.parametrize("form", ("simple", "counterbore", "countersink"))
@pytest.mark.parametrize("condition", ("blind", "through_all"))
@pytest.mark.parametrize("reverse", (False, True))
def test_holes_forms_volumes_directions_and_semantic_names(form, condition, reverse):
    doc, service, body, _, positions = setup_holes(reverse=reverse, points=((15, 15), (40, 20)))
    p = HoleParameters(form=form, depth=12., end_condition=condition, reversed=reverse)
    feature, result = hole(doc, service, body, positions, p)
    assert result.status is ResultStatus.READY and BRepCheck_Analyzer(result.shape, True).IsValid()
    assert volume(result.shape) == pytest.approx(48000 - removed(p, 2), rel=1e-8)
    for point in positions.sketch.entities:
        assert any(f"{feature.id}:hole:{point.id}:bore:wall" == label for label, _ in result.topology.faces)
    assert all(":untracked:" not in label for label, _ in result.topology.faces if label.startswith(feature.id))


def test_holes_associative_points_equations_undo_reload_and_activation(tmp_path):
    from cadastrophe.core.expressions import Variable, Expression
    from cadastrophe.core.parameters import set_expression
    from cadastrophe.core.feature_activation import set_enabled
    doc, service, body, extrusion, positions = setup_holes(patterned=True)
    p = HoleParameters(form="counterbore", end_condition="through_all")
    types = registry(service)
    root = os.environ.get("CADASTROPHE_HOLE_EXAMPLE")
    if root:
        Path(root).mkdir(parents=True, exist_ok=True)
        save_document(doc, Path(root) / "01-avant.cada", registry=types)
    feature, result = hole(doc, service, body, positions, p)
    assert volume(result.shape) == pytest.approx(48000 - removed(p, 3))
    wall_names = {label for label, _ in result.topology.faces if ":hole:" in label}
    variable = Variable("Diametre", Expression("variable", "=8"), unit="length")
    doc.update_construction((set_expression(feature, "diameter", Expression("diameter", "=Diametre")),),
        variables=(variable,), title="Diamètre piloté")
    fresh = doc.objects[feature.id]
    assert fresh.parameters.diameter == 8.
    # Shrink and restore the associative motif; retained point face names persist.
    pattern = positions.sketch.patterns[0]
    smaller = replace(positions, sketch=replace(positions.sketch, patterns=(replace(pattern, count=2),)))
    candidate = {**doc.objects, positions.id: smaller,
                 extrusion.id: replace(extrusion, parameters=replace(extrusion.parameters, depth=30.))}
    results, prefixes = preview_construction(doc, candidate, affected_bodies(candidate, (positions.id, extrusion.id)), service.evaluator)
    doc.update_construction((smaller, candidate[extrusion.id]), results=results, prefixes=prefixes)
    expected = 72000 - removed(fresh.parameters, 2, 30.)
    assert volume(doc.objects[body.id].shape) == pytest.approx(expected)
    assert wall_names & {label for label, _ in results[body.id].topology.faces}
    doc.history.undo()
    doc.history.redo()
    assert volume(doc.objects[body.id].shape) == pytest.approx(expected)
    set_enabled(doc, (feature.id,), False, service.evaluator)
    assert volume(doc.objects[body.id].shape) == pytest.approx(72000)
    set_enabled(doc, (feature.id,), True, service.evaluator)
    assert volume(doc.objects[body.id].shape) == pytest.approx(expected)
    path = tmp_path / "perçages.cada"
    save_document(doc, path, registry=types)
    restored = load_document(path, registry=types)
    assert set(restored.objects) == set(doc.objects)
    assert restored.objects[feature.id].expressions == doc.objects[feature.id].expressions
    assert volume(restored.objects[body.id].shape) == pytest.approx(expected)
    if root:
        save_document(doc, Path(root) / "02-apres.cada", registry=types)


def test_holes_invalid_position_missing_point_repair_and_atomic_failure():
    from cadastrophe.core.reference_repair import reference_entries, entry_issue, replacement_objects
    doc, service, body, _, positions = setup_holes(points=((15, 15), (300, 20)))
    snapshot, count = dict(doc.objects), doc.history.count
    with pytest.raises(ValueError, match="aucune matière"):
        hole(doc, service, body, positions)
    assert dict(doc.objects) == snapshot and doc.history.count == count
    first, outside = positions.sketch.entities
    ref = GeometryReference("sketch_point", positions.id, entity_id=first.id, part="position")
    feature, _ = hole(doc, service, body, positions, HoleParameters(positions=(ref,)))
    changed = replace(positions, sketch=positions.sketch.without(first.id))
    candidate = {**doc.objects, positions.id: changed}
    results, prefixes = preview_construction(doc, candidate, (body.id,), service.evaluator, strict=False)
    assert results[body.id].status is ResultStatus.ERROR and results[body.id].shape is None
    doc.update_construction((changed,), results=results, prefixes=prefixes)
    entry = next(e for e in reference_entries(doc, feature.id) if e.role == "point")
    assert entry_issue(doc, entry, service._profiles)
    moved = replace(changed, sketch=changed.sketch.with_replaced(replace(outside, position=Vec2(20, 20))))
    doc.update_construction((moved,))
    replacement = GeometryReference("sketch_point", positions.id, entity_id=outside.id, part="position")
    repaired, = replacement_objects(doc, entry, replacement, service._profiles)
    candidate = {**doc.objects, repaired.id: repaired}
    results, prefixes = preview_construction(doc, candidate, (body.id,), service.evaluator)
    doc.update_construction((repaired,), results=results, prefixes=prefixes)
    assert not entry_issue(doc, next(e for e in reference_entries(doc, feature.id) if e.role == "point"), service._profiles)
    assert BRepCheck_Analyzer(doc.objects[body.id].shape).IsValid()


def test_holes_through_all_3d_motif_rebounds_target_and_copy(tmp_path):
    from cadastrophe.core.clipboard import encode_objects, decode_objects
    doc, service, body, _, positions = setup_holes()
    feature, _ = hole(doc, service, body, positions, HoleParameters(end_condition="through_all"))
    _, motif, result = append(doc, service, body=body, operation=Operation.LINEAR_PATTERN,
        replay=ReplayParameters((feature.id,), GeometryReference("axis", values=(0, 0, 0, 1, 0, 0)), count=3, spacing=15.))
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(48000 - removed(feature.parameters, 3))
    types = registry(service)
    copied = decode_objects(encode_objects(doc, (body.id,), types), types)
    copy = copied.document.objects[copied.roots[0]]
    assert volume(copy.shape) == pytest.approx(volume(result.shape))
    assert copy.id != body.id


def test_holes_motif_recomputes_depth_after_a_later_boss():
    doc, service, body, _, positions = setup_holes()
    feature, _ = hole(doc, service, body, positions, HoleParameters(end_condition="through_all"))
    boss_profile = profile(doc, ((28, 0), (58, 0), (58, 40), (28, 40)))
    _, _, result = append(doc, service, boss_profile, body=body, operation=Operation.BOSS,
                           parameters=ExtrusionParameters(depth=40.))
    before = volume(result.shape)
    _, _, result = append(doc, service, body=body, operation=Operation.LINEAR_PATTERN,
        replay=ReplayParameters((feature.id,), GeometryReference("axis", values=(0, 0, 0, 1, 0, 0)), count=2, spacing=30.))
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(before - pi*9*40)


def test_holes_multi_position_through_all_motif_is_not_invalidated_by_its_first_cut():
    doc, service, body, _, positions = setup_holes(points=((10, 10), (10, 25)))
    feature, _ = hole(doc, service, body, positions, HoleParameters(end_condition="through_all"))
    _, _, result = append(doc, service, body=body, operation=Operation.LINEAR_PATTERN,
        replay=ReplayParameters((feature.id,), GeometryReference("axis", values=(0, 0, 0, 1, 0, 0)), count=3, spacing=20.))
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(48000 - 6*pi*9*20)


def test_holes_finishing_reference_survives_diameter_and_point_count_changes():
    from cadastrophe.core.native_naming import subshapes, capture
    from plugins.modeling.edge_geometry import edge_reference
    from cadastrophe.core.feature_objects import EdgeFeature
    from cadastrophe.core.feature_model import EdgeParameters
    doc, service, body, _, positions = setup_holes(patterned=True)
    feature, result = hole(doc, service, body, positions)
    # Select the upper circular edge of the first blind bore by its known
    # native surface, then retain only the captured persistent reference.
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.GeomAbs import GeomAbs_Circle
    from OCC.Core.TopoDS import topods
    edge = next(e for e in subshapes(result.shape, "Edge")
        if (c := BRepAdaptor_Curve(topods.Edge(e))).GetType() == GeomAbs_Circle
        and abs(c.Circle().Location().X()-15) < 1e-7 and abs(c.Circle().Location().Z()) < 1e-7)
    ref = capture(result.shape, edge, "Edge", result.topology)
    finish = EdgeFeature("Chanfrein du trou", None, body.color, kind=ObjectType.FEATURE, body_id=body.id,
        operation=Operation.CHAMFER, parameters=EdgeParameters((edge_reference(result.shape, topods.Edge(edge)),), size=.5, references=(ref,)))
    owner = doc.objects[body.id]
    candidate = {**doc.objects, body.id: replace(owner, features=(*owner.features, finish.id)), finish.id: finish}
    results, prefixes = preview_construction(doc, candidate, (body.id,), service.evaluator)
    doc.update_construction((candidate[body.id], finish), results=results, prefixes=prefixes)
    changed = replace(feature, parameters=replace(feature.parameters, diameter=8.))
    pattern = positions.sketch.patterns[0]
    points = replace(positions, sketch=replace(positions.sketch, patterns=(replace(pattern, count=2),)))
    candidate = {**doc.objects, changed.id: changed, points.id: points}
    results, prefixes = preview_construction(doc, candidate, (body.id,), service.evaluator)
    assert results[body.id].status is ResultStatus.READY
    assert BRepCheck_Analyzer(results[body.id].shape, True).IsValid()
    doc.update_construction((changed, points), results=results, prefixes=prefixes)
    assert doc.objects[finish.id].parameters.references == (ref,)


@pytest.mark.parametrize("options", ({"diameter": 0}, {"form": "invalid"}, {"angle": 180},
                                      {"form": "counterbore", "head_diameter": 4}, {"reversed": 1}))
def test_holes_invalid_dimensions(options):
    with pytest.raises(ValueError):
        HoleParameters(**options)


def test_holes_codec_strictness():
    doc, service, body, _, positions = setup_holes()
    feature, _ = hole(doc, service, body, positions)
    data = HOLE_CODEC.encode(feature)
    # Schema keys are checked before any native restoration.
    with pytest.raises(ValueError):
        HOLE_CODEC.decode(feature, {**data, "extra": True})


def test_holes_support_move_changes_blind_depth_and_invalid_heads_are_atomic():
    doc, service, body, _, positions = setup_holes(reverse=True)
    feature, result = hole(doc, service, body, positions, HoleParameters(reversed=True))
    support = doc.objects[positions.sketch.support.plane_id]
    moved = replace(support, parameters=replace(support.parameters, distance=25.))
    candidate = {**doc.objects, support.id: moved}
    results, prefixes = preview_construction(doc, candidate, affected_bodies(candidate, (support.id,)), service.evaluator)
    assert volume(results[body.id].shape) == pytest.approx(48000 - pi*9*5)
    doc.update_construction((moved,), results=results, prefixes=prefixes)
    snapshot = dict(doc.objects)
    impossible = replace(feature, parameters=replace(feature.parameters, form="counterbore", head_depth=12.))
    with pytest.raises(ValueError, match="moins profonde"):
        preview_construction(doc, {**doc.objects, feature.id: impossible}, (body.id,), service.evaluator)
    assert dict(doc.objects) == snapshot
    doc.history.undo()
    assert doc.objects[support.id].parameters.distance == 20.
    doc.history.redo()
    assert volume(doc.objects[body.id].shape) == pytest.approx(48000 - pi*9*5)


def test_holes_repair_whole_position_sketch_replaces_explicit_points_atomically():
    from cadastrophe.core.reference_repair import reference_entries, replacement_objects
    doc, service, body, _, positions = setup_holes()
    point, = positions.sketch.entities
    ref = GeometryReference("sketch_point", positions.id, entity_id=point.id, part="position")
    feature, _ = hole(doc, service, body, positions, HoleParameters(positions=(ref,)))
    new_sketch = commands.create_sketch(doc, plane(doc).id)
    commands.add_entity(doc, new_sketch.id, Point(Vec2(30, 20)))
    entry = next(e for e in reference_entries(doc, feature.id) if e.role == "sketch")
    replacement, = replacement_objects(doc, entry, (new_sketch.id, ()), service._profiles)
    assert replacement.id == feature.id and replacement.source.sketch_id == new_sketch.id
    assert replacement.parameters.positions == ()
    results, _ = preview_construction(doc, {**doc.objects, replacement.id: replacement}, (body.id,), service.evaluator)
    assert results[body.id].status is ResultStatus.READY


def test_holes_motif_keeps_surviving_point_names_when_an_earlier_point_is_removed():
    doc, service, body, _, positions = setup_holes(points=((10, 10), (10, 25)))
    feature, _ = hole(doc, service, body, positions, HoleParameters(end_condition="through_all"))
    _, motif, result = append(doc, service, body=body, operation=Operation.LINEAR_PATTERN,
        replay=ReplayParameters((feature.id,), GeometryReference("axis", values=(0, 0, 0, 1, 0, 0)), count=3, spacing=20.))
    first, second = positions.sketch.entities
    labels = {label for label, _ in result.topology.faces if label.startswith(motif.id) and second.id in label}
    assert labels
    fewer = replace(positions, sketch=positions.sketch.without(first.id))
    candidate = {**doc.objects, fewer.id: fewer}
    results, _ = preview_construction(doc, candidate, (body.id,), service.evaluator)
    assert labels <= {label for label, _ in results[body.id].topology.faces}
    assert volume(results[body.id].shape) == pytest.approx(48000 - 3*pi*9*20)
