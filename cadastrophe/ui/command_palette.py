"""Search and execute the shared registry; no CAD callbacks are duplicated."""
from PySide6.QtCore import QEvent, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox,
    QHeaderView, QLabel, QLineEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from .themes import theme_tokens

PALETTE_COMMAND = "window.command_palette"


class CommandPalette(QDialog):
    def __init__(self, registry, parent):
        super().__init__(parent)
        self.registry = registry
        self.setObjectName("commandPalette")
        self.setWindowTitle("Rechercher une commande")
        # A modal child owns Enter/Escape without committing/cancelling a tool.
        # open() keeps the Qt loop free; no nested exec() for this palette.
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(800, 480)
        self.setMinimumSize(560, 300)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Nom, catégorie ou raccourci…")
        self.search.setAccessibleName("Rechercher une commande")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.results = QTreeWidget()
        self.results.setAccessibleName("Commandes trouvées")
        self.results.setHeaderLabels(["Commande", "Catégorie", "Raccourcis", "Disponibilité"])
        self.results.setRootIsDecorated(False)
        self.results.setUniformRowHeights(True)
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            self.results.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.results, 1)
        self.count = QLabel()
        self.count.setTextFormat(Qt.TextFormat.PlainText)
        self.count.setProperty("role", "secondaryText")
        layout.addWidget(self.count)
        self.description = QLabel()
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        self.description.setWordWrap(True)
        self.description.setMinimumHeight(self.fontMetrics().height() * 2)
        layout.addWidget(self.description)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setText("Fermer")
        self.run_button = self.buttons.addButton("Exécuter", QDialogButtonBox.ButtonRole.AcceptRole)
        for button in self.buttons.buttons():
            button.setAutoDefault(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.search.textChanged.connect(self.refresh)
        self.results.currentItemChanged.connect(self.update_current)
        self.results.itemDoubleClicked.connect(lambda item, column: self.accept())
        self.search.installEventFilter(self)
        self.results.installEventFilter(self)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh_visible)
        registry.changed.connect(self._registry_changed)

    def open_palette(self):
        if self.isVisible():
            self.raise_()
            self.activateWindow()
            self.search.setFocus()
            self.search.selectAll()
            return
        self.search.clear()
        self.refresh()
        parent = self.parentWidget()
        available = parent.screen().availableGeometry()
        self.resize(min(800, available.width()), min(480, available.height()))
        center = parent.frameGeometry().center()
        self.move(max(available.left(), min(center.x() - self.width() // 2, available.right() - self.width())),
                  max(available.top(), min(center.y() - self.height() // 2, available.bottom() - self.height())))
        self.open()
        self.search.setFocus(Qt.FocusReason.OtherFocusReason)

    def _registry_changed(self):
        if self.isVisible():
            self._refresh_timer.start(0)

    def _refresh_visible(self):
        if self.isVisible():
            self.refresh()

    @staticmethod
    def _available(command):
        return command.implemented and command.enabled

    @staticmethod
    def _status(command):
        if not command.implemented:
            return "À venir"
        if not command.enabled:
            return "Indisponible"
        if command.action.isCheckable():
            return "Disponible · coché" if command.action.isChecked() else "Disponible · non coché"
        return "Disponible"

    def selected_id(self):
        item = self.results.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None

    def refresh(self):
        current_id = self.selected_id()
        commands = [command for command in self.registry.search(self.search.text()) if command.id != PALETTE_COMMAND]
        with QSignalBlocker(self.results):
            self.results.clear()
            current = None
            for command in commands:
                shortcuts = " ; ".join(key.toString(QKeySequence.SequenceFormat.NativeText)
                                        for key in command.action.shortcuts())
                row = QTreeWidgetItem([command.label.replace("&", ""), command.category,
                                       shortcuts or "—", self._status(command)])
                row.setData(0, Qt.ItemDataRole.UserRole, command.id)
                row.setIcon(0, command.action.icon())
                row.setToolTip(0, command.action.toolTip())
                if not self._available(command):
                    for column in range(4):
                        row.setForeground(column, QColor(theme_tokens().disabled))
                self.results.addTopLevelItem(row)
                if command.id == current_id:
                    current = row
            self.results.setCurrentItem(current or self.results.topLevelItem(0))
        self.count.setText(f"{len(commands)} résultat(s) · ↑ ↓ pour choisir · Entrée pour exécuter · Échap pour fermer"
                           if commands else "Aucune commande trouvée. Modifiez la recherche.")
        self.update_current()

    def update_current(self):
        command_id = self.selected_id()
        try:
            command = self.registry.command(command_id)
        except KeyError:
            self.run_button.setEnabled(False)
            self.description.clear()
            return
        available = self._available(command)
        self.run_button.setEnabled(available)
        text = command.action.toolTip() or command.label
        if not available:
            text = ("Commande à venir. " if not command.implemented else "Indisponible dans le contexte actuel. ") + text
        self.description.setText(text)

    def accept(self):
        # Requery by id at activation: neither a stale row nor an old QAction
        # can bypass the registry guard after a context/plugin change.
        command_id = self.selected_id()
        try:
            command = self.registry.command(command_id)
        except KeyError:
            self.refresh()
            return
        if not self._available(command):
            self.update_current()
            return
        self.done(QDialog.DialogCode.Accepted)
        # Modal state/focus have been restored before dialogs or tools launch.
        if any(command.id == command_id for command in self.registry):
            self.registry.execute(command_id)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if not event.isAutoRepeat():
                    self.accept()
                return True
            if key == Qt.Key.Key_Escape:
                self.reject()
                return True
            if watched is self.search and key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                count = self.results.topLevelItemCount()
                if count:
                    row = self.results.indexOfTopLevelItem(self.results.currentItem())
                    step = 8 if key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown) else 1
                    row += -step if key in (Qt.Key.Key_Up, Qt.Key.Key_PageUp) else step
                    item = self.results.topLevelItem(max(0, min(count - 1, row)))
                    self.results.setCurrentItem(item)
                    self.results.scrollToItem(item)
                return True
        return super().eventFilter(watched, event)
