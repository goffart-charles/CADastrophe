"""AIS picking / UUID selection and detached, atomic drag transactions (E4)."""
from dataclasses import replace

from .clipboard import MIME_TYPE, MAX_PAYLOAD, encode_entities, decode_entities
from .commands import add_entities, remove_entities
from .editing import control_points
from .joints import joint_handles, moved_joint
from .model import Point, Vec2, new_id
from .presentation import SketchPresentation
from .connected_drag import connected_entities, translated_group, translated_annotations, rigid_delta
from .constrained_drag import ConstrainedDrag


class EntityInteraction:
    def __init__(self, session):
        self.session = session
        self.selected = ()
        self.pending = None
        self.handles = {}
        self.overlay = SketchPresentation(session.surface, preview=True, selection_priority=10)
        self._paste_payload = None
        self._paste_count = 0

    @property
    def surface(self):
        return self.session.surface

    @property
    def base(self):
        return self.surface.presentation

    @property
    def obj(self):
        from .construction_context import resolve_object
        return resolve_object(self.session.document.objects[self.session.object_id], self.session.document)

    def set_selected(self, ids):
        ids = set(ids)
        self.selected = tuple(entity.id for entity in self.obj.sketch.entities if entity.id in ids)
        self.refresh()
        self.session.selection_changed()

    def refresh(self):
        valid = {entity.id for entity in self.obj.sketch.entities}
        self.selected = tuple(key for key in self.selected if key in valid)
        active = self.session.draft.tool is None
        self.base.set_state(selected_ids=self.selected if active else (), interactive=active)
        if active and self.pending is None and getattr(self.session, "corner_panel", None) is None:
            self._handles()
        elif not active:
            self.overlay.clear()

    def _handles(self):
        entities = ()
        mapping = {}
        if len(self.selected) == 1 and self.obj.sketch.pattern_for(self.selected[0]) is None:
            entity = self.obj.sketch.entity(self.selected[0])
            for part, point in control_points(entity):
                key = next((key for key, target in self.handles.items() if target == (entity.id, part)), None) or new_id()
                entities = (*entities, Point(point, id=key))
                mapping[key] = (entity.id, part)
        self.handles = mapping
        self.overlay.set_state(interactive=True)
        self._show(entities)

    def _show(self, entities):
        obj = self.obj
        self.overlay.update(replace(obj, sketch=obj.sketch.independent(entities)), selected=True, editing=True)

    def press(self, x, y):
        if self.session.draft.tool is not None:
            return
        self.abort()
        self.session.error = ""
        point = self.session._project(x, y)
        if point is None:
            return
        detected = self.surface.detect(x, y)
        handle = self.handles.get(self.overlay.key(detected))
        if handle is None:
            # Coincident native owners can tie on depth/distance. The visible
            # handle of the selected entity keeps priority over base vertices.
            nearest = self.overlay.nearest_vertex(x,y)
            handle = self.handles.get(nearest[0]) if nearest else None
        handle = handle or self.base.vertex_ref(detected) or self.base.nearest_vertex(x,y)
        key = handle[0] if handle else self.base.key(detected)
        previous = self.selected
        ctrl = self.surface.ctrl
        if ctrl:
            handle = None  # Ctrl always toggles the entity, even over one of its handles.
        if key is None:
            if not ctrl:
                self.set_selected(())
            self.pending = dict(box=(x, y), additive=previous if ctrl else ())
            self.session.selection_changed()
            return
        if not handle:
            if ctrl:
                self.set_selected(set(previous) ^ {key})
            elif key not in self.selected:
                self.set_selected((key,))
        elif key not in self.selected:
            self.set_selected((key,))
        if key in self.selected:
            if set(self.selected).intersection(self.obj.sketch._generated_ids):
                self.session.error = "Occurrence pilotée par un motif : modifiez la source ou les paramètres du motif."
                self.session.selection_changed()
                return
            if handle is None and len(self.selected) == 1 and type(self.obj.sketch.entity(key)) is Point:
                handle = (key, "position")
            targets = joint_handles(self.obj.sketch.entities, handle) if handle else ()
            affected = {e.id for e in connected_entities(self.obj.sketch,
                        {key for key,_ in targets} if handle else self.selected)}
            self.pending = dict(start=point, entities=tuple(entity for entity in self.obj.sketch.entities if entity.id in affected),
                                handle=handle, targets=targets, key=key, revision=self.obj.revision, solver=None)
        self.session.selection_changed()

    def candidate(self, point):
        pending = self.pending
        if self.obj.revision != pending["revision"]:
            raise ValueError("L’esquisse a changé pendant le déplacement.")
        if pending["handle"]:
            keys = {e.id for e in pending["entities"]}
            if not any(r.entity_id in keys for c in self.obj.sketch.constraints for r in c.refs):
                return moved_joint(pending["entities"], pending["handle"], point, pending["targets"])
            if pending["solver"] is None:
                pending["solver"] = ConstrainedDrag(self.obj.sketch,pending["entities"],pending["handle"],pending["start"],
                                                     joints=pending["targets"])
            return pending["solver"].move(point)
        delta = Vec2(point.u - pending["start"].u, point.v - pending["start"].v)
        try:
            entities = translated_group(self.obj.sketch, pending["entities"], delta)
            pending["deforming"] = False
            return entities
        except ValueError:
            if pending["solver"] is None:
                pending["solver"] = ConstrainedDrag(self.obj.sketch,pending["entities"],(pending["key"],"entity"),pending["start"])
            pending["deforming"] = True
            return pending["solver"].move(point)

    def move(self, x, y):
        if self.session.draft.tool is not None:
            return
        if self.pending is None:
            self.surface.detect(x, y)
            return
        if not self.surface.dragging:
            return
        if "box" in self.pending:
            self.surface.rectangle(self.pending["box"], (x, y))
            return
        self.session.snapper.reset()
        self.session.snap_result = None
        point = self.session._project(x,y)
        try:
            if point is None:
                raise ValueError("Aucune intersection avec le support.")
            entities = self.candidate(point)
            self.base.set_state(hidden_ids=(entity.id for entity in entities))
            self.overlay.set_state(interactive=False)
            self._show(entities)
            if self.obj.sketch.constraints:
                values = {e.id: e for e in entities}
                sketch = replace(self.obj.sketch,entities=tuple(values.get(e.id,e) for e in self.obj.sketch.entities),
                                 constraints=self.drag_constraints(point))
                self.base.annotations.update(sketch,editing=True)
            self.session.state = "dragging"
            self.session.error = ""
        except ValueError as exc:
            self.base.set_state(hidden_ids=())
            self._show(())
            self.session.error = str(exc)

    def drag_constraints(self, point):
        if self.pending["handle"] or self.pending.get("deforming"):
            solver = self.pending["solver"]
            if solver is not None:
                delta = rigid_delta(self.pending["entities"],solver.last_entities)
                if delta is not None:
                    return translated_annotations(self.obj.sketch,self.pending["entities"],delta)
            return self.obj.sketch.constraints
        start = self.pending["start"]
        return translated_annotations(self.obj.sketch,self.pending["entities"],Vec2(point.u-start.u,point.v-start.v))

    def release(self, x, y, dragged):
        if self.pending is None:
            return
        pending = self.pending
        try:
            if "box" in pending:
                if dragged:
                    keys = {self.base.key(ais) for ais in self.surface.select_rectangle(pending["box"], (x, y))
                            if self.base.vertex_ref(ais) is None}
                    self.pending = None
                    self.set_selected(keys.union(pending["additive"]))
            elif dragged:
                point = self.session._project(x,y)
                if point is None:
                    raise ValueError("Déplacement annulé : le rayon ne rencontre pas le support.")
                candidate = self.candidate(point)
                constraints = self.drag_constraints(point)
                positions = {c.id:c.label_position for c,original in zip(constraints,self.obj.sketch.constraints)
                             if c.label_position != original.label_position}
                extra = {"annotation_positions":positions} if positions else {}
                self.session.commit_entities(candidate, title="Déplacer les entités d’esquisse",**extra)
        except (ValueError, RuntimeError) as exc:
            self.session.error = str(exc)
        finally:
            self.abort()
            self.session.selection_changed()

    def abort(self):
        self.session.snapper.reset()
        self.session.snap_result = None
        if self.session.snap_renderer is not None:
            self.session.snap_renderer.show(None, None)
        self.pending = None
        self.surface.rectangle()
        self.surface.clear_detected()
        self.base.set_state(hidden_ids=())
        self.session.state = self.session.draft.state
        self.refresh()

    def command_enabled(self, command_id):
        if self.session.draft.tool is not None or self.pending is not None or self.session.document.history.blocked:
            return False
        if command_id in ("object.delete", "edit.copy", "edit.cut"):
            return bool(self.selected)
        clipboard = self.surface.clipboard
        return bool(command_id == "edit.paste" and clipboard is not None and clipboard.has(MIME_TYPE))

    def execute(self, command_id):
        if command_id == "object.delete":
            self.session.mutate(lambda: remove_entities(self.session.document, self.session.object_id, self.selected))
        elif command_id in ("edit.copy", "edit.cut"):
            obj = self.obj
            values = tuple(obj.sketch.entity(key) for key in self.selected)
            payload = encode_entities(self.session.document.id, obj.id, values)
            self.surface.clipboard.write(MIME_TYPE, payload)  # Verify OS acceptance BEFORE deleting.
            self._paste_payload, self._paste_count = None, 0
            if command_id == "edit.cut":
                self.session.mutate(lambda: remove_entities(self.session.document, obj.id, self.selected))
        elif command_id == "edit.paste":
            payload = self.surface.clipboard.read(MIME_TYPE, limit=MAX_PAYLOAD)
            count = self._paste_count + 1 if payload == self._paste_payload else 1
            values = decode_entities(payload, Vec2(10 * count, 10 * count))
            self.session.validate_native(values)
            self.session.mutate(lambda: add_entities(self.session.document, self.session.object_id, values,
                                                    title="Coller les entités d’esquisse"))
            self._paste_payload, self._paste_count = payload, count
            self.set_selected(entity.id for entity in values)
        self.refresh()
        self.session.selection_changed()
        return True

    def close(self):
        self.surface.rectangle()
        self.surface.clear_detected()
        if self.base is not None:
            self.base.set_state(selected_ids=(), hidden_ids=(), interactive=False)
        self.overlay.clear()
        self.pending = None
        self.selected = ()
