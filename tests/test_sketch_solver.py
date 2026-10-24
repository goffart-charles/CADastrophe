"""E6 domain/solver reference cases, independent of Qt and OCCT."""
from dataclasses import replace
from math import hypot, pi
import subprocess
import sys

import pytest

from plugins.sketch.constraints import Constraint, Reference as Ref
from plugins.sketch.model import Arc, Circle, Point, Segment, Vec2
from plugins.sketch.solver import solve, diagnose
from test_sketch_model import sketch


def rectangle(width=40, height=20, fixed=True):
    a, b, c, d = Vec2(0, 0), Vec2(40, 0), Vec2(40, 20), Vec2(0, 20)
    edges = (Segment(a, b), Segment(b, c), Segment(c, d), Segment(d, a))
    constraints = [Constraint("coincident", (Ref(e.id, "end"), Ref(edges[(i+1)%4].id, "start")))
                   for i, e in enumerate(edges)]
    constraints += [Constraint("horizontal" if i%2 == 0 else "vertical", (Ref(e.id),)) for i, e in enumerate(edges)]
    constraints += [Constraint("length", (Ref(edges[0].id),), width), Constraint("length", (Ref(edges[1].id),), height)]
    if fixed:
        constraints.append(Constraint("fixed", (Ref(edges[0].id, "start"),), a))
    return replace(sketch(), entities=edges, constraints=tuple(constraints))


def test_unrelated_far_entity_does_not_relax_local_dimensions_or_move():
    distant = Point(Vec2(1e15, -1e15))
    line = Segment(Vec2(0, 0), Vec2(1, 0))
    relation = Constraint("length", (Ref(line.id),), 2.)
    original = replace(sketch(), entities=(distant, line), constraints=(relation,))
    result = solve(original)
    assert result.success, result.message
    actual = result.sketch.entities[1]
    assert hypot(actual.end.u-actual.start.u, actual.end.v-actual.start.v) == pytest.approx(2., abs=1e-8)
    assert result.sketch.entities[0] is distant and original.entities[1] is line


def test_unrepresentable_small_dimension_is_refused_instead_of_silently_accepted():
    line = Segment(Vec2(1e15, 0), Vec2(1e15+1, 0))
    constraints = (Constraint("length", (Ref(line.id),), 1e-4),
                   Constraint("fixed", (Ref(line.id, "start"),), line.start))
    original = replace(sketch(), entities=(line,), constraints=constraints)
    result = solve(original)
    assert not result.success and result.sketch is original


def test_linear_contradictions_report_responsible_relations_and_not_unrelated_circle():
    point, circle = Point(Vec2(0, 0)), Circle(Vec2(20, 20), 3)
    first = Constraint("fixed", (Ref(point.id, "position"),), Vec2(0, 0))
    second = Constraint("fixed", (Ref(point.id, "position"),), Vec2(4, 0))
    radius = Constraint("radius", (Ref(circle.id),), 3.)
    original = replace(sketch(), entities=(point, circle), constraints=(first, second, radius))
    result = solve(original)
    assert not result.success and set(result.conflicts) == {first.id, second.id}
    assert result.sketch is original and radius.id not in result.conflicts


def test_solver_import_has_no_gui_or_occt_dependency():
    subprocess.run([sys.executable, "-c", "import sys; import plugins.sketch.solver; "
                    "assert not any(n.startswith(('PySide6', 'OCC')) for n in sys.modules)"], check=True)


@pytest.mark.parametrize("width,height", [(60, 35), (10, 5), (1000, .1)])
def test_rectangle_dimensions_drive_configuration_without_flipping_or_mutating_original(width, height):
    original = rectangle(width, height)
    result = solve(original)
    assert result.success, result.message
    assert result.dof == 0 and result.rank == 16 and result.residual <= 1e-8
    e = result.sketch.entities
    assert (e[0].start.u, e[0].start.v) == pytest.approx((0, 0), abs=1e-7)
    assert (e[0].end.u, e[0].end.v) == pytest.approx((width, 0), abs=1e-7)
    assert (e[1].end.u, e[1].end.v) == pytest.approx((width, height), abs=1e-7)
    for i, edge in enumerate(e):
        neighbor = e[(i+1)%4]
        assert (edge.end.u, edge.end.v) == pytest.approx((neighbor.start.u, neighbor.start.v), abs=1e-7)
    assert original.entities[0].end == Vec2(40, 0)
    assert result.sketch.constraints == original.constraints
    assert tuple(edge.id for edge in e) == tuple(edge.id for edge in original.entities)
    assert solve(result.sketch).sketch == result.sketch


def test_unanchored_rectangle_retains_translation_freedom_and_local_center():
    original = rectangle(60, 30, fixed=False)
    result = solve(original)
    assert result.success and result.dof == 2
    e = result.sketch.entities
    assert sum(p.u for edge in e for p in (edge.start, edge.end))/8 == pytest.approx(20)
    assert sum(p.v for edge in e for p in (edge.start, edge.end))/8 == pytest.approx(10)


@pytest.mark.parametrize("kind,value,expected", [("radius", 12, 12), ("diameter", 30, 15)])
def test_circle_dimension_keeps_center_and_two_translation_dofs(kind, value, expected):
    circle = Circle(Vec2(7, -3), 5)
    original = replace(sketch(), entities=(circle,), constraints=(Constraint(kind, (Ref(circle.id),), value),))
    result = solve(original)
    assert result.success and result.dof == 2
    assert result.sketch.entities[0].radius == pytest.approx(expected)
    assert result.sketch.entities[0].center == circle.center


def test_fixed_circle_center_and_radius_are_probably_fully_constrained():
    circle = Circle(Vec2(7, -3), 5)
    constraints = (Constraint("radius", (Ref(circle.id),), 10),
                   Constraint("fixed", (Ref(circle.id, "center"),), Vec2(0, 0)))
    result = solve(replace(sketch(), entities=(circle,), constraints=constraints))
    assert result.success and result.dof == 0
    assert "Probablement" in result.message


def test_distance_between_fixed_and_free_point_preserves_initial_direction():
    first, second = Point(Vec2(0, 0)), Point(Vec2(3, 4))
    constraints = (Constraint("fixed", (Ref(first.id, "position"),), first.position),
                   Constraint("distance", (Ref(first.id, "position"), Ref(second.id, "position")), 10))
    result = solve(replace(sketch(), entities=(first, second), constraints=constraints))
    assert result.success and result.dof == 1
    assert (result.sketch.entities[1].position.u, result.sketch.entities[1].position.v) == pytest.approx((6, 8), abs=1e-6)


@pytest.mark.parametrize("kind", ["parallel", "perpendicular"])
def test_optional_segment_relations_converge_without_degenerate_edges(kind):
    a = Segment(Vec2(0, 0), Vec2(20, 0))
    b = Segment(Vec2(0, 10), Vec2(10, 25))
    constraints = (Constraint("fixed", (Ref(a.id, "start"),), a.start),
                   Constraint("fixed", (Ref(a.id, "end"),), a.end),
                   Constraint(kind, (Ref(a.id), Ref(b.id))))
    result = solve(replace(sketch(), entities=(a, b), constraints=constraints))
    assert result.success and result.dof == 3
    a, b = result.sketch.entities
    delta = b.end.v-b.start.v if kind == "parallel" else b.end.u-b.start.u
    assert abs(delta) <= 1e-7


def test_arc_radius_and_endpoint_coincidence_preserve_sweep_branch():
    arc = Arc(Vec2(0, 0), 10, .2, -pi/2)
    point = Point(arc.point_at(1))
    constraints = (Constraint("radius", (Ref(arc.id),), 15),
                   Constraint("coincident", (Ref(arc.id, "end"), Ref(point.id, "position"))))
    result = solve(replace(sketch(), entities=(arc, point), constraints=constraints))
    assert result.success
    arc, point = result.sketch.entities
    assert arc.radius == pytest.approx(15) and -2*pi < arc.sweep < 0
    assert hypot(arc.point_at(1).u-point.position.u, arc.point_at(1).v-point.position.v) <= 1e-8


def test_conflicting_lengths_fail_without_returning_partial_solution():
    line = Segment(Vec2(0, 0), Vec2(20, 0))
    original = replace(sketch(), entities=(line,), constraints=(
        Constraint("length", (Ref(line.id),), 10), Constraint("length", (Ref(line.id),), 30)))
    result = solve(original)
    assert not result.success and result.sketch is original
    assert "Échec" in result.message and result.residual > 1


def test_redundant_equations_do_not_claim_to_be_incompatible():
    original = rectangle()
    duplicate = Constraint("horizontal", original.constraints[4].refs)
    original = replace(original, constraints=(*original.constraints, duplicate))
    result = solve(original)
    assert result.success and result.dof == 0 and result.rank == 16


def test_iteration_budget_and_variable_budget_fail_explicitly():
    original = rectangle(70, 40)
    assert not solve(original, max_iterations=0).success
    assert not solve(original, time_budget=0).success
    entities = tuple(Circle(Vec2(i, 0), 5) for i in range(90))
    original = replace(sketch(), entities=entities,
                       constraints=tuple(Constraint("radius", (Ref(e.id),), 10) for e in entities)+tuple(
                           Constraint("coincident", (Ref(a.id, "center"), Ref(b.id, "center")))
                           for a, b in zip(entities, entities[1:])))
    result = solve(original)
    assert not result.success and result.sketch is original and result.termination == "limit"


def test_independent_large_sketch_is_solved_by_islands_with_atomic_publication():
    circles = tuple(Circle(Vec2(i*20, 0), 5) for i in range(150))
    original = replace(sketch(), entities=circles,
                       constraints=tuple(Constraint("radius", (Ref(e.id),), 10) for e in circles))
    result = solve(original)
    assert result.success and result.rank == 150 and result.dof == 300, result.message
    assert all(entity.radius == pytest.approx(10) for entity in result.sketch.entities)
    assert all(entity.radius == 5 for entity in original.entities)
    assert diagnose(result.sketch).rank == result.rank


def test_active_distant_island_cannot_degrade_precision_of_local_island():
    distant = Point(Vec2(1e15, 1e15))
    line = Segment(Vec2(0, 0), Vec2(1, 0))
    original = replace(sketch(), entities=(distant, line), constraints=(
        Constraint("fixed", (Ref(distant.id, "position"),), distant.position),
        Constraint("length", (Ref(line.id),), 2.)))
    result = solve(original)
    assert result.success, result.message
    changed = result.sketch.entities[1]
    assert hypot(changed.end.u-changed.start.u, changed.end.v-changed.start.v) == pytest.approx(2., abs=1e-8)
    assert result.sketch.entities[0] is distant


def test_failed_island_never_publishes_successful_other_islands():
    circles = (Circle(Vec2(0, 0), 5), Circle(Vec2(20, 0), 5))
    original = replace(sketch(), entities=circles, constraints=(
        Constraint("radius", (Ref(circles[0].id),), 10),
        Constraint("radius", (Ref(circles[1].id),), 10),
        Constraint("diameter", (Ref(circles[1].id),), 30)))
    result = solve(original)
    assert not result.success and result.sketch is original
    assert result.termination == "conflict" and result.conflict_minimal
    assert set(result.conflicts) == {c.id for c in original.constraints[1:]}


def test_failed_diagnostic_never_presents_one_islands_rank_as_the_whole_sketch():
    circles = (Circle(Vec2(0, 0), 5), Circle(Vec2(20, 0), 5))
    original = replace(sketch(), entities=circles, constraints=(
        Constraint("radius", (Ref(circles[0].id),), 5),
        Constraint("radius", (Ref(circles[1].id),), 10)))
    result = diagnose(original)
    assert not result.success and result.sketch is original
    assert result.rank is None and result.dof is None


def test_affine_conflict_is_irreducible_and_excludes_connected_irrelevant_relation():
    line = Segment(Vec2(0, 0), Vec2(20, 0))
    relations = (Constraint("fixed", (Ref(line.id, "start"),), Vec2(0, 0)),
                 Constraint("fixed", (Ref(line.id, "start"),), Vec2(10, 0)),
                 Constraint("horizontal", (Ref(line.id),)))
    original = replace(sketch(), entities=(line,), constraints=relations)
    for result in (solve(original), diagnose(original)):
        assert not result.success and result.termination == "conflict" and result.conflict_minimal
        assert set(result.conflicts) == {relations[0].id, relations[1].id}
    for removed in relations[:2]:
        assert solve(replace(original, constraints=tuple(c for c in relations if c is not removed))).success


def test_local_redundancy_is_reported_without_changing_relation_ids():
    original = rectangle()
    duplicate = Constraint("horizontal", original.constraints[4].refs)
    original = replace(original, constraints=(*original.constraints, duplicate))
    result = diagnose(original)
    assert result.success and duplicate.id in result.redundant
    assert result.sketch is original and result.rank == 16


def test_budget_exhaustion_is_distinct_from_proven_contradiction():
    result = solve(rectangle(70, 40), max_iterations=0)
    assert not result.success and result.termination == "limit" and not result.conflict_minimal
    assert "incompatibles" not in result.message


def test_unconstrained_entities_are_counted_without_expanding_the_active_system():
    circle = Circle(Vec2(0, 0), 5)
    free = tuple(Point(Vec2(i, 3)) for i in range(200))
    original = replace(sketch(), entities=(circle, *free), constraints=(Constraint("radius", (Ref(circle.id),), 10),))
    result = solve(original)
    assert result.success and result.dof == 402
    assert result.sketch.entities[1:] == free
    assert diagnose(result.sketch).dof == result.dof


@pytest.mark.parametrize("damage", ["missing", "wrong_part", "wrong_type", "duplicate_id"])
def test_sketch_rejects_invalid_constraint_references(damage):
    line = Segment(Vec2(0, 0), Vec2(20, 0))
    other = Point(Vec2(0, 0))
    c = Constraint("length", (Ref(line.id),), 20)
    if damage == "missing": c = replace(c, refs=(Ref(other.id),))
    if damage == "wrong_part": c = replace(c, refs=(Ref(line.id, "start"),))
    if damage == "wrong_type": c = replace(c, kind="radius")
    if damage == "duplicate_id": c = replace(c, id=line.id)
    with pytest.raises(ValueError):
        replace(sketch(), entities=(line,), constraints=(c,))


@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf"), "12"])
def test_dimensions_are_strict_finite_positive_internal_lengths(value):
    with pytest.raises(ValueError):
        Constraint("radius", (Ref(Circle(Vec2(0, 0), 5).id),), value)
