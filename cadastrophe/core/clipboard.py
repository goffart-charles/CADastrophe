"""Versioned clipboard snapshots: strict JSON and native B-Rep, never pickle/AIS.

All decoding/reference remapping finishes before a document command is pushed.
The clipboard holds bytes, not source objects, plugin instances or documents.
"""
from base64 import b64encode, b64decode
from dataclasses import replace
from hashlib import sha256
import json
from uuid import uuid4

from PySide6.QtCore import QMimeData
from PySide6.QtCore import QThread
from PySide6.QtGui import QClipboard

from ..brep_io import encode_shape, decode_shape
from ..document import ObjectType, SceneObject
from .document_commands import CutObjects
from .object_types import ObjectTypeRegistry
from .materials import material_record, decode_material
from .persistence import (DocumentFileError, _fields, _uuid, _record,
                          _unique_keys, _invalid_constant)

MIME_TYPE = "application/x-cadastrophe-objects+json"
FORMAT = "cadastrophe.clipboard"
SCHEMA_VERSION = 1
MAX_PAYLOAD = 64 * 1024 * 1024
MAX_SHAPE = 32 * 1024 * 1024
MAX_OBJECTS = 1000


class ClipboardError(ValueError):
    pass


def _references_resolved(registry, records, ids):
    missing = {key for record in records for key in registry.clipboard_references(record.get("extension"))
               if key not in ids}
    if missing:
        raise ClipboardError("Références externes non résolues : " + ", ".join(sorted(missing))
                             + ". Copiez aussi les objets référencés.")


def encode_objects(document, object_ids, registry=None):
    """Freeze the selected whole bodies, preserving document order and mm units."""
    registry = registry if registry is not None else ObjectTypeRegistry()
    try:
        document.history._check_idle()
        ids = set(object_ids)
        if not ids or len(ids) > MAX_OBJECTS or not ids <= document.objects.keys():
            raise ClipboardError("Sélection vide, invalide ou trop volumineuse.")
        from .feature_objects import BodyObject
        if any(isinstance(document.objects[key], BodyObject) for key in ids):
            if any(document.objects[key].kind is not ObjectType.BODY for key in ids):
                raise ClipboardError("Sélectionnez uniquement les corps à copier ; leurs recettes seront incluses.")
            from .clipboard_graph import encode_graph
            return encode_graph(document, tuple(k for k in document.objects if k in ids), registry)
        if any(document.objects[key].expressions for key in ids):
            raise ClipboardError("Les équations exigent une construction paramétrique à copier.")
        records, entries, owners, total = [], [], set(), 0
        for obj in document.objects.values():
            if obj.id not in ids:
                continue
            if obj.kind is ObjectType.REFERENCE_PLANE:
                raise ClipboardError("Les plans de référence ne peuvent pas être copiés ou coupés.")
            if obj.kind is not ObjectType.BODY:
                raise ClipboardError("La copie d’objets logiques n’est pas encore disponible.")
            registry.check_clipboard_object(obj)
            payload = encode_shape(obj.shape, obj.kind)
            total += len(payload)
            if len(payload) > MAX_SHAPE or total > MAX_PAYLOAD * 3 // 4:
                raise ClipboardError("La géométrie dépasse la taille autorisée du presse-papiers.")
            record = dict(id=obj.id, kind=obj.kind.value, name=obj.name, visible=obj.visible,
                          color=obj.color, revision=obj.revision, plane=None, transparency=obj.transparency,
                          brep=dict(path=f"shapes/{obj.id}.brep", sha256=sha256(payload).hexdigest()))
            if obj.material is not None:
                record['material'] = material_record(obj.material)
            extension, owner = registry.encode(obj)
            if extension is not None:
                record["extension"] = extension
                owners.add(owner)
            _record(record, allow_extensions=True, appearance=True)
            records.append(record)
            entries.append(dict(object=record, payload=b64encode(payload).decode("ascii")))
        _references_resolved(registry, records, ids)
        data = dict(format=FORMAT, schema_version=SCHEMA_VERSION, source_document=document.id,
                    length_unit="mm", required_plugins=sorted(owners), objects=entries)
        result = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(result) > MAX_PAYLOAD:
            raise ClipboardError("Le contenu dépasse la taille autorisée du presse-papiers.")
        return result
    except ClipboardError:
        raise
    except (DocumentFileError, ValueError, TypeError, KeyError, RuntimeError, OverflowError, RecursionError) as exc:
        raise ClipboardError(f"Copie impossible : {exc}") from exc


def decode_objects(payload, registry=None):
    """Return detached objects with new identities and remapped internal UUIDs."""
    registry = registry if registry is not None else ObjectTypeRegistry()
    try:
        if type(payload) is not bytes or not payload or len(payload) > MAX_PAYLOAD:
            raise ClipboardError("Contenu de presse-papiers absent ou trop volumineux.")
        data = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_keys,
                          parse_constant=_invalid_constant)
        if isinstance(data, dict) and data.get("schema_version") in (2, 3):
            from .clipboard_graph import decode_graph
            return decode_graph(data, registry)
        _fields(data, "format schema_version source_document length_unit required_plugins objects", "Presse-papiers")
        if data["format"] != FORMAT:
            raise ClipboardError("Ce contenu n’est pas un presse-papiers CADastrophe.")
        if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
            raise ClipboardError(f"Version de presse-papiers non prise en charge : {data['schema_version']}.")
        _uuid(data["source_document"])
        if data["length_unit"] != "mm":
            raise ClipboardError("Unité interne du presse-papiers non prise en charge (mm attendu).")
        registry.check_requirements(data["required_plugins"])
        entries = data["objects"]
        if type(entries) is not list or not 0 < len(entries) <= MAX_OBJECTS:
            raise ClipboardError("Liste d’objets vide, invalide ou trop volumineuse.")
        ids, owners, objects, records = set(), set(), [], []
        # Validate ALL records, B-Reps and plugin data before remapping/insertion.
        for entry in entries:
            _fields(entry, "object payload", "Entrée du presse-papiers")
            record = entry["object"]
            kind, plane, color = _record(record, allow_extensions=True, appearance=True)
            if kind is not ObjectType.BODY or record["id"] in ids:
                raise ClipboardError("Plan de référence ou identifiant dupliqué dans le presse-papiers.")
            ids.add(record["id"])
            encoded = entry["payload"]
            if type(encoded) is not str or len(encoded) > 4 * ((MAX_SHAPE + 2) // 3):
                raise ClipboardError("Charge B-Rep invalide ou trop volumineuse.")
            shape_bytes = b64decode(encoded, validate=True)
            if len(shape_bytes) > MAX_SHAPE or sha256(shape_bytes).hexdigest() != record["brep"]["sha256"]:
                raise ClipboardError("B-Rep corrompu : taille ou empreinte invalide.")
            base = SceneObject(record["name"], decode_shape(shape_bytes, kind), color, plane,
                               record["visible"], record["id"], record["revision"], kind=kind,
                               transparency=record["transparency"], material=decode_material(record.get('material')))
            if "extension" in record:
                base, owner = registry.decode(base, record["extension"])
                registry.check_clipboard_object(base)
                owners.add(owner)
            objects.append(base)
            records.append(record)
        if owners != set(data["required_plugins"]):
            raise ClipboardError("Les plugins requis ne correspondent pas aux objets copiés.")
        _references_resolved(registry, records, ids)
        id_map = {key: str(uuid4()) for key in ids}
        result = []
        for obj, record in zip(objects, records):
            base = SceneObject(obj.name, obj.shape, obj.color, visible=obj.visible,
                               id=id_map[obj.id], transparency=obj.transparency, material=obj.material)
            extension = registry.remap_clipboard_extension(record.get("extension"), id_map)
            if extension is not None:
                base, _ = registry.decode(base, extension)
            result.append(base)
        return tuple(result)
    except ClipboardError:
        raise
    except (DocumentFileError, ValueError, TypeError, KeyError, RuntimeError, OverflowError, RecursionError) as exc:
        raise ClipboardError(f"Collage impossible : {exc}") from exc


class MimeClipboard:
    """Shared verified Qt transport; payloads/commands remain owned by their domain."""
    def __init__(self, backend):
        self._backend = None
        self._presence = {}
        self.backend = backend

    @property
    def backend(self):
        return self._backend

    @backend.setter
    def backend(self, value):
        previous = getattr(self._backend, "dataChanged", None)
        if previous is not None:
            previous.disconnect(self._invalidate)
        self._backend = value
        self._presence.clear()
        signal = getattr(value, "dataChanged", None)
        if signal is not None:
            signal.connect(self._invalidate)

    def _invalidate(self):
        self._presence.clear()

    def has(self, mime_type):
        cached = hasattr(self.backend, "dataChanged")
        if cached and mime_type in self._presence:
            return self._presence[mime_type]
        mime = self.backend.mimeData()
        result = mime is not None and mime.hasFormat(mime_type)
        if cached:
            self._presence[mime_type] = result
        return result

    def read(self, mime_type, *, limit=MAX_PAYLOAD):
        attempts = 3 if isinstance(self.backend, QClipboard) else 1
        for attempt in range(attempts):
            try:
                return self._read_once(mime_type, limit)
            except ClipboardError:
                if attempt+1 == attempts:
                    raise
                self._invalidate()
                QThread.msleep(20)

    def _read_once(self, mime_type, limit):
        mime = self.backend.mimeData()
        if mime is None or not mime.hasFormat(mime_type):
            raise ClipboardError("Le presse-papiers ne contient pas ce type de données CADastrophe.")
        raw = mime.data(mime_type)
        if raw.size() > limit:
            raise ClipboardError("Le contenu dépasse la taille autorisée du presse-papiers.")
        return bytes(raw)

    def write(self, mime_type, payload):
        if type(payload) is not bytes or not payload or len(payload) > MAX_PAYLOAD:
            raise ClipboardError("Charge de presse-papiers vide ou trop volumineuse.")
        # Windows can briefly deny OleSetClipboard while another process reads
        # the shared clipboard. Retry only the real Qt transport, with a fresh
        # owned MIME object each time. Never pump application events here: a
        # reentrant edit could change the source between copy and cut.
        attempts = 3 if isinstance(self.backend, QClipboard) else 1
        for attempt in range(attempts):
            mime = QMimeData()
            mime.setData(mime_type, payload)
            self.backend.setMimeData(mime)
            self._invalidate()
            try:
                if self._read_once(mime_type, MAX_PAYLOAD) == payload:
                    return
            except ClipboardError:
                pass
            if attempt+1 < attempts:
                QThread.msleep(20)
        raise ClipboardError("Le presse-papiers système n’a pas accepté la copie ; aucun objet n’a été coupé.")


class ObjectClipboard:
    def __init__(self, clipboard, registry):
        self.transport, self.registry = MimeClipboard(clipboard), registry
        self.last_message = ""

    @property
    def clipboard(self):
        return self.transport.backend

    @clipboard.setter
    def clipboard(self, backend):
        self.transport.backend = backend

    @property
    def available(self):
        return self.transport.has(MIME_TYPE)

    def copy(self, document, object_ids, *, cut=False):
        object_ids = tuple(object_ids)
        snapshot = dict(document.objects)
        metadata = (document.variables, document.variable_source, document.configuration_state)
        payload = encode_objects(document, object_ids, self.registry)
        # Also exercise decoders/remapping before publishing, especially for cut.
        decoded = decode_objects(payload, self.registry)
        if hasattr(decoded, "roots"):
            from .clipboard_graph import cut_graph_ids
            try:
                remove = cut_graph_ids(document, object_ids, self.registry) if cut else ()
            except ValueError as exc:
                raise ClipboardError(str(exc)) from exc
            finally:
                decoded.document.deleteLater()
            self.transport.write(MIME_TYPE, payload)
            if cut:
                if (set(document.objects) != set(snapshot) or any(document.objects[k] is not v for k, v in snapshot.items())
                        or metadata != (document.variables, document.variable_source, document.configuration_state)):
                    raise ClipboardError("Le document a changé pendant la copie ; aucun objet n’a été coupé.")
                document.remove_graph(remove)
            return len(object_ids)
        if cut:
            # Do not strand declared links from objects left in the source.
            selected = set(object_ids)
            for obj in document.objects.values():
                if obj.id in selected or type(obj) is SceneObject:
                    continue
                try:
                    self.registry.check_clipboard_object(obj)
                except ValueError:
                    continue  # No reference contract: no dependency graph is inferred.
                try:
                    extension, _ = self.registry.encode(obj)
                    if selected.intersection(self.registry.clipboard_references(extension)):
                        raise ClipboardError(f"Coupe refusée : {obj.name} référence un objet sélectionné. "
                                             "Incluez aussi cet objet dans la sélection.")
                except ValueError as exc:
                    raise ClipboardError(str(exc)) from exc
        self.transport.write(MIME_TYPE, payload)
        if cut:
            if (set(document.objects) != set(snapshot) or any(document.objects[k] is not v for k, v in snapshot.items())
                    or metadata != (document.variables, document.variable_source, document.configuration_state)):
                raise ClipboardError("Le document a changé pendant la copie ; aucun objet n’a été coupé.")
            document.history._check_idle()
            document.history.execute(CutObjects(document, set(object_ids)))
        return len(object_ids)

    def paste(self, document):
        document.history._check_idle()
        self.last_message = ""
        objects = decode_objects(self.transport.read(MIME_TYPE, limit=MAX_PAYLOAD), self.registry)
        names = {obj.name for obj in document.objects.values()}
        renamed = []
        for obj in objects:
            name, suffix = obj.name, 1
            while name in names:
                name = f"{obj.name} (copie{f' {suffix}' if suffix > 1 else ''})"
                suffix += 1
            names.add(name)
            renamed.append(replace(obj, name=name))
        if hasattr(objects, "roots"):
            from .persistence import MAX_OBJECTS as MAX_DOCUMENT_OBJECTS
            try:
                if len(document.objects)+len(renamed) > MAX_DOCUMENT_OBJECTS:
                    raise ClipboardError("Le document dépasserait la limite d’objets après collage.")
                if getattr(objects, 'parameters', False):
                    from .clipboard_parameters import paste
                    self.last_message = paste(document, objects, renamed, self.registry)
                else:
                    results, prefixes = objects.calculations()
                    document.insert_graph(renamed, results=results, prefixes=prefixes, selection=objects.roots)
                return objects.roots
            except (ValueError, RuntimeError) as exc:
                raise ClipboardError(str(exc)) from exc
            finally:
                objects.document.deleteLater()
        document.insert_objects(renamed)
        return tuple(obj.id for obj in renamed)
