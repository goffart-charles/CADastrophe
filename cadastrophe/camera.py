"""Model orbit about world Z; sketch orbit about screen axes; optical roll."""
from math import atan2, hypot, pi
from OCC.Core import V3d
from OCC.Core.gp import gp_Dir, gp_Pnt, gp_Quaternion, gp_Trsf, gp_Vec
from .geometry import Ray, Vec3, WorkPlane, natural_plane_up


STANDARD_VIEWS = {
    "Face": "V3d_Yneg", "Arrière": "V3d_Ypos",
    "Gauche": "V3d_Xneg", "Droite": "V3d_Xpos",
    "Dessus": "V3d_Zpos", "Dessous": "V3d_Zneg",
    "Isométrique": "V3d_XposYnegZpos",
}


class CameraController:
    def __init__(self, view, invalidate):
        self.view = view
        self.invalidate = invalidate
        self._zoom_remainder = 0.0
        self._scene_pivot: Vec3 | None = None
        self._explicit_pivot: Vec3 | None = None
        self._rotation_last: tuple[int, int] | None = None
        self._gesture_pivot: Vec3 | None = None
        self._roll_center = (0, 0)
        self._roll_linear = False
        self._span = 1
        self._yaw_sign = 1.0
        self._screen_orbit = False
        self.rotation_speed = self.zoom_speed = 1.

    @property
    def pivot(self) -> Vec3:
        return self._explicit_pivot or self._scene_pivot or Vec3(*self.view.Camera().Center().Coord())

    def set_scene_pivot(self, point: Vec3 | None):
        """Update model bounds without changing the camera or an ongoing gesture."""
        self._scene_pivot = point

    def set_pivot(self, point: Vec3 | None):
        """Override the model pivot; None restores the scene pivot."""
        self._explicit_pivot = point

    def end_rotation(self):
        self._rotation_last = None
        self._gesture_pivot = None
        self._screen_orbit = False

    def capture_navigation(self):
        """Non-camera state for a temporary plane editor; orbit math is untouched."""
        return self._explicit_pivot, self._zoom_remainder

    def restore_navigation(self, state):
        self.end_rotation()
        self._explicit_pivot, self._zoom_remainder = state

    def fit(self):
        self.end_rotation()
        self.set_pivot(None)
        self.view.FitAll(0.08, False)
        self.view.ZFitAll()
        self.invalidate()

    def standard(self, name: str):
        self.end_rotation()
        self._yaw_sign = 1.0
        self.view.SetProj(getattr(V3d, STANDARD_VIEWS[name]))
        self.invalidate()

    def normal_to(self, plane: WorkPlane, *, align_axes=False):
        self.end_rotation()
        camera = self.view.Camera()
        distance = camera.Distance()
        # SetCenter/SetEye each recompute Direction using the previous pose;
        # SetCenter can even early-return for an equidistant, different point.
        camera.SetEyeAndCenter(gp_Pnt(*(plane.origin + plane.normal * distance).tuple()),
                               gp_Pnt(*plane.origin.tuple()))
        camera.SetUp(gp_Dir(*(plane.v if align_axes else self.natural_up(plane)).tuple()))
        self.view.ZFitAll()
        self.invalidate()

    @staticmethod
    def natural_up(plane: WorkPlane) -> Vec3:
        return natural_plane_up(plane.normal)

    def start_rotation(self, x: int, y: int, *, pivot: Vec3 | None = None, screen_axes=False):
        # No camera mutation, GravityPoint recomputation or implicit rim roll.
        self._rotation_last = (x, y)
        self._gesture_pivot = self.pivot if pivot is None else pivot
        self._screen_orbit = screen_axes
        width, height = self.view.Window().Size()
        self._span = max(1, min(width, height))
        # Keep horizontal dragging screen-consistent when upside down. Latch
        # this sign for the WHOLE gesture; never flip at a pole during a drag.
        # At a pole retain the last unambiguous sign instead of using noise.
        vertical = self.view.Camera().Up().Z()
        if abs(vertical) > 1e-6:
            self._yaw_sign = 1.0 if vertical > 0 else -1.0

    @staticmethod
    def orbit_increment(dx: float, dy: float, right: gp_Dir,
                        radians_per_pixel: float, yaw_sign: float) -> gp_Quaternion:
        """Pure rotation math, independent of a view/window or gesture state.

        Q = Q_worldZ(yaw) * Q_right(pitch). Equivalently, yaw first and
        pitch about the yawed right axis. No optical-axis twist is added.
        """
        yaw = gp_Quaternion(gp_Vec(0, 0, 1), -dx * radians_per_pixel * yaw_sign)
        pitch = gp_Quaternion(gp_Vec(right), -dy * radians_per_pixel)
        result = yaw.Multiplied(pitch)
        result.Normalize()
        return result

    def _apply_rotation(self, rotation: gp_Quaternion):
        """Apply one normalized quaternion about the latched world-space pivot."""
        rotation.Normalize()
        transform = gp_Trsf()
        transform.SetRotation(rotation)
        pivot = gp_Pnt(*self._gesture_pivot.tuple())
        # T(p) = R*p + (pivot - R*pivot): the pivot is exactly fixed.
        transform.SetTranslationPart(gp_Vec(pivot.Transformed(transform), pivot))
        self.view.Camera().Transform(transform)
        self.invalidate()

    def rotate(self, x: int, y: int):
        if self._rotation_last is None:
            self.start_rotation(x, y)
            return
        previous_x, previous_y = self._rotation_last
        self._rotation_last = (x, y)
        dx, dy = x - previous_x, y - previous_y
        if not dx and not dy:
            return
        camera = self.view.Camera()
        right = camera.Direction().Crossed(camera.Up())
        radians_per_pixel = self.rotation_speed * pi / self._span
        if self._screen_orbit:
            # A normal sketch can look directly along world Z. Rotate about
            # screen Up/Right so BOTH mouse axes tilt even at that pole.
            axis = gp_Vec(camera.Up()).Multiplied(-dx).Added(gp_Vec(right).Multiplied(-dy))
            rotation = gp_Quaternion(axis, hypot(dx, dy) * radians_per_pixel)
        else:
            rotation = self.orbit_increment(dx, dy, right, radians_per_pixel, self._yaw_sign)
        self._apply_rotation(rotation)

    def start_roll(self, x: int, y: int, *, pivot: Vec3 | None = None):
        self.start_rotation(x, y, pivot=pivot)
        self._roll_center = self.view.Convert(*self._gesture_pivot.tuple())
        cx, cy = self._roll_center
        # An angular drag is undefined at its center: latch a horizontal drag
        # for gestures started inside this small, DPI-independent central zone.
        self._roll_dead_radius = self._span * 0.025
        self._roll_linear = hypot(x - cx, y - cy) < self._roll_dead_radius

    def roll(self, x: int, y: int):
        if self._rotation_last is None:
            self.start_roll(x, y)
            return
        previous_x, previous_y = self._rotation_last
        self._rotation_last = (x, y)
        if self._roll_linear:
            angle = (x - previous_x) * pi / self._span
        else:
            cx, cy = self._roll_center
            ax, ay = previous_x - cx, cy - previous_y
            bx, by = x - cx, cy - y
            if min(hypot(ax, ay), hypot(bx, by)) < self._roll_dead_radius:
                return  # re-anchor after crossing the singular central region
            vx, vy = bx - ax, by - ay
            length2 = vx * vx + vy * vy
            if length2:
                t = max(0.0, min(1.0, -(ax * vx + ay * vy) / length2))
                if hypot(ax + t * vx, ay + t * vy) < self._roll_dead_radius:
                    return  # also reject a single coalesced sample across center
            angle = atan2(ax * by - ay * bx, ax * bx + ay * by)
        if not angle:
            return
        self._apply_rotation(gp_Quaternion(gp_Vec(self.view.Camera().Direction()), angle * self.rotation_speed))

    def pan(self, dx: int, dy: int):
        self.view.Pan(dx, -dy, 1.0, True)
        self.invalidate()

    def zoom(self, x: int, y: int, steps: float):
        amount = steps * 14 * self.zoom_speed + self._zoom_remainder
        pixels = round(amount)
        self._zoom_remainder = amount - pixels
        if not pixels:
            return
        self.view.StartZoomAtPoint(x, y)
        # OCCT's native cursor anchor; fractional wheel / touchpad steps survive.
        self.view.ZoomAtPoint(0, 0, 0, pixels)
        self.invalidate()

    def window(self, x0: int, y0: int, x1: int, y1: int):
        if abs(x1 - x0) > 8 and abs(y1 - y0) > 8:
            self.view.WindowFitAll(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            self.invalidate()

    def screen_ray(self, x: int, y: int) -> Ray:
        xw, yw, zw, dx, dy, dz = self.view.ConvertWithProj(x, y)
        camera = self.view.Camera()
        eye = Vec3(*camera.Eye().Coord())
        forward = Vec3(*camera.Direction().Coord())
        direction = Vec3(dx, dy, dz).normalized()
        if direction.dot(forward) < 0:
            direction = direction * -1
        if camera.IsOrthographic():
            projected = Vec3(xw, yw, zw)
            # ConvertWithProj starts on the projection plane. Move to the eye
            # plane so supports on either side of camera.Center remain pickable.
            origin = projected + forward * ((eye - projected).dot(forward))
        else:
            origin = eye
        return Ray(origin, direction)
