"""Measurements, material selection and personal catalog following selection."""
from cadastrophe.plugin_api import DecimalSpinBox
from PySide6.QtCore import Qt, QTimer, QSignalBlocker
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QLabel, QComboBox, QLineEdit,
                              QPushButton, QGroupBox, QFormLayout, QScrollArea, QFrame)
from cadastrophe.plugin_api import Material, ObjectType

from .geometry import MODES, MeasurementError, measure
from .selection import body_items
from cadastrophe.plugin_api import set_feedback


class MeasurementsPanel(QWidget):
    def __init__(self, services, parent):
        super().__init__(parent)
        self.services = services
        self.last_result = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        self.scroll_area.setWidget(content)
        outer.addWidget(self.scroll_area)
        layout = QVBoxLayout(content)
        title = QLabel("Mesures")
        layout.addWidget(title)
        self.mode = QComboBox()
        for key, (name, _) in MODES.items():
            self.mode.addItem(name, key)
        layout.addWidget(self.mode)
        self.instructions = QLabel()
        self.instructions.setWordWrap(True)
        self.instructions.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.instructions)
        self.selection = QLabel()
        self.selection.setTextFormat(Qt.TextFormat.PlainText)
        self.selection.setWordWrap(True)
        layout.addWidget(self.selection)
        layout.addWidget(QLabel("Résultat"))
        self.result = QLineEdit()
        self.result.setReadOnly(True)
        self.result.setAccessibleName("Résultat de la mesure")
        layout.addWidget(self.result)
        self.detail = QLabel()
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.recalculate = QPushButton("Recalculer")
        self.recalculate.clicked.connect(self.refresh)
        layout.addWidget(self.recalculate)
        self.material_group = QGroupBox('Matériau des corps sélectionnés')
        material_layout = QFormLayout(self.material_group)
        self.material_choice = QComboBox()
        self.material_choice.setAccessibleName('Bibliothèque de matériaux')
        self.material_choice.setToolTip('Choisissez un matériau ou saisissez un nom et une masse volumique personnalisés.')
        material_layout.addRow('Bibliothèque', self.material_choice)
        self.material_name = QLineEdit()
        self.material_name.setMaxLength(200)
        self.material_name.setAccessibleName('Nom du matériau')
        self.density = DecimalSpinBox()
        self.density.setRange(.000001, 1e12)
        self.density.setDecimals(6)
        self.density.setValue(1000.)
        self.density.setSuffix(' kg/m³')
        self.density.setAccessibleName('Masse volumique du matériau')
        material_layout.addRow('Nom', self.material_name)
        material_layout.addRow('Masse volumique', self.density)
        density_hint = QLabel('Les valeurs proposées sont indicatives ; adaptez-les à votre nuance.')
        density_hint.setWordWrap(True)
        material_layout.addRow(density_hint)
        self.save_material = QPushButton('Enregistrer comme matériau personnel')
        material_layout.addRow(self.save_material)
        self.material_feedback = QLabel()
        self.material_feedback.setWordWrap(True)
        self.material_feedback.setTextFormat(Qt.TextFormat.PlainText)
        material_layout.addRow(self.material_feedback)
        self.apply_material = QPushButton('Appliquer aux corps sélectionnés')
        self.clear_material = QPushButton('Retirer le matériau')
        material_layout.addRow(self.apply_material)
        material_layout.addRow(self.clear_material)
        self.apply_material.clicked.connect(self.assign_material)
        self.clear_material.clicked.connect(lambda: self.assign_material(clear=True))
        self.material_name.textChanged.connect(self.update_material_buttons)
        self.material_name.textChanged.connect(self.sync_material_choice)
        self.density.valueChanged.connect(self.sync_material_choice)
        self.material_choice.currentIndexChanged.connect(self.choose_material)
        self.save_material.clicked.connect(self.store_material)
        self.populate_materials()
        if self.services.material_library.error:
            set_feedback(self.material_feedback, 'Bibliothèque personnelle indisponible : ' + self.services.material_library.error)
        self._material_selection = None
        layout.addWidget(self.material_group)
        layout.addStretch()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(60)
        self.timer.timeout.connect(self.refresh)
        self.mode.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def schedule_refresh(self, *_):
        # Clear immediately: never display a value for the previous selection.
        self.last_result = None
        self.result.clear()
        set_feedback(self.detail, '', 'info')
        if self.isVisible():
            self.timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def set_mode(self, mode):
        self.mode.setCurrentIndex(self.mode.findData(mode))
        self.refresh()

    def material_targets(self):
        document = self.services.active_document
        if document is None or not self.services.editable:
            return ()
        try:
            return tuple(item.object_id for item in body_items(document, self.services.selection_items))
        except MeasurementError:
            return ()

    def update_material_buttons(self, *_):
        targets = self.material_targets()
        self.apply_material.setEnabled(bool(targets) and bool(self.material_name.text().strip()))
        document = self.services.active_document
        self.clear_material.setEnabled(bool(targets) and any(document.objects[key].material is not None for key in targets))
        self.save_material.setEnabled(bool(self.material_name.text().strip()) and not self.services.material_library.error)

    def populate_materials(self):
        with QSignalBlocker(self.material_choice):
            self.material_choice.clear()
            self.material_choice.addItem('Matériau personnalisé…', None)
            custom = self.services.material_library.custom_materials
            for material in self.services.material_library.materials:
                suffix = ' — personnel' if material in custom else ''
                self.material_choice.addItem(material.name + suffix, material)
        self.sync_material_choice()

    def sync_material_choice(self, *_):
        match = 0
        for index in range(1, self.material_choice.count()):
            material = self.material_choice.itemData(index)
            if material.name == self.material_name.text().strip() and material.density_kg_m3 == self.density.value():
                match = index
                break
        with QSignalBlocker(self.material_choice):
            self.material_choice.setCurrentIndex(match)

    def choose_material(self, *_):
        material = self.material_choice.currentData()
        with QSignalBlocker(self.material_name), QSignalBlocker(self.density):
            self.material_name.setText(material.name if material is not None else '')
            if material is not None:
                self.density.setValue(material.density_kg_m3)
        error = self.services.material_library.error
        set_feedback(self.material_feedback, 'Bibliothèque personnelle indisponible : ' + error if error else '',
                     'error' if error else 'info')
        self.update_material_buttons()
        if material is None:
            self.material_name.setFocus()

    def store_material(self):
        try:
            material = Material(self.material_name.text(), self.density.value())
            self.services.material_library.add(material)
            self.populate_materials()
            set_feedback(self.material_feedback, 'Matériau enregistré dans votre bibliothèque personnelle.', 'info')
        except (OSError, ValueError, RuntimeError) as exc:
            set_feedback(self.material_feedback, str(exc))

    def assign_material(self, *, clear=False):
        try:
            targets = self.material_targets()
            if not targets:
                raise MeasurementError('Sélectionnez au moins un corps solide.')
            material = None if clear else Material(self.material_name.text(), self.density.value())
            self.services.active_document.set_materials({key: material for key in targets})
            self.refresh()
        except (MeasurementError, ValueError, RuntimeError) as exc:
            set_feedback(self.detail, str(exc))

    def refresh(self):
        self.timer.stop()
        self.last_result = None
        self.result.clear()
        self.selection.clear()
        set_feedback(self.detail, '', 'info')
        key = self.mode.currentData()
        self.material_group.setVisible(key in {'volume', 'mass'})
        targets = self.material_targets()
        document = self.services.active_document
        signature = tuple((key, document.objects[key].material) for key in targets)
        if signature != self._material_selection:
            self._material_selection = signature
            materials = {material for _, material in signature}
            material = next(iter(materials)) if len(materials) == 1 else None
            self.material_name.setText(material.name if material is not None else '')
            if material is not None:
                self.density.setValue(material.density_kg_m3)
        self.update_material_buttons()
        self.instructions.setText(MODES[key][1])
        document = self.services.active_document
        self.recalculate.setEnabled(document is not None and self.services.editable)
        if document is None:
            self.detail.setText("Aucun document ouvert.")
            return
        if not self.services.editable:
            self.detail.setText("Terminez ou annulez l’outil ou l’opération documentaire avant de mesurer.")
            return
        try:
            items = self.services.selection_items
            if key == 'mass':
                bodies = body_items(document, items)
                if ([(i.object_id, i.topology) for i in items]
                        != [(i.object_id, i.topology) for i in bodies]):
                    self.services.select_objects(item.object_id for item in bodies)
                items = bodies
            for item in items:
                obj = document.objects.get(item.object_id)
                if obj is None or obj.revision != item.revision:
                    raise MeasurementError("La sélection n’est plus valide après modification de la forme ; sélectionnez à nouveau les éléments.")
            self.selection.setText("\n".join(f"{document.objects[item.object_id].name} : {item.topology}" for item in items))
            densities = [document.objects[item.object_id].material.density_kg_m3
                         if document.objects[item.object_id].material is not None else None for item in items]
            result = measure(key, [item.shape for item in items], densities=densities)
            self.last_result = result
            self.result.setText(result.formatted(self.services.display_units))
            self.detail.setText(result.explanation)
        except MeasurementError as exc:
            set_feedback(self.detail, str(exc))
