"""Edit global variables and existing recipe dimensions in one document dialog."""
from dataclasses import replace
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
                               QTableWidgetItem, QPushButton, QLabel, QHeaderView)
from ..core.expressions import Expression, Variable, evaluate
from ..core.parameters import graph, set_expression
from ..core.variable_files import bind_library, export_variables, reload_variables
from .file_dialogs import FileDialogs, suggested_filename
from .feedback import set_feedback


class EquationsDialog(QDialog):
    def __init__(self, document, parent=None, *, choose=None):
        super().__init__(parent)
        self.document = document
        self._choose_callback = choose
        self._updating = self._read_only = False
        self.setWindowTitle("Variables et équations")
        self.resize(780, 480)
        layout = QVBoxLayout(self)
        from .configurations import ParameterScope
        self.parameter_scope = ParameterScope(document)
        layout.addWidget(self.parameter_scope)
        help_text = QLabel("Longueurs en mm, angles en degrés. Référence à une cote : "
                           "=[D1@Esquisse1]/2. Les liens restent stables après renommage.")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.file_dialogs = getattr(parent, 'file_dialogs', None) or FileDialogs(parent=self)
        self.source_label = QLabel()
        self.source_label.setTextFormat(Qt.TextFormat.PlainText)
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)
        source_buttons = QHBoxLayout()
        self.link_button = QPushButton('Lier un fichier…')
        self.export_button = QPushButton('Enregistrer dans un fichier…')
        self.reload_button = QPushButton('Recharger')
        self.unlink_button = QPushButton('Délier')
        for button, callback in ((self.link_button, self.link_file), (self.export_button, self.export_file),
                                 (self.reload_button, self.reload_file), (self.unlink_button, self.unlink_file)):
            button.setAutoDefault(False)
            button.clicked.connect(lambda _=False, action=callback: action())
            source_buttons.addWidget(button)
        layout.addLayout(source_buttons)
        self.table = QTableWidget(0, 5 if choose is not None else 4)
        self.table.setObjectName("equationsTable")
        self.table.setHorizontalHeaderLabels(("Nom", "Expression", "Valeur calculée", "Unité") +
                                            (("Choisir",) if choose is not None else ()))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._edit)
        layout.addWidget(self.table)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        row = QHBoxLayout()
        self.add_button = QPushButton("Ajouter une variable")
        self.add_button.clicked.connect(self._add)
        self.remove_button = QPushButton("Supprimer la variable")
        self.remove_button.clicked.connect(self._remove)
        close = QPushButton("Fermer")
        close.clicked.connect(self.accept)
        for button in (self.add_button, self.remove_button, close):
            row.addWidget(button)
        layout.addLayout(row)
        self.document.history.changed.connect(self.refresh)
        self.finished.connect(lambda _: self.document.history.changed.disconnect(self.refresh))
        self.refresh()

    def setReadOnly(self, value):
        self._read_only = value
        self.parameter_scope.setEnabled(not value)
        self.add_button.setEnabled(not value)
        self.remove_button.setEnabled(not value)
        if value:
            self.message.setText("Terminez l’outil actif pour modifier les variables.")
        self.refresh()

    def refresh(self):
        if self._updating:
            return
        self._updating = True
        try:
            source = self.document.variable_source
            self.source_label.setText('Variables locales : aucun fichier lié.' if source is None else
                'Fichier : '+source.path+'\nRechargement explicite ; les valeurs enregistrées restent utilisables sans ce fichier.')
            editable = not self._read_only and not self.document.history.blocked
            self.link_button.setEnabled(editable)
            self.export_button.setEnabled(editable and bool(self.document.variables))
            self.reload_button.setEnabled(editable and source is not None)
            self.unlink_button.setEnabled(editable and source is not None)
            nodes, _, _, dims = graph(self.document.objects, self.document.variables)
            values = evaluate(nodes)
            rows = [("variable", v.id, v.name, v.expression.text, values[v.id], v.unit)
                    for v in self.document.variables]
            formulas = {obj.id+":"+e.target: e for obj in self.document.objects.values() for e in obj.expressions}
            rows += [("dimension", d.id, d.label, formulas[d.id].text if d.id in formulas else str(d.value),
                      values[d.id], d.unit) for d in dims]
            if self._choose_callback is not None:
                # Qt defers deletion of replaced index widgets. In a nested
                # modal loop they can stay visible at the viewport origin.
                # Hide and detach them before rebuilding the row widgets.
                for row in range(self.table.rowCount()):
                    button = self.table.cellWidget(row, 4)
                    if button is not None:
                        button.hide()
                        self.table.removeCellWidget(row, 4)
            self.table.setRowCount(len(rows))
            for row, (kind, key, name, expression, value, unit) in enumerate(rows):
                for col, text in enumerate((name, expression, f"{value:.10g}",
                                           {"length": "mm", "angle": "°", "number": "nombre"}[unit])):
                    item = QTableWidgetItem(text)
                    item.setData(Qt.ItemDataRole.UserRole, (kind, key))
                    if self._read_only or not (col == 1 or kind == "variable" and col in (0, 3)):
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setToolTip(key if col == 0 else "Valeur ou équation. Unités : mm, °, nombre.")
                    self.table.setItem(row, col, item)
                if self._choose_callback is not None:
                    button = QPushButton("Choisir")
                    button.setAutoDefault(False)
                    button.setAccessibleName(f"Choisir {name}")
                    button.clicked.connect(lambda _, node_id=key: self._choose_id(node_id))
                    self.table.setCellWidget(row, 4, button)
        finally:
            self._updating = False

    def _source_action(self, action):
        if self._read_only:
            return
        try:
            action()
            self.message.clear()
        except (ValueError, OSError, RuntimeError) as error:
            set_feedback(self.message, str(error))
        self.refresh()

    def link_file(self):
        if self._read_only:
            return
        path = self.file_dialogs.choose(self, purpose='import', title='Lier un fichier de variables',
            name_filter='Variables CADastrophe (*.txt *.json);;Tous les fichiers (*)')
        if path is not None:
            self._source_action(lambda: bind_library(self.document, path))

    def export_file(self):
        if self._read_only:
            return
        source = self.document.variable_source
        path = self.file_dialogs.choose(self, purpose='export', title='Enregistrer les variables partagées', save=True,
            name_filter='Variables CADastrophe (*.txt);;JSON (*.json)', suffix='txt',
            initial_path=source.path if source else None, suggested_name=suggested_filename(self.document.name+'-variables', '.txt'))
        if path is not None and self.file_dialogs.confirm_overwrite(self, path):
            self._source_action(lambda: export_variables(self.document, path))

    def reload_file(self):
        self._source_action(lambda: reload_variables(self.document))

    def unlink_file(self):
        self._source_action(lambda: self.document.update_construction(variable_source=None, title='Délier le fichier de variables'))

    def _choose_id(self, key):
        if self._choose_callback is None:
            return
        try:
            nodes, _, _, _ = graph(self.document.objects, self.document.variables)
            node = next((node for node in nodes if node.id == key), None)
            if node is None:
                raise ValueError("La variable ou la cote sélectionnée n’existe plus.")
            self._choose_callback(node)
        except (ValueError, RuntimeError) as exc:
            set_feedback(self.message, str(exc))
            return
        self.accept()

    def _edit(self, item):
        if self._updating or self._read_only:
            return
        self._updating = True
        try:
            kind, key = item.data(Qt.ItemDataRole.UserRole)
            row = item.row()
            text = self.table.item(row, 1).text().strip()
            if kind == "variable":
                name = self.table.item(row, 0).text().strip()
                unit_text = self.table.item(row, 3).text().strip()
                if unit_text not in ("mm", "°", "nombre"):
                    raise ValueError("Unité attendue : mm, ° ou nombre.")
                unit = {"mm": "length", "°": "angle", "nombre": "number"}[unit_text]
                variable = next(v for v in self.document.variables if v.id == key)
                expression = text if text.startswith("=") else "="+text
                exp = variable.expression if variable.expression.text == expression else Expression(key, expression, variable.expression.bindings)
                variable = replace(variable, name=name, expression=exp, unit=unit)
                self.document.set_variables(tuple(variable if v.id == key else v for v in self.document.variables))
            elif text.startswith("="):
                owner, target = key.split(":", 1)
                self.document.set_equation(owner, target, text)
            else:
                from ..core.parameters import dimensions
                owner, target = key.split(":", 1)
                value = float(text.replace(',', '.'))
                dim = next(d for d in dimensions(self.document.objects) if d.id == key)
                if dim.integer and not value.is_integer():
                    raise ValueError("Un entier est requis.")
                value = int(value) if dim.integer else value
                obj = set_expression(self.document.objects[owner], target, None)
                obj = (obj.with_equation_values({target: value}) if hasattr(obj, "with_equation_values")
                       else replace(obj, parameters=replace(obj.parameters, **{target: value})))
                self.document.update_construction((obj,), title="Modifier une dimension")
            self.message.clear()
        except (ValueError, RuntimeError) as exc:
            set_feedback(self.message, str(exc))
            item.setToolTip(str(exc))
            return
        finally:
            self._updating = False
        self.refresh()

    def _add(self):
        names = {v.name for v in self.document.variables}
        index = 1
        while f"variable{index}" in names:
            index += 1
        self.document.set_variables((*self.document.variables, Variable(f"variable{index}", Expression("value", "=10"))))
        self.message.clear()

    def _remove(self):
        row = self.table.currentRow()
        if row < 0:
            return
        kind, key = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if kind != "variable":
            self.message.setText("Pour libérer une cote, remplacez son équation par une valeur numérique.")
            return
        try:
            self.document.set_variables(tuple(v for v in self.document.variables if v.id != key))
            self.message.clear()
        except (ValueError, RuntimeError) as exc:
            set_feedback(self.message, str(exc))
