"""Read-only result panel following current selection and display units."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QComboBox, QLineEdit, QPushButton, QGroupBox, QFormLayout, QDoubleSpinBox
from cadastrophe.plugin_api import Material, ObjectType

from .geometry import MODES, MeasurementError, measure


class MeasurementsPanel(QWidget):
    def __init__(self, services, parent):
        super().__init__(parent)
        self.services = services
        self.last_result = None
        layout = QVBoxLayout(self)
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
        self.material_name = QLineEdit()
        self.material_name.setAccessibleName('Nom du matériau')
        self.density = QDoubleSpinBox()
        self.density.setRange(.000001, 1e12)
        self.density.setDecimals(6)
        self.density.setValue(1000.)
        self.density.setSuffix(' kg/m³')
        self.density.setAccessibleName('Masse volumique du matériau')
        material_layout.addRow('Nom', self.material_name)
        material_layout.addRow('Masse volumique', self.density)
        self.apply_material = QPushButton('Appliquer aux corps sélectionnés')
        self.clear_material = QPushButton('Retirer le matériau')
        material_layout.addRow(self.apply_material)
        material_layout.addRow(self.clear_material)
        self.apply_material.clicked.connect(self.assign_material)
        self.clear_material.clicked.connect(lambda: self.assign_material(clear=True))
        self.material_name.textChanged.connect(self.update_material_buttons)
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
        self.detail.clear()
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
        return tuple(dict.fromkeys(item.object_id for item in self.services.selection_items
                                   if item.object_id in document.objects and document.objects[item.object_id].kind is ObjectType.BODY))

    def update_material_buttons(self, *_):
        targets = self.material_targets()
        self.apply_material.setEnabled(bool(targets) and bool(self.material_name.text().strip()))
        document = self.services.active_document
        self.clear_material.setEnabled(bool(targets) and any(document.objects[key].material is not None for key in targets))

    def assign_material(self, *, clear=False):
        try:
            targets = self.material_targets()
            if not targets:
                raise MeasurementError('Sélectionnez au moins un corps solide.')
            material = None if clear else Material(self.material_name.text(), self.density.value())
            self.services.active_document.set_materials({key: material for key in targets})
            self.refresh()
        except (MeasurementError, ValueError, RuntimeError) as exc:
            self.detail.setText(str(exc))

    def refresh(self):
        self.timer.stop()
        self.last_result = None
        self.result.clear()
        self.selection.clear()
        self.detail.clear()
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
            self.detail.setText(str(exc))
