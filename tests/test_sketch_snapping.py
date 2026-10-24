"""Pure numeric snapping, finite intersections, priorities and hysteresis."""
from dataclasses import replace
from math import pi
from time import perf_counter

import pytest

from plugins.sketch.model import Arc, Circle, Point, Segment, Vec2
from plugins.sketch.snapping import SnapEngine, SnapSettings, intersections


def test_points_endpoints_midpoints_centers_origin_and_arc_midpoint():
    engine = SnapEngine()
    engine.update((Point(Vec2(20, 30)), Segment(Vec2(40, 30), Vec2(60, 30)),
                   Circle(Vec2(100, 30), 10), Arc(Vec2(140, 30), 10, 0, pi)))
    for point, kind in ((Vec2(20, 30), "endpoint"), (Vec2(40, 30), "endpoint"),
                        (Vec2(50, 30), "midpoint"), (Vec2(100, 30), "center"),
                        (Vec2(150, 30), "endpoint"), (Vec2(140, 40), "midpoint"), (Vec2(0, 0), "origin")):
        engine.reset()
        actual, snap = engine.resolve(Vec2(point.u+.1, point.v+.1), lambda p: (p.u*10, p.v*10))
        assert actual == point and snap.kind == kind


@pytest.mark.parametrize("scale", [.01, .5, 1., 10., 100.])
def test_threshold_is_in_logical_pixels_at_every_zoom(scale):
    engine = SnapEngine()
    target = Vec2(10000, 10000)
    engine.update((Point(target),))
    project = lambda p: (p.u*scale, p.v*scale)
    assert engine.resolve(Vec2(target.u+7/scale, target.v), project)[0] == target
    engine.reset()
    assert engine.resolve(Vec2(target.u+9/scale, target.v), project)[1] is None


def test_hysteresis_stays_on_same_target_until_release_radius_then_reacquires():
    first, second = Point(Vec2(100, 100)), Point(Vec2(110, 100))
    engine = SnapEngine()
    engine.update((first, second))
    project = lambda p: (p.u, p.v)
    assert engine.resolve(Vec2(101, 100), project)[0] == first.position
    for x in (104, 106, 105, 108, 110, 109):
        assert engine.resolve(Vec2(x, 100), project)[0] == first.position
    assert engine.resolve(Vec2(113, 100), project)[0] == second.position


def test_priorities_are_explicit_and_do_not_depend_on_entity_order():
    point = Point(Vec2(99, 100))
    line = Segment(Vec2(90, 100), Vec2(110, 100))
    for entities in ((point, line), (line, point)):
        engine = SnapEngine()
        engine.update(entities)
        assert engine.resolve(Vec2(100, 100), lambda p: (p.u, p.v))[1].kind == "endpoint"


def test_axis_inference_grid_disable_exclusion_and_revision_reset():
    engine = SnapEngine()
    entity = Point(Vec2(99, 100))
    engine.update((entity,))
    project = lambda p: (p.u*10, p.v*10)
    point, snap = engine.resolve(Vec2(120, 100.5), project, anchor=Vec2(99, 100))
    assert point == Vec2(120, 100) and snap.kind == "horizontal"
    engine.reset()
    point, snap = engine.resolve(Vec2(99.5, 120), project, anchor=Vec2(99, 100))
    assert point == Vec2(99, 120) and snap.kind == "vertical"
    off = SnapSettings(False, False, False)
    assert engine.resolve(Vec2(99.1, 100), project, off)[1] is None
    grid = replace(off, grid=True, grid_step=10)
    assert engine.resolve(Vec2(120.3, -130.3), project, grid)[0] == Vec2(120, -130)
    assert engine.resolve(entity.position, project, exclude=(entity.id,))[1] is None
    engine.update((replace(entity, position=Vec2(500, 500)),))
    assert engine.lock is None and engine.resolve(Vec2(99, 100), project)[1] is None


@pytest.mark.parametrize("first,second,points", [
    (Segment(Vec2(-10, -10), Vec2(10, 10)), Segment(Vec2(-10, 10), Vec2(10, -10)), [(0, 0)]),
    (Segment(Vec2(20, 20), Vec2(30, 30)), Segment(Vec2(-10, 10), Vec2(10, -10)), []),
    (Segment(Vec2(0, 0), Vec2(5, 0)), Segment(Vec2(2, 0), Vec2(10, 0)), []),
    (Segment(Vec2(-20, 0), Vec2(20, 0)), Circle(Vec2(0, 0), 10), [(-10, 0), (10, 0)]),
    (Segment(Vec2(-20, 10), Vec2(20, 10)), Circle(Vec2(0, 0), 10), [(0, 10)]),
    (Segment(Vec2(-20, 0), Vec2(20, 0)), Arc(Vec2(0, 0), 10, 0, pi/2), [(10, 0)]),
    (Circle(Vec2(0, 0), 5), Circle(Vec2(6, 0), 5), [(3, -4), (3, 4)]),
    (Circle(Vec2(0, 0), 5), Circle(Vec2(10, 0), 5), [(5, 0)]),
    (Circle(Vec2(0, 0), 5), Circle(Vec2(0, 0), 5), []),
    (Circle(Vec2(0, 0), 5), Circle(Vec2(20, 0), 5), []),
    (Circle(Vec2(0, 0), 5), Circle(Vec2(1, 0), 1), []),
    (Arc(Vec2(0, 0), 5, 0, -pi/2), Circle(Vec2(6, 0), 5), [(3, -4)]),
])
def test_finite_intersections_including_tangency_clockwise_arcs_and_ambiguities(first, second, points):
    for a, b in ((first, second), (second, first)):
        actual = sorted((point.u, point.v) for point in intersections(a, b))
        assert len(actual) == len(points)
        for got, expected in zip(actual, sorted(points)):
            assert got == pytest.approx(expected)


def test_intersection_target_and_arc_direction_only_accepts_points_on_fixed_radius():
    engine = SnapEngine()
    engine.update((Segment(Vec2(10, 10), Vec2(30, 30)), Segment(Vec2(10, 30), Vec2(30, 10))))
    assert engine.resolve(Vec2(20.1, 20), lambda p: (p.u*10, p.v*10))[1].kind == "intersection"
    engine.update((Point(Vec2(10, 0)), Point(Vec2(20, 0))))
    assert engine.resolve(Vec2(10.1, 0), lambda p: (p.u*10, p.v*10), circle=(Vec2(0, 0), 10))[0] == Vec2(10, 0)
    assert engine.resolve(Vec2(20, 0), lambda p: (p.u*10, p.v*10), circle=(Vec2(0, 0), 10))[1] is None


def test_query_restricts_projection_and_intersections_to_nearby_geometry():
    engine = SnapEngine()
    engine.update(tuple(Segment(Vec2(i*100, 100), Vec2(i*100+20, 110)) for i in range(1000)))
    calls = []
    def project(point):
        calls.append(point)
        return point.u*10, point.v*10
    start = perf_counter()
    for _ in range(100):
        engine.resolve(Vec2(20.1, 110), project)
    elapsed = perf_counter()-start
    assert len(calls) < 1500 and len(engine.cache) < 10
    print(f"E5 SNAP 1000 segments, 100 queries: {elapsed:.4f}s, projections={len(calls)}")


@pytest.mark.parametrize("scale", [1e-5, 1., 1e5])
def test_intersections_remain_correct_after_uniform_scaling(scale):
    segment = Segment(Vec2(-3*scale, -3*scale), Vec2(3*scale, 3*scale))
    other = Segment(Vec2(-3*scale, 3*scale), Vec2(3*scale, -3*scale))
    assert intersections(segment, other) == (Vec2(0, 0),)
    circle = Circle(Vec2(0, 0), scale)
    outside = Segment(Vec2(-3*scale, 1.01*scale), Vec2(3*scale, 1.01*scale))
    assert not intersections(outside, circle)


def test_snap_search_handles_almost_collinear_projected_axes_in_oblique_views():
    engine = SnapEngine()
    point = Point(Vec2(100, -99.9))
    engine.update((point,))
    # Long world-space displacement, only 1 pixel from the cursor in projection.
    project = lambda p: (p.u+p.v, p.v*.01)
    assert engine.resolve(Vec2(0, 0), project)[0] == point.position
