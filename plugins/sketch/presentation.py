"""Derived native OCCT edges and point datums, never business storage."""
from math import pi

from OCC.Core.AIS import AIS_Point, AIS_Shape
from OCC.Core.Aspect import Aspect_TOM_USERDEFINED, Aspect_TOL_DASH
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
from OCC.Core.Geom import Geom_CartesianPoint
from OCC.Core.Graphic3d import Graphic3d_ZLayerId_Default, Graphic3d_ZLayerId_Top, Graphic3d_ZLayerId_Topmost
from OCC.Core.Prs3d import Prs3d_Drawer, Prs3d_LineAspect
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_sRGB
from OCC.Core.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

from .model import Arc, Circle, Point, Segment, Ellipse, Spline
from .annotations import Annotations
from .editing import control_points
from .markers import point_aspect
from .constraint_status import classify, Status


# Classic sketch colors. E6 reports a local global DOF estimate in the panel;
# it does not claim an individual constraint classification for every entity.
COLORS = {"light": {"editing": (0., 0., 1.), "inactive": (.5, .5, .5), "selected": (0., .65, 1.)},
          "dark": {"editing": (.42, .62, 1.), "inactive": (.66, .70, .76), "selected": (.1, .8, 1.)}}
HOVER = (1., .5, 0.)
DEFINED = {"light": (0.,0.,0.),"dark": (.92,.93,.95)}


def _construction_style(drawer, color, ratio):
    aspect = Prs3d_LineAspect(color, Aspect_TOL_DASH, 1.5 * ratio)
    drawer.SetWireAspect(aspect)
    drawer.SetLineAspect(aspect)


def entity_shape(sketch, entity):
    """Exact native edge; a Point uses AIS_Point instead of a fake edge."""
    world = sketch.to_world
    if type(entity) in (Ellipse, Spline):
        from .curve_native import edge
        return edge(sketch, entity)
    if type(entity) is Segment:
        builder = BRepBuilderAPI_MakeEdge(gp_Pnt(*world(entity.start).tuple()), gp_Pnt(*world(entity.end).tuple()))
    elif isinstance(entity, Circle):
        frame = sketch.support.frame
        circle = gp_Circ(gp_Ax2(gp_Pnt(*world(entity.center).tuple()), gp_Dir(*frame.normal.tuple()),
                               gp_Dir(*frame.u.tuple())), entity.radius)
        if type(entity) is Arc:
            # Geometry only: clockwise and counterclockwise arcs share the
            # exact supporting curve; keep the intended interval across zero.
            start = entity.start_angle % (2*pi)
            first, last = sorted((start, start + entity.sweep))
            builder = BRepBuilderAPI_MakeEdge(circle, first, last)
        else:
            builder = BRepBuilderAPI_MakeEdge(circle)
    else:
        raise ValueError("Cette entité ne définit pas une arête.")
    if not builder.IsDone():
        raise ValueError("Impossible de construire l’arête d’esquisse.")
    return builder.Edge()


class SketchPresentation:
    def __init__(self, surface, *, preview=False, selection_priority=None, hover=False):
        self.surface = surface
        self.preview = preview
        self.entities = {}
        self.vertices = {}
        self.hover = hover
        self._frame = None
        self._style = None
        self._styles = {}
        self.selected_ids = set()
        self.hidden_ids = set()
        self.interactive = False
        self.selection_priority = selection_priority
        self._state = None
        self.annotations = Annotations(surface)
        self._constraint_key = None
        self.constraint_status = Status()

    def key(self, ais):
        vertex = self.vertex_ref(ais)
        return vertex[0] if vertex else next((key for key, (_, presentation) in self.entities.items() if presentation == ais), None)

    def owns(self, ais):
        """Route native owners to this logical sketch outside entity editing."""
        return self.key(ais) is not None

    def set_hover(self, hovered):
        self.hover = hovered
        if self._state is not None:
            obj, selected, editing = self._state
            self.update(obj, selected=selected, editing=editing)

    def vertex_ref(self, ais):
        return next((ref for ref, (_, item) in self.vertices.items() if item == ais), None)

    def nearest_vertex(self, x, y):
        """Logical-pixel hit area, independent of OCCT's point sensitivity cap."""
        if not self.interactive or self._state is None:
            return None
        sketch = self._state[0].sketch
        candidates = [(ref,data[0][0]) for ref,data in self.vertices.items()]
        candidates += [((e.id,"position"),e.position) for e,_ in self.entities.values()
                       if type(e) is Point and e.id not in self.hidden_ids]
        close = []
        for ref,p in candidates:
            px,py = self.surface.project(sketch.to_world(p))
            distance = (px-x)**2+(py-y)**2
            if distance <= 10**2:
                close.append((distance,ref))
        return min(close)[1] if close else None

    def set_state(self, *, selected_ids=None, hidden_ids=None, interactive=None):
        if selected_ids is not None:
            self.selected_ids = set(selected_ids)
        if hidden_ids is not None:
            self.hidden_ids = set(hidden_ids)
        if interactive is not None:
            self.interactive = interactive
        if self._state is not None:
            obj, selected, editing = self._state
            self.update(obj, selected=selected, editing=editing)

    def update(self, obj, *, selected=False, editing=False):
        context = self.surface.context
        from cadastrophe.core.reference_objects import DatumPlaneObject
        if obj.producer_refs or isinstance(self.surface.document.objects.get(obj.sketch.support.plane_id), DatumPlaneObject):
            from .construction_context import resolve_object
            try:
                obj = resolve_object(obj, self.surface.document)
            except (ValueError, RuntimeError):
                self.clear()
                return
        sketch = obj.sketch
        constraint_key = (sketch.entities,sketch.constraints,sketch.external)
        if not self.preview and constraint_key != self._constraint_key:
            self.constraint_status = classify(sketch)
            self._constraint_key = constraint_key
        if self._frame != sketch.support.frame:
            self.clear()
            self._frame = sketch.support.frame
        current = {entity.id: entity for entity in sketch.all_entities}
        external_ids = {e.geometry.id for e in sketch.external}
        for key in tuple(self.entities):
            if key not in current or current[key] != self.entities[key][0]:
                context.Remove(self.entities.pop(key)[1], False)
                self._styles.pop(key, None)
        ratio = self.surface.pixel_ratio
        theme = self.surface.preferences.theme
        interactive = self.interactive or (not self.preview and self.surface.selection_enabled)
        for key, entity in current.items():
            active = selected or (editing and key in self.selected_ids)
            visible = obj.visible and key not in self.hidden_ids
            fully_defined = key in self.constraint_status.entities
            style = (active, editing, visible, ratio, interactive, self.selection_priority, theme, fully_defined, self.hover)
            role = "selected" if active else "editing" if editing else "inactive"
            rgb = HOVER if self.hover else (.75,.15,.65) if key in external_ids and not active else DEFINED[theme] if fully_defined and editing and not active else COLORS[theme][role]
            color = Quantity_Color(*rgb,Quantity_TOC_sRGB)
            new = key not in self.entities
            if new:
                if type(entity) is Point:
                    ais = AIS_Point(Geom_CartesianPoint(gp_Pnt(*sketch.to_world(entity.position).tuple())))
                    ais.SetMarker(Aspect_TOM_USERDEFINED)
                else:
                    ais = AIS_Shape(entity_shape(sketch, entity))
                ais.SetInfiniteState(self.preview)  # drafts never change native FitAll bounds
                hover = Prs3d_Drawer()
                hover.SetColor(Quantity_Color(*HOVER, Quantity_TOC_sRGB))
                hover.SetZLayer(Graphic3d_ZLayerId_Topmost)
                if entity.construction:
                    _construction_style(hover, Quantity_Color(*HOVER, Quantity_TOC_sRGB), ratio)
                    selected_drawer = Prs3d_Drawer()
                    _construction_style(selected_drawer, Quantity_Color(*COLORS[theme]["selected"], Quantity_TOC_sRGB), ratio)
                    selected_drawer.SetZLayer(Graphic3d_ZLayerId_Topmost)
                    ais.SetHilightAttributes(selected_drawer)
                ais.SetDynamicHilightAttributes(hover)
                self.entities[key] = (entity, ais)
            else:
                ais = self.entities[key][1]
            if new or style != self._styles.get(key):
                ais.DynamicHilightAttributes().SetZLayer(Graphic3d_ZLayerId_Topmost if editing else Graphic3d_ZLayerId_Top)
                if entity.construction:
                    ais.HilightAttributes().SetZLayer(Graphic3d_ZLayerId_Topmost if editing else Graphic3d_ZLayerId_Top)
                ais.SetColor(color)
                if type(entity) is Point:
                    ais.Attributes().SetPointAspect(point_aspect(color,ratio,selected=active,hover=self.hover))
                else:
                    ais.SetWidth(1.5 * ratio)
                    if entity.construction:
                        _construction_style(ais.Attributes(), color, ratio)
                ais.SetZLayer(Graphic3d_ZLayerId_Topmost if editing else Graphic3d_ZLayerId_Default)
                if visible:
                    ais.SetDisplayMode(0)
                    context.Display(ais, False)
                    # Redisplay can rebuild selection: activate its final native
                    # representation, not a selection-disabled display (-1).
                    context.Redisplay(ais, False)
                    context.Deactivate(ais)
                    if interactive:
                        context.Activate(ais, 0, True)
                        context.SetSelectionSensitivity(ais, 0, max(1, round((10 if type(entity) is Point else 6) * ratio)))
                        owner = ais.GlobalSelOwner()
                        if self.selection_priority is not None and owner is not None:
                            owner.SetPriority(self.selection_priority)
                else:
                    context.Erase(ais, False)
                self._styles[key] = style
        self._state = (obj, selected, editing)
        if not self.preview:
            self._update_vertices(obj, editing)
            self.annotations.update(sketch, visible=obj.visible, editing=editing)
        self.surface.invalidate()

    def _update_vertices(self, obj, editing):
        context, sketch = self.surface.context, obj.sketch
        ratio, theme = self.surface.pixel_ratio, self.surface.preferences.theme
        wanted, seen = {}, set()
        if editing and obj.visible:
            for e in sketch.all_entities:
                if type(e) is Point or e.id in self.hidden_ids:
                    continue
                for part,p in control_points(e):
                    if part == "radius" or p in seen:
                        continue
                    seen.add(p)
                    wanted[(e.id,part)] = p
        for ref in tuple(self.vertices):
            if ref not in wanted or self.vertices[ref][0] != (wanted[ref],ratio,theme,self.interactive,ref[0] in self.selected_ids,ref in self.constraint_status.points):
                context.Remove(self.vertices.pop(ref)[1],False)
        for ref,p in wanted.items():
            if ref in self.vertices:
                continue
            ais = AIS_Point(Geom_CartesianPoint(gp_Pnt(*sketch.to_world(p).tuple())))
            selected = ref[0] in self.selected_ids
            color = Quantity_Color(*(COLORS[theme]["selected"] if selected else DEFINED[theme] if ref in self.constraint_status.points else COLORS[theme]["editing"]),Quantity_TOC_sRGB)
            ais.SetMarker(Aspect_TOM_USERDEFINED)
            ais.SetColor(color)
            ais.Attributes().SetPointAspect(point_aspect(color,ratio,selected=selected))
            ais.SetInfiniteState(True)
            ais.SetZLayer(Graphic3d_ZLayerId_Topmost)
            context.Display(ais,False)
            context.Deactivate(ais)
            if self.interactive:
                context.Activate(ais,0,True)
                context.SetSelectionSensitivity(ais,0,max(1,round(10*ratio)))
                owner = ais.GlobalSelOwner()
                if owner is not None:
                    owner.SetPriority(8)
            self.vertices[ref] = ((p,ratio,theme,self.interactive,selected,ref in self.constraint_status.points),ais)

    def clear(self):
        self.annotations.clear()
        for _,ais in self.vertices.values():
            self.surface.context.Remove(ais,False)
        self.vertices.clear()
        for _, ais in self.entities.values():
            self.surface.context.Remove(ais, False)
        self.entities.clear()
        self._frame = self._style = None
        self._styles.clear()
        self._state = None
        self.surface.invalidate()
