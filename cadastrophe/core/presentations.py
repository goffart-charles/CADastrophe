"""Owned presentation factories; no OCCT or window imports in the registry."""
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from ..document import SceneObject


@dataclass(frozen=True)
class PresentationBinding:
    owner: str
    factory: object
    label: str
    icon: str
    edit_command: str | None


class PresentationRegistry(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bindings = {}

    def register(self, owner, object_class, factory, *, label, icon, edit_command=None):
        if (not isinstance(object_class, type) or not issubclass(object_class, SceneObject)
                or object_class in self._bindings or not callable(factory) or not label):
            raise ValueError("Contribution de présentation invalide ou dupliquée.")
        self._bindings[object_class] = PresentationBinding(owner, factory, label, icon, edit_command)
        self.changed.emit()

    def unregister(self, owner, object_class):
        if self._bindings[object_class].owner != owner:
            raise ValueError("Cette présentation appartient à une autre extension.")
        del self._bindings[object_class]
        self.changed.emit()

    def binding(self, obj):
        return self._bindings.get(type(obj))
