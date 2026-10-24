"""Explicit persistent relations and driving dimensions in the parameter panel."""
from dataclasses import replace
from math import hypot, degrees

from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QHBoxLayout, QGroupBox,
                               QLabel, QComboBox, QListWidget, QListWidgetItem, QPushButton, QLayout, QSizePolicy,
                               QToolButton, QGridLayout)

from cadastrophe.plugin_api import LengthSpinBox
from .constraints import Constraint, Reference, LABELS, DIMENSIONS, POINT_KINDS, PAIR_KINDS, point
from .commands import set_constraints
from .editing import control_points
from .joints import joint_handles
from .model import Arc, Circle, Point, Segment
from .solver import diagnose
from .dimension_value import AngleSpinBox
from .markers import relation_icon


class ConstraintPanel(QWidget):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._snapshot = self._selection = None
        self._expression_snapshot = None
        self._updating = False
        self._loaded_editable = True
        self._external_key = None
        self._diagnosis = None
        self.list = None
        self.body_layout = QVBoxLayout(self)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.diagnostic = QLabel()
        self.diagnostic.setWordWrap(True)
        self.diagnostic.setAccessibleName("État des contraintes")
        self.body_layout.addWidget(self.diagnostic)

    @property
    def sketch(self):
        return self.session.view_tools.working_sketch if self.session.view_tools is not None else self.session.document.objects[self.session.object_id].sketch

    def _build(self):
        self.group = QGroupBox("Propriétés · Relations")
        layout = QVBoxLayout(self.group)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        header = QLabel("Entités sélectionnées")
        header.setStyleSheet("font-weight: 600;")
        layout.addWidget(header)
        self.selected_list = QListWidget()
        self.selected_list.setAccessibleName("Références sélectionnées dans la vue")
        self.selected_list.setMaximumHeight(self.fontMetrics().height()*4)
        self.selected_list.setMinimumHeight(self.fontMetrics().height()*2)
        self.selected_list.setProperty("role", "selectionReferences")
        layout.addWidget(self.selected_list)
        self.selection_help = QLabel("Ctrl : ajouter une référence\nCtrl+Alt+clic : privilégier un solide superposé.")
        self.selection_help.setWordWrap(True)
        layout.addWidget(self.selection_help)
        self.axis = QComboBox()
        self.axis.setAccessibleName("Orientation de la cotation intelligente")
        for label, key in (("Orientation automatique", "auto"), ("Distance alignée", "aligned"),
                           ("Distance horizontale", "horizontal"), ("Distance verticale", "vertical")):
            self.axis.addItem(label, key)
        self.axis.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.axis.setMinimumContentsLength(10)
        def align():
            tools = self.session.view_tools
            if tools is not None:
                tools.alignment = self.axis.currentData()
                if tools.mode == "dimension" and self.session.pointer is not None:
                    tools.moved(*self.session.pointer)
        self.axis.currentIndexChanged.connect(align)
        layout.addWidget(self.axis)
        header = QLabel("Relations existantes")
        header.setStyleSheet("font-weight: 600;")
        layout.addWidget(header)
        self.list = QListWidget()
        self.list.setAccessibleName("Contraintes de l’esquisse")
        self.list.setMaximumHeight(self.fontMetrics().height()*6)
        self.list.setMinimumHeight(self.fontMetrics().height()*3)
        self.list.currentRowChanged.connect(self._load)
        layout.addWidget(self.list)
        header = QLabel("Ajouter des relations")
        header.setStyleSheet("font-weight: 600;")
        layout.addWidget(header)
        self.context_widget = QWidget()
        self.context_grid = QGridLayout(self.context_widget)
        self.context_grid.setContentsMargins(0, 0, 0, 0)
        self.context_grid.setSpacing(3)
        layout.addWidget(self.context_widget)
        self._context_key = None
        self.advanced = QToolButton()
        self.advanced.setText("Paramètres et références")
        self.advanced.setCheckable(True)
        self.advanced.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout.addWidget(self.advanced)
        self.form_container = QWidget()
        self.form = QFormLayout(self.form_container)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.kind = QComboBox()
        self.kind.setAccessibleName("Type de contrainte")
        for key, label in LABELS.items():
            self.kind.addItem(label, key)
        self.first, self.second = QComboBox(), QComboBox()
        for combo in (self.kind, self.first, self.second):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(10)
        self.first.setAccessibleName("Première référence de contrainte")
        self.second.setAccessibleName("Seconde référence de contrainte")
        units = self.session.surface.units
        self.value = LengthSpinBox(units, 1e-9, 1e12, 10)
        self.value.setAccessibleName("Valeur de la cote")
        self.angle_value = AngleSpinBox()
        self.value.bind_equations(self.session.document, "new", owner=self.session.object_id,
                                  scale=units.to_internal(1.))
        self.angle_value.bind_equations(self.session.document, "new", owner=self.session.object_id, unit="angle")
        self.angle_value.setAccessibleName("Valeur de la cote angulaire")
        self.fixed_u = LengthSpinBox(units, -1e12, 1e12, 0)
        self.fixed_v = LengthSpinBox(units, -1e12, 1e12, 0)
        for label, control in (("Relation", self.kind), ("Élément 1", self.first), ("Élément 2", self.second),
                               ("Valeur", self.value), ("Angle", self.angle_value), ("Fixer U", self.fixed_u), ("Fixer V", self.fixed_v)):
            self.form.addRow(label, control)
        layout.addWidget(self.form_container)
        self.form_container.hide()
        self.advanced.toggled.connect(self.form_container.setVisible)
        self.advanced.toggled.connect(lambda checked: self.advanced.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow))
        row = QHBoxLayout()
        self.add = QPushButton("Ajouter")
        self.modify = QPushButton("Modifier")
        self.remove = QPushButton("Supprimer")
        for control, callback in ((self.add, self.add_constraint), (self.modify, self.update_constraint),
                                   (self.remove, self.remove_constraint)):
            control.clicked.connect(callback)
            if control is not self.remove:
                row.addWidget(control)
        layout.addLayout(row)
        layout.addWidget(self.remove)
        self.link = QPushButton("Lier les points communs")
        self.link.setToolTip("Créer explicitement des coïncidences entre les extrémités déjà confondues de cette esquisse.")
        self.link.clicked.connect(self.link_common_points)
        layout.addWidget(self.link)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.message.hide()
        note = QLabel("Cotation : sélectionner, puis placer dans la vue.\nDouble-clic sur une cote : modifier sa valeur.\nGlisser son annotation : déplacer l’affichage.")
        note.setWordWrap(True)
        note.setProperty("role", "secondaryText")
        layout.addWidget(note)
        self.body_layout.addWidget(self.group)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.first.currentIndexChanged.connect(self._suggest_value)
        self.second.currentIndexChanged.connect(self._suggest_value)

    def refresh(self):
        if self._updating:
            return
        session = self.session
        if session.surface is None or session.interaction is None:
            return
        if self.list is None:
            self._build()
        sketch, selected = self.sketch, session.interaction.selected
        expressions = session.document.objects[session.object_id].expressions
        changed = self._snapshot != sketch or self._expression_snapshot != expressions
        external_key = tuple((e.body_id, getattr(session.document.objects.get(e.body_id), "revision", None)) for e in sketch.external)
        if changed or external_key != self._external_key:
            self._diagnosis = diagnose(sketch)
        self._context()
        self._updating = True
        try:
            if changed:
                self.message.clear()
                key = self._current_id()
                blocker = QSignalBlocker(self.list)
                self.list.clear()
                dimension_number = 0
                for c in sketch.constraints:
                    suffix = ""
                    if c.kind in DIMENSIONS:
                        dimension_number += 1
                        suffix = " : " + (f"{degrees(c.value):.2f}°" if c.kind == "angle" else session.surface.units.format_length(c.value))
                    item = QListWidgetItem(f"{LABELS[c.kind]}{suffix}" + (f" · D{dimension_number}" if c.kind in DIMENSIONS else ""))
                    if c.driven:
                        item.setText(item.text()+" · mesure")
                        item.setToolTip("Cote de mesure, pilotée par les sources et les paramètres du motif.")
                    if c.id in self._diagnosis.conflicts and self._diagnosis.termination == "conflict":
                        item.setText(item.text()+" · contradiction")
                        item.setToolTip("Cette relation appartient à une contradiction linéaire prouvée. Sélectionnez-la pour la modifier ou la supprimer.")
                    elif c.id in self._diagnosis.redundant:
                        item.setText(item.text()+" · redondance locale")
                        item.setToolTip("Cette relation n’ajoute pas de contrainte indépendante au voisinage de la configuration actuelle.")
                    item.setData(Qt.ItemDataRole.UserRole, c.id)
                    self.list.addItem(item)
                    if c.id == key:
                        self.list.setCurrentItem(item)
                del blocker
            if changed or selected != self._selection:
                self._populate(prefer_selection=selected != self._selection)
            if changed or external_key != self._external_key:
                from .external import status
                from .construction_context import support_objects
                error = status(sketch, support_objects(session.document.objects[session.object_id], session.document)) if sketch.external else ""
                self.diagnostic.setText(error or self._diagnosis.message)
                self._external_key = external_key
            self._snapshot, self._selection = sketch, selected
            self._expression_snapshot = expressions
        finally:
            self._updating = False
        self._visibility()
        if changed and self._current_id() is not None:
            self._load()
        active = session.draft.tool is None and session.interaction.pending is None
        self.group.setEnabled(active)
        self.modify.setEnabled(self._current_id() is not None and self._loaded_editable)
        self.remove.setEnabled(self._current_id() is not None)

    def _current_id(self):
        item = self.list.currentItem() if self.list is not None else None
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _context(self):
        tools = self.session.view_tools
        if tools is None:
            return
        refs = tuple(tools.refs)
        self.selection_help.setText("Cotation : cliquer les entités, puis dans le vide pour placer.\nÉchap : retirer la dernière référence."
                                    if tools.mode == "dimension" else
                                    "Ctrl : ajouter une référence\nCtrl+Alt+clic : privilégier un solide superposé.")
        self.selected_list.clear()
        for ref in refs:
            try:
                self.selected_list.addItem(tools.name(ref))
            except KeyError:
                pass
        kinds = tools.available()
        self.axis.setVisible(tools.mode == "dimension")
        context_key = kinds, self.session.surface.preferences.theme
        if context_key == self._context_key:
            return
        self._context_key = context_key
        while self.context_grid.count():
            widget = self.context_grid.takeAt(0).widget()
            widget.deleteLater()
        if not kinds:
            label = QLabel("Sélectionnez une entité ou deux références compatibles.")
            label.setWordWrap(True)
            self.context_grid.addWidget(label, 0, 0)
        for index, key in enumerate(kinds):
            button = QToolButton()
            button.setText(LABELS[key])
            button.setIcon(relation_icon(key,self.session.surface.pixel_ratio,self.session.surface.preferences.theme))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setAccessibleName("Ajouter " + LABELS[key])
            button.setToolTip(LABELS[key])
            color = "#8bd69a" if self.session.surface.preferences.theme == "dark" else "#287d35"
            button.setStyleSheet("QToolButton { color: " + color + "; text-align: left; padding: 3px; }")
            button.clicked.connect(lambda checked=False, kind=key: tools.add_relation(kind))
            self.context_grid.addWidget(button, index, 0)

    @staticmethod
    def _select(combo, reference):
        for index in range(combo.count()):
            if combo.itemData(index) == reference:
                combo.setCurrentIndex(index)
                return

    def _populate(self, *, prefer_selection=False):
        kind = self.kind.currentData()
        selected = self.session.interaction.selected
        names = {Point: "Point", Segment: "Segment", Circle: "Cercle", Arc: "Arc"}
        parts = {"position": "point", "start": "départ", "end": "fin", "center": "centre"}
        entities = list(enumerate(self.sketch.all_entities, 1))
        entities.sort(key=lambda pair: pair[1].id not in selected)
        for index, combo in enumerate((self.first, self.second)):
            choices = []
            point_reference = kind in POINT_KINDS or (kind in {"point_on_line", "line_distance"} and index == 0)
            if point_reference and kind != "fixed":
                choices.append(("Origine de l’esquisse", Reference(self.sketch.id, "position")))
            for entity_index, entity in entities:
                label = self.session.view_tools.name(Reference(entity.id)) if self.session.view_tools is not None else f"{names[type(entity)]} {entity_index}"
                if point_reference:
                    choices.extend((label + " · " + parts[part], Reference(entity.id, part))
                                   for part, _ in control_points(entity) if part in parts)
                elif (isinstance(entity, Circle) if kind in {"radius", "diameter"} else type(entity) is Segment):
                    choices.append((label, Reference(entity.id)))
                    if kind == "angle":
                        choices.extend(((label + " · vers fin", Reference(entity.id, "end")),
                                        (label + " · vers départ", Reference(entity.id, "start"))))
            old = combo.currentData()
            blocker = QSignalBlocker(combo)
            combo.clear()
            for label, reference in choices:
                combo.addItem(label, reference)
            if choices:
                combo.setCurrentIndex(min(index, len(choices)-1))
            if not prefer_selection:
                self._select(combo, old)
            del blocker

    def _visibility(self):
        kind = self.kind.currentData()
        for widget, visible in ((self.second, kind in PAIR_KINDS), (self.value, kind in DIMENSIONS and kind != "angle"),
                                 (self.angle_value, kind == "angle"),
                                 (self.fixed_u, kind == "fixed"), (self.fixed_v, kind == "fixed")):
            widget.setVisible(visible)
            self.form.labelForField(widget).setVisible(visible)

    def _kind_changed(self):
        if self._updating:
            return
        self._populate(prefer_selection=True)
        self._visibility()
        self._suggest_value()

    def _suggest_value(self):
        if self._updating or self.first.currentData() is None:
            return
        ref, kind = self.first.currentData(), self.kind.currentData()
        entity = self.sketch.entity(ref.entity_id)
        if kind == "fixed":
            p = point(entity, ref.part)
            self.fixed_u.set_internal_value(p.u)
            self.fixed_v.set_internal_value(p.v)
        elif kind in DIMENSIONS:
            from .dimension_geometry import measure
            refs = (ref, self.second.currentData()) if kind in PAIR_KINDS else (ref,)
            if any(r is None for r in refs):
                return
            try:
                value = measure(self.sketch, kind, refs)
            except ValueError:
                return
            if 1e-9 <= value <= 1e12:
                (self.angle_value if kind == "angle" else self.value).set_internal_value(value)

    def _load(self):
        if self._updating:
            return
        c = next((c for c in self.sketch.constraints if c.id == self._current_id()), None)
        if c is None:
            return
        self._loaded_editable = not c.driven
        self.advanced.setChecked(True)
        self._updating = True
        try:
            if self.session.view_tools is not None:
                self.session.view_tools.highlight_constraint(c)
            self.kind.setCurrentIndex(self.kind.findData(c.kind))
            self._populate()
            self._select(self.first, c.refs[0])
            if len(c.refs) == 2:
                self._select(self.second, c.refs[1])
            if c.kind in DIMENSIONS:
                field = self.angle_value if c.kind == "angle" else self.value
                field.set_internal_value(c.value)
                if not c.driven:
                    field.bind_equations(self.session.document, c.id, owner=self.session.object_id,
                                         unit="angle" if c.kind == "angle" else "length",
                                         scale=1. if c.kind == "angle" else self.session.surface.units.to_internal(1.))
            if c.kind == "fixed":
                self.fixed_u.set_internal_value(c.value.u)
                self.fixed_v.set_internal_value(c.value.v)
        except ValueError as exc:
            self._loaded_editable = False
            self.message.setText(f"Valeur hors de la plage d’édition : {exc}")
            self.modify.setEnabled(False)
            return
        finally:
            self._updating = False
        self._visibility()
        self.modify.setEnabled(not c.driven)
        self.remove.setEnabled(True)

    def _constraint(self):
        from .model import Vec2
        kind = self.kind.currentData()
        refs = (self.first.currentData(), self.second.currentData()) if kind in PAIR_KINDS else (self.first.currentData(),)
        value = None
        for field in (self.value, self.angle_value, self.fixed_u, self.fixed_v):
            field.interpretText()
        if kind in DIMENSIONS:
            value = (self.angle_value if kind == "angle" else self.value).internal_value
        elif kind == "fixed":
            value = Vec2(self.fixed_u.internal_value, self.fixed_v.internal_value)
        driven = kind in DIMENSIONS and any(self.sketch.pattern_for(r.entity_id) is not None for r in refs)
        if driven:
            from .dimension_geometry import measure
            value = measure(self.sketch, kind, refs)
        return Constraint(kind, refs, value, driven=driven)

    def _commit(self, constraints, title, expressions=None):
        if not self.group.isEnabled():
            return False
        try:
            self.session.error = ""
            used = {r.entity_id for c in constraints for r in c.refs}
            external = tuple(e for e in self.sketch.external if e.geometry.id in used)
            self.session.mutate(lambda: set_constraints(self.session.document, self.session.object_id,
                constraints, title=title, external=external, validate=self.session.validate_native,
                expressions=expressions,
                validate_sketch=self.session.validate_sketch))
            self.message.clear()
            self.session.error = ""
            return True
        except (ValueError, RuntimeError) as exc:
            self.message.setText(str(exc))
            self.session.error = str(exc)
            self.session.selection_changed()
            return False

    def add_constraint(self):
        try:
            c = self._constraint()
            field = self.angle_value if c.kind == "angle" else self.value
            exp = field.equation_expression() if c.kind in DIMENSIONS and not c.driven else None
            return self._commit((*self.sketch.constraints, c), "Ajouter une contrainte d’esquisse",
                                {c.id: replace(exp, target=c.id) if exp else None})
        except ValueError as exc:
            self.message.setText(str(exc))
            self.session.report_error(str(exc))
            return False

    def update_constraint(self):
        key = self._current_id()
        if key is None or not self._loaded_editable:
            return False
        try:
            original = next(c for c in self.sketch.constraints if c.id == key)
            changed = replace(self._constraint(), id=key, label_position=original.label_position)
            field = self.angle_value if changed.kind == "angle" else self.value
            exp = field.equation_expression() if changed.kind in DIMENSIONS and not changed.driven else None
            return self._commit(tuple(changed if c.id == key else c for c in self.sketch.constraints),
                                "Modifier une cote ou relation d’esquisse",
                                {key: replace(exp, target=key) if exp else None})
        except ValueError as exc:
            self.message.setText(str(exc))
            self.session.report_error(str(exc))
            return False

    def remove_constraint(self):
        key = self._current_id()
        if key is None:
            return False
        from .commands import remove_constraints
        self.session.mutate(lambda: remove_constraints(self.session.document, self.session.object_id, (key,)))
        self.message.clear()
        return True

    def link_common_points(self):
        from .joints import endpoints
        constraints, seen = list(self.sketch.constraints), set()
        existing = {frozenset(c.refs) for c in constraints if c.kind == "coincident"}
        for entity in self.sketch.entities:
            for part, _ in endpoints(entity):
                handle = (entity.id, part)
                if handle in seen:
                    continue
                targets = joint_handles(self.sketch.entities, handle)
                seen.update(targets)
                source = Reference(*handle)
                for other in targets:
                    target = Reference(*other)
                    pair = frozenset((source, target))
                    if target != source and pair not in existing:
                        constraints.append(Constraint("coincident", (source, target)))
                        existing.add(pair)
        return self._commit(constraints, "Lier les points communs de l’esquisse")

    def focus_relations(self, *, dimension=False):
        if dimension:
            selected = self.session.interaction.selected
            entity = self.sketch.entity(selected[0]) if selected else None
            kind = "radius" if isinstance(entity, Circle) else "length"
            self.kind.setCurrentIndex(self.kind.findData(kind))
            self._populate(prefer_selection=True)
            self._suggest_value()
            self.value.setFocus()
            self.value.selectAll()
        else:
            self.kind.setFocus()

    def confirm(self):
        return self.update_constraint() if self._current_id() is not None else self.add_constraint()
