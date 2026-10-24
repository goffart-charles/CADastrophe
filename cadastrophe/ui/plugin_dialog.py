"""Startup activation choices and diagnostics for installed local extensions."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QTableWidget,
                              QTableWidgetItem, QHeaderView, QDialogButtonBox, QMessageBox)

STATES = {"active": "Active", "disabled": "Inactive", "incompatible": "Incompatible",
          "blocked": "Bloquée", "error": "Erreur"}


class PluginDialog(QDialog):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("Extensions")
        self.resize(760, 360)
        row = QVBoxLayout(self)
        note = QLabel("Les choix d’activation s’appliquent au prochain démarrage.")
        note.setWordWrap(True)
        row.addWidget(note)
        if manager.settings_error:
            error = QLabel("Configuration illisible : " + manager.settings_error)
            error.setWordWrap(True)
            row.addWidget(error)
        self.table = QTableWidget(len(manager.records), 4)
        self.table.setHorizontalHeaderLabels(["Activer", "Extension", "Version", "État / diagnostic"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        for index, record in enumerate(manager.records):
            manifest = record.manifest
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            selected = (manager.settings.get(manifest.id, manifest.enabled_by_default or record.state == "active")
                        if manifest is not None else False)
            check.setCheckState(Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked)
            if manifest is None or "dupliqué" in record.error:
                check.setFlags(Qt.ItemFlag.NoItemFlags)
            self.table.setItem(index, 0, check)
            for column, text in enumerate((f"{manifest.name} ({manifest.id})" if manifest else record.path.name,
                                          manifest.version if manifest else "—",
                                          STATES.get(record.state, record.state) + (" · " + record.error if record.error else "")), 1):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setToolTip(text)
                self.table.setItem(index, column, item)
        row.addWidget(self.table)
        if not manager.records:
            row.addWidget(QLabel("Aucune extension locale installée."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Appliquer au prochain démarrage")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)

    def save(self):
        enabled = dict(self.manager.settings)
        for index, record in enumerate(self.manager.records):
            if record.manifest is not None:
                enabled[record.manifest.id] = self.table.item(index, 0).checkState() == Qt.CheckState.Checked
        try:
            self.manager.write_settings(enabled)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Configuration non enregistrée", str(exc))
            return
        self.accept()
