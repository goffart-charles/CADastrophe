"""STEP import and modular exports, owned by a standard API v1 plugin."""
from functools import partial
from pathlib import Path

from OCC.Core.STEPControl import STEPControl_Reader, STEPControl_Writer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from cadastrophe.plugin_api import ObjectType
from .dialog import ExchangeDialog


class ImportExportPlugin:
    def activate(self, context):
        # Fail visibly at plugin activation if installed bindings are insufficient.
        if not hasattr(STEPControl_Reader, "SetSystemLengthUnit") or not hasattr(STEPControl_Writer, "Write"):
            raise RuntimeError("Bindings STEP avec unités indisponibles.")
        self.context, self.dialog = context, None
        for mode, title in (("import", "Importer STEP…"), ("export", "Exporter STEP…")):
            action = context.register_action(mode + "_step", title, partial(self.open, mode),
                                             enabled_when=partial(self.available, mode))
            action.setIcon(QIcon(str(Path(__file__).with_name("icons") / f"{mode}.svg")))
            action.setIconText(title)
            action.setToolTip("Échange STEP avec conversion d’unités, progression et annulation.")
        action = context.register_action("export","Exporter…",partial(self.open,"export",format_key="stl"),
                                         enabled_when=partial(self.available,"export"))
        action.setIcon(QIcon(str(Path(__file__).with_name("icons") / "export.svg")))
        action.setToolTip("Exporter les corps en STL, STEP, OBJ, 3MF, glTF ou GLB.")
        context.bind_action_slot("file.export","export")
        context.register_ribbon("step", "Affichage", "Échanges", ("import_step", "export"))

    def available(self, mode, services):
        return self.dialog is None and (mode == "import" or any(
            obj.kind is ObjectType.BODY for obj in services.active_document.objects.values()))

    def open(self, mode, *, format_key="step"):
        if self.dialog is not None or not self.context.services.editable:
            return
        self.dialog = ExchangeDialog(self.context, mode, QApplication.activeWindow(),format_key=format_key)
        self.dialog.finished.connect(self._closed)
        self.dialog.show()

    def _closed(self, result):
        self.dialog = None
        self.context.refresh()
        self.context.services.state_changed.emit()

    def deactivate(self):
        if self.dialog is not None:
            self.dialog.close()
        self.context = None


def create_plugin():
    return ImportExportPlugin()
