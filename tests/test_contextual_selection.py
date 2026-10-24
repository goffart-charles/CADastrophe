"""Simultaneous native AIS picking, never switching modes between gestures."""
import pytest
from OCC.Display.OCCViewer import Viewer3d
from OCC.Core.AIS import AIS_Shape
from OCC.Core import TopAbs
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.gp import gp_Pnt
from OCC.Core.StdSelect import StdSelect_BRepOwner
from cadastrophe.camera import CameraController
from cadastrophe.document import Document, SceneObject
from cadastrophe.selection import SelectionController


@pytest.fixture(scope="module")
def display():
    viewer = Viewer3d()
    viewer.Create(display_glinfo=False)
    viewer.View.SetImmediateUpdate(False)
    yield viewer
    viewer.Context.RemoveAll(False)
    viewer.View.Remove()


@pytest.fixture
def picker(display):
    display.Context.RemoveAll(False)
    document = Document()
    obj = SceneObject("Cube", BRepPrimAPI_MakeBox(40, 40, 40).Shape(), (.6, .7, .8))
    document.add(obj)
    ais = AIS_Shape(obj.shape)
    display.Context.Display(ais, False)
    selection = SelectionController(display.Context, display.View, document, {obj.id: ais}, lambda items: None, lambda: None)
    selection.configure_object(obj.id)
    camera = CameraController(display.View, lambda: None)
    camera.standard("Dessus")
    camera.fit()
    display.View.Redraw()
    yield selection
    selection.detach()


@pytest.mark.parametrize("point,topology", [((20, 20, 40), TopAbs.TopAbs_FACE),
    ((20, 0, 40), TopAbs.TopAbs_EDGE), ((0, 0, 40), TopAbs.TopAbs_VERTEX)])
def test_automatic_native_priorities(picker, point, topology):
    picker.click(*picker.view.Convert(*point))
    item, = picker.items()
    assert item.shape.ShapeType() == topology
    assert item.topology == {TopAbs.TopAbs_FACE: "Face", TopAbs.TopAbs_EDGE: "Arête", TopAbs.TopAbs_VERTEX: "Sommet"}[topology]


@pytest.mark.parametrize("point,offset,expected", [
    ((20, 0, 40), (0, -2), "Arête"), ((0, 0, 40), (2, -2), "Sommet"),
    ((20, 0, 40), (0, -12), "Face"), ((0, 0, 40), (12, -12), "Face")])
def test_automatic_screen_proximity_priorities(picker, point, offset, expected):
    x, y = picker.view.Convert(*point)
    picker.click(x + offset[0], y + offset[1])
    item, = picker.items()
    assert item.topology == expected


def test_automatic_tree_object_can_be_replaced_by_face_and_ctrl_toggles_edges(picker):
    obj = next(iter(picker.document.objects.values()))
    picker.from_tree([obj.id])
    item, = picker.items()
    assert item.topology == "Objet" and item.shape.IsSame(obj.shape)
    picker.click(*picker.view.Convert(20., 20., 40.))
    face, = picker.items()
    assert face.topology == "Face"
    edge_pixel = picker.view.Convert(20., 0., 40.)
    picker.click(*edge_pixel, ctrl=True)
    assert {item.topology for item in picker.items()} == {"Face", "Arête"}
    picker.click(*edge_pixel, ctrl=True)
    remaining, = picker.items()
    assert remaining.shape.IsSame(face.shape)
    picker.from_tree([obj.id])
    assert picker.items()[0].topology == "Objet"
    picker.click(-100, -100)
    assert picker.items() == [] and picker.document.selected_ids == ()


def test_automatic_hover_and_click_resolve_same_topological_owner(picker):
    for point in ((20., 20., 40.), (20., 0., 40.), (0., 0., 40.)):
        pixel = picker.view.Convert(*point)
        picker.hover(*pixel)
        detected = StdSelect_BRepOwner.DownCast(picker.context.DetectedOwner()).Shape()
        picker.click(*pixel)
        assert picker.items()[0].shape.IsSame(detected)
        picker.leave()
        assert not picker.context.HasDetected()
        assert picker.items()[0].shape.IsSame(detected)


@pytest.mark.parametrize("kind", ("face", "edge"))
def test_hover_respects_foreground_occlusion_in_native_render(picker, tmp_path, kind):
    from PySide6.QtGui import QImage
    from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
    front = SceneObject("Plaque devant", BRepPrimAPI_MakeBox(gp_Pnt(20, 10, 45), 30, 20, 5).Shape(), (.7, .2, .3))
    picker.document.add(front)
    ais = AIS_Shape(front.shape)
    picker.presentations[front.id] = ais
    picker.context.Display(ais, False)
    picker.configure_object(front.id)
    for item in picker.presentations.values():
        picker.context.SetDisplayMode(item, 1, False)
    picker.context.SetColor(ais, Quantity_Color(*front.color, Quantity_TOC_RGB), False)
    picker.view.Redraw()
    before = tmp_path/"before.png"
    after = tmp_path/"hover.png"
    assert picker.view.Dump(str(before))
    visible = (10., 20., 40.) if kind == "face" else (40., 5., 40.)
    covered = (40., 20., 50.)
    picker.hover(*picker.view.Convert(*visible))
    assert picker.context.HasDetected()
    assert picker.object_id(picker.context.DetectedInteractive()) != front.id
    assert picker.view.Dump(str(after))
    a, b = QImage(str(before)), QImage(str(after))
    def pixel(image, point):
        x, y = picker.view.Convert(*point)
        color = image.pixelColor(x, y)
        return color.red(), color.green(), color.blue()
    assert pixel(a, covered) == pytest.approx(pixel(b, covered), abs=2)
    uncovered = (0., 20., 40.) if kind == "face" else visible
    assert pixel(a, uncovered) != pixel(b, uncovered)


@pytest.mark.parametrize("ratio", [1., 1.5, 2.])
def test_automatic_aperture_is_in_logical_pixels(picker, ratio):
    picker.set_pixel_ratio(ratio)
    assert picker.context.PixelTolerance() == round(12 * ratio)
    x, y = picker.view.Convert(20., 0., 40.)
    picker.click(x, y - round(3 * ratio))
    assert picker.items()[0].topology == "Arête"
    picker.click(x, y - round(9 * ratio))
    assert picker.items()[0].topology == "Face"


def test_automatic_hidden_vertex_does_not_win_over_occluding_face(picker):
    # Rear cube corner projects into the interior of the front plate.
    front = SceneObject("Plaque", BRepPrimAPI_MakeBox(gp_Pnt(-10, -10, 60), 60, 60, 10).Shape(), (.8, .7, .6))
    picker.document.add(front)
    ais = AIS_Shape(front.shape)
    picker.presentations[front.id] = ais
    picker.context.Display(ais, False)
    picker.configure_object(front.id)
    picker.view.Redraw()
    picker.click(*picker.view.Convert(0., 0., 40.))
    item, = picker.items()
    assert item.object_id == front.id and item.topology == "Face"


def test_automatic_edge_aperture_survives_cursor_zoom_and_pan(picker):
    pixel = picker.view.Convert(20., 0., 40.)
    picker.view.StartZoomAtPoint(*pixel)
    picker.view.ZoomAtPoint(0, 0, 0, 100)
    picker.view.Pan(0, 40, 1., True)
    picker.view.Redraw()
    x, y = picker.view.Convert(20., 0., 40.)
    picker.click(x, y - 3)
    assert picker.items()[0].topology == "Arête"
    picker.click(x, y - 12)
    assert picker.items()[0].topology == "Face"
