"""Edit a draft; persist/apply only after full validation, including shortcuts."""
from dataclasses import asdict

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QTabWidget, QWidget, QFormLayout,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QLabel, QLineEdit, QPushButton,
    QHBoxLayout, QDialogButtonBox, QColorDialog, QTableWidget, QTableWidgetItem, QHeaderView)

from ..core.preferences import Preferences, LOCKED_SHORTCUTS


class PreferencesDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Préférences")
        self.resize(680, 520)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        general = QWidget()
        form = QFormLayout(general)
        self.theme = QComboBox()
        self.theme.addItem("Clair", "light")
        self.theme.addItem("Sombre", "dark")
        form.addRow("Thème", self.theme)
        self.unit = QComboBox()
        for label, key in (("Millimètres", "mm"), ("Centimètres", "cm"), ("Mètres", "m"), ("Pouces", "in")):
            self.unit.addItem(label, key)
        form.addRow("Unité d’affichage", self.unit)
        self.decimals = QSpinBox()
        self.decimals.setRange(0, 6)
        form.addRow("Décimales affichées", self.decimals)
        self.rotation_speed, self.zoom_speed = QDoubleSpinBox(), QDoubleSpinBox()
        for field in (self.rotation_speed, self.zoom_speed):
            field.setRange(0.25, 4.)
            field.setSingleStep(0.25)
            field.setDecimals(2)
            field.setSuffix(" ×")
        form.addRow("Sensibilité de rotation", self.rotation_speed)
        form.addRow("Sensibilité du zoom", self.zoom_speed)
        self.invert_zoom = QCheckBox("Inverser le sens de la molette")
        form.addRow(self.invert_zoom)
        note = QLabel("Les documents restent en millimètres. Les unités changent l’affichage et la saisie des longueurs.")
        note.setWordWrap(True)
        form.addRow(note)
        self.tabs.addTab(general, "Général")
        sketch = QWidget()
        sketch_form = QFormLayout(sketch)
        self.sketch_snapping = QCheckBox("Extrémités, milieux, centres, origine et intersections")
        self.sketch_inference = QCheckBox("Inférences horizontales et verticales")
        self.sketch_grid = QCheckBox("Accrochage à la grille")
        self.sketch_grid_step = QDoubleSpinBox()
        self.sketch_grid_step.setRange(.001, 1_000_000)
        self.sketch_grid_step.setDecimals(3)
        self.sketch_grid_step.setSuffix(" mm")
        for field in (self.sketch_snapping, self.sketch_inference, self.sketch_grid):
            sketch_form.addRow(field)
        sketch_form.addRow("Pas de grille", self.sketch_grid_step)
        note = QLabel("Seuil : 8 pixels logiques, maintien jusqu’à 12 pixels. Ces aides ne créent aucune contrainte persistante.")
        note.setWordWrap(True)
        sketch_form.addRow(note)
        self.tabs.addTab(sketch, "Esquisse")
        recovery = QWidget()
        recovery_form = QFormLayout(recovery)
        self.autosave_enabled = QCheckBox("Créer des copies de récupération automatiquement")
        self.autosave_interval_minutes = QSpinBox()
        self.autosave_interval_minutes.setRange(1, 60)
        self.autosave_interval_minutes.setSuffix(" min")
        recovery_form.addRow(self.autosave_enabled)
        recovery_form.addRow("Fréquence", self.autosave_interval_minutes)
        self.autosave_enabled.toggled.connect(self.autosave_interval_minutes.setEnabled)
        note = QLabel("Les copies restent séparées de vos projets. Après un arrêt anormal, vous pourrez les récupérer puis choisir où les enregistrer.")
        note.setWordWrap(True)
        recovery_form.addRow(note)
        self.tabs.addTab(recovery, "Sauvegarde automatique")
        colors = QWidget()
        form = QFormLayout(colors)
        self.colors = {}
        for key, title in (("background_top", "Fond 3D haut"), ("background_bottom", "Fond 3D bas"),
                           ("preselection", "Présélection"), ("selection", "Sélection")):
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            field = QLineEdit()
            field.setPlaceholderText("Automatique selon le thème" if key.startswith("background") else "#RRGGBB")
            button = QPushButton("Choisir…")
            button.clicked.connect(lambda checked=False, editor=field: self.choose_color(editor))
            line.addWidget(field, 1)
            line.addWidget(button)
            self.colors[key] = field
            form.addRow(title, row)
        note = QLabel("Laisser les deux fonds vides utilise les couleurs du thème. Les couleurs des corps appartiennent au document.")
        note.setWordWrap(True)
        form.addRow(note)
        self.tabs.addTab(colors, "Couleurs")
        shortcuts = QWidget()
        column = QVBoxLayout(shortcuts)
        note = QLabel("Raccourcis séparés par ; (exemple : Ctrl+7; 0). Échap et Entrée restent réservés aux sessions. Les conflits sont refusés.")
        note.setWordWrap(True)
        column.addWidget(note)
        self.shortcuts = QTableWidget(0, 2)
        self.shortcuts.setHorizontalHeaderLabels(["Commande", "Raccourcis"])
        self.shortcuts.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.shortcuts.setColumnWidth(1, 230)
        column.addWidget(self.shortcuts)
        self.tabs.addTab(shortcuts, "Raccourcis")
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.RestoreDefaults)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Enregistrer")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        self.buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).setText("Valeurs par défaut")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(lambda: self.fill(Preferences()))
        layout.addWidget(self.buttons)
        self.fill(window.preferences)

    def choose_color(self, field):
        color = QColorDialog.getColor(QColor(field.text() or "#808080"), self, "Couleur",
                                      options=QColorDialog.ColorDialogOption.DontUseNativeDialog)
        if color.isValid():
            field.setText(color.name())

    def fill(self, preferences):
        self.theme.setCurrentIndex(self.theme.findData(preferences.theme))
        self.unit.setCurrentIndex(self.unit.findData(preferences.length_unit))
        for key in ("decimals", "rotation_speed", "zoom_speed"):
            getattr(self, key).setValue(getattr(preferences, key))
        self.invert_zoom.setChecked(preferences.invert_zoom)
        for key in ("sketch_snapping", "sketch_inference", "sketch_grid"):
            getattr(self, key).setChecked(getattr(preferences, key))
        self.sketch_grid_step.setValue(preferences.sketch_grid_step)
        self.autosave_enabled.setChecked(preferences.autosave_enabled)
        self.autosave_interval_minutes.setValue(preferences.autosave_interval_minutes)
        self.autosave_interval_minutes.setEnabled(preferences.autosave_enabled)
        for key, field in self.colors.items():
            field.setText(getattr(preferences, key) or "")
        self._unknown_shortcuts = {key: value for key, value in preferences.shortcuts.items()
                                   if key not in self.window.command_registry.default_shortcuts()}
        defaults = self.window.command_registry.default_shortcuts()
        commands = sorted(self.window.command_registry, key=lambda command: (command.category, command.id))
        self.shortcuts.setRowCount(len(commands))
        for row, command in enumerate(commands):
            title = QTableWidgetItem(command.label)
            title.setData(Qt.ItemDataRole.UserRole, command.id)
            title.setFlags(title.flags() & ~Qt.ItemFlag.ItemIsEditable)
            value = QTableWidgetItem("; ".join(preferences.shortcuts.get(command.id, defaults[command.id])))
            if command.id in LOCKED_SHORTCUTS:
                value.setFlags(value.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.shortcuts.setItem(row, 0, title)
            self.shortcuts.setItem(row, 1, value)

    def draft(self):
        values = asdict(Preferences())
        values.update(theme=self.theme.currentData(), length_unit=self.unit.currentData(), decimals=self.decimals.value(),
                      rotation_speed=self.rotation_speed.value(), zoom_speed=self.zoom_speed.value(), invert_zoom=self.invert_zoom.isChecked(),
                      autosave_enabled=self.autosave_enabled.isChecked(), autosave_interval_minutes=self.autosave_interval_minutes.value())
        values.update({key: getattr(self, key).isChecked() for key in ("sketch_snapping", "sketch_inference", "sketch_grid")})
        values["sketch_grid_step"] = self.sketch_grid_step.value()
        values.update({key: field.text().strip() or None for key, field in self.colors.items()})
        shortcuts = dict(self._unknown_shortcuts)
        defaults = self.window.command_registry.default_shortcuts()
        for row in range(self.shortcuts.rowCount()):
            key = self.shortcuts.item(row, 0).data(Qt.ItemDataRole.UserRole)
            keys = [part.strip() for part in self.shortcuts.item(row, 1).text().split(";") if part.strip()]
            if keys != defaults[key] and key not in LOCKED_SHORTCUTS:
                shortcuts[key] = keys
        values["shortcuts"] = shortcuts
        return Preferences(**values)

    def accept(self):
        try:
            self.window.apply_preferences(self.draft())
        except (ValueError, TypeError, OSError) as exc:
            self.error.setText(str(exc))
            return
        super().accept()
