"""Review recovery copies without writing to their original project paths."""
from .feedback import ErrorLabel, set_feedback
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QTreeWidget,
    QTreeWidgetItem, QDialogButtonBox, QHeaderView, QAbstractItemView)

from ..core.recovery import RecoveryError


class RecoveryDialog(QDialog):
    def __init__(self, service, parent):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Récupération de documents")
        self.resize(820, 420)
        layout = QVBoxLayout(self)
        note = QLabel("Des copies ont été conservées après un arrêt anormal. Récupérer ouvre un document modifié à enregistrer sous un nom choisi. Les fichiers d’origine restent intacts.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.list = QTreeWidget()
        self.list.setHeaderLabels(["Document", "Date de la copie", "Fichier d’origine"])
        self.list.setAccessibleName("Copies de récupération disponibles")
        self.list.setRootIsDecorated(False)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.list.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.list.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.list, 1)
        self.error = ErrorLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setText("Plus tard")
        self.restore_button = self.buttons.addButton("Récupérer une copie", QDialogButtonBox.ButtonRole.ActionRole)
        self.delete_button = self.buttons.addButton("Supprimer la copie", QDialogButtonBox.ButtonRole.ActionRole)
        self.delete_button.setToolTip("Supprimer définitivement la copie de récupération sélectionnée")
        self.restore_button.clicked.connect(self.restore_selected)
        self.delete_button.clicked.connect(self.discard_selected)
        self.buttons.rejected.connect(self.reject)
        self.list.currentItemChanged.connect(self.update_buttons)
        layout.addWidget(self.buttons)
        self.refresh()

    def refresh(self):
        self.list.clear()
        for entry in self.service.entries():
            when = datetime.fromisoformat(entry.saved_at).astimezone().strftime("%d/%m/%Y %H:%M:%S")
            row = QTreeWidgetItem([entry.name, when, entry.original_path or "Jamais enregistré"])
            row.setData(0, Qt.ItemDataRole.UserRole, entry)
            row.setToolTip(2, entry.original_path or "Jamais enregistré")
            self.list.addTopLevelItem(row)
        self.list.setCurrentItem(self.list.topLevelItem(0))
        self.update_buttons()
        if self.list.topLevelItemCount() == 0:
            set_feedback(self.error, "Aucune copie de récupération disponible. Les sessions encore ouvertes sont exclues.", "info")

    def selected(self):
        row = self.list.currentItem()
        return row.data(0, Qt.ItemDataRole.UserRole) if row is not None else None

    def update_buttons(self):
        available = self.selected() is not None and not self.service.documents.blocked
        self.restore_button.setEnabled(available)
        self.delete_button.setEnabled(available)

    def restore_selected(self):
        entry = self.selected()
        if entry is None:
            return
        try:
            self.service.restore(entry)
            self.accept()
        except RecoveryError as exc:
            self.error.setText(str(exc))

    def discard_selected(self):
        entry = self.selected()
        if entry is None:
            return
        try:
            self.service.discard(entry)
            self.refresh()
        except RecoveryError as exc:
            self.error.setText(str(exc))
