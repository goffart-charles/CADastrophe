"""Immutable substitutions over the document's one construction graph."""
from dataclasses import dataclass, field
from math import isfinite
from uuid import uuid4

from .feature_model import uuid
from .expressions import Expression


@dataclass(frozen=True)
class ParameterSetting:
    target: str
    value: object

    def __post_init__(self):
        if type(self.target) is not str or len(self.target) > 1024:
            raise ValueError("Cible de substitution invalide.")
        owner, separator, key = self.target.partition(":")
        uuid(owner)
        if separator and not key:
            raise ValueError("Cible de substitution invalide.")
        if key == "enabled":
            if type(self.value) is not bool:
                raise ValueError("L’activation doit être booléenne.")
        elif not isinstance(self.value, Expression) and (
                type(self.value) not in (int, float) or not isfinite(self.value)):
            raise ValueError("La substitution exige une valeur finie ou une expression.")


def checked_settings(settings):
    if (type(settings) is not tuple or len(settings) > 100000
            or any(type(s) is not ParameterSetting for s in settings)
            or len({s.target for s in settings}) != len(settings)):
        raise ValueError("Substitutions invalides ou dupliquées.")


@dataclass(frozen=True)
class Configuration:
    name: str
    id: str = field(default_factory=lambda: str(uuid4()))
    overrides: tuple[ParameterSetting, ...] = ()

    def __post_init__(self):
        uuid(self.id)
        if type(self.name) is not str or not self.name.strip() or len(self.name) > 200:
            raise ValueError("Nom de configuration invalide (1 à 200 caractères).")
        checked_settings(self.overrides)


@dataclass(frozen=True)
class ConfigurationState:
    configurations: tuple[Configuration, ...]
    active_id: str
    default_id: str
    # Only values which differ between configurations need a common baseline.
    common_values: tuple[ParameterSetting, ...] = ()

    def __post_init__(self):
        if (type(self.configurations) is not tuple or not 1 <= len(self.configurations) <= 256
                or any(type(c) is not Configuration for c in self.configurations)
                or len({c.id for c in self.configurations}) != len(self.configurations)
                or len({c.name.casefold() for c in self.configurations}) != len(self.configurations)):
            raise ValueError("Liste de configurations invalide ou dupliquée (1 à 256).")
        ids = {c.id for c in self.configurations}
        if self.active_id not in ids or self.default_id not in ids:
            raise ValueError("Configuration active ou par défaut absente.")
        checked_settings(self.common_values)
        common = {s.target for s in self.common_values}
        if any(s.target not in common for c in self.configurations for s in c.overrides):
            raise ValueError("Une substitution exige sa valeur commune.")

    def configuration(self, key=None):
        key = self.active_id if key is None else key
        try:
            return next(c for c in self.configurations if c.id == key)
        except StopIteration:
            raise ValueError("Configuration absente.") from None


def initial_state(document_id):
    from .construction_state import default_configuration
    identity = default_configuration(document_id)
    return ConfigurationState((Configuration(identity.name, identity.id),), identity.id, identity.id)
