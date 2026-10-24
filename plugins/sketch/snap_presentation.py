"""Transient inference marker, dashed guide and bounded native grid."""
from math import ceil, floor

from OCC.Core.AIS import AIS_Point, AIS_Shape
from OCC.Core.Aspect import Aspect_TOM_O_POINT, Aspect_TOM_O, Aspect_TOM_X, Aspect_TOL_DASH
from OCC.Core.BRep import BRep_Builder
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeVertex, BRepBuilderAPI_MakeEdge
from OCC.Core.Geom import Geom_CartesianPoint
from OCC.Core.Graphic3d import Graphic3d_ZLayerId_Topmost
from OCC.Core.Prs3d import Prs3d_PointAspect, Prs3d_LineAspect
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCC.Core.TopoDS import TopoDS_Compound
from OCC.Core.gp import gp_Pnt


class SnapPresentation:
    def __init__(self, surface, frame):
        self.surface, self.frame = surface, frame
        self.marker = self.guide = self.grid = None
        self._grid_key = None

    def _point(self, point):
        return gp_Pnt(*self.frame.to_world(point.u, point.v).tuple())

    def _remove(self, key):
        ais = getattr(self, key)
        if ais is not None:
            self.surface.context.Remove(ais, False)
            setattr(self, key, None)

    def _display(self, ais):
        ais.SetInfiniteState(True)
        ais.SetZLayer(Graphic3d_ZLayerId_Topmost)
        self.surface.context.Display(ais, 0, -1, False)
        self.surface.context.Deactivate(ais)

    def show(self, snap, anchor):
        self._remove("marker")
        self._remove("guide")
        if snap is not None:
            color = Quantity_Color(.08, .65, .37, Quantity_TOC_RGB)
            marker = Aspect_TOM_X if snap.kind == "intersection" else Aspect_TOM_O
            self.marker = AIS_Point(Geom_CartesianPoint(self._point(snap.point)))
            self.marker.Attributes().SetPointAspect(Prs3d_PointAspect(marker, color, 5*self.surface.pixel_ratio))
            self._display(self.marker)
            if anchor is not None and snap.kind in ("horizontal", "vertical") and anchor != snap.point:
                self.guide = AIS_Shape(BRepBuilderAPI_MakeEdge(self._point(anchor), self._point(snap.point)).Edge())
                aspect = Prs3d_LineAspect(color, Aspect_TOL_DASH, self.surface.pixel_ratio)
                self.guide.Attributes().SetWireAspect(aspect)
                self.guide.Attributes().SetLineAspect(aspect)
                self._display(self.guide)
        self.surface.invalidate()

    def update_grid(self, settings, project):
        if not settings.grid:
            self._remove("grid")
            self._grid_key = None
            return
        width, height = self.surface.size
        corners = [project(x, y) for x, y in ((0, 0), (width, 0), (0, height), (width, height))]
        if any(point is None for point in corners):
            self._remove("grid")
            self._grid_key = None
            return
        step = settings.grid_step
        u0, u1 = floor(min(p.u for p in corners)/step), ceil(max(p.u for p in corners)/step)
        v0, v1 = floor(min(p.v for p in corners)/step), ceil(max(p.v for p in corners)/step)
        key = u0, u1, v0, v1, step, self.surface.pixel_ratio
        if key == self._grid_key:
            return
        self._remove("grid")
        self._grid_key = key
        if (u1-u0+1)*(v1-v0+1) > 1600:
            return  # Dense grids are hidden; the snap marker still shows the exact target.
        compound, builder = TopoDS_Compound(), BRep_Builder()
        builder.MakeCompound(compound)
        for u in range(u0, u1+1):
            for v in range(v0, v1+1):
                builder.Add(compound, BRepBuilderAPI_MakeVertex(gp_Pnt(*self.frame.to_world(u*step, v*step).tuple())).Vertex())
        self.grid = AIS_Shape(compound)
        self.grid.Attributes().SetPointAspect(Prs3d_PointAspect(Aspect_TOM_O_POINT,
            Quantity_Color(.48, .55, .63, Quantity_TOC_RGB), self.surface.pixel_ratio))
        self._display(self.grid)

    def clear(self):
        for key in ("marker", "guide", "grid"):
            self._remove(key)
        self._grid_key = None
        self.surface.invalidate()
