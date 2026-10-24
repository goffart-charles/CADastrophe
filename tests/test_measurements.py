"""Native analytic references: trimmed geometry, transforms and squared units."""
import importlib.util
from math import pi, sqrt
from pathlib import Path

import pytest
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeVertex
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeSphere
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Cylinder, GeomAbs_Sphere
from OCC.Core.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Pln, gp_Circ, gp_Trsf, gp_Vec
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.TopoDS import TopoDS_Shape, topods

from cadastrophe.core.units import DisplayUnits

PLUGIN = Path(__file__).resolve().parents[1] / "plugins/measurements"


@pytest.fixture(scope="module")
def geometry():
    spec = importlib.util.spec_from_file_location("measurement_geometry_test", PLUGIN / "geometry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def vertex(x, y=0, z=0):
    return BRepBuilderAPI_MakeVertex(gp_Pnt(x, y, z)).Vertex()


def line(first=(0, 0, 0), last=(10, 0, 0)):
    return BRepBuilderAPI_MakeEdge(gp_Pnt(*first), gp_Pnt(*last)).Edge()


def plane(normal=(0, 0, 1), origin=(0, 0, 0)):
    return BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(*origin), gp_Dir(*normal)), 0, 10, 0, 20).Face()


def curved_face(shape, kind):
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = topods.Face(explorer.Current())
        if BRepAdaptor_Surface(face, True).GetType() == kind:
            return face
        explorer.Next()
    raise AssertionError("Expected analytic face")


@pytest.mark.parametrize("first,last,expected", [((0, 0, 0), (3, 4, 0), 5.), ((1, 2, 3), (1, 2, 15), 12.)])
def test_distance_between_vertices(geometry, first, last, expected):
    assert geometry.measure("distance", [vertex(*first), vertex(*last)]).value == pytest.approx(expected)


def test_distances_use_trimmed_shapes_and_solid_containment(geometry):
    assert geometry.measure("distance", [line(), vertex(15, 4)]).value == pytest.approx(sqrt(41))
    assert geometry.measure("distance", [plane(), plane(origin=(0, 0, 7))]).value == pytest.approx(7)
    box = BRepPrimAPI_MakeBox(10, 20, 30).Shape()
    other = BRepPrimAPI_MakeBox(gp_Pnt(15, 0, 0), 10, 20, 30).Shape()
    assert geometry.measure("distance", [box, other]).value == pytest.approx(5)
    assert geometry.measure("distance", [box, vertex(5, 5, 5)]).value == pytest.approx(0)
    assert geometry.measure("distance", [box, vertex(10, 10, 10)]).value == pytest.approx(0)


def test_length_of_line_circle_and_trimmed_arc(geometry):
    circle = gp_Circ(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 12)
    assert geometry.measure("length", [line((0, 0, 0), (3, 4, 0))]).value == pytest.approx(5)
    assert geometry.measure("length", [BRepBuilderAPI_MakeEdge(circle).Edge()]).value == pytest.approx(24 * pi)
    arc = BRepBuilderAPI_MakeEdge(circle, 0, pi / 2).Edge()
    assert geometry.measure("length", [arc]).value == pytest.approx(6 * pi)
    assert geometry.measure("radius", [arc]).value == pytest.approx(12)


@pytest.mark.parametrize("kind", ["cylinder", "sphere"])
def test_radius_of_native_analytic_faces_including_location(geometry, kind):
    shape = BRepPrimAPI_MakeCylinder(7.5, 20).Shape() if kind == "cylinder" else BRepPrimAPI_MakeSphere(7.5).Shape()
    face = curved_face(shape, GeomAbs_Cylinder if kind == "cylinder" else GeomAbs_Sphere)
    move = gp_Trsf()
    move.SetTranslation(gp_Vec(123, -45, 67))
    moved = face.Moved(TopLoc_Location(move))
    assert geometry.measure("radius", [moved]).value == pytest.approx(7.5)


@pytest.mark.parametrize("normal,expected", [((0, 0, 1), 0.), ((1, 0, 0), 90.), ((1, 0, 1), 45.), ((0, 0, -1), 0.)])
def test_angle_between_planes_is_minimal_and_not_normal_orientation(geometry, normal, expected):
    assert geometry.measure("angle", [plane(), plane(normal, origin=(0, 0, 5))]).value == pytest.approx(expected)


def test_angles_of_lines_and_line_plane_are_acute_and_orientation_independent(geometry):
    first = line()
    second = line((0, 0, 0), (10, 10, 0))
    assert geometry.measure("angle", [first, second]).value == pytest.approx(45)
    assert geometry.measure("angle", [first, second.Reversed()]).value == pytest.approx(45)
    assert geometry.measure("angle", [line((0, 0, 0), (0, 0, 10)), plane()]).value == pytest.approx(90)
    assert geometry.measure("angle", [first, plane()]).value == pytest.approx(0)
    assert geometry.measure("angle", [plane(), line((0, 0, 0), (10, 0, 10))]).value == pytest.approx(45)


def test_areas_use_exact_faces_and_full_body_surfaces(geometry):
    assert geometry.measure("area", [plane()]).value == pytest.approx(200)
    assert geometry.measure("area", [BRepPrimAPI_MakeBox(10, 20, 30).Shape()]).value == pytest.approx(2200)
    assert geometry.measure("area", [BRepPrimAPI_MakeCylinder(5, 10).Shape()]).value == pytest.approx(150 * pi)
    assert geometry.measure("area", [BRepPrimAPI_MakeSphere(5).Shape()]).value == pytest.approx(100 * pi)


@pytest.mark.parametrize("unit,factor", [("mm", 1.), ("cm", 10.), ("m", 1000.), ("in", 25.4)])
def test_units_are_linear_or_squared_and_angles_remain_degrees(geometry, unit, factor):
    units = DisplayUnits(unit, 4)
    length = geometry.Measurement(factor * 3, "length", "")
    area = geometry.Measurement(factor ** 2 * 9, "area", "")
    angle = geometry.Measurement(45, "angle", "")
    assert length.formatted(units) == f"3.0000 {unit}"
    assert area.formatted(units) == f"9.0000 {unit}²"
    assert angle.formatted(units) == "45.0000 °"


@pytest.mark.parametrize("mode", ["distance", "length", "radius", "angle", "area"])
def test_empty_or_excess_selection_has_explanation(geometry, mode):
    for shapes in ([], [vertex(0), vertex(1), vertex(2)]):
        with pytest.raises(geometry.MeasurementError, match="Sélectionnez"):
            geometry.measure(mode, shapes)


def test_unsupported_geometry_null_and_identical_shapes_are_explained(geometry):
    circle = BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(), 5)).Edge()
    for mode, shapes, message in (
        ("length", [plane()], "arête"), ("radius", [line()], "cercle"),
        ("radius", [plane()], "rayon unique"), ("angle", [circle, line()], "arêtes droites"),
        ("area", [line()], "aire"), ("area", [TopoDS_Shape()], "B-Rep"),
        ("distance", [circle, circle.Reversed()], "distincts"),
    ):
        with pytest.raises(geometry.MeasurementError, match=message):
            geometry.measure(mode, shapes)
