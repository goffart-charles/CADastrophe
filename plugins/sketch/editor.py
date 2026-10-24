"""Persistent plane editor: native preview + click drafting, never an undo per move."""
from dataclasses import replace
import logging
from math import degrees

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QHBoxLayout, QToolButton, QWidget, QSpinBox, QCheckBox, QPushButton

from .commands import add_entities, replace_entities
from .drawing import DrawingController, distance
from .model import Arc, Circle, Point, Segment, Ellipse, Spline, new_id
from .objects import SketchObject, support_status
from .presentation import SketchPresentation
from .interaction import EntityInteraction
from .editing_panel import EntityPanel
from .constraint_panel import ConstraintPanel
from .view_tools import ViewTools
from .snapping import SnapEngine, SnapSettings
from .snap_presentation import SnapPresentation
from .notice import SketchNotice
from .profile_panel import ProfilePanel

log = logging.getLogger(__name__)
TITLES = {"point": "Point", "line": "Ligne", "rectangle": "Rectangle", "circle": "Cercle", "arc": "Arc par centre",
          "construction_line": "Ligne de construction",
          "arc_three": "Arc par 3 points", "arc_tangent": "Arc tangent", "slot": "Rainure",
          "polygon": "Polygone", "ellipse": "Ellipse", "spline": "Spline"}
NO_SUPPORT_HIT = "Aucune intersection avec le support. Ctrl+8 : revenir à la vue normale."


class SketchEditSession:
    def __init__(self, obj, *, actions=(), tool_changed=None, created=False, profiles=None):
        self.title = f"Esquisse · {obj.name}"
        self.created = created
        self.object_id = obj.id
        self.workplane = obj.sketch.support.frame
        self.state = "editing"
        self.draft = DrawingController()
        self.actions, self.tool_changed = actions, tool_changed
        self.details = self.instructions = self.values = None
        self.surface = self.renderer = self.document = None
        self.pointer = None
        self._revision = None
        self._committing = False
        self._markers = tuple(new_id() for _ in range(66))
        self.error = ""
        self.interaction = None
        self.entity_panel = None
        self.constraint_panel = None
        self.view_tools = None
        self.snapper = SnapEngine()
        self.snap_result = self.snap_renderer = None
        self.snap_settings = SnapSettings()
        self._rectangle_active = False
        self.notice = None
        self.profiles = profiles
        self.profile_panel = None
        self.corner_panel = None
        self.checking_profiles = False

    def is_valid(self, document):
        obj = document.objects.get(self.object_id)
        if isinstance(obj, SketchObject):
            from .construction_context import resolve_object
            try:
                obj = resolve_object(obj, document)
            except ValueError:
                return False
        return (isinstance(obj, SketchObject) and obj.visible and not support_status(obj, document)
                and obj.sketch.support.frame == self.workplane)

    def history_display(self, document):
        from cadastrophe.core.body_history import sketch_history_display
        return sketch_history_display(document, self.object_id)

    def create_editor(self, parent, changed):
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        self.details = QLabel()
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        self.context_title = QLabel("Esquisse")
        self.context_title.setProperty("role","panelTitle")
        layout.addWidget(self.context_title)
        self.polygon_options = QWidget(widget)
        options = QHBoxLayout(self.polygon_options)
        options.addWidget(QLabel("Côtés"))
        self.polygon_count = QSpinBox()
        self.polygon_count.setRange(3, 64)
        self.polygon_count.setValue(6)
        options.addWidget(self.polygon_count)
        self.polygon_incircle = QCheckBox("Cercle inscrit")
        options.addWidget(self.polygon_incircle)
        layout.addWidget(self.polygon_options)
        self.spline_options = QWidget(widget)
        options = QHBoxLayout(self.spline_options)
        self.spline_closed = QCheckBox("Spline fermée")
        self.finish_spline = QPushButton("Terminer (Entrée)")
        options.addWidget(self.spline_closed)
        options.addWidget(self.finish_spline)
        layout.addWidget(self.spline_options)
        self.polygon_options.hide()
        self.spline_options.hide()
        def tool_options():
            self.draft.polygon_sides = self.polygon_count.value()
            self.draft.polygon_inscribed = self.polygon_incircle.isChecked()
            self.draft.spline_closed = self.spline_closed.isChecked()
            self._render()
        self.polygon_count.valueChanged.connect(tool_options)
        self.polygon_incircle.toggled.connect(tool_options)
        self.spline_closed.toggled.connect(tool_options)
        self.finish_spline.clicked.connect(self.confirm_operation)
        self.instructions, self.values = QLabel(), QLabel()
        for label in (self.instructions, self.values):
            label.setWordWrap(True)
            layout.addWidget(label)
        note = QLabel("Échap : quitter l’outil\nMilieu : rotation · Ctrl+milieu : pan\nAlt+milieu : roulis · Molette : zoom\nCtrl+8 : vue normale au support\n"
                      "Ctrl+Z/Y : annuler / rétablir\nAccrochages : Préférences > Esquisse\nRelations et cotes : panneau ci-dessous.")
        # Short fixed lines keep the help readable in a narrow parameter panel.
        # without a nested word-wrap height negotiation in the scroll area.
        note.setWordWrap(True)
        self.navigation_note = note
        note.setProperty("role", "secondaryText")
        help_button = QToolButton()
        help_button.setText("Aide et navigation")
        help_button.setCheckable(True)
        help_button.setArrowType(Qt.ArrowType.RightArrow)
        help_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        help_button.toggled.connect(note.setVisible)
        help_button.toggled.connect(lambda opened: help_button.setArrowType(Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow))
        self.help_button = help_button
        layout.addWidget(help_button)
        layout.addWidget(note)
        note.hide()
        self.entity_panel = EntityPanel(self, widget)
        self.constraint_panel = ConstraintPanel(self, widget)
        layout.addWidget(self.constraint_panel)
        layout.addWidget(self.entity_panel)
        self.profile_panel = ProfilePanel(self)
        layout.addWidget(self.profile_panel)
        layout.addStretch()
        self._feedback()
        return widget

    def attach(self, surface):
        self.surface, self.document = surface, surface.document
        self.renderer = SketchPresentation(surface, preview=True)
        prefs = surface.preferences
        self.notice = SketchNotice(surface.widget,prefs.theme)
        self.snap_settings = SnapSettings(prefs.sketch_snapping, prefs.sketch_inference, prefs.sketch_grid, prefs.sketch_grid_step)
        self.snap_renderer = SnapPresentation(surface, self.workplane)
        surface.connect("clicked", self.clicked)
        surface.connect("pointer_moved", self.moved)
        surface.connect("pointer_left", self.left)
        surface.connect("view_changed", self.view_changed)
        self.interaction = EntityInteraction(self)
        self.view_tools = ViewTools(self)
        surface.connect("double_clicked", self.view_tools.double_clicked)
        surface.connect("pressed", self.pressed)
        surface.connect("released", self.released)
        surface.connect("gesture_canceled", self.pointer_canceled)
        self.interaction.refresh()

    def pointer_canceled(self):
        if self.view_tools is not None and self.view_tools.drag is not None:
            self.view_tools.drag = None
            self.view_tools.clear_preview()
        if self._rectangle_active:
            self._rectangle_active = False
            self.draft.reset()
        self.snapper.reset()
        self.snap_result = None
        if self.interaction is not None:
            self.interaction.abort()
            self.selection_changed()
        self._render()

    def detach(self):
        # Called while the native context is alive, before the host restores 3D.
        self.close_corner()
        if self.notice is not None:
            self.notice.hide()
            self.notice.deleteLater()
            self.notice = None
        if self.view_tools is not None:
            self.view_tools.close()
            self.view_tools = None
        if self.interaction is not None:
            self.interaction.close()
            self.interaction = None
        if self.renderer is not None:
            self.renderer.clear()
        if self.snap_renderer is not None:
            self.snap_renderer.clear()
        self.snap_renderer = None
        self.renderer = self.surface = self.document = None
        self.pointer = None

    def close_corner(self):
        panel, self.corner_panel = self.corner_panel, None
        if panel is not None:
            panel.dispose()
            if self.document is not None and self.interaction is not None:
                self.selection_changed()

    def select_tool(self, tool):
        self.close_corner()
        self.checking_profiles = False
        if self.view_tools is not None:
            self.view_tools.abort()
        if self.surface is not None:
            self.surface.cancel_pointer()
        self.draft.select(tool)
        self.polygon_options.setVisible(tool == "polygon")
        self.spline_options.setVisible(tool == "spline")
        if tool == "arc_tangent" and self.document is not None:
            sketch = self.document.objects[self.object_id].sketch
            chosen = self.interaction.selected if self.interaction is not None else ()
            self._tangent_source = next((e for e in reversed(sketch.entities)
                                         if type(e) in (Segment, Arc) and (not chosen or e.id in chosen)), None)
        self.snapper.reset()
        self.snap_result = None
        self.state = self.draft.state
        self.error = ""
        if self.surface is not None:
            self.surface.set_cursor(Qt.CursorShape.CrossCursor if tool else None)
            self.surface.focus()
        self._render()
        if self.interaction is not None:
            self.interaction.refresh()
        self.selection_changed()

    def cancel_operation(self):
        if self.corner_panel is not None:
            self.close_corner()
        elif self.checking_profiles:
            self.checking_profiles = False
            self.interaction.set_selected(())
        elif self.view_tools is not None and (self.view_tools.mode is not None or self.view_tools.drag is not None or self.view_tools.selected_constraint is not None):
            self.view_tools.escape()
            if self.view_tools.mode is None:
                self.surface.set_cursor(None)
            self.selection_changed()
        elif self.interaction is not None and self.interaction.pending is not None:
            self.interaction.abort()
            self.selection_changed()
        elif self.draft.tool is not None:
            self.select_tool(None)
        elif self.interaction is not None:
            self.interaction.set_selected(())

    def _project(self, x, y):
        obj = self.document.objects[self.object_id]
        from .construction_context import resolve_object
        obj = resolve_object(obj, self.document)
        return obj.sketch.project_ray(self.surface.screen_ray(x, y))

    def resolve(self, x, y, *, anchor=None, exclude=()):
        point = self._project(x, y)
        if point is None:
            self.snapper.reset()
            self.snap_result = None
            return None
        self.snapper.update(self.document.objects[self.object_id].sketch.entities)
        circle = None
        if self.draft.tool == "arc" and len(self.draft.anchors) == 2:
            circle = self.draft.anchors[0], distance(*self.draft.anchors)
        if anchor is None and self.draft.anchors:
            anchor = self.draft.anchors[-1] if self.draft.tool in {"slot", "ellipse", "arc_three", "spline"} else self.draft.anchors[0]
        point, self.snap_result = self.snapper.resolve(point,
            lambda p: self.surface.project(self.workplane.to_world(p.u, p.v)), self.snap_settings,
            anchor=anchor, exclude=exclude, circle=circle)
        return point

    def toggle_snapping(self):
        self.snap_settings = replace(self.snap_settings, enabled=not self.snap_settings.enabled)
        self.snapper.reset()
        if self.pointer is not None:
            self.moved(*self.pointer)
        self.selection_changed()

    def pressed(self, x, y):
        if self.corner_panel is not None:
            self.corner_panel.pick(x, y)
            return
        if self.checking_profiles:
            self.checking_profiles = False
            self.selection_changed()
        if self.draft.tool is None and self.view_tools is not None and self.view_tools.press(x, y):
            return
        if self.draft.tool == "rectangle":
            self._rectangle_active = True
            if not self.draft.anchors:
                self.clicked(x, y, rectangle=True)
        elif self.interaction is not None:
            self.interaction.press(x, y)

    def moved(self, x, y):
        if self.surface is None or self.surface.closed:
            return
        if self.corner_panel is not None:
            self.surface.detect(x, y)
            return
        self.pointer = (x, y)
        self.draft.current = self.resolve(x, y) if self.draft.tool is not None else self._project(x, y)
        if self.draft.current is None:
            self.error = NO_SUPPORT_HIT
        elif self.error == NO_SUPPORT_HIT:
            self.error = ""
        if self.draft.tool is None and self.view_tools is not None:
            self.view_tools.moved(x, y)
        if self.interaction is not None and (self.view_tools is None or (self.view_tools.mode is None and self.view_tools.drag is None)):
            self.interaction.move(x, y)
        self._render()

    def view_changed(self):
        if self.view_tools is not None:
            self.view_tools.position_toolbar()
        if self.surface is not None and self.surface.presentation is not None:
            self.surface.presentation.annotations.update(self.document.objects[self.object_id].sketch,editing=True)
        if self.surface is not None and self.surface.navigating:
            if self.view_tools is not None:
                self.view_tools.clear_hover()
            return  # Camera motion does not draft, snap or detect entities.
        if self.pointer is not None:
            self.moved(*self.pointer)  # project again AFTER native pan/zoom/resize, never cached mm/px
        else:
            self._render()

    def left(self):
        if self.view_tools is not None:
            self.view_tools.clear_hover()
        self.pointer = self.draft.current = None
        self.snapper.reset()
        self.snap_result = None
        if self.surface is not None:
            self.surface.clear_detected()
        self._render()

    def released(self, x, y, dragged):
        if self.corner_panel is not None:
            return
        if self.draft.tool is None and self.view_tools is not None and self.view_tools.released(x, y, dragged):
            return
        if self.draft.tool == "rectangle" and self._rectangle_active:
            self._rectangle_active = False
            # A zero-size click leaves the first corner for a second click, but
            # a real drag starts on press and commits exactly once on release.
            if self.draft.anchors:
                point = self.resolve(x, y)
                first = self.draft.anchors[0]
                if point is not None and abs(point.u-first.u) > 1e-9 and abs(point.v-first.v) > 1e-9:
                    self.clicked(x, y, rectangle=True)
            return
        if self.interaction is not None:
            try:
                self.interaction.release(x, y, dragged)
            except Exception as exc:
                log.exception("Déplacement d’esquisse impossible")
                self.interaction.abort()
                self.error = str(exc)
                self.selection_changed()

    def selection_changed(self):
        if self.view_tools is not None:
            self.view_tools.sync_selection()
        if self.entity_panel is not None:
            self.entity_panel.refresh()
        if self.constraint_panel is not None:
            self.constraint_panel.refresh()
            active = self.view_tools is not None and (self.view_tools.mode is not None or self.view_tools.selected_constraint is not None)
            selecting = self.draft.tool is None
            self.constraint_panel.setVisible(selecting and not self.checking_profiles)
            self.constraint_panel.group.setVisible(active or bool(self.interaction.selected))
            self.entity_panel.setVisible(selecting and not active and not self.checking_profiles)
            self.navigation_note.setVisible(self.help_button.isChecked())
            selected = self.interaction.selected
            entity = self.document.objects[self.object_id].sketch.entity(selected[0]) if len(selected) == 1 else None
            name = {"segment":"Propriétés de ligne","circle":"Propriétés du cercle","arc":"Propriétés de l’arc","point":"Propriétés du point"}.get(entity.kind if entity else "","Esquisse")
            self.context_title.setText(TITLES.get(self.draft.tool) or ("Cotes au clic" if self.view_tools.mode == "dimension" else "Contraintes d’esquisse" if active else name))
            if self.checking_profiles:
                self.context_title.setText("Vérifier l’esquisse")
            self.profile_panel.setVisible(self.checking_profiles or (selecting and not active and not selected))
            self.profile_panel.refresh(self.document.objects[self.object_id].sketch)
        if self.corner_panel is not None:
            self.entity_panel.hide()
            self.constraint_panel.hide()
            self.profile_panel.hide()
            self.view_tools.toolbar.hide()
            self.context_title.setText(self.corner_panel.title())
            self.corner_panel.refresh()
        if self.instructions is not None:
            self.instructions.setVisible(self.corner_panel is None)
            self.values.setVisible(self.corner_panel is None)
        if self.details is not None and self.document is not None:
            obj = self.document.objects[self.object_id]
            count = len(self.interaction.selected) if self.interaction is not None else 0
            self.details.setText(f"{obj.name}\nSupport : {self.workplane.name}\n"
                                 f"{len(obj.sketch.entities)} entité(s) · {count} sélectionnée(s)")
        self._feedback()
        if self.tool_changed:
            self.tool_changed()
        if self.surface is not None:
            self.surface.notify_state()

    def validate_native(self, entities):
        from .presentation import entity_shape
        sketch = self.document.objects[self.object_id].sketch
        for entity in entities:
            if type(entity) is not Point:
                entity_shape(sketch, entity)

    def validate_sketch(self, sketch):
        from .annotations import make_annotation
        for constraint in sketch.constraints:
            try:
                make_annotation(sketch, constraint, self.surface.units, self.surface.pixel_ratio, self.surface.preferences.theme)
            except (TypeError, ValueError, RuntimeError) as exc:
                raise ValueError(f"Annotation impossible : {exc}") from exc

    def mutate(self, action):
        self._committing = True
        try:
            action()
        finally:
            self._committing = False
        if self.interaction is not None:
            self.interaction.refresh()
        self.selection_changed()

    def commit_entities(self, entities, *, title, annotation_positions=None, expressions=None):
        self.mutate(lambda: replace_entities(self.document, self.object_id, entities, title=title,
                                             validate=self.validate_native, validate_sketch=self.validate_sketch,
                                             annotation_positions=annotation_positions, expressions=expressions))

    def command_enabled(self, command_id):
        if self.corner_panel is not None:
            return False
        if command_id == "object.delete" and self.view_tools is not None and self.view_tools.selected_constraint is not None:
            return self.draft.tool is None and self.view_tools.drag is None and self.interaction.pending is None
        return bool(self.interaction is not None and self.interaction.command_enabled(command_id))

    def execute_command(self, command_id):
        try:
            if command_id == "object.delete" and self.view_tools is not None and self.view_tools.selected_constraint is not None:
                from .commands import remove_constraints
                self.mutate(lambda: remove_constraints(self.document, self.object_id, (self.view_tools.selected_constraint,)))
                return True
            return self.interaction.execute(command_id)
        except Exception as exc:
            log.exception("Commande d’entités impossible")
            self.error = str(exc)
            self.selection_changed()
            return False

    def confirm_operation(self):
        if self.corner_panel is not None:
            self.corner_panel.commit()
            return
        if self.draft.tool == "spline":
            try:
                entity = Spline(self.draft.anchors, self.draft.spline_closed, id=self.draft._ids[0])
                self._commit_draft((entity,), self.draft.anchors[-1])
                self.error = ""
            except (ValueError, RuntimeError) as exc:
                self.error = str(exc)
            self._render()
            return
        focus = QApplication.focusWidget()
        if self.constraint_panel is not None and focus is not None and self.constraint_panel.isAncestorOf(focus):
            self.constraint_panel.confirm()
            return
        if self.entity_panel is not None and self.draft.tool is None:
            self.entity_panel.commit()

    def clicked(self, x, y, *, rectangle=False):
        if self.surface is None or self.surface.closed:
            return
        if self.corner_panel is not None:
            return
        if self.draft.tool is None:
            if self.view_tools is not None:
                self.view_tools.clicked(x, y)
            return
        if self.draft.tool == "rectangle" and not rectangle:
            return
        self.pointer = (x, y)
        point = self.resolve(x, y)
        if point is None:
            self.error = NO_SUPPORT_HIT
            self._render()
            return
        try:
            if self.draft.tool == "arc_tangent" and not self.draft.anchors:
                from .extra_drawing import tangent_at
                self.draft.tangent = tangent_at(self._tangent_source, point)
            entities = self.draft.click(point)
            if entities:
                self._commit_draft(entities, point)
            self.error = ""
        except ValueError as exc:
            self.error = str(exc)
        except Exception as exc:
            log.exception("Échec du dessin dans %s", self.object_id)
            self.error = f"Dessin non validé : {exc}"
        self.state = self.draft.state
        self._render()

    def _commit_draft(self, entities, point):
        self.validate_native(entities)
        sketch = self.document.objects[self.object_id].sketch
        from .creation_relations import creation_relations
        relations = creation_relations(sketch, entities, rectangle=self.draft.tool == "rectangle")
        self._committing = True
        try:
            add_entities(self.document, self.object_id, entities, constraints=relations,
                         validate=self.validate_native, validate_sketch=self.validate_sketch,
                         title=f"Dessiner : {TITLES[self.draft.tool]}")
        finally:
            self._committing = False
        self.draft.committed(point)
        self.snapper.reset()
        self.snap_result = None

    def _render(self):
        if self.surface is None or self.surface.closed or self.renderer is None:
            self._feedback()
            return
        entities = ()
        if self.draft.current is not None:
            try:
                entities = self.draft.candidate(self.draft.current)
            except ValueError:
                # At a newly accepted anchor the live shape is naturally zero.
                # Report degeneracy only on an actual invalid validation click.
                pass
        points = self.draft.anchors
        if self.draft.tool and self.draft.current is not None:
            end = entities[0].point_at(1) if entities and type(entities[0]) is Arc else self.draft.current
            points = (*points, end)
        markers = tuple(Point(point, id=self._markers[i]) for i, point in enumerate(points))
        obj = self.document.objects[self.object_id]
        if self.snap_renderer is not None:
            anchor = self.draft.anchors[0] if self.draft.anchors else None
            self.snap_renderer.show(self.snap_result, anchor)
            self.snap_renderer.update_grid(self.snap_settings, self._project)
        preview = replace(obj, sketch=obj.sketch.independent((*entities, *markers)))
        try:
            self.renderer.update(preview, selected=True, editing=True)
        except Exception as exc:
            log.exception("Aperçu d’esquisse impossible")
            self.renderer.clear()
            self.error = f"Aperçu indisponible : {exc}"
        self._feedback(entities)

    def _feedback(self, entities=()):
        if self.instructions is None:
            return
        tool, anchors = self.draft.tool, self.draft.anchors
        stages = {"point": ("Cliquer pour placer un point ; Échap quitte l’outil.",),
                  "line": ("Cliquer le départ.", "Cliquer la fin ; la ligne reste chaînée."),
                  "rectangle": ("Appuyer et glisser depuis le premier coin.", "Relâcher au coin opposé pour valider."),
                  "circle": ("Cliquer le centre.", "Cliquer un point du cercle."),
                  "arc": ("Cliquer le centre.", "Cliquer le départ (fixe le rayon).",
                          "Cliquer la direction de fin · sens antihoraire U/V."),
                  "arc_three": ("Cliquer le départ.", "Cliquer la fin.", "Cliquer un point de passage."),
                  "arc_tangent": ("Cliquer une extrémité de l’entité sélectionnée.", "Cliquer la fin de l’arc tangent."),
                  "slot": ("Cliquer le premier centre.", "Cliquer le second centre.", "Cliquer pour définir la largeur."),
                  "polygon": ("Cliquer le centre.", "Cliquer pour définir la taille et l’orientation."),
                  "ellipse": ("Cliquer le centre.", "Cliquer l’extrémité du premier axe.", "Cliquer pour définir le second axe."),
                  "spline": ("Cliquer les pôles de contrôle ; Entrée termine la spline.",)}
        stages["construction_line"] = stages["line"]
        self.instructions.setText(f"{TITLES[tool]} — {stages[tool][min(len(anchors), len(stages[tool])-1)]}" if tool
                                  else "Sélectionner une entité · Ctrl : sélection multiple · Glisser : déplacer\n"
                                       "Glisser dans le vide : sélection fenêtre · Poignées : ajuster")
        if self.view_tools is not None and self.view_tools.mode is not None:
            self.instructions.setText("Cotes au clic — cliquer une ou deux références, puis dans le vide pour placer. Échap : retirer la dernière référence."
                                      if self.view_tools.mode == "dimension" else
                                      "Relations — sélectionner dans la vue · Ctrl : deuxième référence · choisir une relation verte.")
        if self.checking_profiles:
            self.instructions.setText("Choisir un défaut dans la liste pour repérer ses entités. Échap ou cliquer dans la vue pour revenir à l’édition.")
        values = []
        if self.surface is not None:
            length = self.surface.units.format_length
            point = self.draft.current
            if point is not None:
                values.append(f"U : {length(point.u)} · V : {length(point.v)}")
            if entities:
                entity = entities[0]
                if tool == "rectangle":
                    values.append(f"Largeur : {length(abs(point.u - anchors[0].u))}\n"
                                  f"Hauteur : {length(abs(point.v - anchors[0].v))}")
                elif isinstance(entity, Circle):
                    values.append(f"Rayon : {length(entity.radius)}")
                    if type(entity) is Arc:
                        values.append(f"Balayage : {degrees(entity.sweep):.1f}°")
                elif type(entity) is Ellipse:
                    values.append(f"Demi-axes : {length(entity.major_radius)} × {length(entity.minor_radius)}")
                elif type(entity) is Spline:
                    values.append(f"{len(entity.points)} pôles · {'fermée' if entity.closed else 'ouverte'}")
                elif type(entity) is Segment:
                    values.append(f"{'Rayon' if tool == 'arc' else 'Longueur'} : {length(distance(entity.start, entity.end))}")
        if self.notice is not None:
            self.notice.set_message(self.error)
        if self.snap_result is not None:
            values.append("Accrochage : " + self.snap_result.label)
        self.values.setText("\n".join(values))

    def report_error(self,message):
        self.error = message
        self._feedback()

    def check_profiles(self):
        self.select_tool(None)
        self.checking_profiles = True
        self.interaction.set_selected(())
        if self.profiles is None:
            from .profile_service import SketchProfiles
            self.profiles = SketchProfiles()
        report = self.profiles.analyze(self.document,self.object_id)
        self.profile_panel.show_report(report)
        return report

    def refresh(self, document):
        self.document = document
        obj = document.objects[self.object_id]
        if self._revision is not None and self._revision != obj.revision and not self._committing:
            if self.view_tools is not None:
                dimension_active = self.view_tools.mode == "dimension"
                self.view_tools.abort(clear_selection=dimension_active)
                if dimension_active:
                    self.view_tools.mode = "dimension"
                    self.view_tools.dimension_state.start()
            self.draft.reset()  # undo/redo/external changes discard a stale unfinished chain
            if self.interaction is not None:
                self.interaction.abort()
                if self.view_tools is not None and self.view_tools.mode == "dimension":
                    self.interaction.set_selected(())
        self._revision = obj.revision
        if self.details is not None:
            self.details.setText(f"{obj.name}\nSupport : {self.workplane.name}\n{len(obj.sketch.entities)} entité(s)")
        self.state = self.draft.state
        if not self._committing:
            if self.interaction is not None:
                self.interaction.refresh()
            self.selection_changed()
            self._render()

    def dispose(self):
        self.detach()
        self.draft.select(None)
        self.details = self.instructions = self.values = None
        self.entity_panel = None
        self.constraint_panel = None
        if self.profile_panel is not None:
            self.profile_panel.report = None
        self.profile_panel = self.profiles = None
        self.checking_profiles = False
        self.actions = ()
        self.tool_changed = None
        self.state = "idle"

    def finish_edit(self, document):
        obj = document.objects.get(self.object_id)
        if self.created and obj is not None and not obj.sketch.all_entities and not obj.sketch.constraints:
            document.remove(obj.id)
