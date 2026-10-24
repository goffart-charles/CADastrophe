"""Mandatory constraints survive cursor objectives, before any UI mutation."""
from dataclasses import replace
from math import hypot, pi, sqrt

import pytest

from plugins.sketch.constraints import Constraint as C, Reference as R
from plugins.sketch.constrained_drag import ConstrainedDrag
from plugins.sketch.connected_drag import connected_entities
from plugins.sketch.creation_relations import creation_relations
from plugins.sketch.model import Segment, Arc, Circle, Point, Vec2, ExternalGeometry, new_id
from plugins.sketch.solver import diagnose
from test_sketch_e6_bis import drawn_rectangle,check_rectangle
from test_sketch_model import sketch


@pytest.mark.parametrize("origin",[(0,0),(120,-350),(1e7,-1e7)])
def test_dimensioned_rectangle_corner_translates_whole_component_and_keeps_ids(origin):
    s = drawn_rectangle(origin=origin)
    a,b = s.entities[:2]
    s = replace(s,constraints=(*s.constraints,C("length",(R(a.id),),100),C("length",(R(b.id),),60)))
    drag = ConstrainedDrag(s,s.entities,(a.id,"start"),a.start)
    target = Vec2(a.start.u+17,a.start.v-12)
    moved = drag.move(target)
    result = replace(s,entities=moved)
    check_rectangle(result,100,60)
    assert moved[0].start.u == pytest.approx(target.u,abs=1e-7)
    assert moved[0].start.v == pytest.approx(target.v,abs=1e-7)
    assert tuple(e.id for e in moved) == tuple(e.id for e in s.entities)
    assert result.constraints == s.constraints
    assert drag.move(target) is moved  # release reuses the exact feasible preview


@pytest.mark.parametrize("whole",[False,True])
def test_fixed_corner_and_height_allow_only_width_motion_even_off_axis(whole):
    s = drawn_rectangle()
    a,b = s.entities[:2]
    s = replace(s,constraints=(*s.constraints,C("fixed",(R(a.id,"start"),),a.start),C("length",(R(b.id),),60)))
    anchor = Vec2(100,30) if whole else a.end
    handle = (b.id,"entity") if whole else (a.id,"end")
    moved = ConstrainedDrag(s,s.entities,handle,anchor).move(Vec2(130,anchor.v+17))
    check_rectangle(replace(s,entities=moved),130,60)
    assert (moved[0].start.u,moved[0].start.v) == pytest.approx((a.start.u,a.start.v),abs=1e-8)


@pytest.mark.parametrize("whole",[False,True])
def test_fully_constrained_rectangle_never_moves_or_changes_constraints(whole):
    s = drawn_rectangle()
    a,b = s.entities[:2]
    s = replace(s,constraints=(*s.constraints,C("fixed",(R(a.id,"start"),),a.start),
        C("length",(R(a.id),),100),C("length",(R(b.id),),60)))
    assert diagnose(s).dof == 0
    handle,anchor = ((b.id,"entity"),Vec2(100,30)) if whole else ((a.id,"end"),a.end)
    drag = ConstrainedDrag(s,s.entities,handle,anchor)
    with pytest.raises(ValueError,match="immobilisé"):
        drag.move(Vec2(120,50))
    assert drag.last_entities == s.entities and diagnose(s).success


def test_hinge_rotates_on_fixed_length_circle_toward_cursor_without_moving_pivot():
    line = Segment(Vec2(0,0),Vec2(50,0))
    s = replace(sketch(),entities=(line,),constraints=(C("fixed",(R(line.id,"start"),),line.start),C("length",(R(line.id),),50)))
    moved = ConstrainedDrag(s,s.entities,(line.id,"entity"),Vec2(25,0)).move(Vec2(20,20))
    assert (moved[0].start.u,moved[0].start.v) == pytest.approx((line.start.u,line.start.v),abs=1e-8)
    assert (moved[0].end.u,moved[0].end.v) == pytest.approx((50/sqrt(2),)*2,abs=1e-4)
    assert diagnose(replace(s,entities=moved)).success


def test_arc_endpoint_respects_fixed_center_radius_and_coincident_line():
    arc = Arc(Vec2(0,0),20,0,pi/2)
    line = Segment(arc.point_at(1),Vec2(-30,35))
    s = replace(sketch(),entities=(arc,line))
    s = replace(s,constraints=(*creation_relations(s,()),C("coincident",(R(arc.id,"end"),R(line.id,"start"))),
        C("fixed",(R(arc.id,"center"),),arc.center),C("radius",(R(arc.id),),20)))
    moved = ConstrainedDrag(s,s.entities,(arc.id,"end"),arc.point_at(1)).move(Vec2(-15,25))
    assert diagnose(replace(s,entities=moved)).success
    assert (moved[0].center.u,moved[0].center.v,moved[0].radius) == pytest.approx((0,0,20),abs=1e-8)
    assert hypot(moved[1].start.u,moved[1].start.v) == pytest.approx(20,abs=1e-7)
    assert (moved[0].point_at(1).u,moved[0].point_at(1).v) == pytest.approx((moved[1].start.u,moved[1].start.v),abs=1e-7)
    assert moved[1].start.u < -5


def test_successive_drags_keep_fixed_pivot_and_length_without_changing_free_unrelated_geometry():
    line = Segment(Vec2(0,0),Vec2(50,0))
    free = Segment(Vec2(100,100),Vec2(150,130))
    s = replace(sketch(),entities=(line,free),constraints=(C("fixed",(R(line.id,"start"),),line.start),C("length",(R(line.id),),50)))
    group = connected_entities(s,(line.id,))
    assert group == (line,)
    drag = ConstrainedDrag(s,group,(line.id,"end"),line.end)
    for target in (Vec2(40,30),Vec2(25,50),Vec2(-15,45),Vec2(45,10)):
        moved = drag.move(target)
        result = replace(s,entities=(*moved,free))
        assert diagnose(result).success and result.entities[-1] is free
        assert (moved[0].start.u,moved[0].start.v) == pytest.approx((line.start.u,line.start.v),abs=1e-8)
        assert hypot(moved[0].end.u,moved[0].end.v) == pytest.approx(50,abs=1e-7)


def test_point_slides_on_fixed_external_line_toward_off_axis_cursor():
    external = ExternalGeometry(new_id(),0,"Edge",1,Segment(Vec2(-50,-50),Vec2(50,50)))
    point = Point(Vec2(0,0))
    s = replace(sketch(),entities=(point,),external=(external,),constraints=(
        C("point_on_line",(R(point.id,"position"),R(external.geometry.id))),))
    moved = ConstrainedDrag(s,s.entities,(point.id,"position"),point.position).move(Vec2(10,20))
    result = replace(s,entities=moved)
    assert diagnose(result).success and result.external == s.external
    assert (moved[0].position.u,moved[0].position.v) == pytest.approx((15,15),abs=1e-7)


def test_fixed_radius_handle_is_blocked_while_center_keeps_its_two_degrees_of_freedom():
    circle = Circle(Vec2(0,0),20)
    s = replace(sketch(),entities=(circle,),constraints=(C("radius",(R(circle.id),),20),))
    assert diagnose(s).dof == 2
    drag = ConstrainedDrag(s,s.entities,(circle.id,"radius"),Vec2(20,0))
    with pytest.raises(ValueError,match="immobilisé"):
        drag.move(Vec2(35,10))
    assert drag.last_entities == s.entities


@pytest.mark.parametrize("fixed",[False,True])
def test_legacy_point_joint_and_constrained_line_are_solved_together_without_visual_repair(fixed):
    line = Segment(Vec2(0,0),Vec2(30,0))
    point = Point(line.end)
    relations = [C("horizontal",(R(line.id),))]
    if fixed:
        relations += [C("fixed",(R(line.id,"start"),),line.start),C("length",(R(line.id),),30)]
    s = replace(sketch(),entities=(line,point),constraints=tuple(relations))
    drag = ConstrainedDrag(s,s.entities,(point.id,"position"),point.position,
                           joints=((line.id,"end"),(point.id,"position")))
    if fixed:
        with pytest.raises(ValueError,match="immobilisé"):
            drag.move(Vec2(45,20))
        return
    moved = drag.move(Vec2(45,20))
    assert (moved[0].end.u,moved[0].end.v) == pytest.approx((moved[1].position.u,moved[1].position.v),abs=1e-8)
    assert moved[0].start.v == pytest.approx(moved[0].end.v,abs=1e-8)
    assert moved[1].position.u == pytest.approx(45,abs=1e-7)
    assert diagnose(replace(s,entities=moved)).success
