"""Shared segment cells: stable recipes and exact native faces, no UI needed."""
from dataclasses import replace

import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer

from cadastrophe.geometry import REFERENCE_PLANES
from cadastrophe.core.extrusion import extrude_face
from plugins.sketch.model import Segment, Vec2
from plugins.sketch.profile_regions import analyze_regions
from plugins.sketch.profiles import analyze
from plugins.sketch.profile_faces import to_faces
from test_sketch_profiles import rectangle, source


def shared(kind="double"):
    if kind == "divider":
        return (*rectangle(width=40, height=20), Segment(Vec2(20, 0), Vec2(20, 20)))
    if kind == "partial":
        return (*rectangle(width=20, height=20), *rectangle((20, 5), width=20, height=10))
    edges = (*rectangle(width=20, height=10), *rectangle((20, 0), width=20, height=10))
    return edges if kind == "double" else tuple(e for i, e in enumerate(edges) if i != 7)


@pytest.mark.parametrize("kind,areas", [("double", [200, 200]), ("single", [200, 200]),
                                        ("divider", [400, 400]), ("partial", [200, 400])])
@pytest.mark.parametrize("plane", REFERENCE_PLANES)
def test_shared_edges_make_two_native_regions_without_changing_sketch(kind, areas, plane):
    sketch = source(shared(kind))
    sketch = replace(sketch, support=replace(sketch.support, frame=plane))
    original = sketch.entities
    assert not analyze(sketch).valid  # Strict E7 contour diagnostics are preserved.
    report = analyze_regions(sketch)
    assert report.valid, report.diagnostics
    assert len(report.regions) == 2
    assert report.sketch is sketch and report.profile_sketch is not sketch
    assert sketch.entities is original
    faces = to_faces(sketch, report)
    assert sorted(face.area for face in faces.regions) == pytest.approx(areas)
    for region in faces.regions:
        assert BRepCheck_Analyzer(region.face, True).IsValid()
        result = extrude_face(region.face, 7)
        assert result.success, result.message
        assert result.volume == pytest.approx(region.area*7)
        one = to_faces(sketch, report, contour_ids=(region.id,))
        assert len(one.regions) == 1 and one.regions[0].id == region.id


@pytest.mark.parametrize("kind", ["double", "single", "divider", "partial"])
def test_region_identity_survives_entity_order_and_resize(kind):
    sketch = source(shared(kind))
    before = analyze_regions(sketch)
    assert before.valid
    reordered = analyze_regions(replace(sketch, entities=tuple(reversed(sketch.entities))))
    assert reordered.contours == before.contours
    reversed_edges = replace(sketch, entities=tuple(replace(e, start=e.end, end=e.start) for e in sketch.entities))
    assert {c.id for c in analyze_regions(reversed_edges).contours} == {c.id for c in before.contours}
    def moved(point):
        return Vec2(point.u*2, point.v)
    resized = replace(sketch, entities=tuple(replace(edge, start=moved(edge.start), end=moved(edge.end))
                                             for edge in sketch.entities))
    after = analyze_regions(resized)
    assert after.valid and {c.id for c in after.contours} == {c.id for c in before.contours}
    assert sum(c.signed_area for c in after.contours) == pytest.approx(2*sum(c.signed_area for c in before.contours))


@pytest.mark.parametrize("kind,areas", [("duplicate", [200]), ("dangling", [200, 200]), ("crossing", [100, 100])])
def test_duplicate_edges_open_branches_and_crossings_preserve_bounded_cells(kind, areas):
    if kind == "duplicate":
        curves = (*rectangle(width=20, height=10), *rectangle(width=20, height=10))
    elif kind == "dangling":
        curves = (*shared(), Segment(Vec2(0, 0), Vec2(-5, -5)))
    else:
        points = tuple(Vec2(*p) for p in ((0, 0), (20, 20), (0, 20), (20, 0)))
        curves = tuple(Segment(p, points[(i+1)%4]) for i, p in enumerate(points))
    sketch = source(curves)
    report = analyze_regions(sketch)
    assert report.valid, report.diagnostics
    assert sorted(r.area for r in to_faces(sketch, report).regions) == pytest.approx(areas)
