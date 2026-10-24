"""General arrangements produce exact, independently selectable planar cells."""
from dataclasses import replace
from math import acos, pi, sqrt

import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer

from cadastrophe.core.extrusion import extrude_face
from cadastrophe.geometry import REFERENCE_PLANES
from plugins.sketch.model import Arc, Circle, Segment, Vec2
from plugins.sketch.profile_regions import analyze_regions
from plugins.sketch.profile_faces import to_faces
from plugins.sketch.profiles import analyze
from test_sketch_profiles import source, rectangle, polygon


def network(kind):
    circle = Circle(Vec2(0, 0), 10)
    diameter = Segment(Vec2(-15, 0), Vec2(15, 0))
    if kind == "polygons":
        return (*polygon(((0, 0), (20, 0), (10, 10))),
                *polygon(((0, 0), (10, -10), (20, 0)))), [100, 100]
    if kind == "overlap_rectangles":
        return (*rectangle(width=20, height=10), *rectangle((10, 5), width=20, height=10)), [50, 150, 150]
    if kind == "cross":
        return (*rectangle((-10, -10), width=20, height=20), diameter,
                Segment(Vec2(0, -15), Vec2(0, 15))), [100]*4
    if kind == "circle_diameter":
        return (circle, diameter), [50*pi]*2
    if kind == "circle_cross":
        return (circle, diameter, Segment(Vec2(0, -15), Vec2(0, 15))), [25*pi]*4
    if kind == "overlap_circles":
        lens = 200*acos(.5)-50*sqrt(3)
        return (circle, Circle(Vec2(10, 0), 10)), [lens, 100*pi-lens, 100*pi-lens]
    if kind == "circle_rectangle":
        return (circle, *rectangle((0, -15), width=15, height=30)), [50*pi, 50*pi, 450-50*pi]
    if kind in ("shared_arc", "duplicate_arc"):
        arc = Arc(Vec2(0, 0), 10, 0, pi)
        curves = (arc, *rectangle((-10, 0), width=20, height=20))
        if kind == "duplicate_arc":
            curves += (Arc(Vec2(0, 0), 10, pi, -pi),)
        return curves, [50*pi, 400-50*pi]
    if kind == "nested":
        return tuple(Circle(Vec2(0, 0), r) for r in (30, 20, 10)), [100*pi, 300*pi, 500*pi]
    if kind == "tangent_circles":
        return (circle, Circle(Vec2(20, 0), 10)), [100*pi]*2
    if kind == "tangent_nested":
        return (circle, Circle(Vec2(5, 0), 5)), [25*pi, 75*pi]
    if kind == "bridge":
        return (*rectangle(width=10, height=10), *rectangle((20, 0), width=10, height=10),
                Segment(Vec2(10, 0), Vec2(20, 0))), [100, 100]
    if kind == "duplicate_circle":
        return (circle, Circle(Vec2(0, 0), 10)), [100*pi]
    if kind == "inscribed_circle":
        return (circle, *rectangle((-10, -10), width=20, height=20)), [100*pi]+[100-25*pi]*4
    if kind == "nested_divided":
        return (circle, Circle(Vec2(0, 0), 5), diameter), [12.5*pi]*2+[37.5*pi]*2
    raise AssertionError(kind)


KINDS = ("polygons", "overlap_rectangles", "cross", "circle_diameter", "circle_cross",
         "overlap_circles", "circle_rectangle", "shared_arc", "duplicate_arc", "nested",
         "tangent_circles", "tangent_nested", "bridge", "duplicate_circle", "inscribed_circle", "nested_divided")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("plane", REFERENCE_PLANES)
def test_every_bounded_cell_has_exact_area_valid_face_and_selectable_prism(kind, plane):
    curves, expected = network(kind)
    sketch = source(curves)
    sketch = replace(sketch, support=replace(sketch.support, frame=plane))
    strict = analyze(sketch)
    report = analyze_regions(sketch)
    assert report.valid, report.diagnostics
    assert report.sketch is sketch and sketch.entities == curves
    assert analyze(sketch) == strict
    faces = to_faces(sketch, report)
    assert sorted(face.area for face in faces.regions) == pytest.approx(sorted(expected))
    for face in faces.regions:
        assert BRepCheck_Analyzer(face.face, True).IsValid()
        selected = to_faces(sketch, report, contour_ids=(face.id,))
        assert len(selected.regions) == 1 and selected.regions[0].id == face.id
        prism = extrude_face(face.face, 7)
        assert prism.success, prism.message
        assert prism.volume == pytest.approx(face.area*7)


@pytest.mark.parametrize("kind", KINDS)
def test_cell_ids_survive_reorder_curve_reversal_and_uniform_resize(kind):
    curves, _ = network(kind)
    sketch = source(curves)
    before = analyze_regions(sketch)
    ids = {outer.id for outer, _ in before.regions}
    def reverse(e):
        if type(e) is Segment:
            return replace(e, start=e.end, end=e.start)
        if type(e) is Arc:
            return replace(e, start_angle=e.start_angle+e.sweep, sweep=-e.sweep)
        return e
    def resize(e):
        def move(p):
            return Vec2(p.u*2+11, p.v*2-7)
        if type(e) is Segment:
            return replace(e, start=move(e.start), end=move(e.end))
        return replace(e, center=move(e.center), radius=e.radius*2)
    for entities in (tuple(reversed(curves)), tuple(map(reverse, curves)), tuple(map(resize, curves))):
        after = analyze_regions(replace(sketch, entities=entities))
        assert after.valid, after.diagnostics
        assert {outer.id for outer, _ in after.regions} == ids


def test_open_curves_alone_remain_invalid_and_gaps_are_not_closed():
    for curves in ((Segment(Vec2(0, 0), Vec2(20, 10)),), rectangle()[:-1]):
        sketch = source(curves)
        report = analyze_regions(sketch)
        assert not report.valid and report.diagnostics == analyze(sketch).diagnostics


def test_constraint_errors_and_curve_limits_are_not_hidden_by_region_detection():
    from plugins.sketch.profiles import Diagnostic
    sketch = source(network("cross")[0])
    report = analyze(sketch)
    for code in ("constraint_conflict", "unresolved_reference", "limit", "time_limit", "degenerate"):
        blocked = replace(report, diagnostics=(*report.diagnostics, Diagnostic(code, "Blocked")))
        assert analyze_regions(sketch, report=blocked) is blocked
    limited = source(tuple(Circle(Vec2(i*30, 0), 10) for i in range(513)))
    assert analyze_regions(limited).diagnostics[0].code == "limit"


def test_tangent_cell_extraction_does_not_depend_on_source_uuid_order():
    from itertools import permutations
    from uuid import UUID
    curves, expected = network("inscribed_circle")
    for order in permutations(range(len(curves))):
        entities = tuple(replace(e, id=str(UUID(int=index+1))) for e, index in zip(curves, order))
        sketch = source(entities)
        report = analyze_regions(sketch)
        assert report.valid, (order, report.diagnostics)
        actual = sorted(outer.signed_area+sum(h.signed_area for h in holes) for outer, holes in report.regions)
        assert actual == pytest.approx(sorted(expected)), order
        try:
            faces = to_faces(sketch, report)
        except ValueError as exc:
            pytest.fail(f"{order}: {exc}; contours={report.contours}; edges={report.geometry.entities}")
        assert sorted(r.area for r in faces.regions) == pytest.approx(sorted(expected)), order
