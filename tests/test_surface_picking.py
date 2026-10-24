"""Exact OCCT ray/B-Rep picking, isolated from AIS selection state."""
from math import sqrt

import pytest
from OCC.Core.AIS import AIS_Shape
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.gp import gp_Pnt
from OCC.Display.OCCViewer import Viewer3d

from cadastrophe.camera import CameraController
from cadastrophe.document import SceneObject
from cadastrophe.geometry import Ray, Vec3
from cadastrophe.sample_scene import demo_document
from cadastrophe.selection import SelectionController


@pytest.fixture(scope="module")
def display():
    display = Viewer3d()
    display.Create(display_glinfo=False)
    display.View.SetImmediateUpdate(False)
    yield display
    display.View.Remove()


@pytest.fixture
def picker(display):
    display.Context.RemoveAll(False)
    document = demo_document()
    presentations = {}
    for obj in document.objects.values():
        ais = AIS_Shape(obj.shape)
        presentations[obj.id] = ais
        display.Context.Display(ais, False)
        if not obj.visible:
            display.Context.Erase(ais, False)
    selection = SelectionController(display.Context, display.View, document,
                                    presentations, lambda items: None, lambda: None)
    for key in presentations:
        selection.configure_object(key)
    camera = CameraController(display.View, lambda: None)
    camera.standard("Isométrique")
    camera.fit()
    display.View.Redraw()
    return selection


@pytest.mark.parametrize("x,y", [(-55, -35), (-74.999, -54.999), (-37, -25)])
def test_actual_face_point_including_near_boundary(picker, x, y):
    point = picker.pick_surface(Ray(Vec3(x, y, 100), Vec3(0, 0, -1)))
    assert point is not None
    assert point.tuple() == pytest.approx((x, y, 40), abs=1e-8)


def test_curved_surface_is_exact_not_a_mesh_or_face_center(picker):
    point = picker.pick_surface(Ray(Vec3(30, -22, 25), Vec3(-1, 0, 0)))
    assert point is not None
    assert point.tuple() == pytest.approx((sqrt(22**2 - 13**2), -22, 25), abs=1e-8)


def test_nearest_surface_wins_regardless_of_document_order(picker):
    shape = BRepPrimAPI_MakeBox(gp_Pnt(-70, -50, 60), 20, 20, 10).Shape()
    picker.document.add(SceneObject("Occluder", shape, (1, 0, 0)))
    point = picker.pick_surface(Ray(Vec3(-55, -35, 100), Vec3(0, 0, -1)))
    assert point is not None and point.z == pytest.approx(70)


def test_empty_space_through_hole_and_hidden_geometry(picker):
    assert picker.pick_surface(Ray(Vec3(67, -34, 100), Vec3(0, 0, -1))) is None
    cube = next(obj for obj in picker.document.objects.values() if obj.name == "Cube")
    picker.document.set_visible(cube.id, False)
    assert picker.pick_surface(Ray(Vec3(-55, -35, 100), Vec3(0, 0, -1))) is None
    for obj in picker.document.objects.values():
        if obj.plane:
            picker.document.set_visible(obj.id, True)
    assert picker.pick_surface(Ray(Vec3(100, 80, 100), Vec3(0, 0, -1))) is None


@pytest.mark.parametrize("source", ["tree", "face", "edge", "vertex"])
def test_contextual_selection_and_preselection_are_not_changed_by_pivot_query(picker, source):
    cube = next(obj for obj in picker.document.objects.values() if obj.name == "Cube")
    picker.from_tree([cube.id])
    if source != "tree":
        point = {"face": (-55., -35., 40.), "edge": (-55., -55., 40.), "vertex": (-75., -55., 40.)}[source]
        picker.click(*picker.view.Convert(*point))
    x, y = picker.view.Convert(-55., -35., 40.)
    picker.hover(x, y)
    detected = picker.context.HasDetected()
    owner = picker.context.DetectedOwner() if detected else None
    before = [(item.object_id, item.topology) for item in picker.items()]
    point = picker.pick_surface(Ray(Vec3(-55, -35, 100), Vec3(0, 0, -1)))
    assert point == Vec3(-55, -35, 40)
    assert [(item.object_id, item.topology) for item in picker.items()] == before
    assert picker.context.HasDetected() == detected
    if detected:
        assert picker.context.DetectedOwner() == owner
