"""Associative recipes: exact curves, identities, real rebuilds and persistence."""
from dataclasses import replace
from math import pi
import json
from uuid import uuid4
import zipfile

import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from PySide6.QtWidgets import QApplication

from cadastrophe.core.expressions import Expression, Variable
from cadastrophe.core.feature_model import BodyStateRef, ExtrusionParameters, Operation, ResultStatus, SketchInput
from cadastrophe.core.feature_objects import BodyObject, ExtrusionFeature
from cadastrophe.core.persistence import load_document, save_document
from cadastrophe.document import ObjectType
from cadastrophe.geometry import REFERENCE_PLANES
from examples.parametric_example import ParametricExample, mass, polygon, registry
from plugins.sketch import commands
from plugins.sketch.codec import CODEC, decode, encode
from plugins.sketch.constraints import Constraint, Reference
from plugins.sketch.model import Arc, Circle, Ellipse, Point, Segment, Spline, Vec2
from plugins.sketch.objects import SketchObject
from plugins.sketch.patterns import SketchPattern, add_pattern, detach, remove
from plugins.sketch.presentation import entity_shape
from plugins.sketch.solver import solve, diagnose
from test_sketch_model import sketch


def source_entities():
    return (Point(Vec2(10, 2)), Segment(Vec2(10, 2), Vec2(14, 2)), Circle(Vec2(13, 6), 1),
            Arc(Vec2(20, 10), 3, .2, -1.2), Ellipse(Vec2(20, -5), 3, 2, .7),
            Spline((Vec2(10, 0), Vec2(12, 5), Vec2(15, 2), Vec2(18, 6))),
            Spline((Vec2(11, 0), Vec2(11, 1), Vec2(10, 1)), degree=2,
                   knots=(0., 0., 0., 1., 1., 1.), weights=(1., 2**-.5, 1.)))


@pytest.mark.parametrize("frame", REFERENCE_PLANES)
@pytest.mark.parametrize("mode", ("linear_pattern", "circular_pattern"))
def test_sketch_patterns_exact_types_source_changes_ids_and_full_circle(frame, mode):
    entities = source_entities()
    base = replace(sketch(frame), entities=entities)
    patterned = add_pattern(base, tuple(e.id for e in entities), mode, count=4, du=30., angle=-pi)
    pattern = patterned.patterns[0]
    ids = tuple(e.id for e in patterned.entities)
    assert patterned.source_entities == entities and len(ids) == 4*len(entities)
    for e in patterned.entities:
        if type(e) is not Point:
            assert BRepCheck_Analyzer(entity_shape(patterned, e)).IsValid()
    for _ in range(3):
        moved = replace(entities[0], position=Vec2(12, 3))
        fresh = patterned.with_replaced(moved)
        assert tuple(e.id for e in fresh.entities) == ids
        if mode == "linear_pattern":
            assert fresh.entity(pattern.occurrence_id(moved.id, 2)).position == Vec2(72, 3)
        else:
            point = fresh.entity(pattern.occurrence_id(moved.id, 3)).position
            assert (point.u, point.v) == pytest.approx((-12, -3))
        patterned = fresh
    small = replace(patterned, patterns=(replace(pattern, count=2),))
    assert len(small.entities) == 2*len(entities)
    restored = replace(small, patterns=(pattern,))
    assert restored.entities == patterned.entities
    if mode == "circular_pattern":
        full = replace(restored, patterns=(replace(pattern, angle=2*pi),))
        assert tuple(e.id for e in full.entities) == ids
        p = full.entity(pattern.occurrence_id(entities[0].id, 3)).position
        assert (p.u, p.v) == pytest.approx((3, -12))
        assert len({full.entity(pattern.occurrence_id(entities[0].id, i)).position for i in range(1, 4)}) == 3
        assert full.source_entities[0].position not in {full.entity(pattern.occurrence_id(entities[0].id, i)).position for i in range(1, 4)}


def test_sketch_patterns_solver_sources_measurements_and_driving_refusal():
    line = Segment(Vec2(0, 0), Vec2(10, 0))
    base = replace(sketch(), entities=(line,), constraints=(
        Constraint("fixed", (Reference(line.id, "start"),), Vec2(0, 0)),
        Constraint("horizontal", (Reference(line.id),)), Constraint("length", (Reference(line.id),), 10.)))
    patterned = add_pattern(base, (line.id,), "linear_pattern", count=5, dv=20.)
    copy = patterned.entities[2]
    measure = Constraint("length", (Reference(copy.id),), 10., driven=True)
    patterned = replace(patterned, constraints=(*patterned.constraints, measure))
    changed = replace(patterned, constraints=tuple(replace(c, value=25.) if c.kind == "length" and not c.driven else c
                                                  for c in patterned.constraints))
    result = solve(changed)
    assert result.success, result.message
    assert result.dof == 0 and diagnose(result.sketch).dof == 0
    assert result.sketch.entity(copy.id).end.u == pytest.approx(65.)
    assert result.sketch.constraints[-1].value == pytest.approx(25.)
    for constraint in (Constraint("length", (Reference(copy.id),), 30.),
                       Constraint("horizontal", (Reference(copy.id),))):
        with pytest.raises(ValueError, match="pilotantes"):
            replace(patterned, constraints=(*patterned.constraints, constraint))
    with pytest.raises(ValueError, match="Occurrence pilotée"):
        patterned.with_replaced(replace(copy, end=Vec2(100, 20)))
    with pytest.raises(ValueError, match="Source du motif absente"):
        patterned.without(line.id)
    with pytest.raises(ValueError, match="occurrence référencée"):
        replace(patterned, patterns=(replace(patterned.patterns[0], count=2),))
    baked = detach(patterned, patterned.patterns[0].id)
    assert baked.entities == patterned.entities and not baked.patterns
    assert baked.with_replaced(replace(copy, end=Vec2(100, 20))).entity(copy.id).end == Vec2(100, 20)
    assert remove(patterned, patterned.patterns[0].id).entities == (line,)
    # Coincident measurement anchors cannot form a native length annotation.
    # Keep the existing positive-dimension contract, before publication.
    a, b = Point(Vec2(0, 0)), Point(Vec2(15, 0))
    points = add_pattern(replace(sketch(), entities=(a, b)), (a.id,), "linear_pattern", count=2, du=10.)
    measured = replace(points, constraints=(Constraint("distance", (
        Reference(points.entities[-1].id, "position"), Reference(b.id, "position")), 5., driven=True),))
    with pytest.raises(ValueError, match="strictement positive"):
        measured.with_replaced(replace(b, position=Vec2(10, 0)))


@pytest.mark.parametrize("parameters", ({"count": 1}, {"count": 201}, {"count": True}, {"count": 2.5},
    {"du": 0., "dv": 0.}, {"du": float("nan")}, {"mode": "circular_pattern", "angle": 0.},
    {"mode": "circular_pattern", "angle": 7.}))
def test_sketch_patterns_invalid_recipes_are_atomic(parameters):
    point = Point(Vec2(1, 2))
    original = replace(sketch(), entities=(point,))
    with pytest.raises(ValueError):
        add_pattern(original, (point.id,), **dict({"mode": "linear_pattern"}, **parameters))
    assert original.entities == (point,) and not original.patterns


def test_sketch_patterns_budget_nested_missing_and_multiple_detach():
    entities = tuple(Point(Vec2(i, 0)) for i in range(3))
    original = replace(sketch(), entities=entities)
    with pytest.raises(ValueError, match="512"):
        add_pattern(original, tuple(e.id for e in entities), "linear_pattern", count=200)
    spline = Spline(tuple(Vec2(i, i % 3) for i in range(64)))
    with pytest.raises(ValueError, match="budget de complexité"):
        add_pattern(replace(sketch(), entities=(spline,)), (spline.id,), "linear_pattern", count=200)
    patterned = add_pattern(original, (entities[0].id,), "linear_pattern")
    with pytest.raises(ValueError, match="imbriqués"):
        add_pattern(patterned, (patterned.entities[-1].id,), "circular_pattern")
    with pytest.raises(ValueError, match="Source du motif absente"):
        add_pattern(original, (str(uuid4()),), "linear_pattern")
    second = add_pattern(patterned, (entities[1].id,), "circular_pattern")
    baked = detach(second, second.patterns[0].id)
    assert {e.id: e for e in baked.entities} == {e.id: e for e in second.entities}
    assert len(baked.patterns) == 1
    assert set(e.id for e in remove(second, second.patterns[0].id).entities) == {e.id for e in original.entities} | set(second.patterns[1].generated_ids)


def test_sketch_patterns_codec_recipe_only_and_remap():
    from cadastrophe.core.recipe_references import recipe_references, remap_recipe
    from plugins.sketch.reshape import copies
    entities = source_entities()
    original = replace(sketch(), entities=entities)
    patterned = add_pattern(original, tuple(e.id for e in entities), "circular_pattern", count=4)
    obj = SketchObject("Esquisse", None, (.1, .2, .3), id=patterned.id, kind=ObjectType.FEATURE, sketch=patterned)
    data = encode(obj)
    assert len(data["entities"]) == len(entities) and len(data["patterns"]) == 1
    assert not set(patterned._generated_ids).intersection(e["id"] for e in data["entities"])
    assert decode(obj, data).sketch == patterned
    plane_id = original.support.plane_id
    assert recipe_references(data) == (plane_id,)
    remapped = remap_recipe(data, {plane_id: str(uuid4())})
    assert decode(obj, remapped).sketch.entities == patterned.entities
    # Missing pattern recipes are refused rather than inferred from copies.
    copied = copies(original, (entities[0].id,), "linear_pattern")
    old = encode(replace(obj, sketch=copied))
    old.pop("patterns")
    with pytest.raises(ValueError):
        decode(obj, old)


@pytest.mark.parametrize("mode", ("linear_pattern", "circular_pattern"))
def test_sketch_patterns_text_counters_and_groups_remain_separate_after_regeneration(mode):
    from plugins.sketch.text_contours import text_entities
    from plugins.sketch.native_regions import report, faces
    app = QApplication.instance() or QApplication([])
    entities = text_entities("B8", "Arial", 10.)
    original = replace(sketch(), entities=entities)
    patterned = add_pattern(original, tuple(e.id for e in entities), mode, count=3, du=30., center=Vec2(-40, 0))
    groups = {e.id: e.text_group for e in patterned.entities}
    assert len(set(groups.values())) == 3
    native = faces(patterned, report(patterned))
    assert len(native.regions) == 6 and sum(len(r.hole_ids) for r in native.regions) == 12
    small = replace(patterned, patterns=(replace(patterned.patterns[0], count=2),))
    restored = replace(small, patterns=patterned.patterns)
    assert {e.id: e.text_group for e in restored.entities} == groups
    native = faces(restored, report(restored))
    assert len(native.regions) == 6 and sum(len(r.hole_ids) for r in native.regions) == 12
    assert app is not None


def example_with_pattern(mode="linear_pattern"):
    app = QApplication.instance() or QApplication([])
    example = ParametricExample("Motifs associatifs")
    plate = example.sketch("plate", "Plaque", example.plane("XY"), polygon(((-80, -80), (120, -80), (120, 80), (-80, 80))))
    example.append("base", "Plaque", Operation.NEW_BODY, ExtrusionParameters(depth=5.), plate)
    circle = Circle(Vec2(20, 20), 3.)
    obj = example.sketch("motif", "Perçages", example.plane("XY"), (circle,))
    patterned = add_pattern(obj.sketch, (circle.id,), mode, count=4, du=20.)
    commands.apply_edit(example.document, obj.id, patterned, title="Motif")
    obj = example.document.objects[obj.id]
    example.append("cut", "Découpe répétée", Operation.CUT, ExtrusionParameters(depth=5.), obj)
    return example, obj.id, circle.id, patterned.patterns[0], app


@pytest.mark.parametrize("mode", ("linear_pattern", "circular_pattern"))
def test_sketch_patterns_native_rebuild_equations_undo_persistence_and_detach(tmp_path, mode):
    example, key, source, pattern, app = example_with_pattern(mode)
    doc = example.document
    import os
    from pathlib import Path
    example_path = os.environ.get("CADASTROPHE_SKETCH_PATTERN_EXAMPLE") if mode == "linear_pattern" else None
    if example_path:
        from examples.parametric_example import independent_copy
        target = Path(example_path)
        target.mkdir(parents=True, exist_ok=True)
        baseline = independent_copy(example)
        save_document(baseline.document, target/"01-avant.cada", registry=registry(example.service))
    doc.equation_evaluator = example.service.evaluator
    base_volume = 200*160*5
    assert mass(example.body.shape) == pytest.approx(base_volume-4*pi*9*5)
    doc.set_variables((Variable("rayon", Expression("value", "=4")), Variable("nombre", Expression("value", "=4"), unit="number")))
    doc.set_equation(key, source+".radius", "=rayon")
    doc.set_equation(key, pattern.id+".count", "=nombre")
    if mode == "linear_pattern":
        doc.set_equation(key, pattern.id+".du", "=rayon*5")
    else:
        doc.set_equation(key, pattern.id+".angle", "=180")
    example.rebuild()
    ids = tuple(e.id for e in doc.objects[key].sketch.entities)
    count = doc.history.count
    for radius in (5., 3., 4.):
        doc.set_variables(tuple(replace(v, expression=Expression("value", f"={radius}")) if v.name == "rayon" else v for v in doc.variables))
        example.rebuild()
        assert BRepCheck_Analyzer(example.body.shape, True).IsValid()
        assert mass(example.body.shape) == pytest.approx(base_volume-4*pi*radius**2*5)
        assert tuple(e.id for e in doc.objects[key].sketch.entities) == ids
    assert doc.history.count == count+3
    if mode == "circular_pattern":
        doc.set_equation(key, pattern.id+".angle", "=360")
        example.rebuild()
        assert tuple(e.id for e in doc.objects[key].sketch.entities) == ids
        assert mass(example.body.shape) == pytest.approx(base_volume-4*pi*16*5)
    snapshot = doc.objects[key]
    variables = tuple(replace(v, expression=Expression("value", "=3")) if v.name == "nombre" else v for v in doc.variables)
    doc.set_variables(variables)
    example.rebuild()
    assert mass(example.body.shape) == pytest.approx(base_volume-3*pi*16*5)
    doc.history.undo()
    example.rebuild()
    assert doc.objects[key].sketch == snapshot.sketch
    doc.history.redo()
    example.rebuild()
    types = registry(example.service)
    path = tmp_path / "motifs.cada"
    save_document(doc, path, registry=types)
    if example_path:
        save_document(doc, target/"02-apres.cada", registry=types)
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        records = manifest["objects"]
        record = next(r for r in records if r["id"] == key)
        assert record["extension"]["version"] == CODEC.version
        assert len(record["extension"]["data"]["entities"]) == 1
    loaded = load_document(path, registry=types)
    assert loaded.objects[key].sketch == doc.objects[key].sketch
    assert loaded.objects[key].expressions == doc.objects[key].expressions
    assert mass(loaded.objects[example.body.id].shape) == pytest.approx(mass(example.body.shape))
    loaded.equation_evaluator = example.service.evaluator
    before = dict(loaded.objects)
    for text in ("=1/0", "=absente", "=2.5", "=201"):
        with pytest.raises(ValueError):
            loaded.set_equation(key, pattern.id+".count", text)
        assert dict(loaded.objects) == before
    baked = detach(loaded.objects[key].sketch, pattern.id)
    commands.apply_edit(loaded, key, baked, title="Dissocier le motif")
    assert not loaded.objects[key].sketch.patterns
    assert not any(e.target.startswith(pattern.id) for e in loaded.objects[key].expressions)
    assert loaded.objects[key].sketch.entities == baked.entities
    loaded.history.undo()
    assert loaded.objects[key].sketch.patterns
    loaded.history.redo()
    assert not loaded.objects[key].sketch.patterns
    assert app is not None


def test_sketch_patterns_selected_occurrence_named_support_failure_recovery_and_graph_transport(tmp_path):
    from cadastrophe.core.body_history import evaluate_body
    from cadastrophe.core.clipboard_graph import encode_graph
    from cadastrophe.core.clipboard import decode_objects
    from cadastrophe.core.reference_geometry import resolve_plane
    app = QApplication.instance() or QApplication([])
    example = ParametricExample("Occurrence sélectionnée")
    circle = Circle(Vec2(10, 10), 3.)
    obj = example.sketch("sketch", "Cercles", example.plane("XY"), (circle,))
    patterned = add_pattern(obj.sketch, (circle.id,), "linear_pattern", count=4, du=20.)
    commands.apply_edit(example.document, obj.id, patterned, title="Motif")
    pattern = patterned.patterns[0]
    selected = pattern.occurrence_id(circle.id, 3)
    feature = ExtrusionFeature("Occurrence 4", None, example.body.color, kind=ObjectType.FEATURE,
        body_id=example.body.id, operation=Operation.NEW_BODY, parameters=ExtrusionParameters(depth=10., contours=(selected,)),
        source=SketchInput(obj.id))
    doc = example.document
    doc.update_construction((replace(example.body, features=(feature.id,)), feature))
    example.rebuild()
    datum = example.face_datum("top", "Face de l’occurrence 4", feature, 10.)
    top = datum.parameters.references[0]
    def face_center(document):
        from cadastrophe.core.reference_geometry import resolve_topology
        from OCC.Core.BRepGProp import brepgprop
        from OCC.Core.GProp import GProp_GProps
        face = resolve_topology(document, BodyStateRef(top.object_id, top.producer_id), top.topology)
        props = GProp_GProps()
        brepgprop.SurfaceProperties(face, props)
        return props.CentreOfMass().X()
    before = resolve_plane(doc, datum)
    assert before.origin.z == pytest.approx(10.) and face_center(doc) == pytest.approx(70.)
    assert mass(example.body.shape) == pytest.approx(pi*9*10)
    fresh = replace(pattern, du=25.)
    commands.apply_edit(doc, obj.id, replace(patterned, patterns=(fresh,)), title="Modifier le pas")
    example.rebuild()
    assert face_center(doc) == pytest.approx(85.)
    assert doc.objects[datum.id].parameters.references[0] == top
    assert BRepCheck_Analyzer(example.body.shape, True).IsValid()
    # A referenced occurrence is never silently rebound to a surviving one.
    reduced = replace(doc.objects[obj.id].sketch, patterns=(replace(fresh, count=2),))
    commands.apply_edit(doc, obj.id, reduced, title="Réduire le motif")
    state = evaluate_body(doc, example.body.id, example.service.evaluator(doc), reuse=True)
    assert state.status is ResultStatus.ERROR and state.feature_id == feature.id
    assert state.shape is None and "absent" in state.message.lower()
    doc.history.undo()
    example.rebuild()
    assert face_center(doc) == pytest.approx(85.)
    baked = detach(doc.objects[obj.id].sketch, pattern.id)
    commands.apply_edit(doc, obj.id, baked, title="Dissocier")
    example.rebuild()
    assert doc.objects[feature.id].parameters.contours == (selected,)
    assert face_center(doc) == pytest.approx(85.)
    doc.history.undo()
    example.rebuild()
    types = registry(example.service)
    path = tmp_path / "occurrence-support.cada"
    save_document(doc, path, registry=types)
    loaded = load_document(path, registry=types)
    assert face_center(loaded) == pytest.approx(85.)
    payload = encode_graph(doc, (example.body.id,), types)
    pasted = decode_objects(payload, registry=types)
    new_sketch = next(o for o in pasted if isinstance(o, SketchObject))
    new_feature = next(o for o in pasted if isinstance(o, ExtrusionFeature))
    assert new_sketch.id != obj.id and new_feature.source.sketch_id == new_sketch.id
    assert new_sketch.sketch.patterns == doc.objects[obj.id].sketch.patterns
    assert new_feature.parameters.contours == (selected,)
    assert mass(next(o for o in pasted if isinstance(o, BodyObject)).shape) == pytest.approx(pi*9*10)
    # Equations preview the same failure and refuse the entire edit atomically.
    doc.equation_evaluator = example.service.evaluator
    doc.set_equation(obj.id, pattern.id+".count", "=4")
    snapshot, count = dict(doc.objects), doc.history.count
    with pytest.raises(ValueError, match="absent"):
        doc.set_equation(obj.id, pattern.id+".count", "=2")
    assert dict(doc.objects) == snapshot and doc.history.count == count
    assert app is not None


def test_sketch_patterns_generated_points_drive_datum_plane_and_survive_detach_reload(tmp_path):
    from cadastrophe.core.operation_model import GeometryReference, PlaneParameters
    from cadastrophe.core.reference_geometry import resolve_plane
    app = QApplication.instance() or QApplication([])
    example = ParametricExample("Points répétés")
    point = Point(Vec2(10, 0))
    obj = example.sketch("points", "Points", example.plane("XY"), (point,))
    patterned = add_pattern(obj.sketch, (point.id,), "circular_pattern", count=3)
    commands.apply_edit(example.document, obj.id, patterned, title="Motif de points")
    pattern = patterned.patterns[0]
    ids = (point.id, *pattern.generated_ids)
    refs = tuple(GeometryReference("sketch_point", obj.id, entity_id=key, part="position") for key in ids)
    datum = example.datum("points_plane", "Plan des points", PlaneParameters("three_points", refs))
    assert resolve_plane(example.document, datum).normal.z == pytest.approx(1.)
    changed = patterned.with_replaced(replace(point, position=Vec2(15, 0)))
    commands.apply_edit(example.document, obj.id, changed, title="Déplacer la source")
    frame = resolve_plane(example.document, example.document.objects[datum.id])
    assert frame.origin.x == pytest.approx(15.) and frame.normal.z == pytest.approx(1.)
    baked = detach(example.document.objects[obj.id].sketch, pattern.id)
    commands.apply_edit(example.document, obj.id, baked, title="Dissocier les points")
    assert resolve_plane(example.document, example.document.objects[datum.id]) == frame
    path = tmp_path / "points.cada"
    types = registry(example.service)
    save_document(example.document, path, registry=types)
    loaded = load_document(path, registry=types)
    assert loaded.objects[datum.id].parameters.references == refs
    assert resolve_plane(loaded, loaded.objects[datum.id]) == frame
    loaded.history.clear()
    assert app is not None
