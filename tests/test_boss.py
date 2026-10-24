"""X5 native boolean invariants; no Qt, plugin or viewport."""
import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.BRepTools import breptools
from OCC.Core.TopAbs import TopAbs_SOLID
from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.gp import gp_Pnt

from cadastrophe.core.boss import fuse_boss
from test_primitives import volume


def box(x, y, z, dx=20, dy=20, dz=10):
    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), dx, dy, dz).Shape()


@pytest.mark.parametrize("origin,expected", [((10, 10, 20), 124000), ((10, 10, 15), 122000),
                                             ((90, 10, 15), 123000), ((100, 10, 5), 124000)])
def test_face_contact_overlap_and_partial_overlap(origin, expected, tmp_path):
    base, tool = box(0, 0, 0, 100, 60, 20), box(*origin)
    path = tmp_path / "before.brep"
    breptools.Write(base, str(path))
    before = path.read_bytes()
    result = fuse_boss(base, tool)
    assert result.success, result.message
    assert result.shape.ShapeType() == TopAbs_SOLID and BRepCheck_Analyzer(result.shape).IsValid()
    assert result.volume == pytest.approx(expected)
    assert volume(base) == pytest.approx(120000) and volume(tool) == pytest.approx(4000)
    breptools.Write(base, str(path))
    assert path.read_bytes() == before


@pytest.mark.parametrize("origin", [(200, 0, 0), (100, 60, 0), (100, 60, 20), (0, 0, 21)])
def test_disjoint_edge_and_point_contact_are_rejected(origin):
    result = fuse_boss(box(0, 0, 0, 100, 60, 20), box(*origin))
    assert not result.success and result.shape is None and result.volume is None
    assert result.diagnostics and result.message


@pytest.mark.parametrize("origin", [(0, 0, 0), (10, 10, 10)])
def test_no_material_added_is_rejected(origin):
    result = fuse_boss(box(0, 0, 0, 100, 60, 20), box(*origin))
    assert not result.success and result.diagnostics[0].code == "no_added_material"


@pytest.mark.parametrize("invalid", [None, object(), TopoDS_Shape()])
def test_invalid_input_never_produces_a_shape(invalid):
    result = fuse_boss(invalid, box(0, 0, 0))
    assert not result.success and result.shape is None


@pytest.mark.parametrize("tolerance", [0, -1, float("nan"), float("inf"), True])
def test_invalid_tolerance(tolerance):
    result = fuse_boss(box(0, 0, 0), box(0, 0, 10), tolerance=tolerance)
    assert not result.success and result.diagnostics[0].code == "invalid_tolerance"


@pytest.mark.parametrize("offset", [0, 10000, -100000])
def test_translated_geometry(offset):
    result = fuse_boss(box(offset, offset, offset, 100, 60, 20), box(offset+10, offset+10, offset+20))
    assert result.success, result.message
    assert result.volume == pytest.approx(124000)
