"""Viewport selection, contextual relations, placed dimensions and Modify dialog."""
from dataclasses import replace
from math import hypot

from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QVBoxLayout, QLabel, QFrame,
                               QHBoxLayout, QToolButton, QScrollArea)

from cadastrophe.plugin_api import LengthSpinBox
from .annotations import Annotations
from .commands import set_constraints, place_constraint
from .constraints import Constraint, Reference, DIMENSIONS, LABELS, point
from .dimension_geometry import measure, label_position
from .editing import control_points
from .model import Point, Segment, Circle, Arc, Ellipse, Spline, new_id
from .dimension_state import DimensionState, Phase
from .dimension_geometry import angle_geometry
from .dimension_value import AngleSpinBox
from .presentation import SketchPresentation, HOVER
from .markers import point_aspect, relation_icon


class ViewTools:
    def __init__(self, session):
        self.session = session
        self.mode = None
        self.alignment = "auto"
        self.dimension_state = DimensionState()
        self.refs = []
        self.external = {}
        self._before_press = ()
        self._ignore_click = False
        self.drag = None
        self.position = None
        self.dialog = None
        self.selected_constraint = None
        self.preview = Annotations(session.surface)
        self.hover = SketchPresentation(session.surface, preview=True, hover=True)
        self.hover_ref = None
        self._hover_external = None
        self._hover_shape = None
        self._body_hover = None
        self.toolbar = QFrame(session.surface.widget)
        self.toolbar.setObjectName("sketchRelationToolbar")
        self.toolbar.setFrameShape(QFrame.Shape.StyledPanel)
        self.toolbar.setStyleSheet("QFrame#sketchRelationToolbar { background: palette(window); border: 1px solid #a5aab0; }")
        self.row = QHBoxLayout(self.toolbar)
        self.row.setContentsMargins(4, 3, 4, 3)
        self.row.setSpacing(3)
        self.toolbar.hide()
        self.toolbar_anchor = None

    @property
    def refs(self):
        return self.dimension_state.refs

    @refs.setter
    def refs(self, value):
        self.dimension_state.refs = list(value)

    @property
    def sketch(self):
        from .construction_context import resolve_object
        obj = self.session.document.objects[self.session.object_id]
        return resolve_object(obj, self.session.document).sketch

    @property
    def working_sketch(self):
        current = {e.geometry.id: e for e in self.sketch.external}
        current.update(self.external)
        return replace(self.sketch, external=tuple(current.values()))

    def name(self, ref):
        if ref.entity_id == self.sketch.id:
            return "Origine de l’esquisse"
        sketch = self.working_sketch
        ext = next((e for e in sketch.external if e.geometry.id == ref.entity_id), None)
        if ext is not None:
            body = self.session.document.objects.get(ext.body_id)
            label = f"{'Sommet' if ext.topology == 'Vertex' else 'Arête'} {ext.index}@{body.name if body else 'Solide absent'}"
        else:
            e = sketch.entity(ref.entity_id)
            index = next(i for i, item in enumerate(sketch.entities, 1) if item.id == e.id)
            label = f"{ {Point:'Point', Segment:'Ligne', Circle:'Cercle', Arc:'Arc', Ellipse:'Ellipse', Spline:'Spline'}[type(e)]} {index}"
        part = {"position": "point", "start": "départ", "end": "fin", "center": "centre"}.get(ref.part)
        return label + (" · " + part if part else "")

    def sync_selection(self):
        if self.selected_constraint not in {c.id for c in self.sketch.constraints}:
            self.selected_constraint = None
        valid = {e.id for e in self.working_sketch.all_entities} | {self.sketch.id}
        self.refs = [r for r in self.refs if r.entity_id in valid]
        if self.mode is not None:
            return
        internal = {e.id for e in self.sketch.entities}
        selected = set(self.session.interaction.selected)
        if selected != {r.entity_id for r in self.refs if r.entity_id in internal}:
            self.refs = [Reference(key, "position" if type(self.sketch.entity(key)) is Point else "entity")
                         for key in self.session.interaction.selected]

    def start(self, dimension=False):
        self.session.checking_profiles = False
        external = dict(self.external)
        selected = list(self.refs) or [Reference(key, "position" if type(self.sketch.entity(key)) is Point else "entity")
                    for key in self.session.interaction.selected]
        self.abort()
        self.external = external
        self.mode = "dimension" if dimension else "relations"
        self.refs = selected
        if dimension:
            self.dimension_state.start(selected)
        self.toolbar.hide()
        self.session.surface.set_cursor(Qt.CursorShape.CrossCursor)
        self.session.selection_changed()

    def pick(self, x, y, *, stage=True):
        s = self.session
        origin_x, origin_y = s.surface.project(s.workplane.to_world(0., 0.))
        if not s.surface.alt and hypot(origin_x-x, origin_y-y) <= 8:
            return Reference(self.sketch.id, "position")
        if s.surface.alt:
            owned = [ais for _, ais in s.surface.presentation.entities.values()]
            owned += [ais for _, ais in s.surface.presentation.vertices.values()]
            owned += [ais for _, ais in s.interaction.overlay.entities.values()]
            owned += [ais for _, ais in s.surface.presentation.annotations.items.values()]
            return self._external_reference(s.surface.pick_topology(x, y, exclude=tuple((ais, 0) for ais in owned)), stage=stage)
        ais = s.surface.detect(x, y)
        # During Smart Dimension the sketch geometry takes precedence over its
        # existing annotations. Their text must not turn an entity click into
        # an empty-space placement.
        if self.mode == "dimension" and s.surface.presentation.annotations.key(ais) is not None:
            annotations = [item for _, item in s.surface.presentation.annotations.items.values()]
            try:
                for item in annotations:
                    s.surface.context.Deactivate(item)
                ais = s.surface.detect(x, y)
            finally:
                for item in annotations:
                    s.surface.context.Activate(item, 0, True)
        handle = s.interaction.handles.get(s.interaction.overlay.key(ais))
        vertex = s.surface.presentation.vertex_ref(ais) or s.surface.presentation.nearest_vertex(x,y)
        if vertex is not None:
            return Reference(vertex[0], vertex[1] if vertex[1] in {"position", "start", "end", "center"} else "entity")
        key = handle[0] if handle else s.surface.presentation.key(ais)
        if key is not None:
            entity = self.working_sketch.entity(key)
            close = []
            for part, p in control_points(entity):
                if part not in {"position", "start", "end", "center"}:
                    continue
                a, b = s.surface.project(s.workplane.to_world(p.u, p.v))
                close.append((hypot(a-x, b-y), part))
            if close and min(close)[0] <= 8:
                return Reference(key, min(close)[1])
            return Reference(key, "position" if type(entity) is Point else "entity")
        return self._external_reference(s.surface.pick_topology(x, y), stage=stage)

    def _external_reference(self, item, *, stage=True):
        if item is not None:
            from .external import capture
            external = capture(self.session.document, self.working_sketch, item)
            if stage:
                self.external[external.geometry.id] = external
            else:
                self._hover_external = external
                self._hover_shape = item.shape
            return Reference(external.geometry.id, "position" if type(external.geometry) is Point else "entity")
        return None

    def clear_hover(self):
        self.hover_ref = None
        self.hover.clear()
        if self._body_hover is not None:
            self.session.surface.context.Remove(self._body_hover,False)
            self._body_hover = None
        self._hover_external = self._hover_shape = None

    def update_hover(self, x, y, position):
        self._hover_external = self._hover_shape = None
        try:
            ref = self.pick(x,y,stage=False)
            sketch = self.working_sketch
            if self._hover_external is not None and self._hover_external.geometry.id not in {e.id for e in sketch.all_entities}:
                sketch = replace(sketch,external=(*sketch.external,self._hover_external))
            valid = ref is not None and (ref in self.refs or not self.refs or
                    (len(self.refs) == 1 and self.dimension(position,refs=[*self.refs,ref],sketch=sketch) is not None))
            if not valid:
                self.clear_hover()
                self.session.surface.clear_detected()
                return
            self.hover_ref = ref
            if self._hover_shape is not None:
                from OCC.Core.AIS import AIS_Shape
                from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_sRGB
                from OCC.Core.Graphic3d import Graphic3d_ZLayerId_Topmost
                self.hover.clear()
                if self._body_hover is not None:
                    self.session.surface.context.Remove(self._body_hover,False)
                ais = AIS_Shape(self._hover_shape)
                color = Quantity_Color(*HOVER,Quantity_TOC_sRGB)
                ais.SetColor(color)
                ais.SetWidth(3*self.session.surface.pixel_ratio)
                ais.Attributes().SetPointAspect(point_aspect(color,self.session.surface.pixel_ratio,hover=True))
                ais.SetInfiniteState(True)
                ais.SetZLayer(Graphic3d_ZLayerId_Topmost)
                self.session.surface.context.Display(ais,False)
                self.session.surface.context.Deactivate(ais)
                self._body_hover = ais
            else:
                if self._body_hover is not None:
                    self.session.surface.context.Remove(self._body_hover,False)
                    self._body_hover = None
                e = sketch.entity(ref.entity_id)
                if ref.part != "entity":
                    e = Point(point(e,ref.part),id=new_id() if e.id == sketch.id else e.id)
                obj = self.session.document.objects[self.session.object_id]
                self.hover.update(replace(obj,sketch=sketch.independent((e,))),editing=True)
            self.session.surface.invalidate()
        except (ValueError,RuntimeError):
            self.clear_hover()

    def press(self, x, y):
        self._before_press = tuple(self.refs)
        if self.mode is not None:
            return True
        base = self.session.surface.presentation
        key = base.annotations.key(self.session.surface.detect(x, y))
        if key is not None:
            self.select_constraint(key)
            self.drag = (key, self.session.document.objects[self.session.object_id].revision)
            self.position = None
            return True
        self.deselect_constraint()
        return False

    def moved(self, x, y):
        p = self.session._project(x, y)
        if p is None:
            self.clear_hover()
            self.preview.clear()
            self.session.surface.invalidate()
            return
        self.position = p
        if self.drag is not None and self.session.surface.dragging:
            c = next((c for c in self.sketch.constraints if c.id == self.drag[0]), None)
            if c is not None:
                self._preview(replace(c, label_position=p), hide=c.id)
        elif self.mode == "dimension" and self.dialog is None:
            self.update_hover(x,y,p)
            c = self.dimension(p)
            self.dimension_state.preview(c is not None)
            if c is not None:
                try:
                    self._preview(c)
                except (ValueError, RuntimeError) as exc:
                    self.preview.clear()
                    self.session.error = str(exc)
            elif self.preview.items:
                self.preview.clear()
                self.session.surface.invalidate()

    def released(self, x, y, dragged):
        if self.drag is None:
            return self.mode is not None
        key, revision = self.drag
        self.drag = None
        self._ignore_click = not dragged
        try:
            if dragged and self.position is not None:
                if revision != self.session.document.objects[self.session.object_id].revision:
                    raise ValueError("L’esquisse a changé pendant le placement.")
                self.session.mutate(lambda: place_constraint(self.session.document, self.session.object_id, key, self.position))
        except ValueError as exc:
            self.session.error = str(exc)
        self.clear_preview()
        return True

    def clicked(self, x, y):
        if self._ignore_click:
            self._ignore_click = False
            return True
        try:
            if self.dialog is not None:
                return True
            ref = self.pick(x, y)
            if ref is None and self.selected_constraint is not None:
                self.clear_constraint_selection()
                return True
            if self.mode == "dimension" and ref is None:
                p = self.session._project(x, y)
                candidate = self.dimension(p) if p is not None else None
                self.dimension_state.preview(candidate is not None)
                if candidate is not None and self.dimension_state.place():
                    # Selecting an already dimensioned reference edits its driving
                    # constraint, including its identifier, rather than duplicating it.
                    existing = next((c for c in self.sketch.constraints if c.kind == candidate.kind and
                                     (c.refs == candidate.refs or (c.kind in {"distance", "horizontal_distance", "vertical_distance"} and
                                      frozenset(c.refs) == frozenset(candidate.refs)) or
                                      (c.kind == "line_distance" and all(r.part == "entity" for r in self.refs) and
                                       {r.entity_id for r in c.refs} == {r.entity_id for r in candidate.refs}))), None)
                    if existing is not None:
                        self.open_editor(existing, x, y, new=False)
                        return True
                    self._preview(candidate)
                    self.open_editor(candidate, x, y, new=True)
                return True
            if ref is not None:
                self.selected_constraint = None
                self.session.constraint_panel.list.setCurrentRow(-1)
                self.session.surface.presentation.annotations.selected = None
                previous = list(self._before_press) if self.session.surface.ctrl else []
                if self.mode == "dimension":
                    p = self.session._project(x, y)
                    self.dimension_state.select(ref, lambda proposed: self.dimension(p, refs=proposed) is not None)
                else:
                    self.refs = [r for r in previous if r != ref] if ref in previous else [*previous, ref]
                self.session.error = ""
                ids = {r.entity_id for r in self.refs} & {e.id for e in self.sketch.entities}
                self.session.interaction.set_selected(ids)
                self.show_toolbar(x, y)
                self.session.selection_changed()
                if self.mode == "dimension":
                    self.moved(x, y)
            elif self.mode is None:
                self.deselect_constraint()
                self.refs = []
                self.toolbar.hide()
                self.session.selection_changed()
            return self.mode is not None
        except (ValueError, RuntimeError) as exc:
            self.dimension_state.cancel_placement()
            self.session.error = str(exc)
            self.session._feedback()
            return True

    def available(self):
        from .constraints import relation_key, fixed_point_aliases
        aliases = fixed_point_aliases(self.sketch.constraints)
        existing = {relation_key(c, aliases) for c in self.sketch.constraints}
        refs = tuple(self.refs)
        result = []
        for kind in self._available_candidates():
            ordered = tuple(sorted(refs, key=lambda r: r.part == "entity")) if kind == "point_on_line" else refs
            value = point(self.working_sketch.entity(ordered[0].entity_id), ordered[0].part) if kind == "fixed" else None
            if relation_key(Constraint(kind, ordered, value), aliases) not in existing:
                result.append(kind)
        return tuple(result)

    def _available_candidates(self):
        if not self.refs:
            return ()
        sketch = self.working_sketch
        if any(sketch.pattern_for(r.entity_id) is not None for r in self.refs):
            return ()
        entities = [sketch.entity(r.entity_id) for r in self.refs]
        if len(self.refs) == 1:
            if self.refs[0].part != "entity":
                return ("fixed",)
            if type(entities[0]) is Segment:
                return ("horizontal", "vertical")
            return ()
        if len(self.refs) != 2:
            return ()
        if all(r.part != "entity" for r in self.refs):
            return ("coincident",)
        if all(r.part == "entity" and type(e) is Segment for r, e in zip(self.refs, entities)):
            return ("parallel", "perpendicular")
        if any(r.part != "entity" for r in self.refs) and any(r.part == "entity" and type(e) is Segment for r, e in zip(self.refs, entities)):
            return ("point_on_line",)
        return ()

    def add_relation(self, kind):
        refs = tuple(self.refs)
        if kind == "point_on_line":
            refs = tuple(sorted(refs, key=lambda r: r.part == "entity"))
        value = point(self.working_sketch.entity(refs[0].entity_id), refs[0].part) if kind == "fixed" else None
        try:
            self.commit(Constraint(kind, refs, value))
            self.session.error = ""
        except (ValueError, RuntimeError) as exc:
            self.session.error = str(exc)
        self.session.selection_changed()
        if self.toolbar.isVisible() and not self.available():
            self.toolbar.hide()

    def dimension(self, position, *, refs=None, sketch=None):
        refs = list(self.refs if refs is None else refs)
        if not refs:
            return None
        sketch = self.working_sketch if sketch is None else sketch
        entities = [sketch.entity(r.entity_id) for r in refs]
        kind = None
        if len(refs) == 1 and refs[0].part == "entity":
            e = entities[0]
            kind = "length" if type(e) is Segment else "radius" if type(e) is Arc else "diameter" if type(e) is Circle else None
        elif len(refs) == 2:
            refs = [Reference(r.entity_id, "center") if r.part == "entity" and isinstance(e, Circle) else r
                    for r, e in zip(refs, entities)]
            if all(r.part != "entity" for r in refs):
                a, b = (point(sketch.entity(r.entity_id), r.part) for r in refs)
                du, dv = abs(a.u-b.u), abs(a.v-b.v)
                if self.alignment != "auto": kind = {"aligned":"distance", "horizontal":"horizontal_distance", "vertical":"vertical_distance"}[self.alignment]
                elif du < 1e-8: kind = "vertical_distance"
                elif dv < 1e-8: kind = "horizontal_distance"
                elif position is not None:
                    if min(a.u,b.u) <= position.u <= max(a.u,b.u) and min(a.v,b.v) <= position.v <= max(a.v,b.v):
                        kind = "distance"
                    else:
                        kind = "horizontal_distance" if abs(position.v-(a.v+b.v)/2) > abs(position.u-(a.u+b.u)/2) else "vertical_distance"
                else: kind = "distance"
            elif sum(r.part == "entity" for r in refs) == 1:
                refs.sort(key=lambda r: r.part == "entity")
                if type(sketch.entity(refs[1].entity_id)) is Segment:
                    kind = "line_distance"
            elif all(type(e) is Segment for e in entities):
                a, b = entities
                dx, dy, ex, ey = a.end.u-a.start.u, a.end.v-a.start.v, b.end.u-b.start.u, b.end.v-b.start.v
                if abs(dx*ey-dy*ex) <= 1e-7*hypot(dx, dy)*hypot(ex, ey):
                    refs[0] = Reference(refs[0].entity_id, "start")
                    kind = "line_distance"
                else:
                    kind = "angle"
                    if position is not None:
                        _, center, _ = angle_geometry(sketch, refs)
                        refs = [Reference(r.entity_id, "end" if (position.u-center.u)*(e.end.u-e.start.u)+
                                          (position.v-center.v)*(e.end.v-e.start.v) >= 0 else "start")
                                for r, e in zip(refs, entities)]
        if kind is None:
            return None
        value = measure(sketch, kind, tuple(refs))
        if value <= 1e-8:
            return None
        return Constraint(kind, tuple(refs), value, label_position=position,
                          driven=any(sketch.pattern_for(r.entity_id) is not None for r in refs))

    def commit(self, constraint, *, new=True, expression=None):
        extra = ()
        # A line-to-line distance must retain the detected parallelism when its
        # value changes. Both accepted relations belong to the same undo step.
        if (not constraint.driven and constraint.kind == "line_distance" and len(self.refs) == 2 and
                all(r.part == "entity" and type(self.working_sketch.entity(r.entity_id)) is Segment for r in self.refs)):
            pair = frozenset(self.refs)
            if not any(c.kind == "parallel" and frozenset(c.refs) == pair for c in self.sketch.constraints):
                extra = (Constraint("parallel", tuple(self.refs)),)
        constraints = (*self.sketch.constraints, *extra, constraint) if new else (*tuple(
            constraint if c.id == constraint.id else c for c in self.sketch.constraints), *extra)
        used = {r.entity_id for c in constraints for r in c.refs}
        external = (*self.sketch.external, *(e for key, e in self.external.items()
                    if key in used and key not in {v.geometry.id for v in self.sketch.external}))
        self.session.mutate(lambda: set_constraints(self.session.document, self.session.object_id, constraints,
            external=external, validate=self.session.validate_native, validate_sketch=self.session.validate_sketch,
            expressions={constraint.id: expression},
            title="Ajouter une cote ou relation" if new else "Modifier la cote"))

    def _preview(self, constraint, *, hide=None):
        sketch = self.working_sketch
        self.preview.update(replace(sketch, constraints=(constraint,)), editing=False)
        if hide is not None:
            self.session.surface.presentation.annotations.update(replace(self.sketch,
                constraints=tuple(c for c in self.sketch.constraints if c.id != hide)), editing=True)

    def clear_preview(self):
        self.preview.clear()
        if (self.session.surface is not None and not self.session.surface.closed and self.session.surface.presentation is not None and
                self.session.document is not None and self.session.object_id in self.session.document.objects):
            self.session.surface.presentation.annotations.update(self.sketch, editing=True)

    def select_constraint(self, key):
        panel = self.session.constraint_panel
        for row in range(panel.list.count()):
            if panel.list.item(row).data(Qt.ItemDataRole.UserRole) == key:
                panel.list.setCurrentRow(row)
                break

    def deselect_constraint(self):
        self.selected_constraint = None
        panel = self.session.constraint_panel
        exists = self.session.document is not None and self.session.object_id in self.session.document.objects
        if exists and panel is not None and panel.list is not None:
            panel.list.setCurrentRow(-1)
        base = self.session.surface.presentation if self.session.surface is not None else None
        if base is not None and base.annotations.selected is not None:
            base.annotations.selected = None
            if exists:
                base.annotations.update(self.sketch,editing=True)

    def highlight_constraint(self, constraint):
        self.selected_constraint = constraint.id
        # Angular reference parts encode oriented rays, not a point selection.
        self.refs = ([Reference(r.entity_id) for r in constraint.refs] if constraint.kind == "angle" else
                     list(constraint.refs))
        ids = {r.entity_id for r in self.refs} & {e.id for e in self.sketch.entities}
        if set(self.session.interaction.selected) != ids:
            self.session.interaction.set_selected(ids)
        base = self.session.surface.presentation
        if base is not None:
            base.annotations.selected = constraint.id
            base.annotations.update(self.sketch, editing=True)
            base.set_state(selected_ids={r.entity_id for r in self.refs})

    def clear_constraint_selection(self):
        self.drag = None
        self.deselect_constraint()
        self.refs = []
        self.external.clear()
        if self.mode == "dimension":
            self.dimension_state.finish()
        self.clear_preview()
        self.toolbar.hide()
        self.session.interaction.set_selected(())

    def double_clicked(self, x, y):
        if self.mode == "dimension":
            # Qt can deliver a double-click for two nearby entity hits. It is
            # still a selection gesture until an empty-space placement occurs.
            self.clicked(x, y)
            return
        key = self.session.surface.presentation.annotations.key(self.session.surface.detect(x, y))
        c = next((c for c in self.sketch.constraints if c.id == key), None)
        if c is not None:
            self.select_constraint(key)
            if c.kind in DIMENSIONS:
                self.open_editor(c, x, y, new=False)

    def open_editor(self, constraint, x, y, *, new):
        if self.dialog is not None:
            return
        self.clear_hover()
        dialog = QDialog(self.session.surface.widget)
        dialog.setWindowTitle("Modifier")
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(dialog)
        dimensions = [c for c in self.sketch.constraints if c.kind in DIMENSIONS]
        number = len(dimensions)+1 if new else next(i for i,c in enumerate(dimensions,1) if c.id == constraint.id)
        title = QLabel(f"{LABELS[constraint.kind]} · D{number}@{self.session.document.objects[self.session.object_id].name}")
        layout.addWidget(title)
        editor = (AngleSpinBox(constraint.value) if constraint.kind == "angle" else
                  LengthSpinBox(self.session.surface.units, 1e-9, 1e12, constraint.value))
        editor.setAccessibleName("Modifier la cote dans la vue")
        if not constraint.driven:
            editor.bind_equations(self.session.document, constraint.id, owner=self.session.object_id,
                                  unit="angle" if constraint.kind == "angle" else "length",
                                  scale=1. if constraint.kind == "angle" else self.session.surface.units.to_internal(1.))
        external_ids = {e.geometry.id for e in self.working_sketch.external}
        if constraint.driven or all(r.entity_id in external_ids for r in constraint.refs):
            editor.setReadOnly(True)
            layout.addWidget(QLabel("Cote de référence · le solide reste inchangé"))
        layout.addWidget(editor)
        error = QLabel()
        error.setWordWrap(True)
        layout.addWidget(error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("✓")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("role", "accept")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("✕")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setProperty("role", "cancel")
        layout.addWidget(buttons)
        self.dialog = dialog
        if self.mode == "dimension":
            self.dimension_state.edit()
        self.value_editor = editor
        revision = self.session.document.objects[self.session.object_id].revision
        def accept():
            try:
                if revision != self.session.document.objects[self.session.object_id].revision:
                    raise ValueError("L’esquisse a changé ; recommencer la cotation.")
                editor.interpretText()
                self.commit(replace(constraint, value=editor.internal_value), new=new,
                            expression=editor.equation_expression())
                self.session.error = ""
                dialog.accept()
            except (ValueError, RuntimeError) as exc:
                error.setText(str(exc))
        def finished():
            self.dialog = None
            self._ignore_click = False
            self.deselect_constraint()
            self.dimension_state.finish() if self.mode == "dimension" else self.dimension_state.abort()
            self.external.clear()
            if self.session.document is not None and self.session.object_id in self.session.document.objects:
                self.clear_preview()
                self.session.interaction.set_selected(())
                self.session.selection_changed()
                self.session.surface.focus()
                if self.mode == "dimension":
                    self.session.surface.set_cursor(Qt.CursorShape.CrossCursor)
            else:
                self.preview.clear()
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        dialog.finished.connect(finished)
        dialog.adjustSize()
        at = self.session.surface.widget.mapToGlobal(QPoint(round(x+12), round(y+12)))
        bounds = dialog.screen().availableGeometry()
        dialog.move(max(bounds.left(), min(at.x(), bounds.right()-dialog.width())),
                    max(bounds.top(), min(at.y(), bounds.bottom()-dialog.height())))
        dialog.open()
        editor.setFocus()
        editor.selectAll()

    def show_toolbar(self, x, y):
        # Rebuilding a visible overlay leaves newly parented widgets hidden in
        # Qt. Hide the frame until every replacement button has its geometry.
        self.toolbar.hide()
        while self.row.count():
            widget = self.row.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        kinds = self.available()
        if self.mode == "dimension" or not kinds:
            self.toolbar.hide()
            return
        for key in kinds:
            button = QToolButton(self.toolbar)
            button.setIcon(relation_icon(key,self.session.surface.pixel_ratio,self.session.surface.preferences.theme))
            button.setToolTip(LABELS[key])
            button.setAccessibleName("Relation " + LABELS[key])
            button.clicked.connect(lambda checked=False, kind=key: self.add_relation(kind))
            self.row.addWidget(button)
            button.show()
        button = QToolButton(self.toolbar)
        button.setText("↔")
        button.setToolTip("Cotes au clic")
        button.clicked.connect(lambda: self.start(True))
        self.row.addWidget(button)
        button.show()
        self.row.invalidate()
        self.row.activate()
        self.toolbar.adjustSize()
        self.toolbar_anchor = self.session._project(x, y)
        self.position_toolbar(x, y)
        self.toolbar.show()
        self.toolbar.raise_()

    def position_toolbar(self, x=None, y=None):
        if x is None:
            if not self.toolbar.isVisible() or self.toolbar_anchor is None:
                return
            p = self.toolbar_anchor
            x, y = self.session.surface.project(self.session.workplane.to_world(p.u, p.v))
        width, height = self.session.surface.size
        self.toolbar.move(max(0, min(round(x+16), width-self.toolbar.width())), max(0, min(round(y+16), height-self.toolbar.height())))

    def escape(self):
        self.session.error = ""
        self.clear_hover()
        if self.dialog is not None:
            self.dialog.reject()
            return
        if self.selected_constraint is not None:
            self.clear_constraint_selection()
            return
        if self.mode == "dimension" and self.dimension_state.escape():
            used = {r.entity_id for r in self.refs}
            self.external = {k: e for k, e in self.external.items() if k in used}
            self.clear_preview()
            self.session.interaction.set_selected(used & {e.id for e in self.sketch.entities})
            if self.position is not None:
                c = self.dimension(self.position)
                self.dimension_state.preview(c is not None)
                if c is not None:
                    self._preview(c)
            return
        self.abort()
        self.deselect_constraint()
        self.session.interaction.set_selected(())

    def abort(self, *, clear_selection=True):
        self.clear_hover()
        self._ignore_click = False
        self.drag = None
        self.mode = None
        if self.dialog is not None:
            self.dialog.reject()
        self.clear_preview()
        self.toolbar.hide()
        self.dimension_state.abort()
        self.external.clear()
        if clear_selection:
            self.deselect_constraint()

    def close(self):
        self.abort()
        self.toolbar.deleteLater()
