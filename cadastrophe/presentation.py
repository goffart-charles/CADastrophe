"""Small native rendering surface exposed to owned plugin presenters."""
from weakref import ref

from PySide6.QtCore import QPointF, QRect, Qt
from OCC.Core.AIS import AIS_SelectionScheme_Replace
from OCC.Core.Graphic3d import Graphic3d_Vec2i
from OCC.Core.gp import gp_Pnt
from .interaction import Gesture


class PresentationSurface:
    def __init__(self, viewport):
        self._viewport = ref(viewport)

    @property
    def context(self):
        return self._viewport().context

    @property
    def pixel_ratio(self):
        return self._viewport().devicePixelRatioF()

    @property
    def document(self):
        return self._viewport().document

    @property
    def preferences(self):
        return self._viewport().preferences

    @property
    def navigating(self):
        return self._viewport().navigating

    def invalidate(self):
        self._viewport().update()

    def project(self, world):
        viewport = self._viewport()
        point = viewport.view.Camera().Project(gp_Pnt(*world.tuple()))
        return (point.X()+1)*viewport.width()/2, (1-point.Y())*viewport.height()/2-1/self.pixel_ratio

    @property
    def size(self):
        viewport = self._viewport()
        return viewport.width(), viewport.height()

    def screen_ray(self, x: float, y: float):
        viewport = self._viewport()
        return viewport.camera.screen_ray(*viewport.pixel(QPointF(x, y)))

    @property
    def occlusion_state(self):
        """Camera and visible-body state used to invalidate label visibility."""
        return self._viewport().selection.occlusion_state

    def visible_points(self, points):
        """Batch visibility of world anchors, independent of selection/highlight."""
        return self._viewport().selection.visible_points(points)

    @property
    def selection_enabled(self):
        return not self._viewport().selection.suspended


class ToolInteractionSurface(PresentationSurface):
    """Owned input bridge for parameter tools; camera/picking stay in the host."""
    def __init__(self, viewport, clicked, hovered=None):
        super().__init__(viewport)
        if viewport.tool_pick_handler is not None:
            raise RuntimeError("Une sélection d’outil est déjà active.")
        self.closed = False
        self._handler = clicked
        self._hover_handler = hovered
        self._hovered_object = None
        self._feedback = []
        self._feedback_key = None
        self._reference_guides = None
        viewport.tool_pick_handler = clicked
        viewport.tool_hover_handler = hovered

    def highlight_object(self, key):
        if key == self._hovered_object:
            return
        viewport = self._viewport()
        for value, hovered in ((self._hovered_object, False), (key, True)):
            entry = viewport.document_adapter.features.get(value)
            if entry is not None and callable(getattr(entry[1], "set_hover", None)):
                entry[1].set_hover(hovered)
        self._hovered_object = key
        viewport.update()

    def highlight_regions(self, selected=(), hovered=None, *, outline=False):
        key = tuple(id(shape) for shape in selected), id(hovered) if hovered is not None else None, outline
        if key == self._feedback_key:
            return
        from OCC.Core.AIS import AIS_Shape
        from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
        from OCC.Core.Aspect import Aspect_POM_Fill
        from OCC.Core.Graphic3d import Graphic3d_TOSM_UNLIT
        self._clear_feedback()
        self._feedback_key = key
        items = [(face, (.2, .55, .9)) for face in selected if face is not hovered]
        if hovered is not None:
            items.append((hovered, (1., .55, .12)))
        for shape, rgb in items:
            ais = AIS_Shape(shape)
            mode = 0 if outline else 1
            ais.SetDisplayMode(mode)
            ais.SetWidth(2*self.pixel_ratio)
            ais.SetColor(Quantity_Color(*rgb, Quantity_TOC_RGB))
            ais.SetTransparency(.25)
            # Both terms pull the fill towards the camera. A positive slope
            # term fights the solid again on tilted coplanar faces.
            ais.SetPolygonOffsets(Aspect_POM_Fill, -2., -4.)
            ais.Attributes().ShadingAspect().Aspect().SetShadingModel(Graphic3d_TOSM_UNLIT)
            ais.Attributes().SetFaceBoundaryDraw(True)
            self.context.Display(ais, mode, -1, False)
            self.context.Deactivate(ais)
            ais.Presentation().SetInfiniteState(True)
            self._feedback.append(ais)
        self.invalidate()

    def _clear_feedback(self):
        for ais in self._feedback:
            self.context.Remove(ais, False)
        self._feedback.clear()
        self._feedback_key = None

    def detect_object(self, x, y, *, redraw=False):
        viewport = self._viewport()
        viewport.selection.move_to(*viewport.pixel(QPointF(x, y)), redraw)
        if viewport.context.HasDetected():
            return viewport.selection.object_id(viewport.context.DetectedInteractive())
        return None

    @property
    def ctrl(self):
        return bool(self._viewport()._edit_modifiers & Qt.KeyboardModifier.ControlModifier)

    def show_reference_guides(self, kind=None):
        if self._reference_guides is None and kind is not None:
            from .reference_guides import ReferenceGuides
            self._reference_guides = ReferenceGuides(self)
        if self._reference_guides is not None:
            self._reference_guides.show(kind)

    def pick_face(self, x, y):
        source_picker = getattr(getattr(self._handler, "__self__", None), "pick_shape", None)
        if not callable(source_picker):
            return PlaneEditSurface.pick_topology(self, x, y, kinds=("Face",))
        # Historical previews hide the final AIS. Intersect the exact source
        # B-Rep using the common logical-pixel ray, as for source-edge picking.
        from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector
        from OCC.Core.gp import gp_Lin, gp_Dir
        from .document import ObjectType, SelectionItem
        ray = self.screen_ray(x, y)
        hits = []
        for original in self.document.objects.values():
            obj = self._viewport().document_adapter.display_object(original)
            if obj.kind is not ObjectType.BODY or not obj.visible:
                continue
            shape = source_picker(obj.id)
            if shape is None:
                continue
            intersector = IntCurvesFace_ShapeIntersector()
            intersector.Load(shape, 1e-7)
            intersector.Perform(gp_Lin(gp_Pnt(*ray.origin.tuple()), gp_Dir(*ray.direction.tuple())), 0., 1e100)
            hits.extend((intersector.WParameter(i), SelectionItem(obj.id, "Face", intersector.Face(i), obj.revision))
                        for i in range(1, intersector.NbPnt()+1))
        return min(hits, key=lambda hit: hit[0])[1] if hits else None

    def face_point(self, item, x, y):
        from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector
        from OCC.Core.gp import gp_Lin, gp_Dir
        from .geometry import Vec3
        ray = self.screen_ray(x, y)
        intersector = IntCurvesFace_ShapeIntersector()
        intersector.Load(item.shape, 1e-7)
        intersector.Perform(gp_Lin(gp_Pnt(*ray.origin.tuple()), gp_Dir(*ray.direction.tuple())), 0., 1e100)
        hits = sorted(((intersector.WParameter(i), intersector.Pnt(i)) for i in range(1, intersector.NbPnt()+1)),
                      key=lambda hit: hit[0])
        return Vec3(*hits[0][1].Coord()) if hits else None

    def pick_edge(self, x, y):
        item = PlaneEditSurface.pick_topology(self, x, y)
        if item is not None and item.topology == "Edge":
            return item
        # A parameter preview hides the original AIS. Retain picking of its
        # source edges using exact B-Rep curves projected into logical pixels.
        from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
        from OCC.Core.TopExp import topexp
        from OCC.Core.TopTools import TopTools_IndexedMapOfShape
        from OCC.Core.TopAbs import TopAbs_EDGE
        from OCC.Core.TopoDS import topods
        from .geometry import Vec3
        from .document import ObjectType, SelectionItem
        candidates = []
        source_picker = getattr(getattr(self._handler, "__self__", None), "pick_shape", None)
        for original in self.document.objects.values():
            obj = self._viewport().document_adapter.display_object(original)
            if obj.kind is not ObjectType.BODY or not obj.visible:
                continue
            source_shape = source_picker(obj.id) if callable(source_picker) else obj.shape
            if source_shape is None:
                continue
            mapping = TopTools_IndexedMapOfShape()
            topexp.MapShapes(source_shape, TopAbs_EDGE, mapping)
            for index in range(1, mapping.Size()+1):
                edge = topods.Edge(mapping.FindKey(index))
                curve = BRepAdaptor_Curve(edge)
                first, last = curve.FirstParameter(), curve.LastParameter()
                points = []
                for i in range(33):
                    t = first+(last-first)*i/32
                    p = curve.Value(t)
                    world = Vec3(p.X(), p.Y(), p.Z())
                    points.append((t, self.project(world)))
                for (ta, a), (tb, b) in zip(points, points[1:]):
                    dx, dy = b[0]-a[0], b[1]-a[1]
                    ratio = max(0., min(1., ((x-a[0])*dx+(y-a[1])*dy)/max(dx*dx+dy*dy, 1e-20)))
                    distance = (x-a[0]-ratio*dx)**2+(y-a[1]-ratio*dy)**2
                    if distance <= 8**2:
                        p = curve.Value(ta+ratio*(tb-ta))
                        world = Vec3(p.X(), p.Y(), p.Z())
                        if self.visible_points((world,))[0]:
                            candidates.append((distance, obj, edge))
        if candidates:
            _, obj, edge = min(candidates, key=lambda c: c[0])
            return SelectionItem(obj.id, "Edge", edge, obj.revision)
        return None

    def pick_curve(self, x, y):
        """Capture a curve by sketch entity UUID or named body producer."""
        from .core.operation_model import GeometryReference
        from .core.reference_geometry import capture_reference
        from .core.feature_objects import SketchSourceObject
        from .core.curve_model import CurveObject
        key = self.detect_object(x, y)
        obj = self.document.objects.get(key)
        entry = self._viewport().document_adapter.features.get(key)
        if isinstance(obj, CurveObject):
            return GeometryReference('curve', obj.id)
        if isinstance(obj, SketchSourceObject) and entry and self.context.HasDetected() and hasattr(entry[1], "key"):
            entity_id = entry[1].key(self.context.DetectedInteractive())
            if entity_id is not None and obj.sketch.entity(entity_id).kind != "point":
                return GeometryReference("sketch_curve", key, entity_id=entity_id)
        item = self.pick_edge(x, y)
        return capture_reference(self.document, item) if item is not None else None

    def pick_reference(self, x, y, kind):
        """Precise topology or stable sketch entity for an active reference field."""
        from .core.operation_model import GeometryReference
        from .core.reference_geometry import capture_reference
        viewport = self._viewport()
        key = self.detect_object(x, y)
        if self._reference_guides is not None and self.context.HasDetected():
            reference = self._reference_guides.detected_reference()
            if reference is not None:
                return reference
        obj = self.document.objects.get(key)
        if kind == "plane" and obj is not None and obj.plane is not None:
            return GeometryReference("plane", obj.id)
        if kind == "axis":
            entry = viewport.document_adapter.features.get(key)
            entity_id = entry[1].key(self.context.DetectedInteractive()) if entry and hasattr(entry[1], "key") else None
            if entity_id is not None and obj.sketch.entity(entity_id).kind == "segment":
                return GeometryReference("sketch_line", key, entity_id=entity_id)
        if kind == "point":
            from .core.feature_objects import SketchSourceObject
            candidates = []
            for original in self.document.objects.values():
                sketch = viewport.document_adapter.display_object(original)
                if not isinstance(sketch, SketchSourceObject) or not sketch.visible:
                    continue
                sketch = sketch.resolved(self.document)
                for entity in sketch.sketch.entities:
                    for part in ("start", "end", "center", "position"):
                        point = getattr(entity, part, None)
                        if point is None:
                            continue
                        world = sketch.sketch.to_world(point)
                        px, py = self.project(world)
                        distance = (px-x)**2+(py-y)**2
                        if distance <= 10**2 and self.visible_points((world,))[0]:
                            candidates.append((distance, GeometryReference("sketch_point", sketch.id, entity_id=entity.id, part=part)))
            if candidates:
                return min(candidates, key=lambda v: v[0])[1]
        owner = getattr(self._handler, "__self__", None)
        capture_pick = getattr(owner, "reference_from_pick", None)
        names = {"plane": ("Face",), "axis": ("Edge", "Face"), "point": ("Vertex",)}[kind]
        item = self.pick_edge(x, y) if kind == "axis" and callable(capture_pick) else None
        if item is None:
            item = PlaneEditSurface.pick_topology(self, x, y, kinds=names)
        if item is None and kind == "axis":
            item = self.pick_edge(x, y)
        if item is None and callable(capture_pick) and kind in {"plane", "axis"}:
            item = self.pick_face(x, y)
        if item is None:
            return None
        return capture_pick(item) if callable(capture_pick) else capture_reference(self.document, item)

    def visible_points(self, points):
        return self._viewport().selection.visible_points(points, from_near_plane=True)

    def close(self):
        if not self.closed:
            viewport = self._viewport()
            self.highlight_object(None)
            self._clear_feedback()
            if self._reference_guides is not None:
                self._reference_guides.close()
                self._reference_guides = None
            if viewport is not None and viewport.tool_pick_handler is self._handler:
                viewport.tool_pick_handler = None
                viewport.tool_hover_handler = None
            self.closed = True


class PlaneEditSurface(PresentationSurface):
    """Owned input/render bridge. Coordinates are Qt logical pixels, not 3D.

    The host disconnects every subscription before restoring/destroying the view.
    Navigation remains entirely in CadViewport; plugins only receive valid clicks.
    """
    def __init__(self, viewport, *, clipboard=None, state_changed=None):
        super().__init__(viewport)
        self.clipboard = clipboard
        self._state_changed = state_changed
        self._connections = []
        self.closed = False

    @property
    def document(self):
        return self._viewport().document

    @property
    def units(self):
        return self._viewport().preferences.units

    @property
    def widget(self):
        """Qt parent for owned contextual overlays and input dialogs."""
        return self._viewport()

    def pick_topology(self, x, y, *, exclude=(), kinds=("Vertex", "Edge")):
        """Read an edge/vertex without publishing document selection in edit mode."""
        from OCC.Core.AIS import AIS_Shape
        from OCC.Core.TopAbs import TopAbs_VERTEX, TopAbs_EDGE, TopAbs_FACE
        from .document import ObjectType, SelectionItem
        viewport, context = self._viewport(), self.context
        activated = []
        try:
            for ais, _ in exclude:
                context.Deactivate(ais)
            for key, ais in viewport.presentations.items():
                obj = self.document.objects.get(key)
                if obj is not None and obj.visible and obj.kind is ObjectType.BODY and context.IsDisplayed(ais):
                    activated.append(ais)
            # Prefer a nearby vertex to an overlapping edge, including edges
            # perpendicular to the sketch plane whose projection is a point.
            # OCCT's mixed-mode depth ranking can otherwise hide the vertex.
            topology_types = {"Vertex": TopAbs_VERTEX, "Edge": TopAbs_EDGE, "Face": TopAbs_FACE}
            for name in kinds:
                topology = topology_types[name]
                mode = AIS_Shape.SelectionMode(topology)
                for ais in activated:
                    context.Deactivate(ais)
                    context.Activate(ais, mode, True)
                    context.SetSelectionSensitivity(ais, mode, max(1, round(6*self.pixel_ratio)))
                viewport.selection.move_to(*viewport.pixel(QPointF(x, y)))
                if context.HasDetected() and context.HasDetectedShape():
                    key = viewport.selection.object_id(context.DetectedInteractive())
                    if key is not None and self.document.objects[key].kind is ObjectType.BODY:
                        shape = context.DetectedShape()
                        if shape.ShapeType() == topology:
                            return SelectionItem(key, name, shape, self.document.objects[key].revision)
        finally:
            for ais in activated:
                context.Deactivate(ais)
            for ais, mode in exclude:
                context.Activate(ais, mode, True)
            if not viewport.selection.suspended:
                for key in viewport.presentations:
                    viewport.selection.configure_object(key)
            context.ClearDetected(False)

    @property
    def presentation(self):
        viewport = self._viewport()
        entry = viewport.document_adapter.features.get(viewport.editing_id)
        return entry[1] if entry is not None else None

    @property
    def ctrl(self):
        return bool(self._viewport()._edit_modifiers & Qt.KeyboardModifier.ControlModifier)

    @property
    def alt(self):
        return bool(self._viewport()._edit_modifiers & Qt.KeyboardModifier.AltModifier)

    @property
    def dragging(self):
        return self._viewport().state.gesture == Gesture.DRAG

    def detect(self, x, y):
        viewport = self._viewport()
        viewport.selection.move_to(*viewport.pixel(QPointF(x, y)), True)
        return viewport.context.DetectedInteractive() if viewport.context.HasDetected() else None

    def clear_detected(self):
        self.context.ClearDetected(False)

    def rectangle(self, start=None, end=None):
        viewport = self._viewport()
        if start is None:
            viewport.rubber.hide()
        else:
            viewport.rubber.setGeometry(QRect(QPointF(*start).toPoint(), QPointF(*end).toPoint()).normalized())
            viewport.rubber.show()

    def select_rectangle(self, start, end):
        viewport = self._viewport()
        low = QPointF(min(start[0], end[0]), min(start[1], end[1]))
        high = QPointF(max(start[0], end[0]), max(start[1], end[1]))
        context = self.context
        context.SelectRectangle(Graphic3d_Vec2i(*viewport.pixel(low)), Graphic3d_Vec2i(*viewport.pixel(high)),
                                viewport.view, AIS_SelectionScheme_Replace)
        selected = []
        context.InitSelected()
        while context.MoreSelected():
            selected.append(context.SelectedInteractive())
            context.NextSelected()
        context.ClearSelected(False)
        context.ClearDetected(False)
        return tuple(selected)

    def cancel_pointer(self):
        self._viewport().cancel_gesture()

    def notify_state(self):
        if not self.closed and self._state_changed is not None:
            self._state_changed()

    def connect(self, event: str, slot):
        if self.closed or event not in ("clicked", "pressed", "released", "gesture_canceled",
                                        "pointer_moved", "pointer_left", "view_changed", "double_clicked"):
            raise ValueError("Événement d’édition indisponible.")
        signal = getattr(self._viewport(), "edit_" + event)
        signal.connect(slot)
        self._connections.append((signal, slot))

    def focus(self):
        if not self.closed:
            self._viewport().setFocus(Qt.FocusReason.OtherFocusReason)

    def set_cursor(self, cursor=None):
        if not self.closed:
            self._viewport().edit_cursor = cursor
            self._viewport().restore_cursor()

    def close(self):
        if self.closed:
            return
        self.set_cursor(None)
        self.closed = True
        for signal, slot in self._connections:
            signal.disconnect(slot)
        self._connections.clear()
