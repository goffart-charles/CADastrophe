"""Arithmetic input shared by unit-aware lengths, angles and recipe counts."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QCompleter, QSpinBox, QMenu, QToolButton
from .numeric_input import DecimalSpinBox

from ..core.expressions import Expression, ExpressionError, Node, bind, evaluate
from ..core.parameters import graph, set_expression


class EquationInput:
    def clear_equation(self):
        """A deliberate numeric preset replaces the draft driving expression."""
        self._formula = None
        self._equation_error = ""
        if hasattr(self, "equation_button"):
            self._indicate()

    def bind_equations(self, document, key, *, owner=None, unit="length", scale=1.):
        self.equation_document = document
        self.equation_key = key
        self.equation_owner = owner
        self.equation_unit = unit
        self.equation_scale = scale
        obj = document.objects.get(owner)
        self._formula = next((e for e in obj.expressions if e.target == key), None) if obj else None
        self._equation_error = ""
        if not hasattr(self, "equation_button"):
            self.equation_button = QToolButton(self)
            self.equation_button.setText("ƒx")
            self.equation_button.setToolTip("Insérer une variable · saisir = pour écrire une équation")
            self.equation_button.setAccessibleName("Variables disponibles")
            self.equation_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.equation_button.clicked.connect(self._variables_menu)
            self.lineEdit().textEdited.connect(self._equation_edited)
            self._completer = QCompleter([], self)
            self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            self._completer.setWidget(self.lineEdit())
            self._completer.activated[str].connect(self._insert_variable)
        self._refresh_completions()
        self._place_button()
        self._indicate()

    def _refresh_completions(self):
        document = self.equation_document
        stamp = (document.variables, tuple((o.id, o.revision) for o in document.objects.values()))
        if getattr(self, "_completion_stamp", None) == stamp:
            return
        try:
            nodes, _, _, _ = graph(document.objects, document.variables)
            values = evaluate(nodes)
        except ValueError:
            values = {}
        suggestions = [f"{v.name} — {values.get(v.id, 0):g} " +
                       {"length": "mm", "angle": "°", "number": ""}[v.unit]
                       for v in document.variables]
        self._completer.model().setStringList(suggestions)
        self._completion_stamp = stamp

    def _place_button(self):
        if hasattr(self, "equation_button"):
            width = self.fontMetrics().horizontalAdvance("ƒx")+12
            self.equation_button.setGeometry(2, 2, width, max(10, self.height()-4))
            self.lineEdit().setTextMargins(width, 0, 0, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_button()

    def _indicate(self):
        self.equation_button.setStyleSheet("color: #348dd8; font-weight: bold;" if self._formula else "")
        self.setToolTip(self._formula.text if self._formula else "Valeur numérique ou expression commençant par =")

    def _equation_edited(self, text):
        text = text.removesuffix(self.suffix()).strip() if hasattr(self, "suffix") else text.strip()
        previous = getattr(self, "_formula", None)
        self._formula = Expression(self.equation_key, text, previous.bindings if previous else (), self.equation_scale) if text.startswith("=") else None
        self._indicate()
        if text.startswith("="):
            import re
            self._refresh_completions()
            prefix = re.search(r"([^\W\d]\w*)$", text)
            self._completer.setCompletionPrefix(prefix.group(0) if prefix else "")
            self._completer.complete(self.lineEdit().rect())

    def _variables_menu(self):
        menu = QMenu(self)
        try:
            nodes, _, _, _ = graph(self.equation_document.objects, self.equation_document.variables)
            values = evaluate(nodes)
        except ValueError:
            values = {}
        for variable in self.equation_document.variables:
            unit = {"length": "mm", "angle": "°", "number": ""}[variable.unit]
            action = menu.addAction(f"{variable.name} — {values.get(variable.id, 0):g} {unit}")
            action.triggered.connect(lambda _, name=variable.name: self._insert_variable(name))
        if not self.equation_document.variables:
            menu.addAction("Aucune variable définie").setEnabled(False)
        menu.addSeparator()
        action = menu.addAction("Variables et équations…")
        action.triggered.connect(lambda: self._open_manager())
        menu.exec(self.equation_button.mapToGlobal(self.equation_button.rect().bottomLeft()))

    def _open_manager(self):
        from .equations import EquationsDialog
        dialog = EquationsDialog(self.equation_document, self.window(), choose=self._choose_reference)
        dialog.setReadOnly(self.equation_document.history.blocked)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _choose_reference(self, node):
        # The human label stays readable while the binding carries the exact
        # selected identity, even for duplicate names or renamed dimensions.
        is_variable = any(v.id == node.id for v in self.equation_document.variables)
        text = "=" + (node.label if is_variable else f"[{node.label}]")
        expression = Expression(self.equation_key, text, ((node.label, node.id),), self.equation_scale)
        self._expression_value(expression)  # Reject cycles, units and bounds before editing the field.
        self._formula = expression
        self.lineEdit().setText(text)
        self.lineEdit().textEdited.emit(text)
        self.interpretText()
        self.setFocus()
        self._completer.popup().hide()

    def _insert_variable(self, name):
        import re
        name = name.split(" — ", 1)[0]
        original = self.lineEdit().text().removesuffix(self.suffix()).strip()
        text = original
        if text.startswith("="):
            text = re.sub(r"[^\W\d]\w*$", name, text)
            if text == original and not text.endswith(name):
                text += name
        else:
            text = "="+name
        previous = getattr(self, "_formula", None)
        if previous is not None:
            # Choosing a listed variable explicitly binds its current identity,
            # including when an old name was subsequently reused.
            self._formula = Expression(self.equation_key, text,
                                       tuple(pair for pair in previous.bindings if pair[0] != name),
                                       self.equation_scale)
        self.lineEdit().setText(text)
        self._equation_edited(text)
        self.lineEdit().textEdited.emit(text)
        self.setFocus()
        self._completer.popup().hide()

    def equation_expression(self):
        if not hasattr(self, "equation_document"):
            return None
        text = self.lineEdit().text().strip()
        if text.startswith("="):
            # Qt suffixes are decoration, never part of the stored expression.
            text = text.removesuffix(self.suffix()).strip()
            old = getattr(self, "_formula", None)
            return old if old and old.text == text else Expression(self.equation_key, text, old.bindings if old else (), self.equation_scale)
        return getattr(self, "_formula", None)

    def _expression_value(self, expression):
        nodes, _, _, dims = graph(self.equation_document.objects, self.equation_document.variables)
        aliases = {}
        for node in nodes:
            aliases.setdefault(node.label, []).append(node.id)
        for dim in dims:
            aliases.setdefault(dim.id, []).append(dim.id)
        for obj in self.equation_document.objects.values():
            for i, dim in enumerate((d for d in dims if d.owner == obj.id), 1):
                aliases.setdefault(f"D{i}@{obj.name}", []).append(dim.id)
        expression = bind(expression, aliases)
        key = f"{self.equation_owner}:{self.equation_key}"
        node = Node(key, self.equation_key, 0., self.equation_unit, expression)
        values = evaluate(tuple(n for n in nodes if n.id != key)+(node,))
        value = values[key]
        displayed = value/self.equation_scale
        if isinstance(self, QSpinBox) and not float(displayed).is_integer():
            raise ExpressionError("Un entier est requis.")
        if not self.minimum() <= displayed <= self.maximum():
            raise ExpressionError("Résultat hors des limites du paramètre.")
        self._equation_error = ""
        self._bound_formula = expression
        return int(displayed) if isinstance(self, QSpinBox) else displayed

    def validate(self, text, pos):
        if hasattr(self, "equation_document") and text.strip().startswith("="):
            try:
                previous = getattr(self, "_formula", None)
                self._expression_value(Expression(self.equation_key, text.removesuffix(self.suffix()).strip(),
                                                  previous.bindings if previous else (),
                                                  previous.scale if previous else self.equation_scale))
                return QValidator.State.Acceptable, text, pos
            except ValueError as exc:
                self._equation_error = str(exc)
                self.setToolTip(str(exc))
                return QValidator.State.Intermediate, text, pos
        self._equation_error = ""
        return super().validate(text, pos)

    def valueFromText(self, text):
        if hasattr(self, "equation_document") and text.strip().startswith("="):
            return self._expression_value(self.equation_expression())
        return super().valueFromText(text)

    def textFromValue(self, value):
        formula = getattr(self, "_formula", None)
        if formula is not None and self.hasFocus():
            return formula.text
        return super().textFromValue(value)

    def interpretText(self):
        formula = self.equation_expression()
        if formula is not None:
            value = self._expression_value(formula)
            self._formula = self._bound_formula
            self.setValue(value)
            # Exact internal units must follow even when Qt rounds the display.
            if hasattr(self, "_internal_value"):
                from math import radians
                self._internal_value = (value*self.equation_scale if self.equation_unit == "length"
                                        else radians(value) if getattr(self, "radian_value", False) else value)
            self._indicate()
            return
        super().interpretText()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        formula = getattr(self, "_formula", None)
        if formula is not None:
            self.lineEdit().setText(formula.text)
            self.lineEdit().selectAll()

    def focusOutEvent(self, event):
        formula = self.equation_expression()
        if formula is not None:
            try:
                self.interpretText()
            except ValueError:
                # Keep the invalid draft visible and let the owning tool report it.
                return
        super().focusOutEvent(event)
        if formula is not None:
            self.lineEdit().setText(super().textFromValue(self.value())+self.suffix())

    def stepBy(self, steps):
        if hasattr(self, "equation_document"):
            self._formula = None
            self._indicate()
        super().stepBy(steps)

    @property
    def equation_error(self):
        return getattr(self, "_equation_error", "")


class EquationSpinBox(EquationInput, DecimalSpinBox):
    pass


class EquationIntSpinBox(EquationInput, QSpinBox):
    pass


def bind_fields(session, fields):
    """Explicit plugin recipe/editor mapping, shared by create and edit sessions."""
    session.equation_fields = fields
    feature = getattr(session, "feature", None)
    owner = feature.id if feature is not None else getattr(session, "feature_id", None)
    for key, widget in fields.items():
        scale = widget.units.to_internal(1.) if hasattr(widget, "units") else 1.
        unit = "angle" if key in ("angle", "roll", "phase") else "number" if key in ("count", "starts") else "length"
        widget.bind_equations(session.document, key, owner=owner, unit=unit, scale=scale)


def equation_recipe(session, obj):
    from ..core.parameters import dimensions
    active = {d.key for d in dimensions({obj.id: obj})}
    for key, widget in getattr(session, "equation_fields", {}).items():
        obj = set_expression(obj, key, widget.equation_expression() if widget.isEnabled() and key in active else None)
    return obj
