"""Real Qt camera transition, interruption and natural up, without visible windows."""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from cadastrophe.geometry import Vec3, WorkPlane, X, Y
from test_sketch_mode import system, camera_state
from test_sketch_drawing_ui import begin
from test_sketch_face_navigation_ui import navigation_move


def wait_transition(viewport):
    for _ in range(100):
        if viewport._camera_transition is None:
            break
        QTest.qWait(10)
    assert viewport._camera_transition is None


@pytest.fixture
def animation_clock(system, monkeypatch):
    # Observe native intermediate frames even if a loaded worker misses the
    # whole 220 ms interval before Qt delivers its first timer notification.
    elapsed = [0]
    class AnimationClock:
        def start(self):
            elapsed[0] = 0
        def elapsed(self):
            return elapsed[0]
    monkeypatch.setattr(system[0].viewport, "_camera_animation_clock", AnimationClock())
    return elapsed


@pytest.mark.parametrize("perspective", [False, True])
def test_entry_animates_from_existing_pose_to_natural_normal_and_exit_keeps_last_pose(system, perspective, animation_clock):
    window = system[0]
    vp = window.viewport
    vp.camera_animation_duration = 220
    elapsed = animation_clock
    if perspective:
        vp.view.Camera().SetProjectionType(vp.view.Camera().Projection_Perspective)
    previous = camera_state(vp)
    session = begin(system)
    assert camera_state(vp) == previous and vp._camera_animation_timer.isActive()
    elapsed[0] = 110
    for _ in range(20):
        if camera_state(vp) != previous:
            break
        QTest.qWait(10)
    middle = camera_state(vp)
    assert middle != previous and vp._camera_transition is not None
    elapsed[0] = 220
    wait_transition(vp)
    final = camera_state(vp)
    assert final != previous and final != middle
    assert vp.view.Camera().Direction().Coord() == pytest.approx((0, 0, -1))
    assert vp.view.Camera().Up().Coord() == pytest.approx((0, 1, 0))
    document = session.document
    count = document.history.count
    assert window.command_registry.execute("cadastrophe.sketch.finish")
    assert camera_state(vp) == final and document.history.count == count+1
    assert session.object_id not in document.objects
    QTest.qWait(250)
    assert camera_state(vp) == final and not vp._camera_animation_timer.isActive()


@pytest.mark.parametrize("interrupt", ["exit", "mouse"])
def test_exit_or_mouse_interrupts_animation_at_current_pose_without_a_late_jump(system, interrupt, animation_clock):
    window = system[0]
    vp = window.viewport
    vp.camera_animation_duration = 220
    begin(system)
    animation_clock[0] = 50
    QTest.qWait(50)
    assert vp._camera_transition is not None
    before = camera_state(vp)
    if interrupt == "exit":
        window.command_registry.execute("cadastrophe.sketch.finish")
        assert camera_state(vp) == before
    else:
        start = QPoint(vp.width()//2, vp.height()//2)
        QTest.mousePress(vp, Qt.MouseButton.MiddleButton, pos=start)
        assert camera_state(vp) == before
        navigation_move(vp, start+QPoint(30, 10))
        QTest.mouseRelease(vp, Qt.MouseButton.MiddleButton, pos=start+QPoint(30, 10))
        assert camera_state(vp) != before
    held = camera_state(vp)
    assert not vp._camera_animation_timer.isActive()
    QTest.qWait(250)
    assert camera_state(vp) == held


def test_natural_up_ignores_reversed_support_uv_and_keeps_tilted_faces_upright(system):
    camera = system[0].viewport.camera
    frames = (WorkPlane("Dessous", Vec3(0, 0, 0), X, Y*-1),
              WorkPlane("Face inversée", Vec3(10, 20, 30), X, Vec3(0, 0, -1)),
              WorkPlane("Oblique", Vec3(-20, 30, 70), Vec3(1, 2, 3), Vec3(-2, 1, 0)))
    for frame in frames:
        camera.normal_to(frame)
        view = camera.view.Camera()
        assert view.Direction().Coord() == pytest.approx((frame.normal*-1).tuple())
        up = Vec3(*view.Up().Coord())
        assert abs(up.dot(frame.normal)) < 1e-12
        assert up.y > .99 if abs(frame.normal.z) > .999999 else up.z > 0
