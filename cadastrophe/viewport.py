"""Reusable Qt native surface. OCCT owns drawing; Qt schedules invalidation."""
from __future__ import annotations

import logging
from PySide6.QtCore import Qt, QRect, Signal, QEvent, QTimer, QElapsedTimer
from PySide6.QtWidgets import QWidget, QRubberBand, QApplication
from OCC.Display.OCCViewer import Viewer3d
from OCC.Core.AIS import AIS_ViewCube, AIS_ViewController, AIS_Trihedron
from OCC.Core.Aspect import Aspect_TOTP_RIGHT_UPPER, Aspect_TOTP_LEFT_LOWER, Aspect_GFM_VER
from OCC.Core.Geom import Geom_Axis2Placement
from OCC.Core.Graphic3d import (Graphic3d_TransformPers, Graphic3d_TMF_TriedronPers,
    Graphic3d_Vec2i, Graphic3d_Camera,
    Graphic3d_ZLayerId_Topmost)
from OCC.Core.Prs3d import Prs3d_DatumParts_XAxis, Prs3d_DatumParts_YAxis, Prs3d_DatumParts_ZAxis
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB, Quantity_TOC_sRGB
from OCC.Core.gp import gp_Ax2, gp_Pnt, gp_Dir

from .camera import CameraController
from .document import Document, ObjectType
from .document_adapter import ViewportDocumentAdapter
from .core.history import HistoryBusyError
from .core.preferences import Preferences, rgb
from .geometry import Vec3, WorkPlane
from .interaction import Gesture, InteractionState, WorkMode
from .selection import SelectionController

log = logging.getLogger(__name__)


def color(rgb):
    return Quantity_Color(*rgb, Quantity_TOC_RGB)


class CadViewport(QWidget):
    ready = Signal()
    failed = Signal(str)
    presentation_error = Signal(str)
    selection_changed = Signal(list)
    document_changed = Signal(object)
    coordinates_changed = Signal(str)
    mode_changed = Signal(str)
    edit_clicked = Signal(float, float)
    edit_double_clicked = Signal(float, float)
    edit_pressed = Signal(float, float)
    edit_released = Signal(float, float, bool)
    edit_gesture_canceled = Signal()
    edit_pointer_moved = Signal(float, float)
    edit_pointer_left = Signal()
    edit_view_changed = Signal()

    def __init__(self, parent=None, *, document=None, presentation_registry=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_PaintOnScreen)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.display = None
        self.document = document if document is not None else Document()
        self.presentations = {}
        self.tool_pick_handler = None
        self.tool_hover_handler = None
        self.presentation_registry = presentation_registry
        self.editing_id = None
        self.edit_cursor = None
        self._edit_modifiers = Qt.KeyboardModifier.NoModifier
        self._edit_snapshot = None
        self.camera_animation_duration = 220
        self._camera_transition = None
        self._camera_animation_clock = QElapsedTimer()
        self._camera_animation_timer = QTimer(self)
        self._camera_animation_timer.setInterval(16)
        self._camera_animation_timer.timeout.connect(self.advance_camera_animation)
        self._cube_pressed = False
        self.camera = self.selection = None
        self.cube = self.local_axes = None
        self.state = InteractionState()
        self._navigation_modifiers = Qt.KeyboardModifier.NoModifier
        self._middle_pivot: Vec3 | None = None
        self.mode = WorkMode.MODEL
        self.workplane: WorkPlane | None = None
        self.saved_camera = None
        self.window_zoom = False
        self.rubber = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.render_mode = "Ombré avec arêtes"
        self._failed = False
        self._native_closed = False
        self.document_load_guard = None
        self.preferences = Preferences()

    def paintEngine(self):
        return None

    def initialize(self):
        self.display = Viewer3d()
        self.display.Create(window_handle=int(self.winId()), parent=self)
        self.view = self.display.View
        self.context = self.display.Context
        self.view.SetImmediateUpdate(False)
        self.context.SetDisplayMode(1, False)
        self.view.SetBgGradientColors(color((0.84, 0.86, 0.90)), color((0.98, 0.98, 0.99)), Aspect_GFM_VER, False)
        params = self.view.ChangeRenderingParams()
        params.NbMsaaSamples = 4
        self.camera = CameraController(self.view, self.invalidate_view)
        self.selection = SelectionController(self.context, self.view, self.document,
                                             self.presentations, self.selection_changed.emit, self.update)
        self.selection.set_pixel_ratio(self.devicePixelRatioF())
        self.document_adapter = ViewportDocumentAdapter(self)
        self.cube = AIS_ViewCube()
        self.cube.SetBoxColor(color((0.28, 0.40, 0.56)))
        self.cube.SetInnerColor(color((0.12, 0.18, 0.27)))
        self.cube.SetTextColor(color((0.025, 0.04, 0.07)))
        self.cube_controller = AIS_ViewController()
        self.configure_cube_scale()
        self.context.Display(self.cube, False)
        # Animation would require a frame pump. A deterministic native click is
        # preferable here; all cube orientations still use its native owner.
        self.cube.SetDuration(0.0)
        self.apply_preferences(self.preferences)
        self.load_document(self.document)
        self.ready.emit()

    def apply_preferences(self, preferences):
        self.preferences = preferences
        if not self.camera:
            return
        self.camera.rotation_speed = preferences.rotation_speed
        self.camera.zoom_speed = preferences.zoom_speed
        self.camera._zoom_remainder = 0.
        top, bottom = preferences.backgrounds
        self.view.SetBgGradientColors(Quantity_Color(*rgb(top), Quantity_TOC_sRGB),
                                      Quantity_Color(*rgb(bottom), Quantity_TOC_sRGB), Aspect_GFM_VER, False)
        self.selection.set_highlight_colors(rgb(preferences.preselection), rgb(preferences.selection))
        axis_text = color((0.87, 0.92, 0.98) if preferences.theme == "dark" else (0.12, 0.18, 0.26))
        for part in (Prs3d_DatumParts_XAxis, Prs3d_DatumParts_YAxis, Prs3d_DatumParts_ZAxis):
            self.cube.Attributes().DatumAspect().TextAspect(part).SetColor(axis_text)
        self.view.TriedronDisplay(Aspect_TOTP_LEFT_LOWER, axis_text, 0.035)
        self.context.Redisplay(self.cube, False)
        if hasattr(self, "document_adapter"):
            self.document_adapter.refresh_features()
            self.document_adapter.refresh_reference_planes()
        self.update()

    def configure_cube_scale(self):
        scale = self.devicePixelRatioF()
        self.cube.SetSize(56 * scale)
        self.cube.SetFontHeight(11 * scale)
        self.cube.SetTransformPersistence(Graphic3d_TransformPers(
            Graphic3d_TMF_TriedronPers, Aspect_TOTP_RIGHT_UPPER,
            Graphic3d_Vec2i(round(85 * scale), round(85 * scale))))

    def event(self, event):
        if event.type() == QEvent.Type.DevicePixelRatioChange and getattr(self, "cube", None):
            self.configure_cube_scale()
            self.selection.set_pixel_ratio(self.devicePixelRatioF())
            self.document_adapter.refresh_features()
            self.document_adapter.refresh_reference_planes()
            self.context.Redisplay(self.cube, False)
            self.view.MustBeResized()
            self.invalidate_view()
        return super().event(event)

    def invalidate_view(self):
        self.update()
        if self.editing_id is not None:
            self.edit_view_changed.emit()

    def paintEvent(self, event):
        if self._failed or self._native_closed:
            return
        if self.display is None:
            try:
                self.initialize()
            except Exception as exc:
                log.exception("OCCT initialization failed")
                self._failed = True
                self.failed.emit(str(exc))
                return
        self.view.Redraw()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.display:
            self.view.MustBeResized()
            self.invalidate_view()

    def pixel(self, point):
        ratio = self.devicePixelRatioF()
        return round(point.x() * ratio), round(point.y() * ratio)

    def load_document(self, document):
        if self.document.history.blocked or document.history.blocked:
            raise HistoryBusyError("Terminez la transaction avant de changer de document.")
        if self.document_load_guard is not None:
            self.document_load_guard(document)
        self.exit_plane()
        self.document_adapter.bind(document)
        self.camera.set_pivot(None)
        self.camera.standard("Isométrique")
        self.camera.fit()
        self.document_changed.emit(document)

    def set_visible(self, object_id, visible):
        self.document.set_visible(object_id, visible)

    def remove_object(self, object_id):
        obj = self.document.objects[object_id]
        from .core.reference_objects import DatumPlaneObject
        if obj.plane and not isinstance(obj,DatumPlaneObject):
            return  # global document references cannot be deleted
        self.document.remove(object_id)

    def update_rotation_pivot(self):
        """Use visible B-Rep bodies, never reference planes or screen overlays."""
        from OCC.Core.Bnd import Bnd_Box
        from OCC.Core.BRepBndLib import brepbndlib
        bounds = Bnd_Box()
        for obj in self.document.objects.values():
            obj = self.document_adapter.display_object(obj)
            if obj.visible and obj.kind is ObjectType.BODY and obj.shape is not None:
                brepbndlib.Add(obj.shape, bounds)
        self.camera.set_scene_pivot(None if bounds.IsVoid() else self.bounds_center(bounds))

    @staticmethod
    def bounds_center(bounds):
        xmin, ymin, zmin, xmax, ymax, zmax = bounds.Get()
        return Vec3((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)

    def frame_objects(self, ids):
        from OCC.Core.Bnd import Bnd_Box
        from OCC.Core.BRepBndLib import brepbndlib
        bounds = Bnd_Box()
        for object_id in ids:
            obj = self.document_adapter.display_object(self.document.objects[object_id])
            from .core.curve_model import CurveObject
            from .core.curve_geometry import curve_edge
            if isinstance(obj, CurveObject):
                try:
                    brepbndlib.Add(curve_edge(self.document, obj), bounds)
                except (ValueError, RuntimeError):
                    pass
            if obj.shape is not None:
                brepbndlib.Add(obj.shape, bounds)
        if not bounds.IsVoid():
            self.camera.end_rotation()
            self.view.FitAll(bounds, 0.12, False)
            self.view.ZFitAll()
            self.camera.set_pivot(self.bounds_center(bounds))
            self.update()

    def set_render_mode(self, mode):
        self.render_mode = mode
        for object_id, ais in self.presentations.items():
            if self.document.objects[object_id].plane:
                continue
            ais.Attributes().SetFaceBoundaryDraw(mode == "Ombré avec arêtes")
            self.context.SetDisplayMode(ais, 0 if mode == "Filaire" else 1, False)
            self.context.Redisplay(ais, False)
        self.update()

    def set_background(self, rgb):
        self.view.SetBackgroundColor(color(rgb))
        from OCC.Core.Aspect import Aspect_GFM_NONE
        self.view.SetBgGradientStyle(Aspect_GFM_NONE, False)
        self.update()

    def enter_plane(self, plane, *, align_axes=False):
        if self.editing_id is not None:
            return
        self.exit_plane()
        self.saved_camera = Graphic3d_Camera(self.view.Camera())
        self.workplane = plane
        self.mode = WorkMode.PLANE
        self.camera.normal_to(plane, align_axes=align_axes)
        axis = gp_Ax2(gp_Pnt(*plane.origin.tuple()), gp_Dir(*plane.normal.tuple()), gp_Dir(*plane.u.tuple()))
        self.local_axes = AIS_Trihedron(Geom_Axis2Placement(axis))
        self.local_axes.SetSize(30)
        self.local_axes.SetZLayer(Graphic3d_ZLayerId_Topmost)
        self.local_axes.SetArrowColor(color((0.18, 0.20, 0.24)))
        self.local_axes.SetDatumPartColor(Prs3d_DatumParts_XAxis, color((0.85, 0.12, 0.10)))
        self.local_axes.SetDatumPartColor(Prs3d_DatumParts_YAxis, color((0.10, 0.60, 0.20)))
        self.local_axes.SetDatumPartColor(Prs3d_DatumParts_ZAxis, color((0.15, 0.30, 0.85)))
        self.local_axes.SetTextColor(color((0.12, 0.15, 0.20)))
        self.local_axes.SetLabel(Prs3d_DatumParts_XAxis, "U")
        self.local_axes.SetLabel(Prs3d_DatumParts_YAxis, "V")
        self.local_axes.SetLabel(Prs3d_DatumParts_ZAxis, "N")
        self.context.Display(self.local_axes, False)
        self.context.Deactivate(self.local_axes)
        self.mode_changed.emit(f"Plan {plane.name} · coordonnées en mm · Échap : retour 3D")
        self.update()

    def exit_plane(self, *, restore=True):
        if self.editing_id is not None:
            return
        if self.camera:
            self.camera.end_rotation()
        if self.local_axes and self.display:
            self.context.Remove(self.local_axes, False)
        self.local_axes = None
        if self.saved_camera and self.display and restore:
            self.view.SetCamera(self.saved_camera)
        self.saved_camera = None
        self.workplane = None
        self.mode = WorkMode.MODEL
        self.mode_changed.emit("3D · Milieu : rotation · Alt+milieu : roulis · Ctrl+milieu : panoramique · Molette : zoom")
        self.coordinates_changed.emit("")
        self.update()

    def begin_plane_edit(self, object_id, plane):
        """Temporary native camera/selection context, not an undo transaction."""
        if self.editing_id is not None or self._edit_snapshot is not None:
            raise RuntimeError("Un contexte d’édition est déjà actif.")
        self.cancel_gesture()
        self._edit_snapshot = dict(camera=Graphic3d_Camera(self.view.Camera()),
                                   navigation=self.camera.capture_navigation(), plane=self.workplane,
                                   saved_camera=(Graphic3d_Camera(self.saved_camera)
                                                 if self.saved_camera is not None else None),
                                   selection=self.selection.suspend())
        try:
            self.exit_plane(restore=False)  # Keep the current pose, including a prior normal demo/perspective.
            self.enter_plane(plane, align_axes=True)
            self.editing_id = object_id
            self.local_axes.SetInfiniteState(True)
            self.document_adapter.refresh_features()
            self.mode_changed.emit("Esquisse · Milieu : rotation · Alt+milieu : roulis · Ctrl+milieu : panoramique · Ctrl+8 : vue normale")
        except BaseException:
            self.end_plane_edit(restore_camera=True)
            raise

    def animate_plane_entry(self):
        if self._edit_snapshot is None or self.camera_animation_duration <= 0:
            return
        start = Graphic3d_Camera(self._edit_snapshot["camera"])
        target = Graphic3d_Camera(self.view.Camera())
        self._camera_transition = start, target
        self.view.Camera().Copy(start)
        self._camera_animation_clock.start()
        self._camera_animation_timer.start()
        self.invalidate_view()

    def advance_camera_animation(self):
        if self._camera_transition is None:
            return
        start, target = self._camera_transition
        progress = min(1., self._camera_animation_clock.elapsed()/self.camera_animation_duration)
        if progress >= 1:
            self.stop_camera_animation()
            self.view.Camera().Copy(target)
        else:
            eased = progress*progress*(3-2*progress)
            Graphic3d_Camera.Interpolate(start, target, eased, self.view.Camera())
        self.view.MustBeResized()
        self.view.ZFitAll()
        self.invalidate_view()

    def stop_camera_animation(self):
        self._camera_animation_timer.stop()
        self._camera_transition = None

    def end_plane_edit(self, *, restore_camera=False):
        self.stop_camera_animation()
        snapshot = self._edit_snapshot
        if snapshot is None:
            return
        self._edit_snapshot = None
        self.editing_id = None
        self.edit_cursor = None
        self.cancel_gesture()
        try:
            self.exit_plane(restore=False)
            if restore_camera and snapshot["plane"] is not None:
                self.enter_plane(snapshot["plane"])
                self.saved_camera = snapshot["saved_camera"]
            if restore_camera:
                self.view.SetCamera(snapshot["camera"])
            self.view.MustBeResized()  # Keep the current native aspect after an edit-time resize.
            self.camera.restore_navigation(snapshot["navigation"])
        finally:
            self.context.Activate(self.cube, 0, True)
            self.selection.resume(snapshot["selection"])
            self.document_adapter.refresh_features()
            self.update()

    def button_name(self, button):
        return {Qt.MouseButton.LeftButton: "left", Qt.MouseButton.MiddleButton: "middle"}.get(button, "other")

    @property
    def navigating(self):
        return self.state.gesture in (Gesture.ROTATE, Gesture.ROLL, Gesture.PAN)

    def mousePressEvent(self, event):
        if not self.camera:
            return
        self.stop_camera_animation()
        if self.state.gesture != Gesture.IDLE:
            event.accept()
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        point = event.position()
        self._edit_modifiers = event.modifiers()
        self._navigation_modifiers = event.modifiers()
        self.state.press(self.button_name(event.button()), (point.x(), point.y()),
                         ctrl=bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier),
                         alt=bool(event.modifiers() & Qt.KeyboardModifier.AltModifier),
                         plane=self.mode == WorkMode.PLANE and self.editing_id is None, window=self.window_zoom)
        self._cube_pressed = (self.state.gesture == Gesture.CLICK and self.cube_at(point))
        if self.editing_id is not None and self.state.gesture == Gesture.CLICK and not self._cube_pressed:
            self.edit_pressed.emit(point.x(), point.y())
        if self.state.gesture in (Gesture.ROTATE, Gesture.ROLL, Gesture.PAN):
            x, y = self.pixel(point)
            ray = self.camera.screen_ray(x, y)
            hit = self.workplane.intersect(ray) if self.editing_id is not None and self.workplane else None
            if hit is None:
                hit = self.selection.pick_surface(ray)
            if hit is None:
                # Empty background still defines the clicked point at the
                # current pivot depth; keep that screen position fixed.
                normal = Vec3(*self.view.Camera().Direction().Coord())
                depth = (self.camera.pivot-ray.origin).dot(normal)/ray.direction.dot(normal)
                hit = ray.origin+ray.direction*depth
            self._middle_pivot = hit
        if self.state.gesture == Gesture.ROTATE:
            self.camera.start_rotation(x, y, pivot=self._middle_pivot, screen_axes=self.editing_id is not None)
        elif self.state.gesture == Gesture.ROLL:
            self.camera.start_roll(*self.pixel(point), pivot=self._middle_pivot)
        if self.state.gesture in (Gesture.ROTATE, Gesture.ROLL, Gesture.PAN):
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.selection.leave()
        if self.state.gesture == Gesture.WINDOW:
            self.rubber.setGeometry(QRect(point.toPoint(), point.toPoint()))
            self.rubber.show()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self.camera:
            return
        point = event.position()
        self._edit_modifiers = event.modifiers()
        dx, dy = self.state.move((point.x(), point.y()), QApplication.startDragDistance())
        x, y = self.pixel(point)
        if self.switch_navigation(event.modifiers(), x, y):
            return  # rebase at the cursor; a mode transition never moves camera
        if self.state.gesture == Gesture.ROTATE:
            self.camera.rotate(x, y)
        elif self.state.gesture == Gesture.ROLL:
            self.camera.roll(x, y)
        elif self.state.gesture == Gesture.PAN:
            scale = self.devicePixelRatioF()
            self.camera.pan(round(dx * scale), round(dy * scale))
        elif self.state.gesture == Gesture.WINDOW:
            from PySide6.QtCore import QPoint
            self.rubber.setGeometry(QRect(QPoint(*map(round, self.state.start)), point.toPoint()).normalized())
        elif self.state.gesture == Gesture.IDLE:
            if self.tool_hover_handler is not None:
                self.tool_hover_handler(point.x(), point.y())
            else:
                self.selection.hover(x, y)
        if self.workplane:
            hit = self.workplane.intersect(self.camera.screen_ray(x, y))
            if hit:
                u, v, _ = self.workplane.to_local(hit)
                length = self.preferences.units.format_length
                self.coordinates_changed.emit(f"{self.workplane.name}  u {length(u)}  v {length(v)}  |  XYZ {length(hit.x)}, {length(hit.y)}, {length(hit.z)}")
        if self.editing_id is not None and not self._cube_pressed and not self.navigating:
            self.edit_pointer_moved.emit(point.x(), point.y())

    def mouseReleaseEvent(self, event):
        if not self.camera:
            return
        if self.button_name(event.button()) != self.state.button:
            event.accept()
            return
        # Count a displacement even if Qt compressed all move events.
        point = event.position()
        self._edit_modifiers = event.modifiers()
        if self.state.button == "middle":
            self.mouseMoveEvent(event)  # apply any final coalesced drag sample
        self.state.move((point.x(), point.y()), QApplication.startDragDistance())
        start = self.state.start
        gesture = self.state.release(self.button_name(event.button()))
        if gesture in (Gesture.ROTATE, Gesture.ROLL, Gesture.PAN):
            self.camera.end_rotation()
            self._middle_pivot = None
            if self.editing_id is not None:
                self.edit_pointer_moved.emit(point.x(), point.y())
        cube_pressed, self._cube_pressed = self._cube_pressed, False
        if self.editing_id is not None and gesture in (Gesture.CLICK, Gesture.DRAG) and not cube_pressed:
            self.edit_released.emit(point.x(), point.y(), gesture == Gesture.DRAG)
        if cube_pressed:
            if gesture == Gesture.CLICK and self.cube_at(point):
                if self.editing_id is None:
                    self.exit_plane(restore=False)
                self.cube_controller.SelectInViewer(Graphic3d_Vec2i(*self.pixel(point)))
                self.cube_controller.FlushViewEvents(self.context, self.view, True)
                self.invalidate_view()
        elif gesture == Gesture.CLICK and self.editing_id is not None:
            self.edit_clicked.emit(point.x(), point.y())
        elif gesture == Gesture.CLICK:
            x, y = self.pixel(point)
            self.context.MoveTo(x, y, self.view, False)
            if self.context.HasDetected() and self.context.DetectedInteractive() == self.cube:
                # The bindings omit AIS_ViewCubeOwner's specific DownCast.
                # Let the native controller dispatch this owner in C++.
                self.exit_plane(restore=False)
                self.cube_controller.SelectInViewer(Graphic3d_Vec2i(x, y))
                self.cube_controller.FlushViewEvents(self.context, self.view, True)
                self.update()
            else:
                consumed = self.tool_pick_handler is not None and self.tool_pick_handler(point.x(), point.y())
                if not consumed:
                    self.selection.click(x, y, bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
        elif gesture == Gesture.WINDOW:
            scale = self.devicePixelRatioF()
            self.camera.window(round(start[0] * scale), round(start[1] * scale), *self.pixel(point))
            self.window_zoom = False
        self.rubber.hide()
        self.restore_cursor()
        event.accept()

    def cube_at(self, point):
        self.context.MoveTo(*self.pixel(point), self.view, False)
        return self.context.HasDetected() and self.context.DetectedInteractive() == self.cube

    def mouseDoubleClickEvent(self, event):
        if self.editing_id is not None and event.button() == Qt.MouseButton.LeftButton:
            self.cancel_gesture()
            self.edit_double_clicked.emit(event.position().x(), event.position().y())
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        self.stop_camera_animation()
        if self.camera:
            delta = event.angleDelta().y() / 120
            if not delta:
                delta = event.pixelDelta().y() / 60
            if self.preferences.invert_zoom:
                delta = -delta
            self.camera.zoom(*self.pixel(event.position()), max(-8, min(8, delta)))
        event.accept()

    def leaveEvent(self, event):
        if self.tool_hover_handler is not None:
            self.tool_hover_handler(None, None)
        if self.selection:
            self.selection.leave()
        if self.editing_id is not None:
            self.edit_pointer_left.emit()
        super().leaveEvent(event)

    def focusOutEvent(self, event):
        self.cancel_gesture()
        super().focusOutEvent(event)

    def cancel_gesture(self):
        if self.editing_id is not None and self.state.button == "left":
            self.edit_gesture_canceled.emit()
        if self.camera:
            self.camera.end_rotation()
        self.state.cancel()
        self._cube_pressed = False
        self._middle_pivot = None
        self._navigation_modifiers = Qt.KeyboardModifier.NoModifier
        self.rubber.hide()
        self.window_zoom = False
        self.restore_cursor()

    def restore_cursor(self):
        if self.editing_id is not None and self.edit_cursor is not None and self.state.gesture == Gesture.IDLE:
            self.setCursor(self.edit_cursor)
        elif self.state.gesture == Gesture.IDLE:
            self.unsetCursor()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_gesture()
            if self.editing_id is None:
                self.exit_plane()
        elif self.state.button == "middle":
            self.switch_navigation_key(event, pressed=True)
            event.accept()  # Alt must not focus the menu during a navigation drag
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if self.state.button == "middle":
            self.switch_navigation_key(event, pressed=False)
            event.accept()
        else:
            super().keyReleaseEvent(event)

    def switch_navigation_key(self, event, *, pressed):
        from PySide6.QtCore import QPointF
        flag = {Qt.Key.Key_Control: Qt.KeyboardModifier.ControlModifier,
                Qt.Key.Key_Alt: Qt.KeyboardModifier.AltModifier}.get(event.key())
        if flag is None:
            return
        # Track keys through transitions; mouse events resynchronize the mask.
        # Clear integer bits explicitly: ~ on the installed PySide6 flag enum
        # also clears unrelated modifier bits (Ctrl release must preserve Alt).
        modifiers = self._navigation_modifiers
        bits = modifiers.value | flag.value if pressed else modifiers.value & ~flag.value
        modifiers = Qt.KeyboardModifier(bits)
        self.switch_navigation(modifiers, *self.pixel(QPointF(*self.state.last)))

    def switch_navigation(self, modifiers, x, y):
        if self.state.button == "middle":
            self._navigation_modifiers = modifiers
        if not self.state.update_navigation(
                ctrl=bool(modifiers & Qt.KeyboardModifier.ControlModifier),
                alt=bool(modifiers & Qt.KeyboardModifier.AltModifier),
                plane=self.mode == WorkMode.PLANE and self.editing_id is None):
            return False
        self.camera.end_rotation()
        if self.state.gesture == Gesture.ROTATE:
            self.camera.start_rotation(x, y, pivot=self._middle_pivot, screen_axes=self.editing_id is not None)
        elif self.state.gesture == Gesture.ROLL:
            self.camera.start_roll(x, y, pivot=self._middle_pivot)
        return True

    def closeEvent(self, event):
        if self.document.history.blocked:
            event.ignore()
            return
        if getattr(self, "context", None) and not self._native_closed:
            self.end_plane_edit()
            self._native_closed = True
            self.document_adapter.close()
            self.selection.detach()
            self.context.RemoveAll(False)
            self.view.Remove()
            # Release OCCT while Qt still owns a valid native window. Leaving
            # the Viewer3d -> parent cycle to GC can destroy the driver only
            # after its QWidget/HWND has already gone away.
            self.presentations.clear()
            self.selection = self.camera = None
            self.cube = self.cube_controller = self.local_axes = None
            self.context = self.view = None
            self.display._parent = None
            self.display = None
        super().closeEvent(event)
