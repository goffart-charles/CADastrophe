"""Owned non-selectable previews; additive ghosts retain the real opaque base."""
from OCC.Core.AIS import AIS_Shape, AIS_ColoredShape
from OCC.Core.Graphic3d import Graphic3d_MaterialAspect, Graphic3d_NOM_PLASTIC
from OCC.Core.Graphic3d import Graphic3d_ZLayerId_Default
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB, Quantity_TOC_sRGB
from dataclasses import replace


class ViewportPreview:
    def __init__(self, viewport):
        self.viewport = viewport
        self.presentation = None
        self.hidden = {}
        self.hidden_features = {}
        self.presentations = []

    def show(self, obj, *, hidden_ids=(), parts=None, added_only=False, opaque=False):
        self.clear()
        if obj is None:
            return
        for shape, rgb, base_faces, transparency in (parts if parts is not None else ((obj.shape, obj.color, (), 0.),)):
            replacing = bool(base_faces)
            if added_only and base_faces:
                from OCC.Core.BRep import BRep_Builder
                from OCC.Core.TopAbs import TopAbs_FACE
                from OCC.Core.TopExp import topexp
                from OCC.Core.TopTools import TopTools_IndexedMapOfShape
                from OCC.Core.TopoDS import TopoDS_Compound
                faces = TopTools_IndexedMapOfShape()
                topexp.MapShapes(shape, TopAbs_FACE, faces)
                added, builder = TopoDS_Compound(), BRep_Builder()
                builder.MakeCompound(added)
                for i in range(1, faces.Size()+1):
                    face = faces.FindKey(i)
                    if not any(face.IsSame(old) for old in base_faces):
                        builder.Add(added, face)
                shape, base_faces = added, ()
            ais = AIS_ColoredShape(shape) if base_faces else AIS_Shape(shape)
            ais.SetMaterial(Graphic3d_MaterialAspect(Graphic3d_NOM_PLASTIC))
            ais.SetColor(Quantity_Color(1., 0.78, 0.23, Quantity_TOC_RGB))
            ais.SetDisplayMode(1)
            ais.SetTransparency(0. if opaque else 0.3)
            from OCC.Core.TopAbs import TopAbs_FACE
            from OCC.Core.TopExp import TopExp_Explorer
            mode = 1
            if not TopExp_Explorer(shape, TopAbs_FACE).More() or (obj.plane is not None and shape.IsSame(obj.shape)):
                # Wire previews need a line style: shaded yellow is almost
                # invisible against the light viewport background.
                mode = 0
                from .core.preferences import rgb as background_rgb
                light = sum(sum(background_rgb(c)) for c in self.viewport.preferences.backgrounds) / 6 > .5
                line_color = (.05, .30, .70) if light else (1., .78, .23)
                ais.SetColor(Quantity_Color(*line_color, Quantity_TOC_sRGB))
                ais.SetWidth(3. * self.viewport.devicePixelRatioF())
                ais.SetTransparency(0.)
                ais.SetDisplayMode(mode)
            # The depth buffer must occlude ghosts behind other document bodies.
            ais.SetZLayer(Graphic3d_ZLayerId_Default)
            ais.Attributes().SetFaceBoundaryDraw(True)
            for face in base_faces:
                ais.SetCustomColor(face, Quantity_Color(*rgb, Quantity_TOC_RGB))
                ais.SetCustomTransparency(face, 0. if opaque else transparency)
                ais.CustomAspects(face).SetFaceBoundaryAspect(self.viewport.context.DefaultDrawer().FaceBoundaryAspect())
            self.presentations.append(ais)
            self.viewport.context.Display(ais, mode, -1, False)
            ais.Presentation().SetInfiniteState(not (bool(hidden_ids) or replacing))
            self.viewport.context.Deactivate(ais)
        self.presentation = self.presentations[0] if self.presentations else None
        for key in (() if added_only else hidden_ids):
            original = self.viewport.presentations.get(key)
            if original is not None and self.viewport.context.IsDisplayed(original):
                self.hidden[key] = original
                self.viewport.context.Erase(original, False)
            entry = self.viewport.document_adapter.features.get(key)
            obj = self.viewport.document.objects.get(key)
            if entry is not None and obj is not None and obj.visible:
                self.hidden_features[key] = entry[1]
                entry[1].update(replace(obj, visible=False))
        self.viewport.update()

    def clear(self):
        for key, ais in self.hidden.items():
            obj = self.viewport.document.objects.get(key)
            if (obj is not None and obj.visible and self.viewport.presentations.get(key) is ais):
                self.viewport.context.Display(ais, False)
                self.viewport.selection.configure_object(key)
        self.hidden.clear()
        for key, renderer in self.hidden_features.items():
            entry = self.viewport.document_adapter.features.get(key)
            obj = self.viewport.document.objects.get(key)
            if entry is not None and entry[1] is renderer and obj is not None:
                renderer.update(obj, selected=key in self.viewport.document.selected_ids,
                                editing=key == self.viewport.editing_id)
        self.hidden_features.clear()
        if self.presentations:
            for ais in self.presentations:
                self.viewport.context.Remove(ais, False)
            self.presentations.clear()
            self.presentation = None
            self.viewport.update()
