"""Owned, selectable reference guides; document visibility stays untouched."""
from OCC.Core.AIS import AIS_Shape
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
from OCC.Core.Aspect import Aspect_TOL_DASH
from OCC.Core.Prs3d import Prs3d_LineAspect
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCC.Core.gp import gp_Pnt
from OCC.Core.Graphic3d import Graphic3d_ZLayerId_Topmost
from OCC.Core.TopAbs import TopAbs_EDGE
from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtWidgets import QLabel

from .core.feature_objects import SketchSourceObject
from .core.operation_model import GeometryReference
from .core.reference_geometry import resolve_plane, plane_shape
from .geometry import Vec3


class ReferenceGuides(QObject):
    def __init__(self, surface):
        super().__init__(surface._viewport())
        self.surface = surface
        self.items = []
        self.positions = []
        surface._viewport().installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Paint, QEvent.Type.Resize):
            self.position_labels()
        return False

    def position_labels(self):
        width, height = self.surface.size
        for (_, label, _), position in zip(self.items, self.positions):
            x,y = self.surface.project(position)
            visible = 0 <= x < width and 0 <= y < height
            label.setVisible(visible)
            if visible:
                label.move(min(round(x+6),width-label.width()), min(round(y+4),height-label.height()))

    def add(self, shape, reference, label, position, color):
        context = self.surface.context
        ais = AIS_Shape(shape)
        ais.SetDisplayMode(0)
        ais.SetZLayer(Graphic3d_ZLayerId_Topmost)
        native_color = Quantity_Color(*color, Quantity_TOC_RGB)
        ais.SetColor(native_color)
        ais.SetWidth(2*self.surface.pixel_ratio)
        aspect = Prs3d_LineAspect(native_color, Aspect_TOL_DASH, 2*self.surface.pixel_ratio)
        ais.Attributes().SetLineAspect(aspect)
        ais.Attributes().SetWireAspect(aspect)
        context.Display(ais, 0, AIS_Shape.SelectionMode(TopAbs_EDGE), False)
        if ais.Presentation() is not None:
            ais.Presentation().SetInfiniteState(True)
        text = QLabel(label, self.surface._viewport())
        text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text.setStyleSheet("background: palette(window); color: palette(text); padding: 2px 4px; border-radius: 3px;")
        text.adjustSize()
        self.items.append((ais, text, reference))
        self.positions.append(position)

    def show(self, kind):
        self.clear()
        document = self.surface.document
        if kind == "plane":
            for obj in document.objects.values():
                if obj.plane is None:
                    continue
                try:
                    plane = resolve_plane(document, obj)
                    self.add(plane_shape(plane, 70), GeometryReference("plane", obj.id), obj.name,
                             plane.to_world(70, 70), obj.color)
                except (ValueError, RuntimeError):
                    continue  # A broken support must not become a visual fallback.
        elif kind == "axis":
            for name, direction, color in (("X", Vec3(1,0,0), (.8,.25,.2)), ("Y", Vec3(0,1,0), (.25,.6,.25)),
                                            ("Z", Vec3(0,0,1), (.2,.4,.85))):
                a, b = direction*(-80), direction*80
                self.add(BRepBuilderAPI_MakeEdge(gp_Pnt(*a.tuple()), gp_Pnt(*b.tuple())).Edge(),
                         GeometryReference("axis", values=(0,0,0,*direction.tuple())), "Axe "+name, b, color)
            for obj in document.objects.values():
                if not isinstance(obj, SketchSourceObject) or obj.visible:
                    continue
                try:
                    sketch = obj.resolved(document).sketch
                    for entity in sketch.entities:
                        if entity.kind != "segment" or not entity.construction:
                            continue
                        a, b = sketch.to_world(entity.start), sketch.to_world(entity.end)
                        self.add(BRepBuilderAPI_MakeEdge(gp_Pnt(*a.tuple()), gp_Pnt(*b.tuple())).Edge(),
                                 GeometryReference("sketch_line", obj.id, entity_id=entity.id), obj.name, b, (.65,.4,.12))
                except (ValueError, RuntimeError):
                    continue
        self.surface.invalidate()
        self.position_labels()

    def reference(self, interactive):
        return next((reference for ais, _, reference in self.items if ais == interactive), None)

    def detected_reference(self):
        context = self.surface.context
        reference = self.reference(context.DetectedInteractive())
        if reference is not None and reference.kind == "axis":
            # Prefer an actual sketch reference over the coincident principal
            # axis guide, retaining its parametric identity after sketch edits.
            context.InitDetected()
            while context.MoreDetected():
                interactive = context.DetectedCurrentObject()
                candidate = self.reference(interactive)
                if candidate is not None and candidate.kind == "sketch_line":
                    return candidate
                viewport = self.surface._viewport()
                key = viewport.selection.object_id(interactive)
                entry = viewport.document_adapter.features.get(key)
                entity_id = entry[1].key(interactive) if entry and hasattr(entry[1], "key") else None
                sketch = self.surface.document.objects.get(key)
                if isinstance(sketch, SketchSourceObject) and entity_id is not None and sketch.sketch.entity(entity_id).kind == "segment":
                    return GeometryReference("sketch_line", key, entity_id=entity_id)
                context.NextDetected()
        return reference

    def clear(self):
        for ais, text, _ in self.items:
            self.surface.context.Remove(ais, False)
            text.hide()
            text.deleteLater()
        self.items.clear()
        self.positions.clear()
        self.surface.context.ClearDetected(False)

    def close(self):
        self.clear()
        self.surface._viewport().removeEventFilter(self)
        self.deleteLater()
