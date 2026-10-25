"""Sketch text, reshape and copy tools in the host's existing edit session."""
from cadastrophe.plugin_api import ErrorLabel, set_feedback
from dataclasses import replace
from math import radians

from PySide6.QtWidgets import (QGroupBox, QVBoxLayout, QFormLayout, QLabel, QComboBox,
    QCheckBox, QLineEdit, QFontComboBox, QPushButton, QDialogButtonBox)
from cadastrophe.plugin_api import LengthSpinBox, EquationSpinBox, EquationIntSpinBox
from .model import Segment, Vec2, new_id
from . import reshape

TITLES = {"text": "Texte en contours", "trim": "Rogner", "offset": "Décaler",
          "mirror": "Symétriser", "linear_pattern": "Répétition linéaire",
          "circular_pattern": "Répétition circulaire"}


def open_reshape(session, mode, pattern_id=None):
    session.select_tool(None)
    session.view_tools.abort()
    parent = session.entity_panel.parentWidget()
    panel = ReshapePanel(session, mode, parent, pattern_id=pattern_id)
    session.corner_panel = panel
    parent.layout().insertWidget(2, panel)
    panel.show()
    if pattern_id is not None:
        pattern = next(p for p in session.document.objects[session.object_id].sketch.patterns if p.id == pattern_id)
        session.interaction.set_selected(pattern.entity_ids)
    session.interaction.refresh()
    session.selection_changed()
    session.surface.focus()


class ReshapePanel(QGroupBox):
    def __init__(self, session, mode, parent, *, pattern_id=None):
        super().__init__(TITLES[mode], parent)
        self.setObjectName("sketchReshapePanel")
        self.session, self.mode = session, mode
        self.editing_id = pattern_id
        self.pattern_id = pattern_id or new_id()
        self.equation_fields = {}
        self._expressions = {}
        self._key = self._candidate = None
        self._pick_mode = "position" if mode == "text" else "entities"
        self.trim_target = self.trim_position = None
        self.fields = []
        from .presentation import SketchPresentation
        self.preview = SketchPresentation(session.surface, preview=True)
        self.preview.set_state(interactive=True)
        layout, form = QVBoxLayout(self), QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.collector = QPushButton("Choisir les entités dans la vue")
        self.collector.clicked.connect(self.collect_entities)
        if mode != "text":
            form.addRow(self.collector)
            clear = QPushButton("Effacer la sélection")
            clear.clicked.connect(lambda: session.interaction.set_selected(()))
            form.addRow(clear)
        self.text = self.font = None
        if mode == "text":
            self.text, self.font = QLineEdit("CAD"), QFontComboBox()
            self.text.setMaxLength(100)
            form.addRow("Texte", self.text)
            form.addRow("Police", self.font)
            self.height = self.length(form, "Hauteur des contours", 10., positive=True)
        if mode in ("text", "circular_pattern", "mirror"):
            self.u = self.length(form, "U" if mode == "text" else "Centre / origine U", 0.)
            self.v = self.length(form, "V" if mode == "text" else "Centre / origine V", 0.)
            locate = QPushButton("Placer dans la vue" if mode == "text" else "Choisir le centre / l’origine")
            locate.clicked.connect(lambda: self.set_pick_mode("position"))
            form.addRow(locate)
        if mode in ("text", "circular_pattern"):
            self.angle = EquationSpinBox()
            self.angle.setRange(-360., 360.)
            self.angle.setDecimals(3)
            self.angle.setSuffix("°")
            self.angle.setValue(360. if mode == "circular_pattern" else 0.)
            form.addRow("Angle total" if mode == "circular_pattern" else "Rotation", self.angle)
            self.fields.append(self.angle)
        if mode in ("linear_pattern", "circular_pattern"):
            self.count = EquationIntSpinBox()
            self.count.setRange(2, 200)
            self.count.setValue(3)
            form.addRow("Occurrences, source comprise", self.count)
            self.count.valueChanged.connect(self.changed)
        if mode == "linear_pattern":
            self.du = self.length(form, "Pas U", 20.)
            self.dv = self.length(form, "Pas V", 0.)
        if mode == "offset":
            self.distance = self.length(form, "Distance signée", 3.)
            reverse = QPushButton("Inverser le côté")
            reverse.clicked.connect(self.reverse_offset)
            form.addRow(reverse)
        if mode == "mirror":
            self.axis = QComboBox()
            self.axis.addItems(("Axe V", "Axe U", "Direction personnalisée"))
            form.addRow("Axe passant par l’origine choisie", self.axis)
            self.du = self.length(form, "Direction U", 0.)
            self.dv = self.length(form, "Direction V", 10.)
            axis = QPushButton("Prendre un segment comme axe")
            axis.clicked.connect(lambda: self.set_pick_mode("axis"))
            form.addRow(axis)
            self.axis.currentIndexChanged.connect(self.changed)
        self.remove_relations = QCheckBox("Supprimer les cotes et relations de l’entité rognée")
        if mode == "trim":
            form.addRow(self.remove_relations)
            self.remove_relations.toggled.connect(self.changed)
        layout.addLayout(form)
        help_text = {
            "text": "Cliquer pour placer le coin inférieur gauche. Appliquer crée les contours des lettres ; le texte est ensuite éditable comme des courbes.",
            "trim": "Cliquer le tronçon à retirer entre les intersections. Sans intersection, le clic retire la courbe entière.",
            "offset": "Choisir un contour continu de segments, cercles ou arcs. Inverser le côté change le signe de la distance. Les originaux sont conservés.",
            "mirror": "Choisir les sources puis l’axe. Les copies sont indépendantes ; les contraintes des sources sont conservées.",
            "linear_pattern": "Choisir les sources. Le pas U/V sépare les occurrences associatives. Modifier la source ou le motif met à jour les copies. Les motifs imbriqués sont refusés.",
            "circular_pattern": "Choisir les sources et le centre. Les occurrences restent liées à la source. À 360°, le tour ne double pas la source. Les motifs imbriqués sont refusés.",
        }[mode]
        help_label = QLabel(help_text)
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.error = ErrorLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Appliquer")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Fermer")
        self.buttons.accepted.connect(self.commit)
        self.buttons.rejected.connect(session.close_corner)
        layout.addWidget(self.buttons)
        if mode in ("linear_pattern", "circular_pattern"):
            self.equation_fields = dict(count=self.count)
            if mode == "linear_pattern":
                self.equation_fields.update(du=self.du, dv=self.dv)
            else:
                self.equation_fields.update(center_u=self.u, center_v=self.v, angle=self.angle)
            pattern = next((p for p in session.document.objects[session.object_id].sketch.patterns if p.id == pattern_id), None)
            if pattern is not None:
                from .equation_parameters import pattern_values
                for key, value in pattern_values(pattern).items():
                    field = self.equation_fields[key]
                    field.set_internal_value(value) if isinstance(field, LengthSpinBox) else field.setValue(value)
            for key, field in self.equation_fields.items():
                unit = "number" if key == "count" else "angle" if key == "angle" else "length"
                field.bind_equations(session.document, self.pattern_id+"."+key, owner=session.object_id, unit=unit,
                                     scale=session.surface.units.to_internal(1.) if unit == "length" else 1.)
            self.count.lineEdit().textEdited.connect(self.changed)
        for field in self.fields:
            field.valueChanged.connect(self.changed)
            field.lineEdit().textEdited.connect(self.changed)
        if self.text is not None:
            self.text.textChanged.connect(self.changed)
            self.font.currentFontChanged.connect(self.changed)

    def length(self, form, label, value, positive=False):
        field = LengthSpinBox(self.session.surface.units, 1e-6 if positive else -1e6, 1e6, value)
        form.addRow(label, field)
        self.fields.append(field)
        return field

    def changed(self, *args):
        self._key = self._candidate = None
        self.refresh()

    def reverse_offset(self):
        self.distance.set_internal_value(-self.number(self.distance))
        self.changed()

    def number(self, field):
        if not field.hasAcceptableInput():
            raise ValueError("Paramètre invalide.")
        displayed = field.valueFromText(field.text())
        if isinstance(field, LengthSpinBox):
            return field.internal_value if displayed == field.value() else field.units.to_internal(displayed)
        return displayed

    def set_pick_mode(self, mode):
        self._pick_mode = mode
        set_feedback(self.error, "Cliquer un segment pour définir l’axe." if mode == "axis" else "Cliquer un point dans la vue.", "info")
        self.session.surface.focus()

    def collect_entities(self):
        self.set_pick_mode("entities")
        set_feedback(self.error, "Cliquer les entités sources ; un second clic les retire.", "info")

    def candidate(self):
        from .commands import _current
        for field in self.fields:
            if not field.hasAcceptableInput():
                raise ValueError("Paramètre invalide.")
        obj = _current(self.session.document, self.session.object_id)
        sketch, mode = obj.sketch, self.mode
        selected = self.session.interaction.selected
        if mode == "text":
            from .text_contours import text_entities
            curves = text_entities(self.text.text(), self.font.currentFont().family(), self.number(self.height),
                                   Vec2(self.number(self.u), self.number(self.v)), radians(self.number(self.angle)))
            if len(sketch.entities)+len(curves) > 512:
                raise ValueError("L’opération dépasserait 512 entités.")
            candidate = replace(sketch, entities=(*sketch.entities, *curves))
        elif mode == "trim":
            if self.trim_target is None:
                raise ValueError("Cliquez le tronçon à retirer dans la vue.")
            candidate = reshape.trimmed(sketch, self.trim_target, self.trim_position, remove_relations=self.remove_relations.isChecked())
        elif mode == "offset":
            candidate = reshape.offset(sketch, selected, self.number(self.distance))
        elif mode == "mirror":
            origin = Vec2(self.number(self.u), self.number(self.v))
            index = self.axis.currentIndex()
            du, dv = (0., 1.) if index == 0 else (1., 0.) if index == 1 else (self.number(self.du), self.number(self.dv))
            candidate = reshape.copies(sketch, selected, mode, axis_start=origin, axis_end=Vec2(origin.u+du, origin.v+dv))
        elif mode == "linear_pattern":
            from .patterns import SketchPattern
            pattern = SketchPattern(selected, mode, count=int(self.number(self.count)),
                                    du=self.number(self.du), dv=self.number(self.dv), id=self.pattern_id)
            candidate = self.pattern_candidate(sketch, pattern)
        else:
            from .patterns import SketchPattern
            pattern = SketchPattern(selected, mode, count=int(self.number(self.count)),
                center=Vec2(self.number(self.u), self.number(self.v)), angle=radians(self.number(self.angle)), id=self.pattern_id)
            candidate = self.pattern_candidate(sketch, pattern)
        self._expressions = {self.pattern_id+"."+key: field.equation_expression() for key, field in self.equation_fields.items()}
        if self.equation_fields:
            from cadastrophe.core.parameters import prepare, set_expression
            updated = replace(obj, sketch=candidate)
            for key, exp in self._expressions.items():
                updated = set_expression(updated, key, exp)
            objects, _, _ = prepare({**self.session.document.objects, obj.id: updated}, self.session.document.variables)
            candidate = objects[obj.id].sketch
        self.session.validate_native(candidate.entities)
        self.session.validate_sketch(candidate)
        return obj, candidate

    def pattern_candidate(self, sketch, pattern):
        patterns = tuple(pattern if p.id == self.editing_id else p for p in sketch.patterns)
        if self.editing_id is None:
            patterns += (pattern,)
        elif not any(p.id == self.editing_id for p in sketch.patterns):
            raise ValueError("Le motif a été supprimé pendant son édition.")
        return replace(sketch, patterns=patterns)

    def refresh(self):
        selected = self.session.interaction.selected
        self.collector.setText(f"{len(selected)} entité(s) sélectionnée(s)")
        if self.mode == "mirror":
            self.du.setEnabled(self.axis.currentIndex() == 2)
            self.dv.setEnabled(self.axis.currentIndex() == 2)
        key = (self.session.document.objects[self.session.object_id].revision, selected)
        if key == self._key:
            return
        self._key = key
        self._candidate = None
        self.preview.clear()
        self.session.surface.presentation.set_state(hidden_ids=())
        try:
            original, candidate = self.candidate()
            old = {e.id: e for e in original.sketch.entities}
            preview_ids = set(next(p for p in candidate.patterns if p.id == self.editing_id).generated_ids) if self.editing_id else set()
            shown = tuple(e for e in candidate.entities if e.id in preview_ids or old.get(e.id) != e)
            hidden = (self.trim_target,) if self.mode == "trim" else ()
            self.session.surface.presentation.set_state(hidden_ids=hidden)
            if self.editing_id is not None:
                hidden = next(p for p in original.sketch.patterns if p.id == self.editing_id).generated_ids
                self.session.surface.presentation.set_state(hidden_ids=hidden)
            self.preview.update(replace(original, sketch=candidate.independent(shown)), editing=True)
            self._candidate = (original, candidate)
            set_feedback(self.error, "Aperçu prêt. Entrée : appliquer · Échap : fermer.", "info")
        except Exception as exc:
            self.error.setText(str(exc) if isinstance(exc, ValueError) else "Impossible de construire cet aperçu avec ces paramètres.")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self._candidate is not None)

    def pick(self, x, y):
        session = self.session
        if self._pick_mode == "position":
            point = session._project(x, y)
            if point is not None:
                self.u.set_internal_value(point.u)
                self.v.set_internal_value(point.v)
                self._pick_mode = "position" if self.mode == "text" else "entities"
                self.changed()
            return
        detected = session.surface.detect(x, y)
        key = session.surface.presentation.key(detected) or self.preview.key(detected)
        sketch = session.document.objects[session.object_id].sketch
        if key not in {e.id for e in sketch.entities}:
            self.error.setText("Cliquez une entité de cette esquisse.")
            return
        if self._pick_mode == "axis":
            entity = sketch.entity(key)
            if type(entity) is not Segment:
                self.error.setText("L’axe doit être un segment.")
                return
            self.u.set_internal_value(entity.start.u)
            self.v.set_internal_value(entity.start.v)
            self.du.set_internal_value(entity.end.u-entity.start.u)
            self.dv.set_internal_value(entity.end.v-entity.start.v)
            self.axis.setCurrentIndex(2)
            self._pick_mode = "entities"
            session.interaction.set_selected(tuple(k for k in session.interaction.selected if k != key))
            self.changed()
            return
        if self.mode == "trim":
            self.trim_target, self.trim_position = key, session._project(x, y)
            if self.trim_position is None:
                return
            session.interaction.set_selected((key,))
            self.changed()
            return
        selected = session.interaction.selected
        session.interaction.set_selected(tuple(k for k in selected if k != key) if key in selected else (*selected, key))

    def commit(self):
        try:
            original, candidate = self.candidate()
            from .commands import apply_edit
            self.session.mutate(lambda: apply_edit(self.session.document, original.id, candidate, title=self.title(),
                validate=self.session.validate_native, validate_sketch=self.session.validate_sketch, expressions=self._expressions))
            self.session.close_corner()
            self.session.interaction.set_selected(())
        except Exception as exc:
            self.error.setText(str(exc) if isinstance(exc, ValueError) else "L’opération n’a pas pu être appliquée.")

    def dispose(self):
        self.preview.clear()
        self.session.surface.presentation.set_state(hidden_ids=())
        self.hide()
        self.parentWidget().layout().removeWidget(self)
        self.deleteLater()
