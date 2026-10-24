"""Non-destructive measurements through the public plugin/selection API only."""
from functools import partial
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtCore import QTimer
from OCC.Core.TopAbs import TopAbs_EDGE
from OCC.Core.TopoDS import TopoDS_Shape

from .geometry import MODES, measure, MeasurementError
from .panel import MeasurementsPanel


class MeasurementsPlugin:
    def activate(self, context):
        self.context = context
        self.panel = None
        self.status = context.register_status("edge_length")
        self.status.setAccessibleName("Mesure de l’arête sélectionnée")
        self.status_timer = QTimer(self.status)
        self.status_timer.setSingleShot(True)
        self.status_timer.setInterval(60)
        self.status_timer.timeout.connect(self.refresh_status)
        for mode, (name, instruction) in MODES.items():
            action = context.register_action(mode, f"Mesurer : {name.lower()}", partial(self.show, mode))
            action.setIcon(QIcon(str(Path(__file__).with_name("icons") / f"{mode}.svg")))
            action.setIconText(name)
            action.setToolTip(instruction)
        action = context.register_action('material', 'Affecter un matériau…', partial(self.show, 'mass'))
        action.setIcon(QIcon(str(Path(__file__).with_name('icons')/'mass.svg')))
        context.register_panel("results", "Mesures", self.create_panel)
        context.register_ribbon("measurements", "Évaluation", "Mesures", (*MODES, 'material'))
        context.connect(context.services.selection_changed, self.panel.schedule_refresh)
        context.connect(context.services.state_changed, self.panel.schedule_refresh)
        context.connect(context.services.active_document_changed, self.panel.schedule_refresh)
        for signal in (context.services.selection_changed, context.services.state_changed,
                       context.services.active_document_changed):
            context.connect(signal, self.schedule_status)

    def schedule_status(self, *_):
        self.status.clear()
        self.status_timer.start()

    def refresh_status(self):
        services = self.context.services
        document = services.active_document
        items = services.selection_items
        self.status.clear()
        if not services.editable or document is None or len(items) != 1:
            return
        item = items[0]
        obj = document.objects.get(item.object_id)
        if (obj is None or obj.revision != item.revision or not isinstance(item.shape, TopoDS_Shape)
                or item.shape.IsNull() or item.shape.ShapeType() != TopAbs_EDGE):
            return
        try:
            try:
                radius = measure("radius", [item.shape])
            except MeasurementError:
                radius = None
            if radius is not None:
                self.status.setText("Diamètre : " + services.display_units.format_length(2*radius.value))
            else:
                result = measure("length", [item.shape])
                self.status.setText("Longueur : " + result.formatted(services.display_units))
        except MeasurementError:
            pass

    def create_panel(self, services, parent):
        self.panel = MeasurementsPanel(services, parent)
        return self.panel

    def show(self, mode):
        self.panel.set_mode(mode)
        self.context.show_panel("results")

    def deactivate(self):
        self.status_timer.stop()
        self.status.clear()
        if self.panel is not None:
            self.panel.timer.stop()
        self.panel = self.context = None


def create_plugin():
    return MeasurementsPlugin()
