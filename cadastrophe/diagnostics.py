"""Reproducible binding and real OpenGL smoke checks (not a usability claim)."""
import json
import logging
from pathlib import Path


def run_smoke():
    import sys
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from .app import MainWindow
    from .logging_setup import DiagnosticSession
    path = Path(__file__).resolve().parent.parent/'artifacts/smoke.log'
    with DiagnosticSession(path) as diagnostics:
        app = QApplication(sys.argv)
        app.setApplicationName('CADastrophe')
        window = MainWindow()
        diagnostics.notify = window.services.report_problem
        window.show()
        QTimer.singleShot(1500, lambda: smoke_test(app, window))
        return app.exec()


def probe():
    import sys
    import OCC
    import PySide6
    from OCC.Core.AIS import AIS_ViewCube, AIS_ViewCubeOwner, AIS_ViewController
    from OCC.Core.V3d import V3d_View
    versions = {}
    for name in ("pythonocc-core", "occt"):
        for path in (Path(sys.prefix) / "conda-meta").glob(f"{name}-*.json"):
            versions[name] = json.loads(path.read_text())["version"]
    print(json.dumps({"python": sys.version, "pythonocc_module_version": OCC.VERSION, "packages": versions,
        "PySide6": PySide6.__version__, "ViewCube": hasattr(AIS_ViewCube, "HandleClick"),
        "specific_owner_downcast": "DownCast" in AIS_ViewCubeOwner.__dict__,
        "orbit_z_lock_api": hasattr(AIS_ViewController, "SetLockOrbitZUp"),
        "cursor_zoom": hasattr(V3d_View, "ZoomAtPoint"),
        "screen_ray": hasattr(V3d_View, "ConvertWithProj")}, indent=2))


def smoke_test(app, window):
    # The scripted scenario owns temporary edits; real discard dialogs are
    # exercised separately by the document-lifecycle integration tests.
    window.confirm_discard = lambda document: True
    try:
        from .sample_scene import demo_document
        viewport = window.viewport
        assert viewport.camera and not viewport._failed
        assert len(viewport.document.objects) == 3
        assert all(obj.plane and obj.visible for obj in viewport.document.objects.values())
        viewport.load_document(demo_document())
        assert len(viewport.document.objects) == 7
        assert viewport.view.RenderingParams().NbMsaaSamples == 4
        print(f"Qt device pixel ratio: {viewport.devicePixelRatioF()}", flush=True)
        viewport.camera.fit()
        for name in ("Face", "Arrière", "Gauche", "Droite", "Dessus", "Dessous", "Isométrique"):
            viewport.camera.standard(name)
        viewport.camera.start_rotation(400, 300)
        viewport.camera.rotate(430, 315)
        viewport.camera.pan(12, -8)
        viewport.camera.zoom(400, 300, 1)
        viewport.camera.zoom(400, 300, -1)
        verify_camera(viewport)
        verify_dynamic_pivot(app, window)
        verify_navigation_input(app, window)
        for mode in ("Filaire", "Ombré", "Ombré avec arêtes"):
            viewport.set_render_mode(mode)
        objects = list(viewport.document.objects.values())
        viewport.selection.from_tree([objects[0].id, objects[1].id])
        assert len(viewport.selection.items()) == 2
        viewport.set_visible(objects[0].id, False)
        assert [item.object_id for item in viewport.selection.items()] == [objects[1].id]
        viewport.set_visible(objects[0].id, True)
        viewport.selection.from_tree([])
        verify_picking(app, window, objects)
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        for obj in objects:
            if obj.plane:
                viewport.set_visible(obj.id, True)
                viewport.enter_plane(obj.plane)
                x, y = viewport.pixel(viewport.rect().center())
                ray = viewport.camera.screen_ray(x, y)
                hit = obj.plane.intersect(ray)
                assert hit is not None, (obj.plane.name, ray)
                assert abs(obj.plane.to_local(hit)[2]) < 1e-7
                px, py = viewport.view.Convert(*obj.plane.to_world(85, 70).tuple())
                screen = QPoint(round(px / viewport.devicePixelRatioF()), round(py / viewport.devicePixelRatioF()))
                from PySide6.QtGui import QMouseEvent
                from PySide6.QtCore import QEvent, QPointF
                from PySide6.QtWidgets import QApplication
                QApplication.sendEvent(viewport, QMouseEvent(QEvent.Type.MouseMove, QPointF(screen),
                    QPointF(viewport.mapToGlobal(screen)), Qt.MouseButton.NoButton,
                    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
                assert obj.plane.name in window.coords.text(), (obj.name, window.coords.text(), screen)
                QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=screen)
                assert any(item.object_id == obj.id for item in viewport.selection.items()), obj.name
                if obj.plane.name == "XY":
                    Path("artifacts").mkdir(exist_ok=True)
                    viewport.view.Redraw()
                    viewport.view.Dump("artifacts/plane-xy.png")
                viewport.exit_plane()
                viewport.set_visible(obj.id, False)
        viewport.camera.standard("Isométrique")
        viewport.camera.fit()
        viewport.set_background((0.9, 0.92, 0.95))
        from OCC.Core.Aspect import Aspect_GFM_VER
        from .viewport import color
        viewport.view.SetBgGradientColors(color((0.93, 0.95, 0.97)), color((0.75, 0.80, 0.87)), Aspect_GFM_VER, False)
        old_scale = viewport.view.Camera().Scale()
        window.zoom_window()
        # Stay inside the actual logical viewport when Windows constrains a
        # high-DPI window to the monitor. Fixed 700x500 points can be outside.
        start = QPoint(round(viewport.width() * 0.15), round(viewport.height() * 0.15))
        end = QPoint(round(viewport.width() * 0.75), round(viewport.height() * 0.75))
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(viewport, end)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
        assert viewport.view.Camera().Scale() < old_scale
        viewport.camera.fit()
        available = window.screen().availableGeometry()
        window.resize(min(1280, round(available.width() * .95)), min(800, round(available.height() * .9)))
        app.processEvents()
        viewport.view.Redraw()
        output = Path("artifacts")
        output.mkdir(exist_ok=True)
        assert viewport.view.Dump(str(output / "viewport.png"))
        viewport.frame_objects([objects[0].id])
        viewport.remove_object(objects[0].id)
        assert len(viewport.document.objects) == 6
        viewport.load_document(demo_document())
        assert len(viewport.document.objects) == 7
        print("SMOKE OK: native viewer, primitives, camera, contextual selection, multi-selection, planes, rays, resize, dump", flush=True)
        window.close()
        app.exit(0)
    except Exception:
        logging.getLogger(__name__).exception("SMOKE FAILED")
        window.close()
        app.exit(1)


def verify_picking(app, window, objects):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX
    vp = window.viewport
    vp.camera.standard("Isométrique")
    vp.camera.fit()
    app.processEvents()
    scale = vp.devicePixelRatioF()

    def click(world, ctrl=False):
        px, py = vp.view.Convert(*world)
        point = QPoint(round(px / scale), round(py / scale))
        QTest.mouseClick(vp, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier, point)
        return point

    vp.selection.from_tree([objects[0].id])
    assert vp.selection.items()[0].topology == "Objet"
    for name, topology, point in (
        ("Face", TopAbs_FACE, (-55., -35., 40.)),
        ("Arête", TopAbs_EDGE, (-55., -55., 40.)),
        ("Sommet", TopAbs_VERTEX, (-75., -55., 40.))):
        click(point)
        items = vp.selection.items()
        assert len(items) == 1 and items[0].object_id == objects[0].id, (name, items)
        if topology is not None:
            assert items[0].shape.ShapeType() == topology, name
    point = click((-55., -35., 40.))
    click((0., -35., 52.), True)
    assert len(vp.selection.items()) == 2
    click((0., -35., 52.), True)
    assert len(vp.selection.items()) == 1
    # A left drag must not clear or replace the previous selection.
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseMove(vp, point + QPoint(40, 30))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=point + QPoint(40, 30))
    assert len(vp.selection.items()) == 1
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    assert not vp.selection.items()
    vp.selection.hover(*vp.pixel(point))
    assert vp.context.HasDetected()
    vp.selection.leave()
    assert not vp.context.HasDetected()
    # Hidden planes must be selectable through the tree without losing their row.
    plane_obj = next(obj for obj in objects if obj.plane)
    window.tree_items[plane_obj.id].setSelected(True)
    assert window.selected_ids == [plane_obj.id]
    assert window.tree_items[plane_obj.id].isSelected()
    assert window.normal_action.isEnabled()
    window.normal_to_selected()
    assert vp.workplane == plane_obj.plane
    vp.exit_plane()
    # Verify native ViewCube detection and the real Qt click path.
    vp.camera.standard("Isométrique")
    width, height = vp.pixel(vp.rect().bottomRight())
    found = None
    for y in range(15, min(height, round(170 * scale)), 8):
        for x in range(max(0, width - round(170 * scale)), width, 8):
            vp.context.MoveTo(x, y, vp.view, False)
            if vp.context.HasDetected() and vp.context.DetectedInteractive() == vp.cube:
                found = QPoint(round(x / scale), round(y / scale))
                break
        if found:
            break
    assert found is not None, "ViewCube was not pickable"
    cube_before = vp.view.Camera().Direction().Coord()
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=found)
    cube_after = vp.view.Camera().Direction().Coord()
    assert any(abs(a - b) > 1e-5 for a, b in zip(cube_before, cube_after)), ("ViewCube camera unchanged", cube_before, cube_after)
    # Camera center and scale must not jump at the initial rotation press.
    before = vp.view.Camera().Center().Coord(), vp.view.Camera().Scale()
    QTest.mousePress(vp, Qt.MouseButton.MiddleButton, pos=QPoint(400, 300))
    after = vp.view.Camera().Center().Coord(), vp.view.Camera().Scale()
    assert before == after
    QTest.mouseMove(vp, QPoint(440, 320))
    QTest.mouseRelease(vp, Qt.MouseButton.MiddleButton, pos=QPoint(440, 320))
    assert not vp.selection.items() or all(i.topology == "Plan" for i in vp.selection.items())
    print("PICKING OK: tree object, automatic face/edge/vertex, Ctrl toggle, empty click, drag, hover, hidden tree plane, native ViewCube", flush=True)


def verify_camera(vp):
    from .geometry import WorkPlane, Vec3, X, Y
    vp.camera.standard("Dessus")
    vp.camera.fit()
    camera = vp.view.Camera()
    forward = Vec3(*camera.Direction().Coord())
    # A plane offset from camera.Center detects incorrect half-rays.
    plane = WorkPlane("offset", Vec3(0, 0, 12), X, Y)
    x, y = vp.view.Convert(12., 8., 12.)
    ray = vp.camera.screen_ray(x, y)
    assert ray.direction.dot(forward) > 0.99
    before = plane.intersect(ray)
    assert before is not None
    old_scale = camera.Scale()
    vp.camera.zoom(x, y, 1)
    new_scale = camera.Scale()
    assert new_scale < old_scale, ("Wheel direction", old_scale, new_scale)
    after = plane.intersect(vp.camera.screen_ray(x, y))
    pixel_size = camera.Scale() / (vp.height() * vp.devicePixelRatioF())
    assert after is not None and (after - before).norm() < pixel_size, (before, after)
    print(f"CAMERA OK: cursor anchor error {(after-before).norm():.3g} mm, wheel scale ratio {new_scale/old_scale:.3f}", flush=True)


def verify_dynamic_pivot(app, window):
    """Middle presses choose exact B-Rep points and preserve camera continuity."""
    from PySide6.QtCore import Qt, QEvent, QPointF
    from PySide6.QtGui import QMouseEvent, QKeyEvent
    from OCC.Core.gp import gp_Pnt

    vp = window.viewport
    camera = vp.view.Camera()
    cube = next(obj for obj in vp.document.objects.values() if obj.name == "Cube")
    vp.selection.from_tree([cube.id])
    scale = vp.devicePixelRatioF()

    def pose():
        return (*camera.Eye().Coord(), *camera.Center().Coord(), *camera.Direction().Coord(),
                *camera.Up().Coord(), camera.Scale())

    def mouse(kind, x, y):
        point = QPointF(x / scale, y / scale)
        app.sendEvent(vp, QMouseEvent(kind, point, QPointF(vp.mapToGlobal(point.toPoint())),
            Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.MiddleButton,
            Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.MiddleButton,
            Qt.KeyboardModifier.NoModifier))

    def drag_at(x, y, *, hit=True, stationary=False):
        expected = vp.selection.pick_surface(vp.camera.screen_ray(x, y))
        assert (expected is not None) == hit
        if expected is None:
            expected = vp.camera.pivot
        before = pose()
        selected = [(item.object_id, item.topology) for item in vp.selection.items()]
        mouse(QEvent.Type.MouseButtonPress, x, y)
        assert pose() == before, "Middle press moved camera"
        assert vp.camera._gesture_pivot == expected
        # Mode changes within this middle press must not lose the chosen point.
        app.sendEvent(vp, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Control,
                                   Qt.KeyboardModifier.ControlModifier))
        app.sendEvent(vp, QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Control,
                                   Qt.KeyboardModifier.NoModifier))
        assert vp.camera._gesture_pivot == expected and pose() == before
        point = gp_Pnt(*expected.tuple())
        projection = camera.Project(point).Coord()
        distance = camera.Eye().Distance(point)
        if not stationary:
            for step in range(1, 9):
                mouse(QEvent.Type.MouseMove, x + 3 * step, y + 2 * step)
                assert vp.camera._gesture_pivot == expected
                assert max(abs(a-b) for a, b in zip(projection, camera.Project(point).Coord())) < 1e-9
                assert abs(camera.Eye().Distance(point) - distance) < 1e-8
                assert camera.Scale() == before[-1]
            x, y = x + 24, y + 16
        before_release = pose()
        mouse(QEvent.Type.MouseButtonRelease, x, y)
        assert pose() == before_release, "Middle release moved camera"
        assert vp.camera._gesture_pivot is None and vp._middle_pivot is None
        assert [(item.object_id, item.topology) for item in vp.selection.items()] == selected
        return expected

    def top():
        vp.camera.standard("Dessus")
        vp.camera.fit()

    top()
    drag_at(*vp.view.Convert(-55., -35., 40.), stationary=True)
    drag_at(*vp.view.Convert(-55., -35., 40.))
    top()
    drag_at(*vp.view.Convert(-74.5, -54.5, 40.))
    top()
    zoom_x, zoom_y = vp.view.Convert(-55., -35., 40.)
    for _ in range(4):
        vp.camera.zoom(zoom_x, zoom_y, 8)
    drag_at(*vp.view.Convert(-55., -35., 40.))
    top()
    first = drag_at(*vp.view.Convert(-55., -35., 40.))
    second = drag_at(*vp.view.Convert(67., -20., 26.))
    assert (first - second).norm() > 100
    top()
    vp.camera.pan(85, -35)
    drag_at(*vp.view.Convert(-55., -35., 40.))
    # Whole-object tree selection remains untouched by a surface pivot query.
    top()
    vp.selection.from_tree([cube.id])
    drag_at(*vp.view.Convert(-55., -35., 40.))
    top()
    drag_at(5, 5, hit=False)
    vp.camera.standard("Isométrique")
    vp.camera.fit()
    print("DYNAMIC PIVOT OK: face, near edge, 20x zoom, successive distant points, pan, tree selection, empty space, stationary press, modifier transitions, no release jump, projection/zoom/selection preserved", flush=True)


def verify_navigation_input(app, window):
    """Actual Qt event dispatch: modifier transitions, roll and orbit invariants."""
    from math import cos, sin, pi
    from PySide6.QtCore import Qt, QEvent, QPointF
    from PySide6.QtGui import QMouseEvent, QKeyEvent, QFocusEvent
    from OCC.Core.gp import gp_Pnt
    from .interaction import Gesture

    vp = window.viewport
    vp.camera.standard("Isométrique")
    vp.camera.fit()
    obj = next(obj for obj in vp.document.objects.values() if not obj.plane)
    vp.selection.from_tree([obj.id])
    camera = vp.view.Camera()
    pivot = gp_Pnt(*vp.camera.pivot.tuple())
    model_locations = [obj.shape.Location().Transformation().TranslationPart().Coord()
                       for obj in vp.document.objects.values()]
    none = Qt.KeyboardModifier.NoModifier
    alt = Qt.KeyboardModifier.AltModifier
    ctrl = Qt.KeyboardModifier.ControlModifier
    middle = Qt.MouseButton.MiddleButton
    scale = vp.devicePixelRatioF()
    cx, cy = vp.view.Convert(*vp.camera.pivot.tuple())
    cx, cy = cx / scale, cy / scale
    radius = 100

    def pose():
        return (*camera.Eye().Coord(), *camera.Center().Coord(), *camera.Up().Coord(), camera.Scale())

    def mouse(kind, x, y, modifiers=none):
        point = QPointF(x, y)
        is_move = kind == QEvent.Type.MouseMove
        event = QMouseEvent(kind, point, QPointF(vp.mapToGlobal(point.toPoint())),
                            Qt.MouseButton.NoButton if is_move else middle,
                            Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else middle,
                            modifiers)
        app.sendEvent(vp, event)

    # Real Qt drags of 200 logical pixels, with world yaw/local pitch invariants.
    from OCC.Core.Graphic3d import Graphic3d_Camera
    initial_camera = Graphic3d_Camera(camera)
    for dx, dy in ((200, 0), (0, 200)):
        camera.Copy(initial_camera)
        up, direction = camera.Up(), camera.Direction()
        right = direction.Crossed(up)
        zoom = camera.Scale()
        mouse(QEvent.Type.MouseButtonPress, 200, 200)
        active_pivot = gp_Pnt(*vp.camera._gesture_pivot.tuple())
        projection = camera.Project(active_pivot).Coord()
        eye_distance = camera.Eye().Distance(active_pivot)
        mouse(QEvent.Type.MouseMove, 200 + dx, 200 + dy)
        mouse(QEvent.Type.MouseButtonRelease, 200 + dx, 200 + dy)
        if dx:
            assert abs(camera.Up().Z() - up.Z()) < 1e-12
            assert abs(camera.Direction().Z() - direction.Z()) < 1e-12
        else:
            assert camera.Direction().Crossed(camera.Up()).IsEqual(right, 1e-10)
        assert camera.Scale() == zoom
        assert abs(camera.Eye().Distance(active_pivot) - eye_distance) < 1e-9
        assert max(abs(a - b) for a, b in zip(projection, camera.Project(active_pivot).Coord())) < 1e-10
    camera.Copy(initial_camera)

    initial, projection, distance = pose(), camera.Project(pivot).Coord(), camera.Distance()
    mouse(QEvent.Type.MouseButtonPress, cx + radius, cy, alt)
    assert vp.state.gesture == Gesture.ROLL and pose() == initial
    for step in range(1, 73):
        angle = step * pi / 36
        mouse(QEvent.Type.MouseMove, cx + radius * cos(angle), cy - radius * sin(angle), alt)
        projected = camera.Project(pivot).Coord()
        assert max(abs(a - b) for a, b in zip(projection, projected)) < 1e-10
        assert abs(camera.Distance() - distance) < distance * 1e-12
        assert camera.Scale() == initial[-1]
    assert max(abs(a - b) for a, b in zip(initial, pose())) < 1e-9
    mouse(QEvent.Type.MouseButtonRelease, cx + radius, cy, alt)

    # 45 degree roll, then world-Z yaw without resetting the rolled camera.
    mouse(QEvent.Type.MouseButtonPress, cx + radius, cy, alt)
    mouse(QEvent.Type.MouseMove, cx + radius / 2**0.5, cy - radius / 2**0.5, alt)
    before = pose()
    app.sendEvent(vp, QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Alt, none))
    assert vp.state.gesture == Gesture.ROTATE and pose() == before
    x, y = vp.state.last
    mouse(QEvent.Type.MouseMove, x + 20, y)
    assert pose() != before
    before = pose()
    # Ctrl wins even with Alt held. Pressing/releasing modifiers does not move camera.
    app.sendEvent(vp, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Alt, alt))
    assert vp.state.gesture == Gesture.ROLL and pose() == before
    app.sendEvent(vp, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Control, ctrl | alt))
    assert vp.state.gesture == Gesture.PAN and pose() == before
    x, y = vp.state.last
    up, direction = camera.Up().Coord(), camera.Direction().Coord()
    mouse(QEvent.Type.MouseMove, x + 12, y + 8, ctrl | alt)
    assert vp._navigation_modifiers == ctrl | alt, ("pan modifiers", vp._navigation_modifiers, ctrl | alt)
    assert camera.Up().Coord() == up and camera.Direction().Coord() == direction
    assert camera.Scale() == initial[-1]
    before = pose()
    app.sendEvent(vp, QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Control, alt))
    assert vp.state.gesture == Gesture.ROLL and pose() == before, (vp.state.gesture, vp.state.button, before, pose())
    x, y = vp.state.last
    mouse(QEvent.Type.MouseButtonRelease, x, y, alt)
    assert pose() == before and vp.state.gesture == Gesture.IDLE
    # Losing focus cancels a gesture and cannot select an entity.
    mouse(QEvent.Type.MouseButtonPress, x, y)
    app.sendEvent(vp, QFocusEvent(QEvent.Type.FocusOut))
    assert vp.state.gesture == Gesture.IDLE
    assert [item.object_id for item in vp.selection.items()] == [obj.id]
    assert model_locations == [obj.shape.Location().Transformation().TranslationPart().Coord()
                               for obj in vp.document.objects.values()]

    vp.camera.standard("Isométrique")
    vp.camera.fit()
    px, py = vp.view.Convert(*vp.camera.pivot.tuple())
    vp.camera.start_roll(px + 120, py)
    vp.camera.roll(px + 120, py - 120)
    vp.camera.end_rotation()
    vp.selection.from_tree([])
    vp.view.Redraw()
    Path("artifacts").mkdir(exist_ok=True)
    assert vp.view.Dump("artifacts/navigation-roll45.png")
    vp.camera.standard("Isométrique")
    print("NAVIGATION OK: 200px world-Z yaw/local-right pitch, 360 roll/pivot/zoom, 45 roll then orbit, Ctrl+Alt precedence, live modifier transitions, focus cancellation, selection and model locations preserved", flush=True)
