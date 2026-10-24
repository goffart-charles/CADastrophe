"""Native camera integration tests in one real OCCT offscreen OpenGL context."""
from math import cos, sin, pi

import pytest
from OCC.Display.OCCViewer import Viewer3d
from OCC.Core.gp import gp_Dir, gp_Pnt

from cadastrophe.camera import CameraController
from cadastrophe.geometry import Vec3, WorkPlane, REFERENCE_PLANES, X, Y


@pytest.fixture(scope="module")
def native_view():
    display = Viewer3d()
    display.Create(display_glinfo=False)
    display.View.SetImmediateUpdate(False)
    yield display.View
    display.View.Remove()


@pytest.fixture
def controller(native_view):
    native_view.Reset(False)
    controller = CameraController(native_view, lambda: None)
    controller.standard("Face")
    controller.set_scene_pivot(Vec3(*native_view.Camera().Center().Coord()))
    return controller


def pose(controller):
    cam = controller.view.Camera()
    return (*cam.Eye().Coord(), *cam.Center().Coord(), *cam.Up().Coord(), cam.Scale())


def test_start_and_zero_displacement_do_not_move_camera(controller):
    before = pose(controller)
    controller.start_rotation(5, 5)
    controller.rotate(5, 5)
    assert pose(controller) == before
    controller.start_roll(500, 300)
    controller.roll(500, 300)
    assert pose(controller) == before


def test_can_turn_upside_down_and_continue(controller):
    cam = controller.view.Camera()
    initial_up = cam.Up()
    controller.start_rotation(400, 300)
    span = min(controller.view.Window().Size())
    for step in range(1, 61):
        controller.rotate(400, 300 + round(span * step / 60))
    assert cam.Up().Dot(initial_up) == pytest.approx(-1, abs=1e-10)
    controller.start_rotation(400, 300)
    before = pose(controller)
    controller.rotate(430, 320)
    assert pose(controller) != before
    assert abs(cam.Up().Dot(cam.Direction())) < 1e-12


def test_world_yaw_preserves_45_degree_roll_without_realigning_up(controller):
    cam = controller.view.Camera()
    cx, cy = controller.view.Convert(*controller.pivot.tuple())
    controller.start_roll(cx + 100, cy)
    controller.roll(cx + 100, cy - 100)
    up, direction = cam.Up(), cam.Direction()
    before = pose(controller)
    controller.start_rotation(400, 300)
    assert pose(controller) == before
    controller.rotate(460, 300)
    angle = -60 * pi / min(controller.view.Window().Size())
    assert cam.Up().Coord() == pytest.approx(rodrigues(up.Coord(), (0, 0, 1), angle), abs=1e-12)
    assert abs(cam.Up().Dot(up)) < 0.999
    assert cam.Direction().Dot(direction) < 0.999


def test_360_roll_preserves_projection_pivot_zoom_and_distance(controller):
    cam = controller.view.Camera()
    controller.set_pivot(controller.pivot + Vec3(5, 3, 2))
    pivot = gp_Pnt(*controller.pivot.tuple())
    projected = cam.Project(pivot).Coord()
    before, distance = pose(controller), cam.Distance()
    cx, cy = controller.view.Convert(*controller.pivot.tuple())
    controller.start_roll(cx + 200, cy)
    for step in range(1, 73):
        angle = step * pi / 36
        controller.roll(cx + round(200 * cos(angle)), cy - round(200 * sin(angle)))
        assert cam.Project(pivot).Coord() == pytest.approx(projected, abs=1e-11)
        assert cam.Distance() == pytest.approx(distance, rel=1e-12)
        assert cam.Scale() == before[-1]
    assert pose(controller) == pytest.approx(before, abs=1e-10)


def test_center_roll_fallback_and_crossing_have_no_singularity(controller):
    cx, cy = controller.view.Convert(*controller.pivot.tuple())
    controller.start_roll(cx, cy)
    controller.roll(cx + 100, cy)
    assert abs(controller.view.Camera().Up().X()) > 0.1
    controller.start_roll(cx + 100, cy)
    before = pose(controller)
    controller.roll(cx - 100, cy)
    assert pose(controller) == before


def test_pivot_latched_during_drag(controller):
    controller.set_pivot(Vec3(4, 8, 3))
    fixed = gp_Pnt(*controller.pivot.tuple())
    cam = controller.view.Camera()
    projection = cam.Project(fixed).Coord()
    controller.start_rotation(400, 300)
    controller.set_pivot(Vec3(-3, 2, 0))
    for step in range(1, 51):
        controller.rotate(400 + step * 3, 300 + step * 4)
    assert cam.Project(fixed).Coord() == pytest.approx(projection, abs=1e-10)
    assert abs(cam.Up().Dot(cam.Direction())) < 1e-12


def test_several_axes_compose_without_replaying_previous_drag(controller):
    cam = controller.view.Camera()
    for dx, dy in ((80, 0), (0, 160), (-120, -90), (50, 80)) * 20:
        before = pose(controller)
        controller.start_rotation(400, 300)
        assert pose(controller) == before
        controller.rotate(400 + dx, 300 + dy)
    assert abs(cam.Up().Dot(cam.Direction())) < 1e-12
    controller.standard("Isométrique")
    assert cam.Direction().Coord() == pytest.approx((-1 / 3**0.5, 1 / 3**0.5, -1 / 3**0.5))


def rodrigues(vector, axis, angle):
    """Independent oracle: axis/angle rotation without OCCT quaternion code."""
    v, n = Vec3(*vector), Vec3(*axis).normalized()
    return (v * cos(angle) + n.cross(v) * sin(angle) + n * (n.dot(v) * (1 - cos(angle)))).tuple()


@pytest.mark.parametrize("right", [(1, 0, 0), (0.707, 0.707, 0), (1, 2, 3)])
@pytest.mark.parametrize("sign", [-1, 1])
def test_horizontal_quaternion_has_only_world_z_component(right, sign):
    angle = -200 * pi / 600 * sign
    q = CameraController.orbit_increment(200, 0, gp_Dir(*right), pi / 600, sign)
    assert (q.X(), q.Y(), q.Z(), q.W()) == pytest.approx((0, 0, sin(angle / 2), cos(angle / 2)), abs=1e-14)


@pytest.mark.parametrize("right", [(1, 0, 0), (0.707, 0.707, 0), (1, 2, 3)])
def test_vertical_quaternion_has_only_local_right_component(right):
    axis = Vec3(*right).normalized()
    angle = -200 * pi / 600
    q = CameraController.orbit_increment(0, 200, gp_Dir(*right), pi / 600, -1)
    assert (q.X(), q.Y(), q.Z()) == pytest.approx((axis * sin(angle / 2)).tuple(), abs=1e-14)
    assert q.W() == pytest.approx(cos(angle / 2), abs=1e-14)


@pytest.mark.parametrize("dx,dy", [(200, 0), (0, 200)])
def test_isometric_200px_is_a_single_axis_rotation(controller, dx, dy):
    controller.standard("Isométrique")
    cam = controller.view.Camera()
    up, direction = cam.Up().Coord(), cam.Direction().Coord()
    right = Vec3(*direction).cross(Vec3(*up)).normalized()
    axis = (0, 0, 1) if dx else right.tuple()
    angle = -(dx or dy) * pi / min(controller.view.Window().Size())
    zoom, distance = cam.Scale(), cam.Eye().Distance(gp_Pnt(*controller.pivot.tuple()))
    controller.start_rotation(200, 150)
    controller.rotate(200 + dx, 150 + dy)
    assert cam.Up().Coord() == pytest.approx(rodrigues(up, axis, angle), abs=1e-12)
    assert cam.Direction().Coord() == pytest.approx(rodrigues(direction, axis, angle), abs=1e-12)
    assert cam.Scale() == zoom
    assert cam.Eye().Distance(gp_Pnt(*controller.pivot.tuple())) == pytest.approx(distance, rel=1e-12)


def test_diagonal_sampling_and_horizontal_then_vertical_do_not_add_twist(controller):
    from OCC.Core.Graphic3d import Graphic3d_Camera
    controller.standard("Isométrique")
    cam = controller.view.Camera()
    initial = Graphic3d_Camera(cam)
    controller.start_rotation(200, 100)
    controller.rotate(400, 300)
    diagonal = pose(controller)
    cam.Copy(initial)
    controller.start_rotation(200, 100)
    for step in range(1, 101):
        controller.rotate(200 + 2 * step, 100 + 2 * step)
    assert pose(controller) == pytest.approx(diagonal, abs=1e-10)
    cam.Copy(initial)
    controller.start_rotation(200, 100)
    controller.rotate(400, 100)
    controller.rotate(400, 300)
    assert pose(controller) == pytest.approx(diagonal, abs=1e-10)


def test_many_world_yaw_turns_remain_stable(controller):
    controller.standard("Isométrique")
    cam = controller.view.Camera()
    before = pose(controller)
    span = min(controller.view.Window().Size())
    controller.start_rotation(200, 100)
    for step in range(1, 1001):
        controller.rotate(200 + round(10 * span * step / 1000), 100)
    # Ten spans are five full revolutions, without normalization to world Up.
    assert pose(controller) == pytest.approx(before, abs=1e-9)
    assert abs(cam.Up().Dot(cam.Direction())) < 1e-12


def test_inverted_yaw_sign_is_latched_until_next_gesture(controller):
    cam = controller.view.Camera()
    span = min(controller.view.Window().Size())
    controller.start_rotation(200, 100)
    controller.rotate(200, 100 + span)
    assert cam.Up().Z() < -0.99
    assert controller._yaw_sign == 1  # no mid-gesture sign change at the poles
    controller.end_rotation()
    before = cam.Direction().Coord()
    controller.start_rotation(200, 100)
    assert controller._yaw_sign == -1
    controller.rotate(400, 100)
    assert cam.Direction().Coord() == pytest.approx(rodrigues(before, (0, 0, 1), 200 * pi / span), abs=1e-12)


def test_pitch_after_roll_keeps_the_rolled_right_axis(controller):
    controller.standard("Isométrique")
    cx, cy = controller.view.Convert(*controller.pivot.tuple())
    controller.start_roll(cx + 100, cy)
    controller.roll(cx + 100, cy - 100)
    cam = controller.view.Camera()
    right = cam.Direction().Crossed(cam.Up()).Coord()
    before_up, before_direction = cam.Up().Coord(), cam.Direction().Coord()
    controller.start_rotation(200, 100)
    controller.rotate(200, 300)
    angle = -200 * pi / min(controller.view.Window().Size())
    assert cam.Up().Coord() == pytest.approx(rodrigues(before_up, right, angle), abs=1e-12)
    assert cam.Direction().Coord() == pytest.approx(rodrigues(before_direction, right, angle), abs=1e-12)


def test_temporary_pivot_is_latched_and_does_not_replace_fallback(controller):
    fallback = controller.pivot
    point = Vec3(14, -8, 3)
    before = pose(controller)
    controller.start_rotation(200, 100, pivot=point)
    assert pose(controller) == before
    assert controller.pivot == fallback
    controller.set_scene_pivot(Vec3(0, 3, 5))
    controller.rotate(260, 130)
    assert controller._gesture_pivot == point
    before_release = pose(controller)
    controller.end_rotation()
    assert pose(controller) == before_release
    assert controller._gesture_pivot is None
    controller.start_rotation(200, 100)
    assert controller._gesture_pivot == Vec3(0, 3, 5)


def test_changing_only_pivot_preserves_arcball_orientation_and_sensitivity(controller):
    from OCC.Core.Graphic3d import Graphic3d_Camera
    controller.standard("Isométrique")
    cam = controller.view.Camera()
    initial = Graphic3d_Camera(cam)
    orientations = []
    for point in (Vec3(-50, -25, 40), Vec3(70, 20, 4)):
        cam.Copy(initial)
        before = pose(controller)
        projection = cam.Project(gp_Pnt(*point.tuple())).Coord()
        distance = cam.Eye().Distance(gp_Pnt(*point.tuple()))
        controller.start_rotation(200, 100, pivot=point)
        assert pose(controller) == before
        controller.rotate(400, 300)
        orientations.append((*cam.Up().Coord(), *cam.Direction().Coord(), cam.Scale()))
        assert cam.Project(gp_Pnt(*point.tuple())).Coord() == pytest.approx(projection, abs=1e-10)
        assert cam.Eye().Distance(gp_Pnt(*point.tuple())) == pytest.approx(distance, rel=1e-12)
        controller.end_rotation()
    assert orientations[0] == pytest.approx(orientations[1], abs=1e-12)


def test_normal_alignment_sets_eye_and_center_together_even_for_equidistant_support(controller):
    camera = controller.view.Camera()
    camera.SetEyeAndCenter(gp_Pnt(10, 0, 100), gp_Pnt(10, 0, 0))
    camera.SetUp(gp_Dir(0, 1, 0))
    # This different center is exactly 100 mm from the eye: OCCT SetCenter's
    # distance-only early exit used to ignore it in the old setter sequence.
    plane = WorkPlane("Décalé", Vec3(110, 0, 100), X, Y)
    controller.normal_to(plane)
    assert camera.Center().Coord() == pytest.approx(plane.origin.tuple())
    assert camera.Eye().Coord() == pytest.approx((110, 0, 200))
    assert camera.Direction().Coord() == pytest.approx((0, 0, -1))
    assert camera.Up().Coord() == pytest.approx((0, 1, 0))
    assert camera.Project(gp_Pnt(*plane.origin.tuple())).Coord()[:2] == pytest.approx((0, 0), abs=1e-12)


SKETCH_FRAMES = (*REFERENCE_PLANES,
    WorkPlane("Oblique", Vec3(120, -50, 80), Vec3(1, 2, 3), Vec3(-2, 1, 0)))


@pytest.mark.parametrize("frame", SKETCH_FRAMES)
@pytest.mark.parametrize("delta", [(100, 0), (0, 100)])
def test_screen_orbit_tilts_both_axes_from_each_normal_plane_and_keeps_pivot_zoom(controller, frame, delta):
    controller.normal_to(frame)
    camera = controller.view.Camera()
    assert camera.Direction().Coord() == pytest.approx((frame.normal*-1).tuple())
    natural_up = controller.natural_up(frame)
    assert camera.Up().Coord() == pytest.approx(natural_up.tuple())
    origin = camera.Project(gp_Pnt(*frame.origin.tuple()))
    natural_right = natural_up.cross(frame.normal)
    u = camera.Project(gp_Pnt(*(frame.origin+natural_right*10).tuple()))
    v = camera.Project(gp_Pnt(*(frame.origin+natural_up*10).tuple()))
    assert u.X() > origin.X() and u.Y() == pytest.approx(origin.Y(), abs=1e-12)
    assert v.Y() > origin.Y() and v.X() == pytest.approx(origin.X(), abs=1e-12)
    up, direction = camera.Up().Coord(), camera.Direction().Coord()
    right = camera.Direction().Crossed(camera.Up()).Coord()
    pivot = frame.to_world(7, -3)
    projected = camera.Project(gp_Pnt(*pivot.tuple())).Coord()
    distance, scale = camera.Eye().Distance(gp_Pnt(*pivot.tuple())), camera.Scale()
    before = pose(controller)
    controller.start_rotation(200, 150, pivot=pivot, screen_axes=True)
    assert pose(controller) == before
    dx, dy = delta
    controller.rotate(200+dx, 150+dy)
    axis = up if dx else right
    angle = -100*pi/min(controller.view.Window().Size())
    assert camera.Direction().Coord() == pytest.approx(rodrigues(direction, axis, angle), abs=1e-12)
    assert camera.Direction().Dot(gp_Dir(*direction)) < .999
    assert camera.Project(gp_Pnt(*pivot.tuple())).Coord() == pytest.approx(projected, abs=1e-10)
    assert camera.Eye().Distance(gp_Pnt(*pivot.tuple())) == pytest.approx(distance, rel=1e-12)
    assert camera.Scale() == scale
    controller.end_rotation()
    assert not controller._screen_orbit
