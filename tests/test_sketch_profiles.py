"""E7 analytic contours: topology, intersections, nesting and precision."""
from dataclasses import replace
from math import pi,cos,sin
import subprocess,sys
import pytest
from plugins.sketch.model import Segment,Arc,Circle,Point,Vec2
from plugins.sketch.profiles import analyze,MAX_CURVES
from test_sketch_model import sketch


def polygon(points):
    return tuple(Segment(Vec2(*p),Vec2(*points[(i+1)%len(points)])) for i,p in enumerate(points))


def rectangle(origin=(0,0),width=100,height=60):
    x,y = origin
    return polygon(((x,y),(x+width,y),(x+width,y+height),(x,y+height)))


def source(entities):
    return replace(sketch(),entities=tuple(entities))


def test_profile_import_is_independent_of_qt_and_occt():
    subprocess.run([sys.executable,"-c","import sys; import plugins.sketch.profiles; assert not any(n.startswith(('PySide6','OCC')) for n in sys.modules)"],check=True)


@pytest.mark.parametrize("origin",[(0,0),(-500,370),(1e9,-1e9)])
@pytest.mark.parametrize("reverse",[False,True])
@pytest.mark.parametrize("shuffle",[False,True])
def test_rectangle_has_stable_positive_contour_independent_of_order_and_source_direction(origin,reverse,shuffle):
    edges = rectangle(origin)
    if reverse:
        edges = tuple(replace(e,start=e.end,end=e.start) for e in edges)
    original = source(edges)
    s = replace(original,entities=(edges[2],edges[0],edges[3],edges[1])) if shuffle else original
    report = analyze(s)
    assert report.valid,report.diagnostics
    contour, = report.contours
    assert contour.signed_area == pytest.approx(6000,rel=1e-9)
    assert contour.id == min(e.id for e in edges) and len(contour.edges) == 4
    assert {u.entity_id for u in contour.edges} == {e.id for e in edges}
    assert s.entities == ((edges[2],edges[0],edges[3],edges[1]) if shuffle else edges)
    assert report.contours == analyze(s).contours


@pytest.mark.parametrize("angle",[0,1.4,5.8,-7.])
@pytest.mark.parametrize("sign",[-1,1])
def test_semicircle_closed_by_diameter_handles_clockwise_and_parameter_wrap(angle,sign):
    arc = Arc(Vec2(15,-23),20,angle,sign*pi)
    line = Segment(arc.point_at(1),arc.point_at(0))
    report = analyze(source((line,arc)))
    assert report.valid,report.diagnostics
    assert report.contours[0].signed_area == pytest.approx(200*pi,rel=1e-9)


@pytest.mark.parametrize("sign",[-1,1])
def test_complementary_arcs_form_one_circle_without_false_overlap(sign):
    a = Arc(Vec2(0,0),10,0,sign*pi)
    b = Arc(Vec2(0,0),10,sign*pi,sign*pi)
    report = analyze(source((a,b)))
    assert report.valid,report.diagnostics
    assert report.contours[0].signed_area == pytest.approx(100*pi)


def test_nested_circles_preserve_holes_and_islands_and_ignore_standalone_points():
    curves = tuple(Circle(Vec2(0,0),r) for r in (30,20,10))
    point = Point(Vec2(200,200))
    report = analyze(source((*curves,point)))
    assert report.valid and report.ignored_points == (point.id,)
    by_id = {c.id:c for c in report.contours}
    assert [by_id[e.id].depth for e in curves] == [0,1,2]
    assert [by_id[e.id].hole for e in curves] == [False,True,False]
    assert by_id[curves[1].id].signed_area < 0
    assert by_id[curves[2].id].parent_id == curves[1].id
    assert len(report.regions) == 2


def test_concave_outer_does_not_mistake_its_bounding_box_for_its_interior():
    outer = polygon(((0,0),(100,0),(100,100),(70,100),(70,30),(30,30),(30,100),(0,100)))
    inside,outside = Circle(Vec2(15,60),5),Circle(Vec2(50,60),5)
    report = analyze(source((*outer,inside,outside)))
    assert report.valid,report.diagnostics
    by_id = {c.id:c for c in report.contours}
    assert by_id[inside.id].hole and not by_id[outside.id].hole


def slot():
    left = Arc(Vec2(0,0),10,pi/2,pi)
    right = Arc(Vec2(40,0),10,-pi/2,pi)
    return (left,Segment(left.point_at(1),right.point_at(0)),right,Segment(right.point_at(1),left.point_at(0)))


def test_mixed_line_arc_slot_has_exact_area_and_nested_hole():
    outer = slot()
    hole = Circle(Vec2(20,0),5)
    report = analyze(source((*outer,hole)))
    assert report.valid,report.diagnostics
    c = next(c for c in report.contours if not c.hole)
    assert c.signed_area == pytest.approx(800+100*pi)
    assert len(report.regions[0][1]) == 1


@pytest.mark.parametrize("gap,valid",[(1e-9,True),(1e-5,False),(.25,False)])
def test_small_gaps_use_explicit_tolerance_larger_ones_are_reported_without_healing(gap,valid):
    edges = rectangle()
    edges = (*edges[:-1],replace(edges[-1],end=Vec2(0,gap)))
    report = analyze(source(edges),tolerance=1e-7)
    assert report.valid == valid
    if not valid:
        bad = [d for d in report.diagnostics if d.code == "open_end"]
        assert len(bad) == 2 and bad[0].distance == pytest.approx(gap)
        assert all(d.entity_ids and d.points for d in bad)


@pytest.mark.parametrize("case",["bowtie","crossing","tangent_circles","tangent_hole","duplicate","overlap_arcs","circle_arc","branch"])
def test_ambiguous_profiles_are_rejected_with_structured_source_ids(case):
    if case == "bowtie":
        edges = polygon(((0,0),(40,40),(0,40),(40,0)))
    elif case == "crossing":
        edges = (Circle(Vec2(0,0),10),Circle(Vec2(10,0),10))
    elif case == "tangent_circles":
        edges = (Circle(Vec2(0,0),10),Circle(Vec2(20,0),10))
    elif case == "tangent_hole":
        edges = (*rectangle(),Circle(Vec2(10,30),10))
    elif case == "duplicate":
        rect = rectangle()
        edges = (*rect,Segment(rect[0].end,rect[0].start))
    elif case == "overlap_arcs":
        edges = (Arc(Vec2(0,0),10,0,200*pi/180),Arc(Vec2(0,0),10,190*pi/180,200*pi/180))
    elif case == "circle_arc":
        edges = (Circle(Vec2(0,0),10),Arc(Vec2(0,0),10,0,pi))
    else:
        edges = (*rectangle(),Segment(Vec2(0,0),Vec2(-20,-20)))
    report = analyze(source(edges))
    assert not report.valid
    assert any(d.code in {"intersection","overlap","branch"} for d in report.diagnostics)
    assert all(set(d.entity_ids) <= {e.id for e in edges} for d in report.diagnostics)


def test_collinear_adjacent_segments_are_allowed_but_positive_overlap_is_not():
    edges = polygon(((0,0),(50,0),(100,0),(100,60),(0,60)))
    assert analyze(source(edges)).valid
    bad = (*edges,Segment(Vec2(25,0),Vec2(75,0)))
    assert any(d.code == "overlap" for d in analyze(source(bad)).diagnostics)


@pytest.mark.parametrize("entities",[(),(Point(Vec2(0,0)),)])
def test_empty_or_point_only_sketch_has_no_exportable_contour(entities):
    result = analyze(source(entities))
    assert not result.valid and result.diagnostics[0].code == "no_curves"


def test_limits_and_degenerate_precision_cases_return_diagnostics():
    many = source(Circle(Vec2(i*4,0),1) for i in range(MAX_CURVES+1))
    assert analyze(many).diagnostics[0].code == "limit"
    assert analyze(source(rectangle()),time_budget=1e-12).diagnostics[0].code == "time_limit"
    assert not analyze(source((Circle(Vec2(0,0),1e-10),))).valid


@pytest.mark.parametrize("tol",[0,-1,float("inf"),float("nan"),True])
def test_invalid_tolerance_is_refused(tol):
    with pytest.raises(ValueError):
        analyze(source(rectangle()),tolerance=tol)


@pytest.mark.parametrize("curve",[Circle(Vec2(1e308,0),1e308),Arc(Vec2(1e308,0),1e308,0,pi)])
def test_overflowing_coordinates_produce_a_diagnostic_instead_of_crashing(curve):
    report = analyze(source((curve,)))
    assert not report.valid and report.diagnostics[0].code == "numerical_error"


def test_arc_smaller_than_tolerance_is_degenerate():
    report = analyze(source((Arc(Vec2(0,0),10,0,1e-10),)))
    assert not report.valid and any(d.code == "degenerate" for d in report.diagnostics)


def test_closed_geometry_with_unsatisfied_dimension_is_not_a_valid_profile():
    from plugins.sketch.constraints import Constraint,Reference
    s = source(rectangle())
    c = Constraint("length",(Reference(s.entities[0].id),),150)
    s = replace(s,constraints=(c,))
    report = analyze(s)
    assert not report.valid
    problem = next(d for d in report.diagnostics if d.code == "constraint_conflict")
    assert problem.entity_ids == (s.entities[0].id,)
    assert report.sketch == s and s.entities[0].end.u == 100
