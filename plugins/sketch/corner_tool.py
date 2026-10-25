"""Atomic sketch corner trimming, fillets and chamfers with explicit parameters."""
from cadastrophe.plugin_api import ErrorLabel, set_feedback
from cadastrophe.plugin_api import DecimalSpinBox
from dataclasses import replace
from math import acos, atan2, cos, hypot, pi, sin, tan, radians

from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QFormLayout, QLabel, QComboBox, QCheckBox, QDoubleSpinBox, QDialogButtonBox, QPushButton, QLayout, QSizePolicy
from cadastrophe.plugin_api import LengthSpinBox
from .model import Segment, Arc, Vec2
from .constraints import Constraint, Reference


def rounded_corner(sketch, selected, mode, size, size2=None, *, method="symmetric", angle=45., trim=True, keep_relations=True, flip=False):
    if len(selected) != 2:
        raise ValueError("Choisissez les deux côtés du coin dans la vue.")
    sketch.require_editable(selected)
    a, b = (sketch.entity(key) for key in selected)
    if type(a) is not Segment or type(b) is not Segment:
        raise ValueError("Sélectionnez deux côtés droits du coin à traiter.")
    if a.construction or b.construction:
        raise ValueError("Choisissez deux lignes de contour, hors géométrie de construction.")
    if not size > 0 or not (size2 or size) > 0:
        raise ValueError("Les tailles doivent être positives.")
    joints = [(pa, pb, x) for pa in ("start", "end") for pb in ("start", "end")
              if hypot((x := getattr(a, pa)).u-getattr(b, pb).u, x.v-getattr(b, pb).v) <= 1e-7]
    if len(joints) != 1:
        raise ValueError("Les deux segments doivent partager un seul sommet.")
    pa, pb, corner = joints[0]
    far_a, far_b = getattr(a, "end" if pa == "start" else "start"), getattr(b, "end" if pb == "start" else "start")
    la, lb = hypot(far_a.u-corner.u, far_a.v-corner.v), hypot(far_b.u-corner.u, far_b.v-corner.v)
    da, db = ((far_a.u-corner.u)/la, (far_a.v-corner.v)/la), ((far_b.u-corner.u)/lb, (far_b.v-corner.v)/lb)
    theta = acos(max(-1., min(1., da[0]*db[0]+da[1]*db[1])))
    if min(theta, pi-theta) <= 1e-8:
        raise ValueError("Le coin doit être formé par deux directions non alignées.")
    if mode == "fillet":
        distance_a = distance_b = size/tan(theta/2)
    else:
        distance_a = size
        if method == "distance_angle":
            alpha = radians(angle)
            if not 0 < alpha < pi-theta:
                raise ValueError("L’angle du chanfrein est incompatible avec ce coin.")
            distance_b = size*sin(alpha)/sin(theta+alpha)
        else:
            distance_b = size if method == "symmetric" else size2 or size
        if flip:
            distance_a, distance_b = distance_b, distance_a
    if distance_a >= la-1e-7 or distance_b >= lb-1e-7:
        raise ValueError("La taille demandée consommerait entièrement un côté du coin.")
    first = Vec2(corner.u+da[0]*distance_a, corner.v+da[1]*distance_a)
    last = Vec2(corner.u+db[0]*distance_b, corner.v+db[1]*distance_b)
    if mode == "fillet":
        bx, by = da[0]+db[0], da[1]+db[1]
        norm = hypot(bx, by)
        center = Vec2(corner.u+bx/norm*size/sin(theta/2), corner.v+by/norm*size/sin(theta/2))
        start = atan2(first.v-center.v, first.u-center.u)
        end = atan2(last.v-center.v, last.u-center.u)
        curve = Arc(center, size, start, (end-start+pi) % (2*pi)-pi)
    else:
        curve = Segment(first, last)
    replacements = {a.id: replace(a, **{pa: first}), b.id: replace(b, **{pb: last})} if trim else {}
    corner_refs = {Reference(a.id, pa), Reference(b.id, pb)}
    constraints = tuple(c for c in sketch.constraints if not (
        trim and ((c.kind == "coincident" and set(c.refs) <= corner_refs) or
                  (not keep_relations and any(r.entity_id in selected for r in c.refs)))))
    if trim:
        constraints += (Constraint("coincident", (Reference(a.id, pa), Reference(curve.id, "start"))),
                        Constraint("coincident", (Reference(b.id, pb), Reference(curve.id, "end"))))
    if mode == "fillet":
        constraints += (Constraint("radius", (Reference(curve.id),), size),)
    return replace(sketch, entities=(*(replacements.get(e.id, e) for e in sketch.entities), curve), constraints=constraints)


def open_corner(session, mode):
    session.select_tool(None)
    session.view_tools.abort()
    parent = session.entity_panel.parentWidget()
    panel = CornerPanel(session, mode, parent)
    session.corner_panel = panel
    parent.layout().insertWidget(2, panel)
    panel.show()
    session.interaction.refresh()
    session.selection_changed()
    session.surface.focus()


class CornerPanel(QGroupBox):
    def __init__(self, session, mode, parent):
        super().__init__("Congé d’esquisse" if mode == "fillet" else "Chanfrein d’esquisse", parent)
        self.setObjectName("sketchCornerPanel")
        self.session, self.mode, self._key = session, mode, None
        from .presentation import SketchPresentation
        self.preview = SketchPresentation(session.surface, preview=True)
        self.preview.set_state(interactive=True)
        layout, form = QVBoxLayout(self), QFormLayout()
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.collector = QPushButton("Choisir deux côtés dans la vue")
        self.collector.setObjectName("cornerEdges")
        self.collector.setToolTip("Cliquer les deux côtés du coin dans la vue ; un second clic retire un côté.")
        self.collector.clicked.connect(session.surface.focus)
        form.addRow("Côtés", self.collector)
        clear = QPushButton("Effacer la sélection")
        clear.clicked.connect(lambda: session.interaction.set_selected(()))
        form.addRow(clear)
        self.method = QComboBox()
        self.method.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.method.setMinimumContentsLength(8)
        for label, value in (("Distance égale", "symmetric"), ("Deux distances", "two_distances"), ("Distance et angle", "distance_angle")):
            self.method.addItem(label, value)
        if mode == "chamfer":
            form.addRow("Méthode", self.method)
        self.size = LengthSpinBox(session.surface.units, 1e-7, 1e6, 2.)
        self.size2 = LengthSpinBox(session.surface.units, 1e-7, 1e6, 2.)
        self.size.setObjectName("cornerSize")
        self.size2.setObjectName("cornerSize2")
        form.addRow("Rayon" if mode == "fillet" else "Distance 1", self.size)
        if mode == "chamfer":
            form.addRow("Distance 2", self.size2)
        self.angle = DecimalSpinBox()
        self.angle.setRange(.01, 89.99)
        self.angle.setValue(45.)
        self.angle.setSuffix("°")
        if mode == "chamfer":
            form.addRow("Angle", self.angle)
        self.trim = QCheckBox("Ajuster les entités")
        self.keep = QCheckBox("Conserver les cotes et relations")
        self.flip = QCheckBox("Inverser les côtés")
        self.trim.setChecked(True)
        self.keep.setChecked(True)
        form.addRow(self.trim)
        form.addRow(self.keep)
        if mode == "chamfer":
            form.addRow(self.flip)
        layout.addLayout(form)
        self.error = ErrorLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Appliquer")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Fermer")
        self.buttons.accepted.connect(self.commit)
        self.buttons.rejected.connect(session.close_corner)
        layout.addWidget(self.buttons)
        self.method.currentIndexChanged.connect(self.changed)
        for field in (self.size, self.size2, self.angle):
            field.valueChanged.connect(self.changed)
            field.lineEdit().textEdited.connect(self.changed)
        for option in (self.trim, self.keep, self.flip):
            option.toggled.connect(self.changed)

    def changed(self):
        self._key = None
        self.refresh()

    def candidate(self):
        for field in (self.size, self.size2, self.angle):
            if field.isEnabled() and not field.hasAcceptableInput():
                raise ValueError("Paramètre invalide.")
        from .construction_context import resolve_object
        obj = resolve_object(self.session.document.objects[self.session.object_id], self.session.document)
        return obj, rounded_corner(obj.sketch, self.session.interaction.selected, self.mode,
            self.size.internal_value, self.size2.internal_value, method=self.method.currentData(),
            angle=self.angle.value(), trim=self.trim.isChecked(), keep_relations=self.keep.isChecked(), flip=self.flip.isChecked())

    def refresh(self):
        self.size2.setEnabled(self.mode == "chamfer" and self.method.currentData() == "two_distances")
        self.angle.setEnabled(self.mode == "chamfer" and self.method.currentData() == "distance_angle")
        selected = self.session.interaction.selected
        self.collector.setText(f"{len(selected)}/2 côtés sélectionnés")
        key = (self.session.document.objects[self.session.object_id].revision, selected)
        if key == self._key:
            return
        self._key = key
        self.preview.clear()
        self.session.surface.presentation.set_state(hidden_ids=())
        valid = False
        try:
            obj, sketch = self.candidate()
            new = {e.id for e in sketch.entities}-{e.id for e in obj.sketch.entities}
            shown = set(selected) | new if self.trim.isChecked() else new
            preview = sketch.independent(e for e in sketch.entities if e.id in shown)
            self.session.surface.presentation.set_state(hidden_ids=selected if self.trim.isChecked() else ())
            self.preview.update(replace(obj, sketch=preview), editing=True)
            set_feedback(self.error, "Cliquer un côté sélectionné pour le retirer. Entrée : appliquer · Échap : fermer.", "info")
            valid = True
        except (ValueError, RuntimeError) as exc:
            self.error.setText(str(exc))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(valid)

    def pick(self, x, y):
        base = self.session.surface.presentation
        detected = self.session.surface.detect(x, y)
        key = base.key(detected) or self.preview.key(detected)
        sketch = self.session.document.objects[self.session.object_id].sketch
        if key is None or type(sketch.entity(key)) is not Segment:
            self.error.setText("Cliquez au milieu d’un côté droit du coin.")
            return
        selected = self.session.interaction.selected
        if key in selected:
            selected = tuple(value for value in selected if value != key)
        elif len(selected) < 2:
            selected = (*selected, key)
        else:
            self.error.setText("Retirez un côté sélectionné avant d’en choisir un autre.")
            return
        self.session.interaction.set_selected(selected)

    def commit(self):
        try:
            for field in (self.size, self.size2, self.angle):
                if field.isEnabled() and not field.hasAcceptableInput():
                    raise ValueError("Paramètre invalide.")
                field.interpretText()
            original, candidate = self.candidate()
            from .commands import _commit
            self.session.mutate(lambda: _commit(self.session.document, original, candidate, self.title(),
                                                self.session.validate_native, self.session.validate_sketch))
            self.session.close_corner()
            self.session.interaction.set_selected(())
        except (ValueError, RuntimeError) as exc:
            self.error.setText(str(exc))

    def dispose(self):
        self.preview.clear()
        self.session.surface.presentation.set_state(hidden_ids=())
        self.hide()
        self.parentWidget().layout().removeWidget(self)
        self.deleteLater()
