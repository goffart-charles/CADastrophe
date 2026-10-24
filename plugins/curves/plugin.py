"""Construction curves exposed through the host's owned plugin API."""
from functools import partial
from pathlib import Path
from PySide6.QtGui import QIcon

from cadastrophe.plugin_api import CurveObject
from .codec import CODEC
from .presentation import CurvePresentation
from .tool import CurveSession, TITLES


class CurvesPlugin:
    def activate(self, context):
        self.context = context
        context.register_type(CODEC)
        for kind, title in TITLES.items():
            action = context.register_action(kind, title, partial(self.create, kind),
                enabled_when=lambda services: services.can_start_tool)
            action.setIcon(QIcon(str(Path(__file__).with_name('icons')/(kind+'.svg'))))
        context.register_action('edit', 'Modifier la courbe', self.edit, icon='sketch',
            enabled_when=lambda services: services.can_start_tool and self.selected() is not None)
        context.register_presentation(CurveObject, CurvePresentation, label='Courbe de construction', icon='sketch', edit_action='edit')
        context.register_ribbon('curves', 'Fonctions', 'Courbes', tuple(TITLES))

    def selected(self):
        services = self.context.services
        document = services.active_document
        return next((document.objects[key] for key in services.selected_ids if isinstance(document.objects[key], CurveObject)), None) if document is not None else None

    def create(self, kind):
        self.context.start_tool(CurveSession(self.context.services, kind))

    def edit(self):
        feature = self.selected()
        if feature is not None:
            self.context.start_tool(CurveSession(self.context.services, feature=feature))

    def deactivate(self):
        self.context = None


def create_plugin():
    return CurvesPlugin()
