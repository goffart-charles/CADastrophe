"""X6 real native cuts and directional extent, independent of widgets."""
from math import pi

import pytest
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCC.Core.BRep import BRep_Builder
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCC.Core.BRepTools import BRepTools_ShapeSet
from OCC.Core.TopAbs import TopAbs_SOLID
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Compound
from OCC.Core.gp import gp_Ax2, gp_Dir, gp_Pln, gp_Pnt

from cadastrophe.core.cut import cut_solid, through_all_depth
from cadastrophe.core.extrusion import extrude_face
from cadastrophe.geometry import WorkPlane
from plugins.sketch.model import Circle, Vec2
from test_extrusion import FRAMES, profile


def block(height=20):
    return BRepPrimAPI_MakeBox(100., 60., float(height)).Shape()


def cylinder(depth, *, x=50, y=30, z=20, inward=True):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z), gp_Dir(0, 0, -1 if inward else 1)), 10., depth).Shape()


@pytest.mark.parametrize("depth", [1e-4, 5., 20., 30.])
def test_pocket_and_through_hole_volume_native_validity_and_unchanged_sources(depth):
    base, tool = block(), cylinder(depth)
    before = [BRepTools_ShapeSet.WriteToString(shape, True) for shape in (base, tool)]
    result = cut_solid(base, tool)
    assert result.success, result.message
    removed = 100*pi*min(depth, 20)
    assert result.volume == pytest.approx(120000-removed)
    assert result.removed_volume == pytest.approx(removed, rel=1e-6)
    assert result.shape.ShapeType() == TopAbs_SOLID and BRepCheck_Analyzer(result.shape).IsValid()
    assert result.base_faces
    assert [BRepTools_ShapeSet.WriteToString(shape, True) for shape in (base, tool)] == before


@pytest.mark.parametrize("tool", [cylinder(5., x=300), cylinder(5., inward=False), cylinder(5., x=110)])
def test_disjoint_wrong_direction_and_tangency_have_no_removal(tool):
    result = cut_solid(block(), tool)
    assert not result.success and result.shape is None
    assert result.diagnostics[0].code == "no_removed_material"


@pytest.mark.parametrize("whole", [False, True])
def test_cut_cannot_split_or_delete_the_entire_body(whole):
    tool = (BRepPrimAPI_MakeBox(gp_Pnt(-10, -10, -10), 120., 80., 40.).Shape() if whole else
            BRepPrimAPI_MakeBox(gp_Pnt(45, -10, -10), 10., 80., 40.).Shape())
    result = cut_solid(block(), tool)
    assert not result.success and result.shape is None
    assert result.diagnostics[0].code == ("empty_result" if whole else "split_result")


@pytest.mark.parametrize("invalid", [None, object(), TopoDS_Shape()])
def test_invalid_shapes(invalid):
    assert not cut_solid(invalid, cylinder(5.)).success
    assert not cut_solid(block(), invalid).success


@pytest.mark.parametrize("tolerance", [0, -1, True, float("nan"), float("inf")])
def test_invalid_tolerance(tolerance):
    result = cut_solid(block(), cylinder(5.), tolerance=tolerance)
    assert not result.success and result.diagnostics[0].code == "invalid_tolerance"


@pytest.mark.parametrize("height", [20, 100, 10000])
@pytest.mark.parametrize("axis,origin,reverse", [((0, 0, -1), (50, 30, 0), True),
    ((0, 0, 1), (50, 30, -10), False), ((1, 0, 0), (0, 30, 0), False),
    ((0, 1, 0), (50, 0, 0), False), ((1, 1, 1), (0, 0, 0), False)])
def test_through_all_uses_direction_and_current_target_bounds(height, axis, origin, reverse):
    face = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(*origin), gp_Dir(*axis)), -1., 1., -1., 1.).Face()
    depth = through_all_depth(block(height), face, reversed=reverse)
    normal = gp_Dir(*axis).Coord()
    sign = -1 if reverse else 1
    far = max(sum((p[i]-origin[i])*normal[i]*sign for i in range(3))
              for p in ((x, y, z) for x in (0, 100) for y in (0, 60) for z in (0, height)))
    assert depth > far and depth < far+max(height, 100)*1e-4


def test_through_all_rejects_target_behind_start_plane():
    face = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(50, 30, 20), gp_Dir(0, 0, 1)), -1., 1., -1., 1.).Face()
    with pytest.raises(ValueError, match="inversez"):
        through_all_depth(block(), face)


def test_multi_region_tool_is_cut_as_one_feature():
    tool, builder = TopoDS_Compound(), BRep_Builder()
    builder.MakeCompound(tool)
    builder.Add(tool, cylinder(5., x=25))
    builder.Add(tool, cylinder(5., x=75))
    result = cut_solid(block(), tool)
    assert result.success, result.message
    assert result.volume == pytest.approx(120000-1000*pi)


def test_multi_region_tool_cannot_hide_a_stray_face():
    tool, builder = TopoDS_Compound(), BRep_Builder()
    builder.MakeCompound(tool)
    builder.Add(tool, cylinder(5.))
    face = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(50, 30, 20), gp_Dir(0, 0, 1)), -1., 1., -1., 1.).Face()
    builder.Add(tool, face)
    result = cut_solid(block(), tool)
    assert not result.success and result.diagnostics[0].code == "invalid_input"


@pytest.mark.parametrize("frame", FRAMES)
def test_cut_through_all_on_arbitrary_oriented_and_translated_planes(frame):
    base = extrude_face(profile(frame), 20)
    top = WorkPlane("Face orientée", frame.origin+frame.normal*20, frame.u, frame.v)
    face = profile(top, curves=(Circle(Vec2(50, 30), 5),))
    extent = through_all_depth(base.shape, face, reversed=True)
    tool = extrude_face(face, extent, reversed=True)
    assert tool.success
    result = cut_solid(base.shape, tool.shape)
    assert result.success, result.message
    assert result.volume == pytest.approx(120000-500*pi)
