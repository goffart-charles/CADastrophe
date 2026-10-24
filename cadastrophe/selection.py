"""One selection authority for the tree and AIS topology owners."""
import logging
from sys import float_info
from OCC.Core.AIS import AIS_Shape
from OCC.Core import TopAbs
from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCC.Core.gp import gp_Dir, gp_Lin, gp_Pnt
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB, Quantity_TOC_sRGB
from OCC.Core.Prs3d import (Prs3d_TypeOfHighlight_Dynamic, Prs3d_TypeOfHighlight_LocalDynamic,
                            Prs3d_TypeOfHighlight_Selected, Prs3d_TypeOfHighlight_LocalSelected)
from .document import ObjectType, SelectionItem
from .geometry import Ray, Vec3
from OCC.Core.Graphic3d import Graphic3d_ZLayerId_Top

log = logging.getLogger(__name__)

PICK_TOPOLOGIES = (TopAbs.TopAbs_VERTEX, TopAbs.TopAbs_EDGE, TopAbs.TopAbs_FACE)
TOPOLOGY_NAMES = {TopAbs.TopAbs_FACE: "Face", TopAbs.TopAbs_EDGE: "Arête", TopAbs.TopAbs_VERTEX: "Sommet"}
PICK_SENSITIVITY = {TopAbs.TopAbs_VERTEX: 12, TopAbs.TopAbs_EDGE: 10, TopAbs.TopAbs_FACE: 2}


class SelectionController:
    def __init__(self, context, view, document, presentations, changed, invalidate):
        self.context, self.view = context, view
        self.presentations = presentations
        self.feature_presentations = {}
        self.display_object = lambda obj: obj
        self.changed, self.invalidate = changed, invalidate
        # Tree selections without a visible AIS owner: hidden or logical objects.
        self.hidden_selection = []
        self._publishing = False
        self.suspended = False
        self.document = None
        self.bind_document(document)
        self.set_pixel_ratio(1.)
        for style in (Prs3d_TypeOfHighlight_Dynamic, Prs3d_TypeOfHighlight_LocalDynamic):
            context.HighlightStyle(style).SetColor(Quantity_Color(0.12, 0.85, 0.95, Quantity_TOC_RGB))
            # Immediate feedback shares the scene depth; Topmost clears it and
            # makes occluded parts of the detected face/edge look transparent.
            context.HighlightStyle(style).SetZLayer(Graphic3d_ZLayerId_Top)
        for style in (Prs3d_TypeOfHighlight_Selected, Prs3d_TypeOfHighlight_LocalSelected):
            context.HighlightStyle(style).SetColor(Quantity_Color(1.0, 0.58, 0.08, Quantity_TOC_RGB))

    def bind_document(self, document):
        self._visibility_geometry = None
        self._visibility_intersectors = ()
        if self.document is not None:
            self.document.selection_changed.disconnect(self._document_selection_changed)
        self.document = document
        self.hidden_selection = []
        document.selection_changed.connect(self._document_selection_changed)

    def set_highlight_colors(self, preselection, selection):
        for style in (Prs3d_TypeOfHighlight_Dynamic, Prs3d_TypeOfHighlight_LocalDynamic):
            self.context.HighlightStyle(style).SetColor(Quantity_Color(*preselection, Quantity_TOC_sRGB))
        for style in (Prs3d_TypeOfHighlight_Selected, Prs3d_TypeOfHighlight_LocalSelected):
            self.context.HighlightStyle(style).SetColor(Quantity_Color(*selection, Quantity_TOC_sRGB))
        self.context.UpdateSelected(False)
        self.invalidate()

    def _document_selection_changed(self, object_ids):
        if self._publishing or self.suspended:
            return
        requested = set(object_ids)
        current = set(self.hidden_selection)
        remove = []
        self.context.InitSelected()
        while self.context.MoreSelected():
            object_id = self.object_id(self.context.SelectedInteractive())
            if object_id in requested:
                current.add(object_id)
            else:
                remove.append(self.context.SelectedOwner())
            self.context.NextSelected()
        for owner in remove:
            self.context.AddOrRemoveSelected(owner, False)
        self.hidden_selection = [key for key in self.hidden_selection if key in requested]
        for object_id in object_ids:
            if object_id not in current:
                if self.document.objects[object_id].visible and object_id in self.presentations:
                    self.context.AddOrRemoveSelected(self.presentations[object_id], False)
                else:
                    self.hidden_selection.append(object_id)
        self.publish()

    def detach(self):
        self._visibility_geometry = None
        self._visibility_intersectors = ()
        if self.document is not None:
            self.document.selection_changed.disconnect(self._document_selection_changed)
            self.document = None

    def object_id(self, interactive):
        for object_id, presentation in self.presentations.items():
            if interactive == presentation:
                return object_id
        for object_id, (_, renderer) in self.feature_presentations.items():
            owns = getattr(renderer, "owns", None)
            if callable(owns) and owns(interactive):
                return object_id
        return None

    def set_pixel_ratio(self, ratio):
        """Screen-space sensitivity, constant in Qt logical pixels at any zoom.

        Keep OCCT's depth-aware native ordering: nearby vertices > edges >
        faces at similar depth, never an x-ray priority over occluding faces.
        """
        self._pixel_ratio = ratio
        # A custom global tolerance is ADDED to each native sensitivity in
        # OCCT 7.9, not substituted. Use per-mode sensitivity instead.
        self.context.SetPixelTolerance(-1)
        for object_id in self.presentations:
            self.configure_object(object_id)

    def configure_object(self, object_id):
        obj = self.display_object(self.document.objects[object_id])
        ais = self.presentations[object_id]
        self.context.Deactivate(ais)
        if obj.visible and not self.suspended:
            if obj.plane:
                self.context.Activate(ais, AIS_Shape.SelectionMode(TopAbs.TopAbs_EDGE), True)
            else:
                # Multiple native modes active together, not a per-click mode
                # switch. Default B-Rep owner priorities resolve proximity.
                for topology in PICK_TOPOLOGIES:
                    mode = AIS_Shape.SelectionMode(topology)
                    self.context.Activate(ais, mode, True)
                    self.context.SetSelectionSensitivity(ais, mode, max(1, round(PICK_SENSITIVITY[topology] * self._pixel_ratio)))

    def deselect_object(self, object_id):
        owners = []
        self.context.InitSelected()
        while self.context.MoreSelected():
            if self.object_id(self.context.SelectedInteractive()) == object_id:
                owners.append(self.context.SelectedOwner())
            self.context.NextSelected()
        for owner in owners:
            self.context.AddOrRemoveSelected(owner, False)
        self.hidden_selection = [key for key in self.hidden_selection if key != object_id]

    def hover(self, x, y):
        if self.suspended:
            return
        # OCCT redraws only its immediate highlight layer when necessary.
        self.move_to(x, y, True)

    def move_to(self, x, y, redraw=False):
        """Keep native depth ordering among model objects ahead of support planes."""
        self.context.MoveTo(x, y, self.view, redraw)
        if not self.context.HasDetected():
            return
        key = self.object_id(self.context.DetectedInteractive())
        obj = self.document.objects.get(key)
        if obj is None or obj.plane is None:
            return
        target, count = None, 0
        self.context.InitDetected()
        while self.context.MoreDetected():
            interactive = self.context.DetectedCurrentObject()
            candidate = self.document.objects.get(self.object_id(interactive))
            count += 1
            if target is None and candidate is not None and candidate.plane is None:
                target = interactive
            self.context.NextDetected()
        if target is not None:
            for _ in range(count):
                self.context.HilightNextDetected(self.view, redraw)
                if self.context.DetectedInteractive() == target:
                    break

    def leave(self):
        self.context.ClearDetected(False)
        self.invalidate()

    def pick_surface(self, ray: Ray) -> Vec3 | None:
        """Exact nearest B-Rep hit, without changing AIS selection or detection.

        Navigation queries never activate modes, MoveTo, select or highlight.
        Only visible document bodies participate, not planes or view overlays.
        """
        line = gp_Lin(gp_Pnt(*ray.origin.tuple()), gp_Dir(*ray.direction.tuple()))
        nearest = float_info.max
        result = None
        for obj in map(self.display_object, self.document.objects.values()):
            if not obj.visible or obj.kind is not ObjectType.BODY or obj.shape is None or obj.shape.IsNull():
                continue
            try:
                intersector = IntCurvesFace_ShapeIntersector()
                intersector.Load(obj.shape, 1e-7)  # OCCT modelling tolerance, mm
                intersector.Perform(line, 0.0, nearest)
                if not intersector.IsDone():
                    log.warning("Surface picking did not complete for %s", obj.name)
                    continue
                for index in range(1, intersector.NbPnt() + 1):
                    depth = intersector.WParameter(index)
                    if 0.0 <= depth < nearest:
                        nearest = depth
                        result = Vec3(*intersector.Pnt(index).Coord())
            except (RuntimeError, IndexError):
                log.exception("Surface picking failed for %s", obj.name)
        return result

    def _occluders(self):
        return tuple(obj for obj in map(self.display_object, self.document.objects.values())
                     if obj.visible and obj.kind is ObjectType.BODY and obj.shape is not None and obj.transparency < 1 and not obj.shape.IsNull())

    @property
    def occlusion_state(self):
        camera = self.view.Camera()
        return camera.Eye().Coord(),camera.Direction().Coord(),camera.IsOrthographic(),self._occluders()

    def visible_points(self, points, *, tolerance=1e-6, from_near_plane=False):
        """Whole-label visibility at exact world anchors, without AIS picking.

        Load each B-Rep once per scene change, not once per label or orbit step.
        A surface hit at the anchor itself does not occlude its own annotation.
        """
        objects = self._occluders()
        if objects != self._visibility_geometry:
            intersectors = []
            for obj in objects:
                intersector = IntCurvesFace_ShapeIntersector()
                intersector.Load(obj.shape,1e-7)
                intersectors.append(intersector)
            self._visibility_geometry = objects
            self._visibility_intersectors = tuple(intersectors)
        camera = self.view.Camera()
        eye = Vec3(*camera.Eye().Coord())
        forward = Vec3(*camera.Direction().Coord())
        visible = []
        for point in points:
            delta = point-eye
            if delta.dot(forward) <= tolerance and not (from_near_plane and camera.IsOrthographic()):
                visible.append(False)
                continue
            if camera.IsOrthographic():
                depth = delta.dot(forward)
                if from_near_plane:
                    # Picking a flat sketch after FitAll may start with Eye
                    # on its plane. Annotation occlusion keeps its original
                    # eye-plane policy, independently of this picker option.
                    depth -= camera.ZNear()
                    if depth <= tolerance:
                        visible.append(False)
                        continue
                ray = Ray(point-forward*depth,forward)
            else:
                depth = delta.norm()
                ray = Ray(eye,delta)
            line = gp_Lin(gp_Pnt(*ray.origin.tuple()),gp_Dir(*ray.direction.tuple()))
            clear = True
            for intersector in self._visibility_intersectors:
                intersector.Perform(line,0.,depth-tolerance)
                if not intersector.IsDone() or intersector.NbPnt():
                    clear = False
                    break
            visible.append(clear)
        return tuple(visible)

    def click(self, x, y, ctrl=False):
        if self.suspended:
            return
        if not ctrl:
            self.hidden_selection = []
        self.move_to(x, y)
        if ctrl:
            self.context.ShiftSelect(False)
        else:
            self.context.Select(False)
        self.publish()

    def from_tree(self, object_ids):
        if self.suspended:
            return
        object_ids = list(dict.fromkeys(key for key in object_ids if key in self.document.objects))
        current = self.items()
        if (object_ids == [item.object_id for item in current] and
                all(item.topology in ("Objet", "Plan") for item in current)):
            return
        self.hidden_selection = [key for key in object_ids if not self.display_object(self.document.objects[key]).visible
                                 or key not in self.presentations]
        self.context.ClearSelected(False)
        for object_id in object_ids:
            obj = self.display_object(self.document.objects[object_id])
            if obj.visible and object_id in self.presentations:
                self.context.AddOrRemoveSelected(self.presentations[object_id], False)
        self.publish()

    def prepare_context(self, x, y):
        """Right-click targets the detected owner, preserving an already selected set."""
        if self.suspended:
            return False
        self.move_to(x, y)
        if not self.context.HasDetected() or self.object_id(self.context.DetectedInteractive()) is None:
            return False  # Background, cube and previews never clear the current selection.
        if not (self.context.IsSelected(self.context.DetectedOwner()) or
                self.context.IsSelected(self.context.DetectedInteractive().GlobalSelOwner())):
            self.hidden_selection = []
            self.context.Select(False)
            self.publish()
        return True

    def items(self):
        result = []
        self.context.InitSelected()
        while self.context.MoreSelected():
            object_id = self.object_id(self.context.SelectedInteractive())
            if object_id is not None and object_id in self.document.objects:
                obj = self.display_object(self.document.objects[object_id])
                if obj.kind is ObjectType.FEATURE:
                    if not any(item.object_id == object_id for item in result):
                        result.append(self._feature_item(obj))
                    self.context.NextSelected()
                    continue
                shape = self.context.SelectedShape() if self.context.HasSelectedShape() else obj.shape
                whole_object = self.context.SelectedOwner() == self.context.SelectedInteractive().GlobalSelOwner()
                topology = ("Plan" if obj.plane else "Objet" if whole_object else
                            TOPOLOGY_NAMES.get(shape.ShapeType(), "Objet"))
                result.append(SelectionItem(object_id, topology, shape, obj.revision))
            self.context.NextSelected()
        for object_id in self.hidden_selection:
            if object_id in self.document.objects:
                obj = self.display_object(self.document.objects[object_id])
                result.append(self._feature_item(obj) if obj.kind is ObjectType.FEATURE else
                              SelectionItem(object_id, "Plan" if obj.plane else "Objet", obj.shape, obj.revision))
        return result

    def _feature_item(self, obj):
        from .core.curve_model import CurveObject
        from .core.curve_geometry import curve_edge
        if isinstance(obj, CurveObject):
            try:
                return SelectionItem(obj.id, "Edge", curve_edge(self.document, obj), obj.revision)
            except (ValueError, RuntimeError):
                pass
        return SelectionItem(obj.id, "Objet", None, obj.revision)

    def suspend(self):
        """Latch transient owners only while their AIS and revision remain valid."""
        items, snapshot = self.items(), []
        self.context.InitSelected()
        while self.context.MoreSelected():
            ais = self.context.SelectedInteractive()
            object_id = self.object_id(ais)
            if object_id is not None:
                item = next(item for item in items if item.object_id == object_id and
                            (item.shape is None or item.shape.IsSame(self.context.SelectedShape()))) if self.context.HasSelectedShape() else next(
                                item for item in items if item.object_id == object_id)
                snapshot.append((item, self.context.SelectedOwner(), ais))
            self.context.NextSelected()
        snapshot.extend((item, None, None) for item in items if item.object_id in self.hidden_selection)
        self.suspended = True
        self.leave()
        self.context.ClearSelected(False)
        # A feature presenter may have redisplayed its AIS after the pick.
        # Clearing the old owner alone leaves a highlight on that reused AIS.
        for _, _, ais in snapshot:
            if ais is not None:
                self.context.Unhilight(ais, False)
        self.hidden_selection = []
        self.document.select_objects(())
        for object_id in self.presentations:
            self.configure_object(object_id)
        self.changed([])
        return tuple(snapshot)

    def resume(self, snapshot):
        self.suspended = False
        self.context.ClearSelected(False)
        self.hidden_selection = []
        for object_id in self.presentations:
            self.configure_object(object_id)
        for item, owner, ais in snapshot:
            obj = self.document.objects.get(item.object_id)
            if obj is None or obj.revision != item.revision:
                continue
            current = self.presentations.get(obj.id)
            if owner is not None:
                if obj.visible and current == ais:
                    self.context.AddOrRemoveSelected(owner, False)
                elif obj.kind is ObjectType.FEATURE:
                    self.hidden_selection.append(obj.id)
            elif current is not None and obj.visible:
                self.context.AddOrRemoveSelected(current, False)
            else:
                self.hidden_selection.append(obj.id)
        self.publish()

    def publish(self):
        if self.suspended:
            return
        items = self.items()
        self._publishing = True
        try:
            self.document.select_objects(item.object_id for item in items)
        finally:
            self._publishing = False
        self.changed(items)
        self.invalidate()
