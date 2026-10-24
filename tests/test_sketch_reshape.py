"""Exact sketch edits, native profiles, atomic history and portable curves."""
from dataclasses import replace
from math import pi

import pytest
from PySide6.QtWidgets import QApplication
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from cadastrophe.geometry import REFERENCE_PLANES
from plugins.sketch.model import Point, Segment, Circle, Arc, Ellipse, Spline, Vec2
from plugins.sketch.reshape import copies, trimmed, offset
from plugins.sketch.presentation import entity_shape
from plugins.sketch.entity_codec import _encode_entity, _decode_entity
from plugins.sketch.constraints import Constraint, Reference
from test_sketch_model import sketch


@pytest.mark.parametrize("frame", REFERENCE_PLANES)
@pytest.mark.parametrize("mode", ("mirror", "linear_pattern", "circular_pattern"))
def test_reshape_copies_preserve_sources_and_native_curves(frame, mode):
    entities = (Point(Vec2(3, 2)), Segment(Vec2(3, 2), Vec2(8, 2)), Circle(Vec2(8, 5), 1),
                Arc(Vec2(10, 10), 3, .2, -1.2), Ellipse(Vec2(10, -5), 3, 2, .7),
                Spline((Vec2(0, 0), Vec2(2, 5), Vec2(5, 2), Vec2(8, 6))))
    original = replace(sketch(frame), entities=entities)
    result = copies(original, tuple(e.id for e in entities), mode, count=4)
    factor = 2 if mode == "mirror" else 4
    assert len(result.entities) == len(entities)*factor
    assert result.entities[:len(entities)] == original.entities
    assert len({e.id for e in result.entities}) == len(result.entities)
    arc = result.entities[len(entities)+3]
    assert arc.sweep == (-entities[3].sweep if mode == "mirror" else entities[3].sweep)
    for e in result.entities:
        if type(e) is not Point:
            assert BRepCheck_Analyzer(entity_shape(result, e)).IsValid()
    circular = copies(original, (entities[0].id,), "circular_pattern", count=4)
    p = circular.entities[-1].position
    assert (p.u, p.v) == pytest.approx((2, -3))


@pytest.mark.parametrize("frame", REFERENCE_PLANES)
def test_reshape_trim_segment_circle_ellipse_and_spline_without_sampling(frame):
    for entity, click in (
        (Segment(Vec2(-10, 0), Vec2(10, 0)), Vec2(0, 0)),
        (Circle(Vec2(0, 0), 10), Vec2(10, 0)),
        (Ellipse(Vec2(0, 0), 10, 6), Vec2(10, 0)),
        (Spline((Vec2(-10, 0), Vec2(-3, 1), Vec2(3, -1), Vec2(10, 0))), Vec2(0, 0)),
    ):
        cutters = tuple(Segment(Vec2(x, -20), Vec2(x, 20), construction=True) for x in (-4, 4))
        source = replace(sketch(frame), entities=(entity, *cutters))
        result = trimmed(source, entity.id, click)
        assert result.entities[-2:] == cutters
        assert result.entities[0].id == entity.id
        assert result != source and source.entities[0] == entity
        for e in result.entities:
            assert BRepCheck_Analyzer(entity_shape(result, e)).IsValid()
            assert _decode_entity(_encode_entity(e)) == e
        if type(entity) is Segment:
            assert len(result.entities) == 4
            assert result.entities[0].end.u == pytest.approx(-4)
            assert result.entities[1].start.u == pytest.approx(4)


def test_reshape_trim_requires_explicit_relation_removal_and_keeps_unaffected_ids():
    a = Segment(Vec2(-10, 0), Vec2(10, 0))
    b = Segment(Vec2(0, -10), Vec2(0, 10))
    ca = Constraint("horizontal", (Reference(a.id),))
    cb = Constraint("vertical", (Reference(b.id),))
    source = replace(sketch(), entities=(a, b), constraints=(ca, cb))
    with pytest.raises(ValueError, match="Autorisez"):
        trimmed(source, a.id, Vec2(5, 0))
    result = trimmed(source, a.id, Vec2(5, 0), remove_relations=True)
    assert result.constraints == (cb,)
    assert result.entity(b.id) == b and source.constraints == (ca, cb)
    isolated = replace(sketch(), entities=(Circle(Vec2(0, 0), 5),))
    assert not trimmed(isolated, isolated.entities[0].id, Vec2(5, 0)).entities


@pytest.mark.parametrize("frame", REFERENCE_PLANES)
@pytest.mark.parametrize("kind", ("circle", "rectangle", "line"))
def test_reshape_offset_exact_valid_curves(frame, kind):
    if kind == "circle":
        entities = (Circle(Vec2(0, 0), 10),)
    elif kind == "line":
        entities = (Segment(Vec2(-10, 0), Vec2(10, 0)),)
    else:
        points = tuple(Vec2(*p) for p in ((-10, -10), (10, -10), (10, 10), (-10, 10)))
        entities = tuple(Segment(p, points[(i+1)%4]) for i, p in enumerate(points))
    source = replace(sketch(frame), entities=entities)
    result = offset(source, tuple(e.id for e in entities), 2.)
    assert result.entities[:len(entities)] == entities
    assert len(result.entities) > len(entities)
    for e in result.entities:
        assert BRepCheck_Analyzer(entity_shape(result, e)).IsValid()
    if kind == "circle":
        assert result.entities[-1].radius == pytest.approx(12.)
        assert offset(source, (entities[0].id,), -2.).entities[-1].radius == pytest.approx(8.)
    with pytest.raises(ValueError):
        offset(source, tuple(e.id for e in entities), 0.)


@pytest.mark.parametrize("text", ("B8", "CAD", "Été"))
def test_reshape_text_has_exact_closed_profiles_with_holes_and_extrudes(text):
    app = QApplication.instance() or QApplication([])
    from plugins.sketch.text_contours import text_entities
    from plugins.sketch.native_regions import report as native_report, faces
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCC.Core.gp import gp_Vec
    entities = text_entities(text, "Arial", 10.)
    s = replace(sketch(), entities=entities)
    report = native_report(s)
    assert report.valid, report.diagnostics
    native = faces(s, report, ())
    assert native.regions
    if text == "B8":
        assert len(native.regions) == 2
        assert sum(len(r.hole_ids) for r in native.regions) == 4
    for region in native.regions:
        face = region.face
        solid = BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, 3)).Shape()
        assert BRepCheck_Analyzer(solid, True).IsValid()
    assert tuple(_decode_entity(_encode_entity(e)) for e in entities) == entities
    assert app is not None


def test_reshape_explicit_rational_basis_matches_native_samples():
    e = Spline((Vec2(1, 0), Vec2(1, 1), Vec2(0, 1)), degree=2,
               knots=(0., 0., 0., 1., 1., 1.), weights=(1., 2**-.5, 1.))
    s = replace(sketch(), entities=(e,))
    native = BRepAdaptor_Curve(entity_shape(s, e))
    for t in (0., .2, .5, .9, 1.):
        actual, expected = e.point_at(t), native.Value(t)
        assert (actual.u, actual.v) == pytest.approx((expected.X(), expected.Y()))
    assert _decode_entity(_encode_entity(e)) == e


def test_native_builder_accepts_implicit_spline_without_optional_weights(monkeypatch):
    from plugins.sketch import curve_native
    current = Spline((Vec2(0, 0), Vec2(2, 5), Vec2(7, -3), Vec2(10, 2)))
    class LegacySpline:
        basis = current.basis
    legacy = LegacySpline()
    monkeypatch.setattr(curve_native, "Spline", LegacySpline)
    s = sketch()
    edge = curve_native.edge(s, legacy)
    assert BRepCheck_Analyzer(edge).IsValid()
    native = BRepAdaptor_Curve(edge)
    for t in (0., .25, .5, 1.):
        expected, actual = current.point_at(t), native.Value(t)
        assert (actual.X(), actual.Y()) == pytest.approx((expected.u, expected.v))


def test_reshape_text_real_body_rebuild_edit_undo_and_save(tmp_path):
    app = QApplication.instance() or QApplication([])
    from examples.parametric_example import ParametricExample, registry, mass, polygon
    from plugins.sketch.text_contours import text_entities
    from plugins.sketch import commands
    from cadastrophe.core.feature_model import ExtrusionParameters, Operation
    from cadastrophe.core.persistence import save_document, load_document
    example = ParametricExample("Texte ajouré")
    plate = example.sketch("plate", "Plaque", example.plane("XY"), polygon(((-10, -10), (90, -10), (90, 50), (-10, 50))))
    example.append("plate_body", "Plaque", Operation.NEW_BODY, ExtrusionParameters(depth=5.), plate)
    base_volume = mass(example.body.shape)
    obj = example.sketch("text", "B8", example.plane("XY"), text_entities("B8", "Arial", 10.))
    example.append("extrusion", "Gravure B8", Operation.CUT, ExtrusionParameters(depth=3.), obj)
    before = mass(example.body.shape)
    removed = base_volume-before
    assert removed > 0
    after = replace(obj.sketch, entities=tuple(
        replace(e, start=Vec2(2*e.start.u, 2*e.start.v), end=Vec2(2*e.end.u, 2*e.end.v)) if type(e) is Segment else
        replace(e, points=tuple(Vec2(2*p.u, 2*p.v) for p in e.points)) for e in obj.sketch.entities))
    commands.apply_edit(example.document, obj.id, after, title="Agrandir les contours")
    example.rebuild()
    assert mass(example.body.shape) == pytest.approx(base_volume-4*removed, rel=1e-7)
    example.document.history.undo()
    example.rebuild()
    assert mass(example.body.shape) == pytest.approx(before, rel=1e-7)
    example.document.history.redo()
    example.rebuild()
    path = tmp_path/"text-body.cada"
    types = registry(example.service)
    save_document(example.document, path, registry=types)
    loaded = load_document(path, registry=types)
    assert loaded.objects[obj.id].sketch == after
    assert mass(loaded.objects[example.body.id].shape) == pytest.approx(base_volume-4*removed, rel=1e-7)
    assert app is not None


def test_reshape_partial_reverse_circle_and_open_offset_sides():
    point = Point(Vec2(15, 10))
    original = replace(sketch(), entities=(point,))
    result = copies(original, (point.id,), "circular_pattern", count=3, center=Vec2(5, 10), angle=-pi)
    assert (result.entities[-1].position.u, result.entities[-1].position.v) == pytest.approx((-5, 10))
    assert (result.entities[1].position.u, result.entities[1].position.v) == pytest.approx((5, 0))
    line = Segment(Vec2(-10, 0), Vec2(10, 0))
    original = replace(sketch(), entities=(line,))
    a = offset(original, (line.id,), 2.).entities[-1]
    b = offset(original, (line.id,), -2.).entities[-1]
    assert a.start.v == pytest.approx(-b.start.v)
    assert abs(a.start.v) == pytest.approx(2.)


def test_reshape_rejects_disconnected_offsets_and_corrupt_bases():
    entities = (Circle(Vec2(-10, 0), 3), Circle(Vec2(10, 0), 3))
    original = replace(sketch(), entities=entities)
    with pytest.raises(ValueError, match="continu"):
        offset(original, tuple(e.id for e in entities), 2.)
    for weights, knots in (((1., -1., 1.), (0.,)*3+(1.,)*3), ((1.,), (0.,)*3+(1.,)*3),
                           ((), (0., 0., 1., 0., 1., 1.))):
        with pytest.raises(ValueError):
            Spline((Vec2(1, 0), Vec2(1, 1), Vec2(0, 1)), degree=2, weights=weights, knots=knots)


@pytest.mark.parametrize("mode", ("linear_pattern", "circular_pattern", "mirror"))
def test_reshape_text_copies_and_clipboard_keep_counters_independent(mode):
    app = QApplication.instance() or QApplication([])
    from plugins.sketch.text_contours import text_entities
    from plugins.sketch.native_regions import report, faces
    from plugins.sketch.clipboard import encode_entities, decode_entities
    from plugins.sketch.model import new_id
    entities = text_entities("B8", "Arial", 10.)
    s = replace(sketch(), entities=entities)
    result = copies(s, tuple(e.id for e in entities), mode, count=3, du=30., center=Vec2(-40, 0),
                    axis_start=Vec2(-20, 0), axis_end=Vec2(-20, 10))
    regions = faces(result, report(result)).regions
    occurrences = 2 if mode == "mirror" else 3
    assert len(regions) == 2*occurrences and sum(len(r.hole_ids) for r in regions) == 4*occurrences
    assert len({e.text_group for e in result.entities}) == occurrences
    payload = encode_entities(new_id(), s.id, entities)
    pasted = decode_entities(payload, Vec2(30, 0))
    assert pasted[0].text_group != entities[0].text_group
    result = replace(s, entities=(*entities, *pasted))
    assert len(faces(result, report(result)).regions) == 4
    assert app is not None
