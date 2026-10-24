"""X3 native prism engine: independent of widgets, documents and AIS."""
from dataclasses import replace
from math import pi
import subprocess
import sys

import pytest
from OCC.Core.BRep import BRep_Builder, BRep_Tool
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepClass3d import BRepClass3d_SolidClassifier
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeSphere
from OCC.Core.BRepTools import BRepTools_ShapeSet
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_IN, TopAbs_OUT, TopAbs_SHELL, TopAbs_SOLID, TopAbs_VERTEX
from OCC.Core.TopExp import topexp
from OCC.Core.TopTools import TopTools_IndexedMapOfShape
from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Shape, topods
from OCC.Core.gp import gp_Pln, gp_Pnt

from cadastrophe.core.extrusion import extrude_face
from cadastrophe.geometry import REFERENCE_PLANES, Vec3, WorkPlane
from plugins.sketch.model import Arc, Circle, Segment, Vec2
from plugins.sketch.profile_faces import to_faces
from test_sketch_model import sketch
from test_sketch_profiles import rectangle, slot, source


FRAMES = (*REFERENCE_PLANES,
          WorkPlane("Incliné", Vec3(123, -234, 456), Vec3(1, 2, 3), Vec3(-2, 1, 0)),
          WorkPlane("Retourné", Vec3(-100, 200, -50), Vec3(1, 0, 0), Vec3(0, -1, 0)))


def items(shape, kind):
    mapping = TopTools_IndexedMapOfShape()
    topexp.MapShapes(shape, kind, mapping)
    return tuple(mapping.FindKey(index) for index in range(1, mapping.Size()+1))


def profile(frame=REFERENCE_PLANES[0], curves=None):
    s = replace(sketch(frame), entities=rectangle() if curves is None else curves)
    return to_faces(s).faces[0]


@pytest.mark.parametrize("frame", FRAMES)
@pytest.mark.parametrize("inverted", [False, True])
@pytest.mark.parametrize("kind", ["rectangle", "circle", "slot", "arc", "hole"])
def test_one_valid_closed_solid_correct_volume_local_direction_and_unmodified_inputs(frame, inverted, kind):
    if kind == "rectangle":
        curves, area = rectangle(), 6000
    elif kind == "circle":
        curves, area = (Circle(Vec2(50, 30), 10),), 100*pi
    elif kind == "slot":
        curves, area = slot(), 800+100*pi
    elif kind == "arc":
        arc = Arc(Vec2(20, 30), 10, 5.8, -pi)
        curves, area = (arc, Segment(arc.point_at(1), arc.point_at(0))), 50*pi
    else:
        curves, area = (*rectangle(), Circle(Vec2(50, 30), 10)), 6000-100*pi
    s = replace(sketch(frame), entities=curves)
    face = to_faces(s).faces[0]
    before = BRepTools_ShapeSet.WriteToString(face, True)
    result = extrude_face(face, 20, reversed=inverted)
    assert result.success, result.diagnostics
    assert result.volume == pytest.approx(area*20, rel=1e-8)
    assert result.expected_volume == pytest.approx(area*20, rel=1e-8)
    assert result.shape.ShapeType() == TopAbs_SOLID and len(items(result.shape, TopAbs_SOLID)) == 1
    assert BRepCheck_Analyzer(result.shape, True).IsValid()
    assert all(BRep_Tool.IsClosed(topods.Shell(shell)) for shell in items(result.shape, TopAbs_SHELL))
    expected = frame.normal * (-20 if inverted else 20)
    assert result.translation == pytest.approx(expected.tuple(), abs=1e-12)
    heights = [frame.to_local(Vec3(*BRep_Tool.Pnt(topods.Vertex(vertex)).Coord()))[2]
               for vertex in items(result.shape, TopAbs_VERTEX)]
    assert min(heights) == pytest.approx(-20 if inverted else 0, abs=1e-7)
    assert max(heights) == pytest.approx(0 if inverted else 20, abs=1e-7)
    if kind == "hole":
        def classify(u, v):
            point = frame.to_world(u, v, -10 if inverted else 10)
            return BRepClass3d_SolidClassifier(result.shape, gp_Pnt(*point.tuple()), 1e-7).State()
        assert classify(5, 5) == TopAbs_IN and classify(50, 30) == TopAbs_OUT
    assert BRepTools_ShapeSet.WriteToString(face, True) == before
    assert s.entities == curves


@pytest.mark.parametrize("depth", [1e-6, 1e-4, .001, 1, 1000])
@pytest.mark.parametrize("inverted", [False, True])
def test_small_and_large_valid_depths(depth, inverted):
    result = extrude_face(profile(), depth, reversed=inverted)
    assert result.success, result.diagnostics
    assert result.volume == pytest.approx(6000*depth, rel=1e-7)


@pytest.mark.parametrize("depth", [0, -1, True, None, "20", float("nan"), float("inf"), 10**1000])
def test_invalid_depth_returns_diagnostic_without_shape(depth):
    result = extrude_face(None, depth)
    assert not result.success and result.shape is None and result.volume is None
    assert result.diagnostics[0].code == "invalid_depth"


@pytest.mark.parametrize("values,code", [({"reversed": 1}, "invalid_direction"),
                                        ({"tolerance": 0}, "invalid_tolerance"),
                                        ({"tolerance": float("nan")}, "invalid_tolerance"),
                                        ({"tolerance": True}, "invalid_tolerance")])
def test_invalid_options(values, code):
    result = extrude_face(None, 20, **values)
    assert not result.success and result.diagnostics[0].code == code


@pytest.mark.parametrize("kind", ["none", "null", "solid", "compound", "curved", "unbounded"])
def test_only_one_bounded_planar_face_is_accepted(kind):
    if kind == "none":
        face = None
    elif kind == "null":
        face = TopoDS_Shape()
    elif kind == "solid":
        face = BRepPrimAPI_MakeBox(1, 2, 3).Shape()
    elif kind == "compound":
        face = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(face)
        builder.Add(face, profile())
    elif kind == "curved":
        face = items(BRepPrimAPI_MakeSphere(10).Shape(), TopAbs_FACE)[0]
    else:
        face = BRepBuilderAPI_MakeFace(gp_Pln()).Face()
    result = extrude_face(face, 20)
    assert not result.success and result.shape is None
    assert result.diagnostics[0].code == {"curved": "non_planar", "unbounded": "open_profile"}.get(kind, "invalid_face")


def test_reversed_native_face_uses_its_oriented_normal():
    face = topods.Face(profile().Reversed())
    result = extrude_face(face, 20)
    assert result.success, result.diagnostics
    assert result.translation == (0, 0, -20) and result.volume == pytest.approx(120000)


def test_precision_and_overflow_refusals_are_explicit():
    face = profile()
    assert extrude_face(face, 1e-9).diagnostics[0].code == "depth_precision"
    assert not extrude_face(face, 1e308).success
    far = WorkPlane("Loin", Vec3(1e9, -1e9, 1e9), Vec3(1, 0, 0), Vec3(0, 1, 0))
    face = profile(far)
    result = extrude_face(face, 20)
    assert result.success, result.diagnostics
    assert result.volume == pytest.approx(120000)
    assert not extrude_face(face, 1e-9).success


def test_engine_import_does_not_load_qt_or_occt():
    subprocess.run([sys.executable, "-c", "import sys; import cadastrophe.core.extrusion; "
                    "assert not any(name.startswith(('PySide6','OCC')) for name in sys.modules)"], check=True)


@pytest.mark.parametrize("fault,code", [("not_done", "prism_failed"), ("null", "null_result"),
                                        ("compound", "solid_count"), ("wrong_volume", "volume_mismatch"),
                                        ("reversed_solid", "invalid_volume"), ("exception", "native_error")])
def test_native_failure_never_exposes_a_partial_shape(monkeypatch, fault, code):
    from OCC.Core import BRepPrimAPI
    face = profile()
    before = BRepTools_ShapeSet.WriteToString(face, True)
    actual = BRepPrimAPI.BRepPrimAPI_MakePrism
    class FaultyPrism:
        def __init__(self, *args):
            self.prism = actual(*args)
        def IsDone(self):
            return fault != "not_done"
        def Shape(self):
            if fault == "exception":
                raise RuntimeError("échec injecté")
            if fault == "null":
                return TopoDS_Shape()
            if fault == "wrong_volume":
                return BRepPrimAPI_MakeBox(100, 60, 40).Shape()
            shape = self.prism.Shape()
            if fault == "reversed_solid":
                return shape.Reversed()
            if fault == "compound":
                compound = TopoDS_Compound()
                builder = BRep_Builder()
                builder.MakeCompound(compound)
                builder.Add(compound, shape)
                return compound
            return shape
    monkeypatch.setattr(BRepPrimAPI, "BRepPrimAPI_MakePrism", FaultyPrism)
    result = extrude_face(face, 20)
    assert not result.success and result.diagnostics[0].code == code
    assert result.shape is result.volume is result.translation is None
    assert BRepTools_ShapeSet.WriteToString(face, True) == before
