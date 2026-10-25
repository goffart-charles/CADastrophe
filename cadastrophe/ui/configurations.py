"""Configuration controls share the window action registry and document commands."""
from .feedback import set_feedback
from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QComboBox, QListWidget, QListWidgetItem, QToolButton, QGridLayout, QInputDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton, QMenu, QLineEdit)

from ..core import configurations as operations
from ..core.expressions import Expression
from ..core.parameters import dimensions
from .icons import icon


class ParameterScope(QWidget):
    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.name = QLabel()
        self.name.setWordWrap(True)
        self.scope = QComboBox()
        self.scope.setObjectName("parameterScope")
        self.scope.addItem("Configuration active", "active")
        self.scope.addItem("Valeurs communes", "common")
        form.addRow("Configuration", self.name)
        form.addRow("Modifier", self.scope)
        self.scope.currentIndexChanged.connect(self.changed)
        document.metadata_changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        self.name.setText(self.document.active_configuration.name)
        blocker = QSignalBlocker(self.scope)
        self.scope.setCurrentIndex(self.scope.findData(self.document.parameter_scope))

    def changed(self):
        if self.scope.currentData() != self.document.parameter_scope:
            self.document.set_parameter_scope(self.scope.currentData())


class ConfigurationsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("configurationsPanel")
        self.services = self.document = self.registry = None
        self._updating = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.name = QLabel("Aucun document")
        self.name.setWordWrap(True)
        layout.addWidget(self.name)
        self.list = QListWidget()
        self.list.setObjectName("configurationList")
        self.list.setMaximumHeight(125)
        self.list.currentRowChanged.connect(self.refresh_selected)
        layout.addWidget(self.list)
        self.buttons = QGridLayout()
        layout.addLayout(self.buttons)
        self.scope_container = QVBoxLayout()
        layout.addLayout(self.scope_container)
        self.scope = None
        layout.addWidget(QLabel("Substitutions de la configuration sélectionnée"))
        self.overrides = QTableWidget(0, 2)
        self.overrides.setObjectName("configurationOverrides")
        self.overrides.setHorizontalHeaderLabels(("Paramètre", "Valeur ou expression"))
        self.overrides.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.overrides.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.overrides.setMaximumHeight(160)
        self.overrides.itemChanged.connect(self.edit_override)
        layout.addWidget(self.overrides)
        row = QHBoxLayout()
        self.target = QComboBox()
        self.target.setObjectName("configurationParameter")
        self.target.setMinimumContentsLength(8)
        self.target.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.value = QLineEdit()
        self.value.setObjectName("configurationValue")
        self.value.setPlaceholderText("Valeur ou =expression")
        row.addWidget(self.target, 1)
        row.addWidget(self.value, 1)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.apply = QPushButton("Substituer")
        self.restore = QPushButton("Revenir au commun")
        self.apply.clicked.connect(lambda: self.apply_value(self.target.currentData(), self.value.text()))
        self.restore.clicked.connect(self.restore_selected)
        row.addWidget(self.apply)
        row.addWidget(self.restore)
        layout.addLayout(row)
        units = QLabel("Valeurs en mm, angles en degrés. Activation : active ou inactive.")
        units.setWordWrap(True)
        layout.addWidget(units)
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        layout.addStretch()

    def install(self, registry, services):
        self.registry, self.services = registry, services
        definitions = (("create", "Créer…", self.ask_create, "document"),
            ("duplicate", "Dupliquer…", lambda: self.ask_create(duplicate=True), "copy"),
            ("rename", "Renommer…", self.ask_rename, "properties"),
            ("delete", "Supprimer", lambda: self.run(lambda: operations.remove(self.document, self.selected_id(), incremental=True)), "delete"),
            ("activate", "Activer", self.activate_selected, "check"),
            ("validate_all", "Tout vérifier", self.validate, "reset"))
        self.actions = {}
        for i, (key, label, callback, glyph) in enumerate(definitions):
            action = registry.register("configuration."+key, label, "Configurations", callback, enabled=False)
            action.setIcon(icon(glyph))
            self.actions[key] = action
            button = QToolButton()
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.buttons.addWidget(button, i // 2, i % 2)
        self.list.itemDoubleClicked.connect(lambda _: registry.execute("configuration.activate"))
        self.menu = QMenu(self.list)
        self.menu.addActions(list(self.actions.values()))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(lambda p: self.menu.exec(self.list.mapToGlobal(p)))
        services.state_changed.connect(self.refresh)
        self.refresh()

    def selected_id(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        if self.services is None or self._updating:
            return
        self._updating = True
        try:
            doc = self.services.active_document
            selected = self.selected_id() if doc is self.document else None
            if doc is not self.document:
                if self.scope is not None:
                    self.scope_container.removeWidget(self.scope)
                    self.scope.hide()
                    self.scope.deleteLater()
                self.document = doc
                self.scope = ParameterScope(doc) if doc is not None else None
                if self.scope is not None:
                    self.scope_container.addWidget(self.scope)
                self.message.clear()
            blocker = QSignalBlocker(self.list)
            self.list.clear()
            if doc is not None:
                self.name.setText(doc.display_name)
                for config in doc.configurations:
                    label = config.name + (" · active" if config.id == doc.active_configuration.id else "")
                    item = QListWidgetItem(label, self.list)
                    item.setData(Qt.ItemDataRole.UserRole, config.id)
                    item.setToolTip(config.name)
                selected = selected or doc.active_configuration.id
                self.list.setCurrentRow(next((i for i in range(self.list.count()) if
                    self.list.item(i).data(Qt.ItemDataRole.UserRole) == selected), 0))
                self.scope.refresh()
            else:
                self.name.setText("Aucun document")
            del blocker
        finally:
            self._updating = False
        self.refresh_selected()

    def refresh_selected(self, *_):
        if self._updating or self.services is None:
            return
        self._updating = True
        try:
            doc, key = self.document, self.selected_id()
            available = doc is not None and self.services.can_start_tool and not doc.configuration_editing
            for name, action in self.actions.items():
                action.setEnabled(available and (name in {"create", "validate_all"} or key is not None)
                    and (name != "delete" or key != doc.configuration_state.default_id)
                    and (name != "activate" or key != doc.active_configuration.id))
            self.apply.setEnabled(available and key is not None)
            self.restore.setEnabled(available and key is not None)
            self.target.setEnabled(available)
            self.value.setEnabled(available)
            if self.scope is not None:
                self.scope.setEnabled(available)
            self.overrides.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.EditKeyPressed
                                           if available else QTableWidget.EditTrigger.NoEditTriggers)
            self.overrides.setRowCount(0)
            self.target.clear()
            if doc is None or key is None:
                return
            if not self.isVisible():
                return
            captions = {d.id: d.label for d in dimensions(doc.objects)}
            captions.update((v.id, v.name) for v in doc.variables)
            from ..core.feature_objects import ConstructionFeature
            captions.update((o.id+":enabled", o.name+" · activation") for o in doc.objects.values() if isinstance(o, ConstructionFeature))
            for target, label in captions.items():
                self.target.addItem(label, target)
            for setting in doc.configuration_state.configuration(key).overrides:
                i = self.overrides.rowCount()
                self.overrides.insertRow(i)
                value = setting.value.text if isinstance(setting.value, Expression) else (
                    "active" if setting.value is True else "inactive" if setting.value is False else str(setting.value))
                for column, text in enumerate((captions.get(setting.target, "Paramètre inactif"), value)):
                    item = QTableWidgetItem(text)
                    item.setData(Qt.ItemDataRole.UserRole, setting.target)
                    if column == 0:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.overrides.setItem(i, column, item)
        finally:
            self._updating = False

    def run(self, callback, *, completed=None):
        if self.document is None or not self.services.can_start_tool:
            return
        try:
            document = self.document
            value = callback()
            if hasattr(value, "__next__"):
                from ..core.computation import complete, cooperative_required
                if cooperative_required(document.objects):
                    def done(result):
                        if self.document is document:
                            self.message.clear()
                            self.refresh()
                            if completed is not None:
                                completed(result)
                    def failed(error):
                        if self.document is document:
                            set_feedback(self.message, str(error))
                    return self.services.run_computation(value, "Calcul des configurations",
                        completed=done, failed=failed,
                        cancelled=lambda: failed("Calcul annulé ; configuration conservée."))
                value = complete(value)
            self.message.clear()
            self.refresh()
            if completed is not None:
                completed(value)
            return value
        except (ValueError, RuntimeError) as exc:
            set_feedback(self.message, str(exc))
            return None

    def ask_create(self, *, duplicate=False):
        text, accepted = QInputDialog.getText(self, "Dupliquer la configuration" if duplicate else "Créer une configuration", "Nom")
        if accepted:
            key = self.run(lambda: operations.create(self.document, text, source_id=self.selected_id() if duplicate else None))
            if key is not None:
                self.list.setCurrentRow(next(i for i in range(self.list.count()) if self.list.item(i).data(Qt.ItemDataRole.UserRole) == key))

    def ask_rename(self):
        key = self.selected_id()
        if key is None:
            return
        text, accepted = QInputDialog.getText(self, "Renommer la configuration", "Nom", text=self.document.configuration_state.configuration(key).name)
        if accepted:
            self.run(lambda: operations.rename(self.document, key, text))

    def activate_selected(self):
        key = self.selected_id()
        self.run(lambda: operations.activate(self.document, key, incremental=True))

    def apply_value(self, target, text):
        def apply():
            if not target:
                raise ValueError("Choisissez un paramètre à substituer.")
            if target.endswith(":enabled"):
                if text.strip().casefold() not in {"active", "inactive"}:
                    raise ValueError("Saisissez active ou inactive.")
                value = text.strip().casefold() == "active"
            else:
                value = Expression(target.partition(":")[2] or "value", text.strip()) if text.strip().startswith("=") else float(text.replace(',', '.'))
            return operations.set_override(self.document, self.selected_id(), target, value, incremental=True)
        self.run(apply)

    def edit_override(self, item):
        if not self._updating and item.column() == 1:
            self.apply_value(item.data(Qt.ItemDataRole.UserRole), item.text())

    def restore_selected(self):
        item = self.overrides.currentItem()
        if item is not None:
            self.run(lambda: operations.set_override(self.document, self.selected_id(), item.data(Qt.ItemDataRole.UserRole), None, incremental=True))

    def validate(self):
        def completed(reports):
            failures = [self.document.configuration_state.configuration(key).name+" : "+text for key, text in reports.items() if text]
            set_feedback(self.message, "\n".join(failures) if failures else "Toutes les configurations sont valides.",
                         "error" if failures else "info")
        return self.run(lambda: operations.validate_all(self.document, incremental=True), completed=completed)
