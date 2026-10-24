"""Integration fixture, deliberately outside the production plugin directory."""
from dataclasses import dataclass, field, fields
from PySide6.QtWidgets import QLabel
from cadastrophe.plugin_api import SceneObject, ObjectCodec
from .helper import LABEL


@dataclass(frozen=True)
class TaggedObject(SceneObject):
    tag: str = field(default="test", kw_only=True)


def decode(base, data):
    if set(data) != {"tag"} or type(data["tag"]) is not str:
        raise ValueError("Tag invalide")
    return TaggedObject(**{item.name: getattr(base, item.name) for item in fields(SceneObject)}, tag=data["tag"])


class SamplePlugin:
    def activate(self, context):
        self.context = context
        context.register_type(ObjectCodec("fixture.sample.tagged", TaggedObject, 1,
                                          lambda obj: {"tag": obj.tag}, decode))
        context.register_action("rename", "Renommer depuis l’extension", self.rename,
                                shortcuts=("Ctrl+Alt+P",), mutates=True,
                                enabled_when=lambda services: len(services.selected_ids) == 1)
        context.register_ribbon("test", "Affichage", "Extension de validation", ["rename"])
        context.register_panel("info", "Extension de validation", self.panel)

    def rename(self):
        services = self.context.services
        services.active_document.rename(services.selected_ids[0], LABEL)

    def panel(self, services, parent):
        label = self.label = QLabel(parent)

        def refresh(*_):
            doc = services.active_document
            label.setText(doc.display_name if doc is not None else "Aucun document")

        self.context.connect(services.active_document_changed, refresh)
        self.context.connect(services.state_changed, refresh)
        refresh()
        return label

    def deactivate(self):
        self.context = None


def create_plugin():
    return SamplePlugin()
