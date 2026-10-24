"""Deterministic local plugin discovery, dependencies and owned lifetimes."""
from dataclasses import dataclass
import importlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
from types import ModuleType
from uuid import uuid4

from ..plugin_api import API_VERSION, PluginContext
from .object_types import identifier

log = logging.getLogger(__name__)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Clé JSON dupliquée : {key}.")
        result[key] = value
    return result


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", value):
        raise ValueError("La version doit contenir trois entiers : majeur.mineur.correctif.")
    return tuple(map(int, value.split(".")))


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    version: str
    api_version: int
    entry_point: str
    dependencies: tuple = ()
    enabled_by_default: bool = False

    @classmethod
    def read(cls, path):
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
        required = {"id", "name", "version", "api_version", "entry_point"}
        if not isinstance(data, dict) or not required <= data.keys() or data.keys() - required - {"dependencies", "enabled_by_default"}:
            raise ValueError("Champs du manifeste manquants ou inconnus.")
        identifier(data["id"])
        version(data["version"])
        if not isinstance(data["name"], str) or not data["name"].strip() or type(data["api_version"]) is not int:
            raise ValueError("Nom ou version API invalide.")
        if not isinstance(data["entry_point"], str) or not re.fullmatch(r"[a-zA-Z_]\w*:[a-zA-Z_]\w*", data["entry_point"]):
            raise ValueError("Le point d’entrée doit être module:fabrique, dans le dossier du plugin.")
        enabled = data.get("enabled_by_default", False)
        dependencies = data.get("dependencies", [])
        if type(enabled) is not bool or type(dependencies) is not list:
            raise ValueError("Activation ou dépendances invalides.")
        parsed = []
        for dep in dependencies:
            if type(dep) is not dict or set(dep) != {"id", "min_version"}:
                raise ValueError("Une dépendance exige id et min_version.")
            identifier(dep["id"])
            version(dep["min_version"])
            if dep["id"] in [item[0] for item in parsed]:
                raise ValueError("Dépendance dupliquée.")
            parsed.append((dep["id"], dep["min_version"]))
        return cls(data["id"], data["name"], data["version"], data["api_version"],
                   data["entry_point"], tuple(sorted(parsed)), enabled)


@dataclass
class PluginRecord:
    path: Path
    manifest: PluginManifest | None = None
    state: str = "disabled"
    error: str = ""
    instance: object = None
    context: object = None
    namespace: str = ""


class PluginManager:
    def __init__(self, directory, services, host, types, documents):
        self.directory = Path(directory)
        self.services, self.host, self.types, self.documents = services, host, types, documents
        self.records = []
        self.settings = {}
        self.settings_error = ""
        self._active = []
        self._token = uuid4().hex
        self._changing = self._refreshing = False
        self._discovered = False

    def _report(self, record, message):
        record.error = str(message)
        log.warning("Plugin %s: %s", record.manifest.id if record.manifest else record.path.name, message)
        self.services.message.emit("Extension : " + str(message))

    def discover(self):
        if self._discovered:
            raise RuntimeError("La découverte est effectuée une seule fois au démarrage.")
        self._discovered = True
        if not self.directory.exists():
            return
        for folder in sorted(self.directory.iterdir(), key=lambda item: (item.name.casefold(), item.name)):
            if not folder.is_dir() or folder.name.startswith(".") or not (folder / "plugin.json").is_file():
                continue
            record = PluginRecord(folder)
            self.records.append(record)
            try:
                record.manifest = PluginManifest.read(folder / "plugin.json")
                if record.manifest.api_version != API_VERSION:
                    record.state = "incompatible"
                    self._report(record, f"API {record.manifest.api_version} incompatible ; API {API_VERSION} requise.")
            except Exception as exc:
                record.state = "error"
                self._report(record, f"Manifeste invalide : {exc}")
        for record in self.records:
            if record.manifest and sum(other.manifest is not None and other.manifest.id == record.manifest.id for other in self.records) > 1:
                record.state = "error"
                self._report(record, "Identifiant de plugin dupliqué ; aucune copie n’est chargée.")

    def _record(self, plugin_id):
        matches = [item for item in self.records if item.manifest and item.manifest.id == plugin_id]
        if len(matches) != 1:
            raise ValueError(f"Plugin absent ou ambigu : {plugin_id}.")
        return matches[0]

    def wanted(self, record):
        return record.manifest is not None and self.settings.get(record.manifest.id, record.manifest.enabled_by_default)

    def startup(self):
        try:
            self.discover()
        except OSError as exc:
            self.settings_error = f"Découverte impossible : {exc}"
            log.warning("Plugins: %s", self.settings_error)
            return
        try:
            path = self.directory / "settings.json"
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
                if (type(data) is not dict or set(data) != {"schema_version", "enabled"} or type(data["schema_version"]) is not int
                        or data["schema_version"] != 1 or type(data["enabled"]) is not dict
                        or any(type(key) is not str or type(value) is not bool for key, value in data["enabled"].items())):
                    raise ValueError("Configuration d’extensions invalide.")
                self.settings = dict(data["enabled"])
                for key in self.settings:
                    identifier(key)
        except Exception as exc:
            self.settings_error = str(exc)
            log.warning("Configuration plugins: %s", exc)
            return  # No imports under an unreadable activation policy.
        for record in self.records:
            if self.wanted(record) and record.state == "disabled":
                self.activate(record.manifest.id)

    def write_settings(self, enabled):
        if type(enabled) is not dict or any(type(key) is not str or type(value) is not bool for key, value in enabled.items()):
            raise ValueError("Configuration d’extensions invalide.")
        for key in enabled:
            identifier(key)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.directory, prefix=".plugins-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(dict(schema_version=1, enabled=enabled), stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / "settings.json")
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.settings = dict(enabled)
        self.settings_error = ""

    def _plan(self, plugin_id, visiting=None, ordered=None):
        visiting = set() if visiting is None else visiting
        ordered = [] if ordered is None else ordered
        record = self._record(plugin_id)
        if plugin_id in visiting:
            raise ValueError(f"Cycle de dépendances : {plugin_id}.")
        if record.state not in ("disabled", "active"):
            raise ValueError(record.error or f"Plugin indisponible : {plugin_id}.")
        if self.settings.get(plugin_id) is False:
            raise ValueError(f"Dépendance désactivée : {plugin_id}.")
        if record in ordered or record.state == "active":
            return ordered
        visiting.add(plugin_id)
        for dep_id, minimum in record.manifest.dependencies:
            dep = self._record(dep_id)
            if version(dep.manifest.version) < version(minimum):
                raise ValueError(f"{dep_id} exige au moins la version {minimum}.")
            self._plan(dep_id, visiting, ordered)
        visiting.remove(plugin_id)
        ordered.append(record)
        return ordered

    def _import(self, record):
        namespace = f"_cadastrophe_ext_{self._token}_{record.manifest.id.encode('utf-8').hex()}"
        record.namespace = namespace
        package = ModuleType(namespace)
        package.__path__ = [str(record.path)]
        package.__package__ = namespace
        sys.modules[namespace] = package
        module, factory = record.manifest.entry_point.split(":")
        instance = getattr(importlib.import_module(f"{namespace}.{module}"), factory)()
        if not callable(getattr(instance, "activate", None)) or not callable(getattr(instance, "deactivate", None)):
            raise ValueError("Le plugin doit définir activate(context) et deactivate().")
        return instance

    def _forget_modules(self, record):
        if record.namespace:
            for name in tuple(sys.modules):
                if name == record.namespace or name.startswith(record.namespace + "."):
                    del sys.modules[name]
        record.namespace = ""

    def activate(self, plugin_id):
        if self._changing or self.documents.blocked:
            return False
        target = self._record(plugin_id)
        if target.state == "active":
            return True
        loaded = []
        self._changing = True
        try:
            plan = self._plan(plugin_id)
            for record in plan:
                record.error = ""
                context = PluginContext(record.manifest.id, self.services, self.host, self.types,
                                        lambda message, item=record: self._report(item, message))
                record.context = context
                try:
                    record.instance = self._import(record)
                    record.instance.activate(context)
                    record.state = "active"
                    self._active.append(record)
                    loaded.append(record)
                except (Exception, SystemExit) as exc:
                    log.exception("Activation impossible, extension %s", record.manifest.id)
                    self._stop(record)
                    record.state = "error"
                    self._report(record, f"Activation impossible : {exc}")
                    raise
            self.refresh()
            return True
        except (Exception, SystemExit) as exc:
            for record in reversed(loaded):
                self._stop(record)
            if target.state not in ("error", "incompatible"):
                target.state = "blocked"
                self._report(target, f"Dépendances/activation : {exc}")
            return False
        finally:
            self._changing = False

    def _stop(self, record):
        try:
            if record.instance is not None:
                record.instance.deactivate()
        except (Exception, SystemExit) as exc:
            self._report(record, f"Arrêt du plugin : {exc}")
            log.exception("Arrêt impossible, extension %s", record.manifest.id)
        finally:
            if record.context is not None:
                record.context.close()
            record.instance = record.context = None
            self._forget_modules(record)
            if record in self._active:
                self._active.remove(record)
            record.state = "disabled"

    def deactivate(self, plugin_id):
        if self._changing or self.documents.blocked:
            return False
        record = self._record(plugin_id)
        if record.state != "active":
            return True
        if any(plugin_id in dict(item.manifest.dependencies) for item in self._active if item is not record):
            self._report(record, "Un plugin actif dépend de cette extension.")
            return False
        classes = self.types.owned_classes(plugin_id)
        if classes:
            try:
                for document in self.documents.documents:
                    if any(type(obj) in classes for obj in (*document.objects.values(), *document.history.retained_objects())):
                        self._report(record, "Des objets de ce plugin sont encore présents dans un document ou son historique.")
                        return False
            except (RuntimeError, AttributeError) as exc:
                self._report(record, str(exc))
                return False
        self._changing = True
        try:
            self._stop(record)
            return True
        finally:
            self._changing = False

    def refresh(self):
        if self._refreshing:
            return
        self._refreshing = True
        try:
            for record in tuple(self._active):
                record.context.refresh()
        finally:
            self._refreshing = False

    def shutdown(self):
        if self.documents.documents:
            raise RuntimeError("Fermez les documents avant d’arrêter les extensions.")
        for record in reversed(tuple(self._active)):
            self._stop(record)
