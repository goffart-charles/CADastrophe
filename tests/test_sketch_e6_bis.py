"""A–D/F: construction rules and hard constraints, with no GUI or OCCT imports."""
from dataclasses import replace
from math import cos, sin, hypot, pi, atan2

import pytest

from plugins.sketch.constraints import Constraint as C, Reference as R
from plugins.sketch.creation_relations import creation_relations
from plugins.sketch.drawing import DrawingController
from plugins.sketch.model import Segment, Point, Circle, Arc, Vec2
from plugins.sketch.solver import solve, diagnose, System
from plugins.sketch.dimension_geometry import measure
from plugins.sketch.dimension_state import DimensionState, Phase
from test_sketch_model import sketch


def drawn_rectangle(width=100., height=60., origin=(0., 0.)):
    draft = DrawingController()
    draft.select("rectangle")
    draft.click(Vec2(*origin))
    edges = draft.click(Vec2(origin[0]+width, origin[1]+height))
    empty = sketch()
    # These solver/drag cases deliberately exercise a floating rectangle.
    # Origin anchoring has its own native creation and dimension regressions.
    relations = creation_relations(empty, edges, rectangle=True)
    floating = []
    for c in relations:
        refs = tuple(R(edges[0].id, 'start') if r.entity_id == empty.id else r for r in c.refs)
        if len(refs) != 2 or refs[0] != refs[1]:
            floating.append(replace(c, refs=refs))
    relations = tuple(floating)
    return replace(empty, entities=edges, constraints=relations)


def check_rectangle(s, width, height, tolerance=1e-7):
    assert diagnose(s).success
    for i, e in enumerate(s.entities):
        other = s.entities[(i+1) % 4]
        assert hypot(e.end.u-other.start.u, e.end.v-other.start.v) <= tolerance
        assert (e.start.v if i % 2 == 0 else e.start.u) == pytest.approx(
            e.end.v if i % 2 == 0 else e.end.u, abs=tolerance)
        assert hypot(e.end.u-e.start.u, e.end.v-e.start.v) == pytest.approx(height if i % 2 else width, abs=tolerance)


@pytest.mark.parametrize("axis,target,width,height", [(1,150.,150.,60.), (0,90.,100.,90.)])
def test_a_b_opposite_sides_of_actual_rectangle_keep_all_relations(axis, target, width, height):
    original = drawn_rectangle()
    assert len(original.constraints) == 8
    a, b = original.entities[axis], original.entities[axis+2]
    dimension = C("line_distance", (R(a.id, "start"), R(b.id)), target)
    result = solve(replace(original, constraints=(*original.constraints, dimension)))
    assert result.success, result.message
    check_rectangle(result.sketch, width, height)
    assert result.sketch.constraints[:-1] == original.constraints
    assert [e.id for e in result.sketch.entities] == [e.id for e in original.entities]
    assert result.dof == 3  # free translation and undimensioned other size
    check_rectangle(original, 100., 60.)


def test_c_connected_underconstrained_chain_keeps_joints_and_its_actual_dofs():
    edges = (Segment(Vec2(0,0), Vec2(30,20)), Segment(Vec2(30,20), Vec2(50,60)),
             Segment(Vec2(50,60), Vec2(75,50)))
    empty = sketch()
    relations = creation_relations(empty, edges)
    assert len(relations) == 3  # two joints and the fixed origin
    source = replace(empty, entities=edges, constraints=(*relations, C("length", (R(edges[0].id),), 70)))
    result = solve(source)
    assert result.success and result.dof == 5
    for a, b in zip(result.sketch.entities, result.sketch.entities[1:]):
        assert (a.end.u, a.end.v) == pytest.approx((b.start.u,b.start.v), abs=1e-8)
    assert measure(result.sketch, "length", (R(edges[0].id),)) == pytest.approx(70)


def test_d_linear_conflict_and_nonlinear_conflict_return_original_snapshot():
    original = drawn_rectangle()
    e = original.entities[0]
    cases = ((C("fixed", (R(e.id,"start"),), Vec2(0,0)), C("fixed", (R(e.id,"start"),), Vec2(10,0))),
             (C("length", (R(e.id),), 100), C("length", (R(e.id),), 150)))
    for extra in cases:
        source = replace(original, constraints=(*original.constraints, *extra))
        result = solve(source)
        assert not result.success and result.sketch is source and "Échec" in result.message
        check_rectangle(original, 100, 60)


@pytest.mark.parametrize("scale,origin", [(1e-4,(0.,0.)), (1.,(-735.,1290.)), (1e3,(1e6,-1e6)), (1.,(1e9,-1e9))])
def test_f_successive_resize_small_large_and_translated_coordinates(scale, origin):
    source = drawn_rectangle(100*scale, 60*scale, origin)
    e = source.entities
    width = C("line_distance", (R(e[1].id,"start"), R(e[3].id)), 100*scale)
    height = C("line_distance", (R(e[0].id,"start"), R(e[2].id)), 60*scale)
    source = replace(source, constraints=(*source.constraints, width, height))
    center = tuple(sum(getattr(p, axis) for edge in e for p in (edge.start,edge.end))/8 for axis in ("u","v"))
    for w,h in [(150,60), (150,90), (15,7), (100,60), (240,120)]:
        source = replace(source, constraints=tuple(replace(c, value=(w if c.id == width.id else h)*scale)
                          if c.id in {width.id,height.id} else c for c in source.constraints))
        result = solve(source)
        assert result.success, result.message
        source = result.sketch
        check_rectangle(source,w*scale,h*scale, max(1e-7,scale*1e-7,2e-6 if origin[0] == 1e9 else 0))
        assert solve(source).sketch == source
        now = tuple(sum(getattr(p, axis) for edge in source.entities for p in (edge.start,edge.end))/8 for axis in ("u","v"))
        assert now == pytest.approx(center, abs=max(2e-6,scale*1e-7))


@pytest.mark.parametrize("rotation", [.3, 1.2, -2.1])
def test_f_rotated_connected_geometry_and_combined_relations(rotation):
    def p(u,v):
        return Vec2(200+u*cos(rotation)-v*sin(rotation), -150+u*sin(rotation)+v*cos(rotation))
    vertices = [p(0,0), p(100,0), p(100,60), p(0,60)]
    edges = tuple(Segment(vertices[i],vertices[(i+1)%4]) for i in range(4))
    empty = sketch()
    constraints = (*creation_relations(empty, edges),
        C("parallel", (R(edges[0].id),R(edges[2].id))), C("parallel", (R(edges[1].id),R(edges[3].id))),
        C("perpendicular", (R(edges[0].id),R(edges[1].id))), C("length", (R(edges[1].id),),60),
        C("line_distance", (R(edges[1].id,"start"),R(edges[3].id)),150))
    source = replace(empty, entities=edges, constraints=constraints)
    result = solve(source)
    assert result.success, result.message
    assert result.dof == 3
    first = result.sketch.entities[0]
    assert atan2(first.end.v-first.start.v,first.end.u-first.start.u) == pytest.approx(rotation,abs=1e-7)
    for i, e in enumerate(result.sketch.entities):
        b = result.sketch.entities[(i+1)%4]
        assert (e.end.u,e.end.v) == pytest.approx((b.start.u,b.start.v),abs=1e-7)
        assert hypot(e.end.u-e.start.u,e.end.v-e.start.v) == pytest.approx(60 if i%2 else 150)


@pytest.mark.parametrize("value", [pi/6, pi/2, 5*pi/6])
def test_angle_drives_two_lines_without_breaking_a_shared_vertex(value):
    a,b = Segment(Vec2(0,0),Vec2(40,0)), Segment(Vec2(0,0),Vec2(20,30))
    empty = sketch()
    refs = (R(a.id,"end"),R(b.id,"end"))
    constraints = (*creation_relations(empty,(a,b)), C("fixed", (R(a.id,"start"),),a.start),
                   C("fixed", (R(a.id,"end"),),a.end), C("length", (R(b.id),),hypot(20,30)), C("angle", refs,value))
    result = solve(replace(empty,entities=(a,b),constraints=constraints))
    assert result.success, result.message
    assert measure(result.sketch,"angle",refs) == pytest.approx(value,abs=1e-9)
    assert result.sketch.entities[1].end.v > 0
    assert (result.sketch.entities[1].start.u,result.sketch.entities[1].start.v) == pytest.approx((0,0),abs=1e-8)


def test_degenerate_distance_and_overconstrained_angle_are_refused_cleanly():
    a,b = Point(Vec2(0,0)),Point(Vec2(0,0))
    source = replace(sketch(),entities=(a,b),constraints=(C("distance",(R(a.id,"position"),R(b.id,"position")),10),))
    result = solve(source)
    assert not result.success and result.sketch is source
    for value in [0,pi,-1,float("nan")]:
        with pytest.raises(ValueError):
            C("angle",(R(a.id),R(b.id)),value)


def test_state_machine_never_places_on_entity_click_and_escape_is_progressive():
    state = DimensionState()
    state.start()
    assert state.phase is Phase.SELECTING
    for ref in ("first","second"):
        state.select(ref,lambda refs: True)
        state.preview(True)
        assert state.phase is Phase.PREVIEW
    assert state.refs == ["first","second"]
    with pytest.raises(ValueError):
        state.select("third",lambda refs: True)
    assert state.refs == ["first","second"]
    assert state.escape() and state.refs == ["first"]
    state.preview(True)
    assert state.place() and state.phase is Phase.PLACING
    assert not state.select("second",lambda refs: True)
    state.edit()
    state.finish()
    assert state.phase is Phase.SELECTING and state.refs == []
    assert not state.escape() and state.phase is Phase.INACTIVE


def test_creation_uses_nonredundant_vertex_tree_and_never_infers_nearby_points():
    empty = sketch()
    edges = (Segment(Vec2(0,0),Vec2(10,0)), Segment(Vec2(0,0),Vec2(0,10)), Segment(Vec2(0,0),Vec2(10,10)))
    relations = creation_relations(empty,edges)
    assert len(relations) == 3  # one origin anchor and two further joints
    source = replace(empty,entities=edges,constraints=relations)
    near = Segment(Vec2(0,1e-6),Vec2(5,5))
    assert not creation_relations(source,(near,))


def test_analytic_jacobian_against_independent_numerical_derivatives_all_equations():
    import numpy as np
    a,b = Segment(Vec2(1,2),Vec2(21,12)),Segment(Vec2(3,22),Vec2(16,35))
    p,arc,circle = Point(Vec2(15,-6)),Arc(Vec2(-5,7),12,.3,-1.4),Circle(Vec2(9,20),8)
    constraints = [C(kind,(R(a.id),),10 if kind == "length" else None) for kind in ("horizontal","vertical","length")]
    constraints += [C(kind,(R(a.id),R(b.id))) for kind in ("parallel","perpendicular")]
    constraints += [C(kind,(R(a.id,"end"),R(arc.id,"start")),None if kind == "coincident" else 25)
                    for kind in ("coincident","distance","horizontal_distance","vertical_distance")]
    constraints += [C("fixed",(R(arc.id,"end"),),Vec2(0,0)), C("fixed",(R(p.id,"position"),),Vec2(1,1)),
                    C("point_on_line",(R(p.id,"position"),R(b.id))),
                    C("line_distance",(R(p.id,"position"),R(b.id)),20),
                    C("radius",(R(arc.id),),15),C("diameter",(R(circle.id),),20),
                    C("angle",(R(a.id,"start"),R(b.id,"end")),1.2)]
    system = System(replace(sketch(),entities=(a,b,p,arc,circle),constraints=tuple(constraints)))
    columns = []
    for i in range(len(system.x)):
        delta = np.zeros_like(system.x)
        delta[i] = 1e-5*system.scales[i]
        columns.append((system.residuals(system.x+delta)-system.residuals(system.x-delta))/2e-5)
    np.testing.assert_allclose(system.jacobian(system.x),np.column_stack(columns),rtol=2e-6,atol=2e-7)


def test_impossible_angle_and_collapsed_segment_are_not_committed():
    a,b = Segment(Vec2(0,0),Vec2(20,0)),Segment(Vec2(0,10),Vec2(10,25))
    constraints = tuple(C("fixed",(R(e.id,part),),getattr(e,part)) for e in (a,b) for part in ("start","end"))
    source = replace(sketch(),entities=(a,b),constraints=(*constraints,C("angle",(R(a.id),R(b.id)),pi/2)))
    assert not solve(source).success and solve(source).sketch is source
    source = replace(sketch(),entities=(a,),constraints=(C("length",(R(a.id),),1e-11),))
    result = solve(source)
    assert not result.success and result.sketch is source
