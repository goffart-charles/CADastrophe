"""Owned native OCCT dimension objects and green sketch-relation glyphs."""
from OCC.Core.PrsDim import PrsDim_LengthDimension, PrsDim_RadiusDimension, PrsDim_DiameterDimension, PrsDim_AngleDimension
from math import degrees
from math import hypot
from OCC.Core.Prs3d import Prs3d_DimensionAspect
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_sRGB
from OCC.Core.Graphic3d import Graphic3d_ZLayerId_Topmost
from OCC.Core.gp import gp_Pnt, gp_Dir, gp_Pln, gp_Ax2, gp_Circ, gp_Trsf, gp_Vec
from OCC.Core.TopLoc import TopLoc_Location

from .constraints import DIMENSIONS, point
from .model import Segment, Vec2
from .editing import control_points
from .dimension_geometry import points, label_position, angle_geometry
from .markers import relation_marker


def make_annotation(sketch, constraint, units, ratio, theme, *, selected=False, position=None, equation=False):
    frame = sketch.support.frame
    def world(p):
        return gp_Pnt(*frame.to_world(p.u, p.v).tuple())
    position = world(label_position(sketch, constraint) if position is None else position)
    color = Quantity_Color(*( (1., .5, 0.) if selected else
        ((.88, .9, .94) if theme == "dark" else (.12, .13, .15)) if constraint.kind in DIMENSIONS else
        ((.35, .85, .4) if theme == "dark" else (.12, .55, .18))), Quantity_TOC_sRGB)
    if constraint.kind not in DIMENSIONS:
        # Visibility is tested at the anchor before display. Rendering the
        # whole bitmap in the overlay avoids slicing it with an oblique face.
        ais = relation_marker(constraint.kind,position,ratio,theme,selected)
    else:
        plane = gp_Pln(gp_Pnt(*frame.origin.tuple()), gp_Dir(*frame.normal.tuple()))
        if constraint.kind == "angle":
            ais = PrsDim_AngleDimension(*(world(p) for p in angle_geometry(sketch, constraint.refs)))
        elif constraint.kind in {"radius", "diameter"}:
            e = sketch.entity(constraint.refs[0].entity_id)
            circle = gp_Circ(gp_Ax2(world(e.center), gp_Dir(*frame.normal.tuple()), gp_Dir(*frame.u.tuple())), e.radius)
            ais = PrsDim_RadiusDimension(circle) if constraint.kind == "radius" else PrsDim_DiameterDimension(circle, plane)
        else:
            a, b = points(sketch, constraint.kind, constraint.refs)
            ais = PrsDim_LengthDimension(world(a), world(b), plane)
            if constraint.kind in {"horizontal_distance", "vertical_distance"}:
                ais.SetDirection(gp_Dir(*(frame.u if constraint.kind == "horizontal_distance" else frame.v).tuple()))
        aspect = Prs3d_DimensionAspect()
        aspect.MakeText3d(False)
        aspect.MakeArrows3d(False)
        aspect.MakeUnitsDisplayed(False)
        aspect.TextAspect().SetHeight(13*ratio)
        aspect.TextAspect().SetFont("Arial")
        aspect.ArrowAspect().SetLength(1.5)
        aspect.SetCommonColor(color)
        ais.SetDimensionAspect(aspect)
        # PrsDim_AngleDimension appends its own degree symbol.
        value = (f"{degrees(constraint.value):.2f}" if constraint.kind == "angle" else
                 f"{units.to_display(constraint.value):.{units.decimals}f}")
        if equation:
            value = "ƒx " + value
        if constraint.driven or all(r.entity_id in {e.geometry.id for e in sketch.external} for r in constraint.refs):
            value = "(" + value + ")"
        ais.SetCustomValue(value)
        ais.SetSelToleranceForText2d(5*ratio)
        ais.SetFlyout(8.)
        ais.SetTextPosition(position)
        if not ais.IsValid():
            raise ValueError("La géométrie ne permet pas d’afficher cette cote.")
    if constraint.kind in DIMENSIONS:
        ais.SetColor(color)
    ais.SetInfiniteState(True)
    ais.SetZLayer(Graphic3d_ZLayerId_Topmost)
    return ais


class Annotations:
    def __init__(self, surface):
        self.surface = surface
        self.items = {}
        self._cache = None
        self._model_cache = None
        self._center_sketch = self._center = None
        self._positions, self._shown = {}, {}
        self.selected = None

    def key(self, ais):
        return next((key for key, (_, item) in self.items.items() if item == ais), None)

    def update(self, sketch, *, visible=True, editing=False):
        document = getattr(self.surface, "document", None)
        obj = document.objects.get(sketch.id) if document is not None else None
        equations = tuple(e.target for e in obj.expressions) if obj is not None else ()
        model = (sketch, visible, editing, self.surface.pixel_ratio, self.surface.preferences.theme,
                 equations, self.surface.preferences.units, self.selected)
        if model == self._model_cache and not editing:
            return  # Native dimensions already follow the camera.
        settings = (model, self.surface.size,
                    self.surface.occlusion_state if editing else None,
                    self.surface.project(sketch.support.frame.origin),
                    self.surface.project(sketch.support.frame.to_world(1,0)),
                    self.surface.project(sketch.support.frame.to_world(0,1)))
        if settings == self._cache:
            return
        rebuild = model != self._model_cache
        if rebuild:
            self.clear()
            self._model_cache = model
        self._cache = settings
        if not visible:
            return
        context = self.surface.context
        slots = {}
        prepared,anchors = [],{}
        for constraint in sketch.constraints:
            if not editing and constraint.kind not in DIMENSIONS:
                continue
            position = None
            if constraint.kind not in DIMENSIONS and constraint.label_position is None:
                anchor = constraint.refs[0]
                slot = slots.get(anchor,0)
                slots[anchor] = slot+1
                position = self.relation_position(sketch,constraint,slot)
            prepared.append((constraint,position))
            if constraint.kind not in DIMENSIONS:
                anchors[constraint.id] = sketch.to_world(position if position is not None else label_position(sketch,constraint))
        visibility = dict(zip(anchors,self.surface.visible_points(anchors.values()))) if anchors else {}
        for constraint,position in prepared:
            if rebuild:
                ais = make_annotation(sketch, constraint, self.surface.preferences.units,
                                      self.surface.pixel_ratio, self.surface.preferences.theme, selected=constraint.id == self.selected,
                                      position=position, equation=constraint.id in equations)
                context.Display(ais, False)
                context.Deactivate(ais)
                self.items[constraint.id] = constraint, ais
            else:
                ais = self.items[constraint.id][1]
                if constraint.kind not in DIMENSIONS and self._positions.get(constraint.id) != position:
                    world = sketch.to_world(position if position is not None else label_position(sketch,constraint))
                    placement = gp_Trsf()
                    placement.SetTranslation(gp_Vec(*world.tuple()))
                    context.SetLocation(ais, TopLoc_Location(placement))
            self._positions[constraint.id] = position
            shown = visibility.get(constraint.id,True)
            if rebuild or self._shown.get(constraint.id) != shown:
                if shown:
                    if not rebuild:
                        context.Display(ais, False)
                    if editing:
                        context.Activate(ais, 0, True)
                        context.SetSelectionSensitivity(ais, 0, max(1, round(5*self.surface.pixel_ratio)))
                else:
                    context.Erase(ais,False)
            self._shown[constraint.id] = shown
        self.surface.invalidate()

    def relation_position(self, sketch, constraint, slot=0):
        ref = constraint.refs[0]
        entity = sketch.entity(ref.entity_id)
        if ref.part == "entity" and type(entity) is Segment:
            a,b = entity.start,entity.end
            anchor = Vec2((a.u+b.u)/2,(a.v+b.v)/2)
            ax,ay = self.surface.project(sketch.to_world(a))
            bx,by = self.surface.project(sketch.to_world(b))
            length = max(hypot(bx-ax,by-ay),1e-9)
            dx,dy = -(by-ay)/length,(bx-ax)/length
            # Put icons outside the sketch's center, perpendicular to their edge.
            if self._center_sketch is not sketch:
                coords = [p for e in sketch.entities for _,p in control_points(e)]
                self._center = Vec2(sum(p.u for p in coords)/len(coords),sum(p.v for p in coords)/len(coords)) if coords else None
                self._center_sketch = sketch
            if self._center is not None:
                center = self._center
                cx,cy = self.surface.project(sketch.to_world(center))
                x,y = self.surface.project(sketch.to_world(anchor))
                if (x-cx)*dx+(y-cy)*dy < 0:
                    dx,dy = -dx,-dy
        else:
            anchor = point(entity,ref.part) if ref.part != "entity" else entity.center
            dx,dy = .75,-.75
        x,y = self.surface.project(sketch.to_world(anchor))
        offset = 15+slot*18
        return sketch.project_ray(self.surface.screen_ray(x+dx*offset,y+dy*offset)) or label_position(sketch,constraint)

    def clear(self):
        for _, ais in self.items.values():
            self.surface.context.Remove(ais, False)
        self.items.clear()
        self._cache = None
        self._model_cache = None
        self._center_sketch = self._center = None
        self._positions.clear()
        self._shown.clear()
