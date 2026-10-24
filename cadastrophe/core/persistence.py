"""Versioned native documents: strict JSON metadata + exact OCCT B-Rep payloads.

No AIS, Qt widgets, pickle, extraction of archive paths, or scene regeneration.
"""
from __future__ import annotations

from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path
import tempfile
from uuid import UUID
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED
from zlib import error as ZlibError

from PySide6.QtCore import QLockFile

from ..brep_io import decode_shape, encode_shape
from ..document import Document, ObjectType, SceneObject
from ..geometry import Vec3, WorkPlane
from .object_types import ObjectTypeRegistry
from .materials import material_record, decode_material

FORMAT = "cadastrophe.document"
SCHEMA_VERSION = 9
EXTENSION = ".cada"
FILE_FILTER = "Documents CADastrophe (*.cada)"
SAVE_FILE_FILTER = "Documents CADastrophe (*.cada)"
MAX_MANIFEST = 8 * 1024 * 1024
MAX_SHAPE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_OBJECTS = 10_000


class DocumentFileError(Exception):
    """A native file cannot be read/written without losing information."""


class UnsupportedVersionError(DocumentFileError):
    pass


class DocumentConflictError(DocumentFileError):
    """The current file can no longer be overwritten from this snapshot."""


def _stream_digest(stream):
    position, digest = stream.tell(), sha256()
    try:
        stream.seek(0)
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            digest.update(chunk)
        return digest.digest()
    finally:
        stream.seek(position)


def _path_digest(path):
    try:
        with path.open("rb") as stream:
            return _stream_digest(stream)
    except FileNotFoundError:
        return None


def _fields(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected.split()):
        raise DocumentFileError(f"{label} : champs manquants ou inconnus.")


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise DocumentFileError(f"{label} : texte vide ou invalide.")
    return value


def _uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise DocumentFileError("Identifiant UUID invalide ou non canonique.")
    return value


def _vector(value, label):
    if (not isinstance(value, (tuple, list)) or len(value) != 3 or
            any(type(item) not in (int, float) or not isfinite(item) for item in value)):
        raise DocumentFileError(f"{label} : trois nombres finis sont requis.")
    return tuple(value)


def _plane(value):
    if value is None:
        return None
    _fields(value, "name origin u v", "Plan")
    origin, u, v = (Vec3(*_vector(value[key], f"Plan {key}")) for key in ("origin", "u", "v"))
    if abs(u.norm() - 1) > 1e-10 or abs(v.norm() - 1) > 1e-10:
        raise DocumentFileError("Le repère du plan doit être orthonormal.")
    return WorkPlane(_text(value["name"], "Nom du plan"), origin, u, v)


def _plane_record(plane):
    if plane is None:
        return None
    return dict(name=plane.name, origin=plane.origin.tuple(), u=plane.u.tuple(), v=plane.v.tuple())


def _record(record, *, allow_extensions=False, appearance=False, logical=False, resources=False, equations=False):
    extra = " extension" if allow_extensions and isinstance(record, dict) and "extension" in record else ""
    extra += " resources" if resources and isinstance(record, dict) and "resources" in record else ""
    extra += " expressions" if equations and isinstance(record, dict) and "expressions" in record else ""
    extra += " material" if isinstance(record, dict) and "material" in record else ""
    _fields(record, "id kind name visible color revision plane brep" + extra + (" transparency" if appearance else ""), "Objet")
    _uuid(record["id"])
    _text(record["name"], "Nom d’objet")
    if type(record["visible"]) is not bool:
        raise DocumentFileError("Visibilité d’objet invalide.")
    color = _vector(record["color"], "Couleur")
    if any(not 0 <= component <= 1 for component in color):
        raise DocumentFileError("La couleur doit être comprise entre 0 et 1.")
    if appearance:
        value = record["transparency"]
        if type(value) not in (int, float) or not isfinite(value) or not 0 <= value <= 1:
            raise DocumentFileError("Transparence d’objet invalide (nombre fini entre 0 et 1 attendu).")
    if type(record["revision"]) is not int or record["revision"] < 0:
        raise DocumentFileError("Révision d’objet invalide.")
    try:
        kind = ObjectType(record["kind"])
    except (TypeError, ValueError) as exc:
        raise DocumentFileError(f"Type d’objet non pris en charge : {record['kind']!r}.") from exc
    plane = _plane(record["plane"])
    material = decode_material(record.get('material'))
    if material is not None and kind is not ObjectType.BODY:
        raise DocumentFileError('Seul un corps solide peut porter un matériau.')
    if (kind is ObjectType.REFERENCE_PLANE) != (plane is not None):
        raise DocumentFileError("Le type d’objet et son plan sont incohérents.")
    brep = record["brep"]
    if "resources" in record:
        if type(record["resources"]) is not dict or "extension" not in record or len(record["resources"]) > 16:
            raise DocumentFileError("Ressources natives invalides.")
        for name, resource in record["resources"].items():
            if type(name) is not str or not name.isidentifier() or not name.isascii():
                raise DocumentFileError("Nom de ressource native invalide.")
            _brep_record(resource, f"resources/{record['id']}/{name}.brep")
    if kind is ObjectType.FEATURE:
        if not logical or brep is not None or "extension" not in record:
            raise DocumentFileError("Un objet logique exige un codec et brep: null.")
        return kind, plane, color
    if resources and kind is ObjectType.BODY and brep is None and "extension" in record:
        return kind, plane, color
    _brep_record(brep, f"shapes/{record['id']}.brep")
    return kind, plane, color


def _brep_record(brep, path):
    _fields(brep, "path sha256", "B-Rep")
    if brep["path"] != path:
        raise DocumentFileError("Chemin de B-Rep invalide.")
    digest = brep["sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise DocumentFileError("Empreinte de B-Rep invalide.")


def _manifest(document, records, owners, directory):
    from .expressions import expression_record
    from .variable_files import source_record
    from .configuration_codec import state_record
    metadata = dict(id=document.id, name=document.name, length_unit=document.length_unit,
                    variable_source=source_record(document.variable_source, directory),
                    configuration_state=state_record(document.configuration_state),
                    variables=[dict(id=v.id, name=v.name, unit=v.unit,
                                    expression=expression_record(v.expression)) for v in document.variables])
    return dict(format=FORMAT, schema_version=SCHEMA_VERSION, required_plugins=sorted(owners),
                document=metadata, objects=records)


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DocumentFileError(f"Clé JSON dupliquée : {key}.")
        result[key] = value
    return result


def _invalid_constant(value):
    raise DocumentFileError(f"Constante JSON invalide : {value}.")


def _read_manifest(archive, registry):
    info = archive.getinfo("manifest.json")
    if info.file_size > MAX_MANIFEST:
        raise DocumentFileError("Le manifeste dépasse la taille autorisée.")
    data = json.loads(archive.read(info).decode("utf-8"), object_pairs_hook=_unique_keys,
                      parse_constant=_invalid_constant)
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise DocumentFileError("Ce fichier n’est pas un document CADastrophe.")
    version = data.get("schema_version")
    if type(version) is not int:
        raise DocumentFileError("Version de schéma absente ou invalide.")
    if version not in (6, 7, 8, SCHEMA_VERSION):
        raise UnsupportedVersionError(f"Version de document non prise en charge : {version} (version requise : {SCHEMA_VERSION}).")
    _fields(data, "format schema_version required_plugins document objects", "Manifeste")
    registry.check_requirements(data["required_plugins"])
    metadata = data["document"]
    if version in (6, 7):
        _fields(metadata, 'id name length_unit variables', 'Document')
        metadata['variable_source'] = None
    if version in (6, 7, 8):
        _fields(metadata, "id name length_unit variables variable_source", "Document")
        from .configuration_codec import state_record
        from .configuration_model import initial_state
        metadata['configuration_state'] = state_record(initial_state(metadata['id']))
    _fields(metadata, "id name length_unit variables variable_source configuration_state", "Document")
    _uuid(metadata["id"])
    _text(metadata["name"], "Nom du document")
    if metadata["length_unit"] != "mm":
        raise DocumentFileError("Unité interne non prise en charge (mm attendu).")
    if not isinstance(data["objects"], list) or len(data["objects"]) > MAX_OBJECTS:
        raise DocumentFileError("Liste d’objets invalide ou trop volumineuse.")
    for record in data['objects']:
        if not isinstance(record, dict):
            raise DocumentFileError('Objet de document invalide.')
        if version == 6:
            if 'material' in record:
                raise DocumentFileError('Champ matériau inattendu dans un document de version 6.')
            record['material'] = None
        elif 'material' not in record:
            raise DocumentFileError('Champ matériau absent du document de version 7.')
    data['schema_version'] = SCHEMA_VERSION
    return data


def load_document(path, *, registry=None, reconstruct=True) -> Document:
    """Validate everything before returning an independent, clean document."""
    try:
        registry = registry if registry is not None else ObjectTypeRegistry()
        source = Path(path).resolve()
        with ZipFile(source, "r") as archive:
            entries = archive.infolist()
            if (len(entries) > MAX_OBJECTS * 17 + 1 or len({entry.filename for entry in entries}) != len(entries)
                    or sum(entry.file_size for entry in entries) > MAX_TOTAL):
                raise DocumentFileError("Archive trop volumineuse ou contenant des entrées dupliquées.")
            file_digest = _stream_digest(archive.fp)
            data = _read_manifest(archive, registry)
            objects, ids, expected, owners = [], set(), {"manifest.json"}, set()
            for record in data["objects"]:
                kind, plane, color = _record(record, allow_extensions=True,
                                             appearance=True, logical=True,
                                             resources=True, equations=True)
                if record["id"] in ids:
                    raise DocumentFileError("Identifiant d’objet dupliqué.")
                ids.add(record["id"])
                shape = None
                def read_brep(descriptor, shape_kind):
                    member = descriptor["path"]
                    expected.add(member)
                    info = archive.getinfo(member)
                    if info.file_size > MAX_SHAPE:
                        raise DocumentFileError("Une forme dépasse la taille autorisée.")
                    payload = archive.read(info)
                    if sha256(payload).hexdigest() != descriptor["sha256"]:
                        raise DocumentFileError("B-Rep corrompu : empreinte différente du manifeste.")
                    return decode_shape(payload, shape_kind)
                if record["brep"] is not None:
                    shape = read_brep(record["brep"], kind)
                obj = SceneObject(record["name"], shape, color, plane, record["visible"],
                                  record["id"], record["revision"], kind=kind, transparency=record.get("transparency"),
                                  material=decode_material(record['material']))
                if "expressions" in record:
                    from dataclasses import replace
                    from .expressions import read_expression
                    if type(record["expressions"]) is not list:
                        raise DocumentFileError("Liste d’équations invalide.")
                    obj = replace(obj, expressions=tuple(read_expression(e) for e in record["expressions"]))
                if "extension" in record:
                    obj, owner = registry.decode(obj, record["extension"])
                    owners.add(owner)
                obj = registry.restore_resources(obj, {name: read_brep(descriptor, ObjectType.BODY)
                                                       for name, descriptor in record.get("resources", {}).items()})
                from .feature_objects import BodyObject
                if kind is ObjectType.BODY and record["brep"] is None and not isinstance(obj, BodyObject):
                    raise DocumentFileError("Seul un corps de construction peut omettre son résultat B-Rep.")
                if isinstance(obj, BodyObject) and record["brep"] is not None:
                    raise DocumentFileError("Le résultat dérivé d'un corps ne doit pas remplacer sa recette.")
                objects.append(obj)
            if owners != set(data["required_plugins"]):
                raise DocumentFileError("La liste des plugins requis ne correspond pas aux types du document.")
            if expected != {entry.filename for entry in entries}:
                raise DocumentFileError("L’archive contient des données inconnues.")
            registry.validate_objects(objects)
            if _stream_digest(archive.fp) != file_digest:
                raise DocumentConflictError("Le fichier a changé pendant sa lecture ; ouvrez-le à nouveau.")
        document = Document(data["document"]["name"], document_id=data["document"]["id"])
        from .expressions import Variable, read_expression
        variables = data["document"]["variables"]
        if type(variables) is not list or len(variables) > MAX_OBJECTS:
            raise DocumentFileError("Liste de variables invalide.")
        for v in variables:
            _fields(v, "id name unit expression", "Variable")
        document._variables = tuple(Variable(v["name"], read_expression(v["expression"]), v["id"], v["unit"]) for v in variables)
        from .variable_files import read_source
        document._variable_source = read_source(data['document']['variable_source'], Path(path).resolve().parent)
        from .configuration_codec import read_state, validate_targets
        from .configurations import materialize, bind_state
        document._configuration_state = read_state(data['document']['configuration_state'], document.id)
        raw = {o.id: o for o in objects}
        validate_targets(document.configuration_state, raw, document.variables)
        from .parameters import prepare
        # Validate the stored effective recipes before applying substitutions:
        # a valid configuration must not conceal a corrupt bound reference.
        raw, document._variables, _ = prepare(raw, document.variables)
        document._configuration_state = bind_state(document.configuration_state, raw, document.variables)
        raw, document._variables = materialize(raw, document.variables, document.configuration_state)
        prepared, document._variables, _ = prepare(raw, document.variables)
        objects = tuple(prepared.values())
        for obj in objects:
            document._insert(obj, len(document.objects))
        document.history.clear()
        if reconstruct:
            registry.restore_document(document)
        document.mark_saved(source, file_digest=file_digest)
        return document
    except DocumentFileError:
        raise
    except (OSError, ValueError, TypeError, KeyError, BadZipFile, RuntimeError, OverflowError, RecursionError, EOFError, ZlibError) as exc:
        raise DocumentFileError(f"Lecture impossible : fichier incomplet, corrompu ou inaccessible ({exc}).") from exc


def _protected_write(document, path, *, registry=None, mark_saved=False):
    """Serialize cooperating writers and detect obsolete/external file versions."""
    document.history._check_idle()
    destination = Path(path).resolve()
    try:
        destination.parent.stat()
    except OSError as exc:
        raise DocumentFileError(f"Le dossier d’enregistrement est inaccessible ({exc}).") from exc
    lock = QLockFile(str(destination)+".lock")
    lock.setStaleLockTime(0)  # A live long save never becomes an expired lock.
    if not lock.tryLock(0):
        if lock.error() == QLockFile.LockError.PermissionError:
            raise DocumentFileError("Le dossier d’enregistrement n’est pas accessible en écriture.") from PermissionError(
                13, "Permission denied", str(destination))
        raise DocumentConflictError("Le projet est en cours d’enregistrement par une autre instance, "
                                    "ou son dossier n’est pas accessible. Réessayez ou enregistrez une copie.")
    try:
        expected = _path_digest(destination)
        if document.file_path == destination and document.file_digest is not None and document.file_digest != expected:
            raise DocumentConflictError("Le fichier a été modifié ou supprimé depuis son ouverture. "
                                        "Enregistrez une copie pour conserver vos modifications, puis rechargez le projet.")
        return _write_archive(document, destination, registry=registry, expected_digest=expected, mark_saved=mark_saved)
    except OSError as exc:
        raise DocumentFileError(f"Enregistrement impossible ({exc}).") from exc
    finally:
        lock.unlock()


def _write_archive(document, destination, *, registry, expected_digest, mark_saved):
    temporary = None
    try:
        registry = registry if registry is not None else ObjectTypeRegistry()
        if len(document.objects) > MAX_OBJECTS:
            raise DocumentFileError("Le document contient trop d’objets.")
        registry.validate_objects(document.objects.values())
        with tempfile.NamedTemporaryFile(mode="w+b", prefix=f".{destination.name}.", suffix=".tmp",
                                         dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            records, total, owners = [], 0, set()
            with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
                for obj in document.objects.values():
                    from .feature_objects import BodyObject
                    derived = isinstance(obj, BodyObject)
                    payload = b"" if obj.kind is ObjectType.FEATURE or derived else encode_shape(obj.shape, obj.kind)
                    total += len(payload)
                    if len(payload) > MAX_SHAPE or total > MAX_TOTAL:
                        raise DocumentFileError("La géométrie dépasse la taille autorisée.")
                    member = f"shapes/{obj.id}.brep"
                    record = dict(id=obj.id, kind=obj.kind.value, name=obj.name, visible=obj.visible,
                                  color=obj.color, revision=obj.revision, plane=_plane_record(obj.plane),
                                  brep=None if obj.kind is ObjectType.FEATURE or derived else dict(path=member, sha256=sha256(payload).hexdigest()))
                    if obj.expressions:
                        from .expressions import expression_record
                        record["expressions"] = [expression_record(e) for e in obj.expressions]
                    extension, owner = registry.encode(obj)
                    record["transparency"] = obj.transparency
                    record['material'] = material_record(obj.material)
                    if extension is not None:
                        record["extension"] = extension
                        owners.add(owner)
                    native = registry.native_resources(obj)
                    if native:
                        record["resources"] = {}
                        for name, source in native.items():
                            if source is None:
                                continue
                            resource = encode_shape(source, ObjectType.BODY)
                            total += len(resource)
                            if len(resource) > MAX_SHAPE or total > MAX_TOTAL:
                                raise DocumentFileError("Les sources natives dépassent la taille autorisée.")
                            resource_path = f"resources/{obj.id}/{name}.brep"
                            record["resources"][name] = dict(path=resource_path, sha256=sha256(resource).hexdigest())
                            archive.writestr(resource_path, resource)
                    _record(record, allow_extensions=True, appearance=True, logical=True, resources=True, equations=True)
                    records.append(record)
                    if obj.kind is not ObjectType.FEATURE and not derived:
                        archive.writestr(member, payload)
                payload = json.dumps(_manifest(document, records, owners, destination.parent), ensure_ascii=False, allow_nan=False,
                                     indent=2).encode("utf-8")
                if len(payload) > MAX_MANIFEST or total + len(payload) > MAX_TOTAL:
                    raise DocumentFileError("Le document dépasse la taille autorisée.")
                archive.writestr("manifest.json", payload)
            stream.flush()
            os.fsync(stream.fileno())
        file_digest = _path_digest(temporary)
        if _path_digest(destination) != expected_digest:
            raise DocumentConflictError("Le fichier a changé pendant l’enregistrement ; aucune version n’a été écrasée. "
                                        "Enregistrez vos modifications dans une copie.")
        os.replace(temporary, destination)
        temporary = None
        if mark_saved:
            document.mark_saved(destination, file_digest=file_digest)
    except DocumentFileError:
        raise
    except (OSError, ValueError, TypeError, RuntimeError, OverflowError) as exc:
        raise DocumentFileError(f"Enregistrement impossible ({exc}).") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def write_document(document, path, *, registry=None) -> Path:
    """Write atomically without changing the source's path or clean marker."""
    return _protected_write(document, path, registry=registry)


def save_document(document, path, *, registry=None) -> Path:
    return _protected_write(document, path, registry=registry, mark_saved=True)
