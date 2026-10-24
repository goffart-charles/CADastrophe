"""Coincident-node dragging is a pure, detached edit, not a constraint solver."""
from math import hypot, pi

import pytest

from plugins.sketch.joints import joint_handles, moved_joint
from plugins.sketch.model import Point, Segment, Arc, Circle, Vec2


def move(entities, source, part, target):
    return {entity.id: entity for entity in moved_joint(entities, (source.id, part), target,
            joint_handles(entities, (source.id, part)))}


def test_rectangle_corner_moves_both_ends_and_leaves_other_corners_and_snapshots_unchanged():
    a, b, c, d = Vec2(-20, -20), Vec2(20, -20), Vec2(20, 20), Vec2(-20, 20)
    edges = (Segment(a, b), Segment(b, c), Segment(c, d), Segment(d, a))
    target = Vec2(30, -10)
    result = move(edges, edges[0], "end", target)
    assert set(result) == {edges[0].id, edges[1].id}
    assert result[edges[0].id].end == result[edges[1].id].start == target
    assert result[edges[0].id].start == a and result[edges[1].id].end == c
    assert edges[0].end == edges[1].start == b


def test_branched_node_and_explicit_point_move_together_but_visually_near_points_do_not():
    node, target = Vec2(5, 7), Vec2(12, 17)
    entities = (Point(node), Segment(node, Vec2(20, 7)), Segment(Vec2(-20, 7), node),
                Segment(node, Vec2(5, 30)), Point(Vec2(5.001, 7)))
    result = move(entities, entities[0], "position", target)
    assert len(result) == 4 and entities[-1].id not in result
    assert result[entities[0].id].position == target
    assert result[entities[1].id].start == result[entities[2].id].end == result[entities[3].id].start == target


@pytest.mark.parametrize("part", ["start", "end"])
@pytest.mark.parametrize("sweep", [pi/2, -pi/2, 3*pi/2, -3*pi/2])
def test_attached_arc_adapts_radius_center_and_preserves_opposite_endpoint_and_sweep(part, sweep):
    arc = Arc(Vec2(0, 0), 10, .3, sweep)
    node = arc.point_at(0 if part == "start" else 1)
    opposite = arc.point_at(1 if part == "start" else 0)
    line = Segment(Vec2(40, 30), node)
    point = Vec2(node.u+5, node.v+7)
    changed = move((arc, line), line, "end", point)[arc.id]
    actual = changed.point_at(0 if part == "start" else 1)
    other = changed.point_at(1 if part == "start" else 0)
    assert (actual.u, actual.v) == pytest.approx((point.u, point.v), abs=1e-8)
    assert (other.u, other.v) == pytest.approx((opposite.u, opposite.v), abs=1e-8)
    assert changed.id == arc.id and changed.sweep == arc.sweep


def test_dragged_arc_uses_its_real_fixed_radius_endpoint_for_attached_line():
    arc = Arc(Vec2(0, 0), 10, 0, pi/2)
    line = Segment(arc.point_at(1), Vec2(0, 30))
    result = move((line, arc), arc, "end", Vec2(-20, 20))
    changed, attached = result[arc.id], result[line.id]
    assert changed.radius == arc.radius and changed.center == arc.center
    actual = changed.point_at(1)
    assert attached.start == actual
    assert hypot(actual.u, actual.v) == pytest.approx(10)


def test_degenerate_neighbor_rejects_whole_batch_without_mutating_originals():
    node = Vec2(5, 0)
    first, second = Segment(Vec2(0, 0), node), Segment(node, Vec2(10, 0))
    with pytest.raises(ValueError):
        move((first, second), first, "end", second.end)
    assert first.end == second.start == node


def test_centers_and_radius_handles_are_not_implicit_coincidence_constraints():
    circle, point = Circle(Vec2(0, 0), 10), Point(Vec2(0, 0))
    result = move((circle, point), circle, "center", Vec2(5, 5))
    assert set(result) == {circle.id} and result[circle.id].center == Vec2(5, 5)


def test_nearly_collapsed_source_cannot_silently_split_its_own_joint():
    line = Segment(Vec2(0, 0), Vec2(1e-8, 0))
    with pytest.raises(ValueError, match="ambiguë"):
        move((line,), line, "start", Vec2(10, 10))
