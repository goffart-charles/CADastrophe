"""First production plugin: owned UI declarations and public document mutations."""
from functools import partial
from pathlib import Path

from PySide6.QtGui import QIcon
from .parameters import PrimitiveSession
from .geometry import PRIMITIVES, next_name


class PrimitivesPlugin:
    def activate(self, context):
        self.context = context
        for kind, (title, _, _) in PRIMITIVES.items():
            action = context.register_action(kind, f"Créer une {title.lower()}…" if kind in ("box", "sphere")
                                             else f"Créer un {title.lower()}…",
                                             partial(self.create, kind),
                                             enabled_when=lambda services: services.can_start_tool)
            action.setIcon(QIcon(str(Path(__file__).with_name("icons") / f"{kind}.svg")))
            action.setIconText(title)
            action.setToolTip(f"{title} — paramètres et aperçu dans le panneau gauche ; création annulable.")
        context.register_ribbon("solids", "Fonctions", "Primitives", tuple(PRIMITIVES))

    def create(self, kind):
        document = self.context.services.active_document
        self.context.start_tool(PrimitiveSession(kind, next_name(kind, document), self.context.services.display_units))

    def deactivate(self):
        self.context = None


def create_plugin():
    return PrimitivesPlugin()
