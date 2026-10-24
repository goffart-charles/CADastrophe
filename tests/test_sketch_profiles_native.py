"""Demand-driven exact native wires, holes, gaps and stale snapshot refusal."""
from dataclasses import replace
from math import pi,sin
import pytest
from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.GProp import GProp_GProps
from OCC.Core.TopAbs import TopAbs_EDGE
from OCC.Core.TopExp import TopExp_Explorer
from cadastrophe.geometry import REFERENCE_PLANES,WorkPlane,Vec3
from plugins.sketch.profiles import analyze
from plugins.sketch.profile_native import to_wires,ProfileError
from plugins.sketch.model import Circle,Arc,Segment,Vec2
from test_sketch_model import sketch
from test_sketch_profiles import rectangle,source,slot


FRAMES = (*REFERENCE_PLANES,WorkPlane("Incliné",Vec3(100,-50,70),Vec3(1,2,3),Vec3(-2,1,0)))


@pytest.mark.parametrize("frame",FRAMES)
@pytest.mark.parametrize("kind",["rectangle","circle","slot","cw_arc","near_gap"])
def test_exact_closed_valid_wires_on_all_support_frames(frame,kind):
    if kind == "rectangle":
        edges = rectangle()
    elif kind == "circle":
        edges = (Circle(Vec2(10,20),15),)
    elif kind == "slot":
        edges = slot()
    elif kind == "cw_arc":
        arc = Arc(Vec2(20,30),15,pi,-pi)
        edges = (arc,Segment(arc.point_at(1),arc.point_at(0)))
    else:
        edges = rectangle()
        edges = (*edges[:-1],replace(edges[-1],end=Vec2(0,1e-8)))
    s = replace(sketch(frame),entities=edges)
    report = analyze(s)
    assert report.valid,report.diagnostics
    output = to_wires(s,report)
    assert len(output.contours) == len(output.regions) == 1
    wire = output.contours[0].wire
    assert BRep_Tool.IsClosed(wire) and BRepCheck_Analyzer(wire,True).IsValid()
    face = BRepBuilderAPI_MakeFace(wire,True).Face()
    assert BRepCheck_Analyzer(face,True).IsValid()
    props = GProp_GProps()
    brepgprop.SurfaceProperties(face,props)
    assert props.Mass() == pytest.approx(report.contours[0].signed_area,rel=1e-8)
    assert s.entities == edges


@pytest.mark.parametrize("frame",FRAMES)
def test_holes_and_islands_are_grouped_and_oriented_for_later_planar_faces(frame):
    s = replace(sketch(frame),entities=tuple(Circle(Vec2(0,0),r) for r in (30,20,10)))
    output = to_wires(s)
    assert len(output.contours) == 3 and len(output.regions) == 2
    total = 0
    for outer,holes in output.regions:
        maker = BRepBuilderAPI_MakeFace(outer.wire,True)
        for hole in holes:
            maker.Add(hole.wire)
        face = maker.Face()
        assert BRepCheck_Analyzer(face,True).IsValid()
        props = GProp_GProps()
        brepgprop.SurfaceProperties(face,props)
        total += props.Mass()
    assert total == pytest.approx(600*pi,rel=1e-8)


def test_native_export_rejects_open_intersecting_or_stale_analysis():
    s = source(rectangle())
    report = analyze(s)
    modified = replace(s,entities=(*s.entities,Circle(Vec2(50,30),10)))
    with pytest.raises(ProfileError,match="périmée"):
        to_wires(modified,report)
    for bad in (source(s.entities[:-1]),source((Circle(Vec2(0,0),10),Circle(Vec2(10,0),10)))):
        with pytest.raises(ProfileError) as error:
            to_wires(bad)
        assert error.value.analysis is not None and not error.value.analysis.valid


@pytest.mark.parametrize("angle",[-7.,5.8])
@pytest.mark.parametrize("sign",[-1,1])
def test_native_major_arcs_crossing_parameter_seam_keep_their_exact_area(angle,sign):
    arc = Arc(Vec2(15,-20),12,angle,sign*1.5*pi)
    s = source((arc,Segment(arc.point_at(1),arc.point_at(0))))
    report = analyze(s)
    assert report.valid
    wire = to_wires(s,report).contours[0].wire
    assert BRepCheck_Analyzer(wire,True).IsValid() and BRep_Tool.IsClosed(wire)
    face = BRepBuilderAPI_MakeFace(wire,True).Face()
    assert BRepCheck_Analyzer(face,True).IsValid()
    props = GProp_GProps()
    brepgprop.SurfaceProperties(face,props)
    assert props.Mass() == pytest.approx(.5*12**2*(1.5*pi-sin(1.5*pi)),rel=1e-8)
