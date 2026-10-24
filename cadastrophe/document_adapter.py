"""Project document notifications onto native AIS presentations, not vice versa."""
from weakref import ref
from dataclasses import replace
from OCC.Core.AIS import AIS_Shape
from OCC.Core.Graphic3d import Graphic3d_MaterialAspect, Graphic3d_NOM_PLASTIC
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB, Quantity_TOC_sRGB

from .document import ObjectType
from .presentation import PresentationSurface
import logging

log = logging.getLogger(__name__)


class ViewportDocumentAdapter:
    def __init__(self, viewport):
        self._viewport_ref = ref(viewport)
        self.document = None
        self.features = {}
        self._construction_highlights = {}
        self._history_states, self._history_hidden = {}, set()
        self._edit_history_states, self._edit_history_hidden = {}, set()
        viewport.selection.feature_presentations = self.features
        viewport.selection.display_object = self.display_object
        if viewport.presentation_registry is not None:
            viewport.presentation_registry.changed.connect(self.refresh_features)

    @property
    def viewport(self):
        return self._viewport_ref()

    def bind(self, document):
        self.detach()
        viewport = self.viewport
        viewport.context.ClearSelected(False)
        self._remove_presentations()
        self.document = viewport.document = document
        viewport.selection.bind_document(document)
        for obj in document.objects.values():
            self._create_presentation(obj)
        document.object_added.connect(self._added)
        document.object_changed.connect(self._changed)
        document.object_removed.connect(self._removed)
        document.cleared.connect(self._cleared)
        document.selection_changed.connect(self.refresh_features)
        document.body_result_changed.connect(self.refresh_features)
        document.history_context.changed.connect(self._history_changed)
        self._history_changed()
        viewport.selection.from_tree(document.selected_ids)
        self._sync_construction_highlight()
        viewport.update_rotation_pivot()

    def detach(self):
        if self.document is not None:
            self.document.object_added.disconnect(self._added)
            self.document.object_changed.disconnect(self._changed)
            self.document.object_removed.disconnect(self._removed)
            self.document.cleared.disconnect(self._cleared)
            self.document.selection_changed.disconnect(self.refresh_features)
            self.document.body_result_changed.disconnect(self.refresh_features)
            self.document.history_context.changed.disconnect(self._history_changed)
            self.document = None
        self._history_states, self._history_hidden = {}, set()
        self._edit_history_states, self._edit_history_hidden = {}, set()

    def _history_changed(self):
        self.set_history_display(self._edit_history_states, self._edit_history_hidden)

    def set_history_display(self, states=None, hidden=()):
        previous = set(self._history_states) | self._history_hidden
        self._edit_history_states, self._edit_history_hidden = dict(states or {}), set(hidden)
        context_states, context_hidden = self.document.history_context.display() if self.document else ({}, ())
        self._history_states = {**context_states, **self._edit_history_states}
        self._history_hidden = set(context_hidden) | self._edit_history_hidden
        if self.document is not None:
            for key in previous | set(self._history_states) | self._history_hidden:
                if key in self.document.objects:
                    self._changed(key, ("shape",))
            self.refresh_features()

    def display_object(self, obj):
        from .core.reference_objects import DatumPlaneObject
        if isinstance(obj,DatumPlaneObject) and obj.reference_error_message:
            obj = replace(obj,visible=False)
        if obj.id in self._history_states:
            state = self._history_states[obj.id]
            from .core.feature_model import ResultStatus
            result = self.document.history_context.ensure_prefix(state) if state is not None else None
            obj = replace(obj, shape=result.shape if result is not None and result.status is ResultStatus.READY else None)
        return replace(obj, visible=False) if obj.id in self._history_hidden else obj

    def close(self):
        self.detach()
        self._remove_presentations()
        registry = self.viewport.presentation_registry
        if registry is not None:
            registry.changed.disconnect(self.refresh_features)

    def _create_presentation(self, obj):
        obj = self.display_object(obj)
        if obj.kind is ObjectType.FEATURE:
            self._feature(obj)
            return
        if obj.shape is None:
            return
        viewport = self.viewport
        ais = AIS_Shape(obj.shape)
        material = Graphic3d_MaterialAspect(Graphic3d_NOM_PLASTIC)
        material.SetSpecularColor(Quantity_Color(0.12, 0.12, 0.12, Quantity_TOC_RGB))
        material.SetShininess(0.45)
        ais.SetMaterial(material)
        ais.SetColor(Quantity_Color(*obj.color, Quantity_TOC_RGB))
        ais.SetTransparency(obj.transparency)
        edges = viewport.render_mode == "Ombré avec arêtes"
        ais.SetDisplayMode(0 if viewport.render_mode == "Filaire" else 1)
        ais.Attributes().SetFaceBoundaryDraw(edges or obj.kind is ObjectType.REFERENCE_PLANE)
        ais.Attributes().SetFaceBoundaryAspect(viewport.context.DefaultDrawer().FaceBoundaryAspect())
        if obj.kind is ObjectType.REFERENCE_PLANE:
            # Reference planes stay visible without tinting or covering the
            # part. Their boundary remains available for native selection.
            self._style_plane(ais, obj)
        viewport.presentations[obj.id] = ais
        viewport.context.Display(ais, False)
        if not obj.visible:
            viewport.context.Erase(ais, False)
        viewport.selection.configure_object(obj.id)

    def _style_plane(self, ais, obj):
        from .core.preferences import rgb
        viewport = self.viewport
        light = sum(sum(rgb(c)) for c in viewport.preferences.backgrounds) / 6 > .5
        values = tuple(c * .65 for c in obj.color) if light else obj.color
        ais.SetDisplayMode(0)
        ais.SetTransparency(0.)
        ais.SetWidth(2.5 * viewport.devicePixelRatioF())
        ais.SetColor(Quantity_Color(*values, Quantity_TOC_sRGB))

    def refresh_reference_planes(self):
        if self.document is None:
            return
        for key, ais in self.viewport.presentations.items():
            obj = self.document.objects[key]
            if obj.kind is ObjectType.REFERENCE_PLANE:
                self._style_plane(ais, obj)
                self.viewport.context.Redisplay(ais, False)

    def _added(self, object_id):
        self._create_presentation(self.document.objects[object_id])
        self.viewport.update_rotation_pivot()
        self.viewport.update()

    def _changed(self, object_id, fields):
        viewport = self.viewport
        obj = self.display_object(self.document.objects[object_id])
        if obj.kind is ObjectType.FEATURE:
            self._feature(obj)
            viewport.selection.publish()
            viewport.update()
            return
        if "shape" in fields:
            viewport.selection.deselect_object(object_id)
            previous = viewport.presentations.pop(object_id, None)
            if previous is not None:
                viewport.context.Remove(previous, False)
            self._create_presentation(obj)
        elif "visible" in fields and object_id in viewport.presentations:
            ais = viewport.presentations[object_id]
            if obj.visible:
                viewport.context.Display(ais, False)
                if object_id in viewport.selection.hidden_selection:
                    viewport.selection.hidden_selection.remove(object_id)
                    viewport.context.AddOrRemoveSelected(ais, False)
            else:
                viewport.selection.deselect_object(object_id)
                viewport.context.Erase(ais, False)
            viewport.selection.configure_object(object_id)
        if "transparency" in fields and "shape" not in fields and object_id in viewport.presentations:
            ais = viewport.presentations[object_id]
            if obj.kind is ObjectType.REFERENCE_PLANE:
                self._style_plane(ais, obj)
                viewport.context.Redisplay(ais, False)
            elif obj.transparency:
                viewport.context.SetTransparency(ais, obj.transparency, False)
            else:
                viewport.context.UnsetTransparency(ais, False)
        if "shape" in fields or "visible" in fields:
            viewport.selection.publish()
            viewport.update_rotation_pivot()
            self._sync_construction_highlight()
        viewport.update()

    def _removed(self, object_id):
        viewport = self.viewport
        self._clear_feature(object_id)
        ais = viewport.presentations.pop(object_id, None)
        if ais is not None:
            viewport.context.Remove(ais, False)
        viewport.selection.hidden_selection = [key for key in viewport.selection.hidden_selection if key != object_id]
        viewport.selection.publish()
        viewport.update_rotation_pivot()

    def _remove_presentations(self):
        viewport = self.viewport
        self._construction_highlights.clear()
        for object_id in tuple(self.features):
            self._clear_feature(object_id)
        for ais in viewport.presentations.values():
            viewport.context.Remove(ais, False)
        viewport.presentations.clear()

    def _clear_feature(self, object_id):
        entry = self.features.pop(object_id, None)
        if entry is not None:
            entry[1].clear()

    def _feature(self, obj):
        obj = self.display_object(obj)
        registry = self.viewport.presentation_registry
        binding = registry.binding(obj) if registry is not None else None
        entry = self.features.get(obj.id)
        if entry is not None and entry[0] is not binding:
            self._clear_feature(obj.id)
            entry = None
        if binding is None:
            return
        try:
            if entry is None:
                renderer = binding.factory(PresentationSurface(self.viewport))
                self.features[obj.id] = entry = (binding, renderer)
            entry[1].update(obj, selected=obj.id in self.document.selected_ids,
                            editing=obj.id == self.viewport.editing_id)
        except (Exception, SystemExit):
            log.exception("Présentation de %s impossible", obj.name)
            self._clear_feature(obj.id)
            self.viewport.presentation_error.emit(f"Présentation de {obj.name} impossible ; voir le journal.")

    def refresh_features(self, *_):
        if self.document is None or self.viewport is None or self.viewport._native_closed:
            return
        for obj in self.document.objects.values():
            if obj.kind is ObjectType.FEATURE:
                self._feature(obj)
        self._sync_construction_highlight()

    def _sync_construction_highlight(self):
        """A logical feature highlights its existing body AIS, without selecting
        a second object or creating another solid presentation.
        """
        from .core.feature_objects import ConstructionFeature
        from OCC.Core.Prs3d import Prs3d_TypeOfHighlight_Selected
        viewport = self.viewport
        requested = {}
        for key in self.document.selected_ids:
            obj = self.document.objects[key]
            if isinstance(obj, ConstructionFeature) and obj.id not in self._history_hidden:
                body = self.document.objects[obj.body_id]
                ais = viewport.presentations.get(body.id)
                if body.visible and ais is not None and body.id not in self.document.selected_ids:
                    requested[body.id] = ais
        for key, ais in self._construction_highlights.items():
            if requested.get(key) is not ais:
                viewport.context.Unhilight(ais, False)
        for key, ais in requested.items():
            if self._construction_highlights.get(key) is not ais:
                viewport.context.HilightWithColor(ais, viewport.context.HighlightStyle(Prs3d_TypeOfHighlight_Selected), False)
        self._construction_highlights = requested
        viewport.context.UpdateSelected(False)

    def _cleared(self):
        self._remove_presentations()
        self.viewport.selection.hidden_selection = []
        self.viewport.selection.publish()
        self.viewport.update_rotation_pivot()
