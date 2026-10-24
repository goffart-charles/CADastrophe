"""X2 exact faces: material classification, area, orientation and safe refusal."""
from dataclasses import replace
from math import pi
import subprocess
import sys
from uuid import uuid4

import pytest
from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepClass import BRepClass_FaceClassifier
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_IN, TopAbs_OUT, TopAbs_REVERSED, TopAbs_VERTEX, TopAbs_WIRE
from OCC.Core.TopExp import topexp
from OCC.Core.TopTools import TopTools_IndexedMapOfShape
from OCC.Core.TopoDS import topods
from OCC.Core.gp import gp_Pnt

from cadastrophe.geometry import REFERENCE_PLANES, WorkPlane, Vec3
from plugins.sketch.model import Arc, Circle, Point, Segment, Vec2, ExternalGeometry
from plugins.sketch.profiles import analyze
from plugins.sketch.profile_faces import to_faces
from plugins.sketch.profile_native import ProfileError
from test_sketch_model import sketch
from test_sketch_profiles import rectangle, slot, polygon, source


FRAMES = (*REFERENCE_PLANES,
          WorkPlane("Incliné déplacé", Vec3(123, -234, 456), Vec3(1, 2, 3), Vec3(-2, 1, 0)),
          WorkPlane("Retourné", Vec3(-100, 200, -50), Vec3(1, 0, 0), Vec3(0, -1, 0)))


def count(shape, kind):
    mapping = TopTools_IndexedMapOfShape()
    topexp.MapShapes(shape, kind, mapping)
    return mapping.Size()


def classify(region, frame, u, v):
    return BRepClass_FaceClassifier(region.face, gp_Pnt(*frame.to_world(u, v).tuple()), 1e-7).State()


@pytest.mark.parametrize("frame", FRAMES)
@pytest.mark.parametrize("kind", ["rectangle", "circle", "slot", "clockwise_arc", "rectangle_hole", "multiple_holes"])
def test_faces_have_exact_area_preserve_holes_world_frame_and_support_normal(frame, kind):
    if kind == "rectangle":
        curves, area, holes = rectangle(), 6000., 0
    elif kind == "circle":
        curves, area, holes = (Circle(Vec2(50, 30), 10),), 100*pi, 0
    elif kind == "slot":
        curves, area, holes = slot(), 800+100*pi, 0
    elif kind == "clockwise_arc":
        arc = Arc(Vec2(20, 30), 10, 5.8, -pi)
        curves, area, holes = (arc, Segment(arc.point_at(1), arc.point_at(0))), 50*pi, 0
    elif kind == "rectangle_hole":
        curves, area, holes = (*rectangle(), Circle(Vec2(50, 30), 10)), 6000-100*pi, 1
    else:
        curves, area, holes = (*rectangle(), Circle(Vec2(25, 30), 5), Circle(Vec2(75, 30), 5)), 6000-50*pi, 2
    s = replace(sketch(frame), entities=curves)
    output = to_faces(s)
    assert output.area == pytest.approx(area, rel=1e-8)
    assert len(output.faces) == len(output.regions) == 1
    region = output.regions[0]
    face = region.face
    assert face.ShapeType() == TopAbs_FACE and BRepCheck_Analyzer(face, True).IsValid()
    assert count(face, TopAbs_WIRE) == 1 + holes and len(region.hole_ids) == holes
    surface = BRepAdaptor_Surface(face, True)
    direction = Vec3(*surface.Plane().Axis().Direction().Coord())
    if face.Orientation() == TopAbs_REVERSED:
        direction = direction * -1
    assert direction.dot(frame.normal) == pytest.approx(1, abs=1e-12)
    vertices = TopTools_IndexedMapOfShape()
    topexp.MapShapes(face, TopAbs_VERTEX, vertices)
    for index in range(1, vertices.Size()+1):
        point = Vec3(*BRep_Tool.Pnt(topods.Vertex(vertices.FindKey(index))).Coord())
        assert frame.to_local(point)[2] == pytest.approx(0, abs=output.tolerance)
    if kind in ("rectangle_hole", "multiple_holes"):
        assert classify(region, frame, 5, 5) == TopAbs_IN
        for u in ((50,) if holes == 1 else (25, 75)):
            assert classify(region, frame, u, 30) == TopAbs_OUT
    assert s.entities == curves  # The parameters have not been healed/moved.


@pytest.mark.parametrize("frame", FRAMES)
def test_nested_holes_islands_and_disjoint_regions_remain_separate_faces(frame):
    circles = tuple(Circle(Vec2(0, 0), radius) for radius in (30, 20, 10, 5))
    extra = rectangle((100, 0), 40, 20)
    s = replace(sketch(frame), entities=(*circles, *extra))
    result = to_faces(s)
    assert len(result.regions) == 3
    assert sorted(len(r.hole_ids) for r in result.regions) == [0, 1, 1]
    assert result.area == pytest.approx(575*pi + 800)
    by_id = {r.id: r for r in result.regions}
    ring, island = by_id[circles[0].id], by_id[circles[2].id]
    assert ring.hole_ids == (circles[1].id,) and island.hole_ids == (circles[3].id,)
    assert classify(ring, frame, 25, 0) == TopAbs_IN
    assert classify(ring, frame, 15, 0) == TopAbs_OUT
    assert classify(island, frame, 7, 0) == TopAbs_IN
    assert classify(island, frame, 0, 0) == TopAbs_OUT


def test_concave_region_and_outside_island_do_not_use_bounding_box_nesting():
    outer = polygon(((0, 0), (100, 0), (100, 100), (70, 100), (70, 30), (30, 30), (30, 100), (0, 100)))
    inside, outside = Circle(Vec2(15, 60), 5), Circle(Vec2(50, 60), 5)
    result = to_faces(source((*outer, inside, outside)))
    assert len(result.regions) == 2 and result.area == pytest.approx(7200)
    region = next(r for r in result.regions if r.id != outside.id)
    assert region.hole_ids == (inside.id,)
    assert classify(region, REFERENCE_PLANES[0], 50, 60) == TopAbs_OUT


def test_selected_region_keeps_all_its_holes_and_stable_source_ids():
    outer, hole = rectangle(), Circle(Vec2(50, 30), 10)
    extra = Circle(Vec2(200, 0), 20)
    s = source((*outer, hole, extra))
    outer_id = min(e.id for e in outer)
    for order in (s.entities, tuple(reversed(s.entities))):
        result = to_faces(replace(s, entities=order), contour_ids=(outer_id,))
        assert len(result.regions) == 1 and result.area == pytest.approx(6000-100*pi)
        assert result.regions[0].id == outer_id and result.regions[0].hole_ids == (hole.id,)
    for selection in ((hole.id,), (str(uuid4()),), (outer_id, outer_id), "wire_0", (1,)):
        with pytest.raises(ProfileError) as error:
            to_faces(s, contour_ids=selection)
        assert error.value.analysis.diagnostics[-1].code == "region_selection"


@pytest.mark.parametrize("kind", ["open", "bowtie", "crossing", "tangent", "duplicate", "tiny", "point_only", "conflict"])
def test_invalid_profiles_are_rejected_with_source_diagnostics(kind):
    if kind == "open":
        s = source(rectangle()[:-1])
    elif kind == "bowtie":
        s = source(polygon(((0, 0), (40, 40), (0, 40), (40, 0))))
    elif kind == "crossing":
        s = source((Circle(Vec2(0, 0), 10), Circle(Vec2(10, 0), 10)))
    elif kind == "tangent":
        s = source((*rectangle(), Circle(Vec2(10, 30), 10)))
    elif kind == "duplicate":
        rect = rectangle()
        s = source((*rect, Segment(rect[0].end, rect[0].start)))
    elif kind == "tiny":
        s = source((Circle(Vec2(0, 0), 1e-10),))
    elif kind == "point_only":
        s = source((Point(Vec2(0, 0)),))
    else:
        from plugins.sketch.constraints import Constraint, Reference
        s = source(rectangle())
        s = replace(s, constraints=(Constraint("length", (Reference(s.entities[0].id),), 150),))
    with pytest.raises(ProfileError) as error:
        to_faces(s)
    assert error.value.analysis is not None and not error.value.analysis.valid


def test_stale_analysis_points_and_external_geometry_are_handled_without_modification():
    s = source(rectangle())
    report = analyze(s)
    other = replace(s, entities=(*s.entities, Circle(Vec2(50, 30), 10)))
    with pytest.raises(ProfileError, match="périmée"):
        to_faces(other, report)
    point = Point(Vec2(3, 4))
    external = ExternalGeometry(str(uuid4()), 0, "Edge", 1, Circle(Vec2(300, 300), 10))
    enriched = replace(s, entities=(*s.entities, point), external=(external,))
    assert to_faces(enriched).area == pytest.approx(6000)
    assert enriched.external == (external,) and enriched.entities[-1] is point


@pytest.mark.parametrize("origin", [(1e9, -1e9), (-500, 370)])
@pytest.mark.parametrize("scale", [1e-3, 1, 1000])
def test_precision_and_scale_in_local_coordinates(origin, scale):
    s = source(rectangle(origin, 100*scale, 60*scale))
    result = to_faces(s)
    assert result.area == pytest.approx(6000*scale**2, rel=1e-5)


def test_world_coordinate_precision_is_explicit_and_cannot_hide_degenerate_geometry():
    frame = WorkPlane("Très déplacé", Vec3(1e9, -1e9, 1e9), Vec3(1, 0, 0), Vec3(0, 1, 0))
    s = replace(sketch(frame), entities=rectangle())
    result = to_faces(s)
    assert result.tolerance > 1e-7 and result.area == pytest.approx(6000)
    lost = replace(sketch(replace(frame, origin=Vec3(1e16, 0, 0))), entities=(Circle(Vec2(0, 0), 1),))
    with pytest.raises(ProfileError, match="précision"):
        to_faces(lost)


@pytest.mark.parametrize("gap,accepted", [(1e-8, True), (1e-5, False)])
def test_endpoint_tolerance_is_explicit_and_does_not_move_source_points(gap, accepted):
    edges = rectangle()
    edges = (*edges[:-1], replace(edges[-1], end=Vec2(0, gap)))
    s = source(edges)
    if accepted:
        assert to_faces(s).area == pytest.approx(6000, abs=1e-5)
    else:
        with pytest.raises(ProfileError):
            to_faces(s)
    assert s.entities[-1].end == Vec2(0, gap)


def test_an_open_component_cannot_be_silently_ignored_by_region_selection():
    circle = Circle(Vec2(0, 0), 10)
    s = source((circle, Segment(Vec2(100, 0), Vec2(110, 0))))
    with pytest.raises(ProfileError, match="ouvert"):
        to_faces(s, contour_ids=(circle.id,))


def test_faces_module_can_be_imported_without_qt_or_occt():
    subprocess.run([sys.executable, "-c", "import sys; import plugins.sketch.profile_faces; "
                    "assert not any(n.startswith(('PySide6','OCC')) for n in sys.modules)"], check=True)


@pytest.mark.parametrize("fault,code", [("not_done", "native_face"), ("lost_hole", "missing_hole"),
                                        ("wrong_area", "face_area"), ("reversed", "face_orientation"),
                                        ("off_plane", "non_planar"), ("exception", "native_face")])
def test_native_failures_return_diagnostics_instead_of_partial_faces(monkeypatch, fault, code):
    from OCC.Core import BRepBuilderAPI
    from OCC.Core.TopoDS import topods
    frame = REFERENCE_PLANES[0]
    curves = (Circle(Vec2(0, 0), 20), Circle(Vec2(0, 0), 10)) if fault == "lost_hole" else (Circle(Vec2(0, 0), 10),)
    s = source(curves)
    replacement = None
    if fault == "wrong_area":
        replacement = to_faces(source((Circle(Vec2(0, 0), 1),))).faces[0]
    elif fault == "off_plane":
        replacement = to_faces(replace(s, support=replace(s.support, frame=replace(frame, origin=Vec3(0, 0, 5))))).faces[0]
    actual = BRepBuilderAPI.BRepBuilderAPI_MakeFace
    class FaultyMaker:
        def __init__(self, *args):
            self.maker = actual(*args)
        def IsDone(self):
            return fault != "not_done" and self.maker.IsDone()
        def Error(self):
            return self.maker.Error()
        def Add(self, wire):
            if fault != "lost_hole":
                self.maker.Add(wire)
        def Face(self):
            if fault == "exception":
                raise RuntimeError("échec injecté")
            if replacement is not None:
                return replacement
            face = self.maker.Face()
            return topods.Face(face.Reversed()) if fault == "reversed" else face
    monkeypatch.setattr(BRepBuilderAPI, "BRepBuilderAPI_MakeFace", FaultyMaker)
    before = s
    with pytest.raises(ProfileError) as error:
        to_faces(s)
    assert error.value.analysis.diagnostics[-1].code == code
    assert error.value.analysis.diagnostics[-1].entity_ids and s == before
