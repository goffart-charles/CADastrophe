"""Owned serializers for native B-Reps and logical plugin records."""
from dataclasses import dataclass, fields, replace
from math import isfinite
import re
from types import MappingProxyType
from uuid import UUID

from ..document import ObjectType, SceneObject


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*", value):
        raise ValueError(f"Identifiant invalide : {value!r}.")
    return value


def json_data(value):
    """Copy only JSON data, refusing implicit key coercion and nonfinite values."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and isfinite(value):
        return value
    if type(value) is list:
        return [json_data(item) for item in value]
    if type(value) is dict and all(type(key) is str for key in value):
        return {key: json_data(item) for key, item in value.items()}
    raise ValueError("Les données d’extension doivent être des valeurs JSON finies.")


@dataclass(frozen=True)
class ObjectCodec:
    type_id: str
    object_class: type
    version: int
    encode: object
    decode: object
    # Opt in explicitly: JSON data can contain document UUID references.
    clipboard_references: object = None  # data -> list/tuple of referenced UUIDs
    clipboard_remap: object = None  # (data, read-only old/new UUID map) -> data
    validate_references: object = None  # (object, read-only complete UUID/object map) -> None
    # Exact B-Reps stored separately by schema v5, never encoded into JSON.
    native_resources: tuple[str, ...] = ()
    migrate: object = None  # (older version, JSON data) -> current JSON data


@dataclass(frozen=True)
class TypeBinding:
    owner: str
    codec: ObjectCodec


class ObjectTypeRegistry:
    def __init__(self):
        self._types = {}
        self._document_restorers = {}

    def register_document_restorer(self, name, callback):
        identifier(name)
        if name in self._document_restorers or not callable(callback):
            raise ValueError("Restaurateur de document invalide ou déjà enregistré.")
        self._document_restorers[name] = callback

    def unregister_document_restorer(self, name):
        del self._document_restorers[name]

    def restore_document(self, document):
        from .computation import complete
        return complete(self.restore_document_steps(document))

    def restore_document_steps(self, document):
        from .computation import ReservedComputation
        from collections.abc import Iterator
        def steps():
            for callback in tuple(self._document_restorers.values()):
                result = callback(document)
                if isinstance(result, Iterator):
                    yield from result
        return ReservedComputation(document, steps())

    def register(self, owner, codec):
        identifier(owner)
        identifier(codec.type_id)
        if not codec.type_id.startswith(owner + "."):
            raise ValueError("Le type doit appartenir à l’espace de noms du plugin.")
        if (not isinstance(codec.object_class, type) or not issubclass(codec.object_class, SceneObject)
                or codec.object_class is SceneObject):
            raise ValueError("Un type d’extension doit dériver de SceneObject.")
        if type(codec.version) is not int or codec.version < 1 or not callable(codec.encode) or not callable(codec.decode):
            raise ValueError("Contrat de sérialisation invalide.")
        hooks = (codec.clipboard_references, codec.clipboard_remap)
        if any(hook is not None for hook in hooks) and not all(callable(hook) for hook in hooks):
            raise ValueError("Les deux fonctions de copie des références sont requises.")
        if codec.validate_references is not None and not callable(codec.validate_references):
            raise ValueError("Le validateur de références doit être appelable.")
        if codec.migrate is not None and not callable(codec.migrate):
            raise ValueError("Le migrateur de recette doit être appelable.")
        common = {item.name for item in fields(SceneObject)}
        available = {item.name for item in fields(codec.object_class)}
        if (type(codec.native_resources) is not tuple or len(codec.native_resources) > 16 or len(set(codec.native_resources)) != len(codec.native_resources)
                or any(type(key) is not str or not re.fullmatch(r"[a-z][a-z0-9_]*", key)
                       or key in common or key not in available for key in codec.native_resources)):
            raise ValueError("Champs de ressources B-Rep invalides.")
        if codec.type_id in self._types or any(item.codec.object_class is codec.object_class for item in self._types.values()):
            raise ValueError("Type ou classe déjà enregistré.")
        self._types[codec.type_id] = TypeBinding(owner, codec)

    def unregister(self, type_id, owner):
        if self._types[type_id].owner != owner:
            raise ValueError("Le type appartient à un autre plugin.")
        del self._types[type_id]

    def owned_classes(self, owner):
        return tuple(item.codec.object_class for item in self._types.values() if item.owner == owner)

    def binding(self, type_id):
        try:
            return self._types[type_id]
        except KeyError as exc:
            raise ValueError(f"Type d’extension indisponible : {type_id}.") from exc

    def check_clipboard_object(self, obj):
        if type(obj) is SceneObject:
            return
        binding = next((item for item in self._types.values() if type(obj) is item.codec.object_class), None)
        if binding is None or binding.codec.clipboard_references is None:
            raise ValueError(f"Le type {type(obj).__name__} ne déclare pas de contrat de copie des références.")

    def clipboard_references(self, extension):
        if extension is None:
            return ()
        codec = self.binding(extension["type_id"]).codec
        if codec.clipboard_references is None:
            raise ValueError(f"Le type {codec.type_id} ne prend pas en charge le presse-papiers.")
        try:
            values = codec.clipboard_references(json_data(extension["data"]))
            if not isinstance(values, (tuple, list)) or any(
                    type(value) is not str or str(UUID(value)) != value for value in values):
                raise ValueError("Les références doivent être une liste de UUID canoniques.")
            return tuple(values)
        except (Exception, SystemExit) as exc:
            raise ValueError(f"Références de {codec.type_id} invalides : {exc}") from exc

    def remap_clipboard_extension(self, extension, id_map):
        if extension is None:
            return None
        references = self.clipboard_references(extension)
        codec = self.binding(extension["type_id"]).codec
        try:
            data = codec.clipboard_remap(json_data(extension["data"]), MappingProxyType(id_map))
            result = dict(extension, data=json_data(data))
            if self.clipboard_references(result) != tuple(id_map[key] for key in references):
                raise ValueError("Le remappage des références est incomplet.")
            return result
        except (Exception, SystemExit) as exc:
            raise ValueError(f"Copie de {codec.type_id} impossible : {exc}") from exc

    def encode(self, obj):
        if type(obj) is SceneObject:
            if obj.kind is ObjectType.FEATURE:
                raise ValueError("Un objet logique exige un codec de plugin.")
            return None, None
        binding = next((item for item in self._types.values() if type(obj) is item.codec.object_class), None)
        if binding is None:
            raise ValueError(f"Sérialiseur indisponible pour {type(obj).__name__}.")
        try:
            data = json_data(binding.codec.encode(obj))
            if not isinstance(data, dict):
                raise ValueError("Le sérialiseur doit retourner un dictionnaire JSON.")
            extension = dict(type_id=binding.codec.type_id, version=binding.codec.version, data=data)
            base = SceneObject(**{item.name: getattr(obj, item.name) for item in fields(SceneObject)})
            restored, _ = self.decode(base, extension)
            for item in fields(obj):
                if item.name != "shape" and item.name not in binding.codec.native_resources and getattr(obj, item.name) != getattr(restored, item.name):
                    raise ValueError(f"Le sérialiseur perd le champ {item.name}.")
            return extension, binding.owner
        except (Exception, SystemExit) as exc:
            raise ValueError(f"Sérialisation de {binding.codec.type_id} impossible : {exc}") from exc

    def native_resources(self, obj):
        binding = next((item for item in self._types.values() if type(obj) is item.codec.object_class), None)
        return {} if binding is None else {key: getattr(obj, key) for key in binding.codec.native_resources}

    def restore_resources(self, obj, resources):
        allowed = self.native_resources(obj)
        if set(resources) - set(allowed):
            raise ValueError("Ressource B-Rep non déclarée par le codec.")
        return replace(obj, **resources) if resources else obj

    def decode(self, base, extension):
        if type(extension) is not dict or set(extension) != {"type_id", "version", "data"}:
            raise ValueError("Métadonnées d’extension invalides.")
        binding = self.binding(identifier(extension["type_id"]))
        codec = binding.codec
        version = extension["version"]
        if (type(version) is not int or version < 1 or version > codec.version
                or version != codec.version and codec.migrate is None):
            raise ValueError(f"Version du type non prise en charge : {codec.type_id}.")
        try:
            data = json_data(extension["data"])
            if version != codec.version:
                data = json_data(codec.migrate(version, data))
            if not isinstance(data, dict):
                raise ValueError("Données du type invalides.")
            result = codec.decode(base, data)
            if type(result) is not codec.object_class or result.shape is not base.shape:
                raise ValueError("Le décodeur doit conserver la forme (ou son absence) et retourner la classe enregistrée.")
            for item in fields(SceneObject):
                if item.name != "shape" and getattr(result, item.name) != getattr(base, item.name):
                    raise ValueError(f"Le décodeur a changé le champ commun {item.name}.")
            return result, binding.owner
        except (Exception, SystemExit) as exc:
            raise ValueError(f"Lecture de {codec.type_id} impossible : {exc}") from exc

    def validate_objects(self, objects):
        """Validate cross-object links after decoding the complete, detached batch."""
        objects = tuple(objects)
        by_id = MappingProxyType({obj.id: obj for obj in objects})
        from .body_history import validate_graph
        validate_graph(by_id)
        for obj in objects:
            if type(obj) is SceneObject:
                continue
            binding = next((item for item in self._types.values() if type(obj) is item.codec.object_class), None)
            if binding is None:
                raise ValueError(f"Sérialiseur indisponible pour {type(obj).__name__}.")
            if binding.codec.validate_references is not None:
                try:
                    binding.codec.validate_references(obj, by_id)
                except (Exception, SystemExit) as exc:
                    raise ValueError(f"Références de {binding.codec.type_id} invalides : {exc}") from exc

    def check_requirements(self, requirements):
        available = {item.owner for item in self._types.values()}
        if (type(requirements) is not list or any(type(item) is not str for item in requirements)
                or requirements != sorted(set(requirements))):
            raise ValueError("Liste des plugins requis invalide.")
        missing = set(requirements) - available
        if missing:
            raise ValueError(f"Plugins nécessaires non disponibles : {', '.join(sorted(missing))}.")
