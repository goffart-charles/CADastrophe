"""Closed recipe graph clipboard; exact native sources, never final shape caches."""
from base64 import b64encode, b64decode
from dataclasses import replace
from hashlib import sha256
import json
from uuid import uuid4

from ..document import Document, SceneObject, ObjectType
from ..brep_io import encode_shape, decode_shape
from .feature_objects import BodyObject, ConstructionFeature, SketchSourceObject
from .reference_objects import DatumPlaneObject
from .body_history import body_result, prefix_results
from .feature_model import ResultStatus, EvaluationResult
from .persistence import _record, _fields, _uuid, _plane_record
from .materials import material_record, decode_material
from .clipboard import ClipboardError, FORMAT, MAX_OBJECTS, MAX_SHAPE, MAX_PAYLOAD


def references(registry, obj):
    extension, _ = registry.encode(obj)
    return registry.clipboard_references(extension)


def encode_graph(document, roots, registry):
    from .clipboard_parameters import transport
    from .expressions import expression_record
    ids, prepared, parameters = transport(document, roots, registry)
    driven = bool(parameters['variables'] or parameters['configuration_state']['common_values']
                  or len(document.configurations) > 1 or any(prepared[k].expressions for k in ids))
    registry.validate_objects(document.objects[k] for k in ids)
    for key in ids:
        obj = document.objects[key]
        if isinstance(obj, BodyObject) and body_result(document, key).status is not ResultStatus.READY:
            raise ClipboardError("Recalculez les corps en erreur avant de copier leur construction.")
    owners, entries, total = set(), [], 0

    def native(shape, kind):
        nonlocal total
        payload = encode_shape(shape, kind)
        total += len(payload)
        if len(payload) > MAX_SHAPE or total > MAX_PAYLOAD*3//4:
            raise ClipboardError("Les sources natives dépassent la taille autorisée du presse-papiers.")
        return payload

    for obj in document.objects.values():
        if obj.id not in ids:
            continue
        obj = prepared[obj.id]
        logical = obj.kind is ObjectType.FEATURE or isinstance(obj, BodyObject)
        payload = None if logical else native(obj.shape, obj.kind)
        extension, owner = registry.encode(obj)
        record = dict(id=obj.id, kind=obj.kind.value, name=obj.name, visible=obj.visible,
            color=obj.color, revision=obj.revision, plane=_plane_record(obj.plane), transparency=obj.transparency,
            brep=None if payload is None else dict(path=f"shapes/{obj.id}.brep", sha256=sha256(payload).hexdigest()))
        if obj.material is not None:
            record['material'] = material_record(obj.material)
        if obj.expressions:
            record['expressions'] = [expression_record(e) for e in obj.expressions]
        if extension is not None:
            record["extension"] = extension
            owners.add(owner)
        resources = {}
        native_fields = registry.native_resources(obj)
        if native_fields:
            record["resources"] = {}
            for name, shape in native_fields.items():
                if shape is None:
                    continue
                source = native(shape, ObjectType.BODY)
                resources[name] = b64encode(source).decode("ascii")
                record["resources"][name] = dict(path=f"resources/{obj.id}/{name}.brep", sha256=sha256(source).hexdigest())
        _record(record, allow_extensions=True, appearance=True, logical=True, resources=True, equations=driven)
        entries.append(dict(object=record, payload=None if payload is None else b64encode(payload).decode("ascii"), resources=resources))
    data = dict(format=FORMAT, schema_version=2, source_document=document.id, length_unit="mm",
                required_plugins=sorted(owners), roots=list(roots), objects=entries)
    if driven:
        data.update(schema_version=3, parameters=parameters)
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_PAYLOAD:
        raise ClipboardError("La construction dépasse la taille autorisée du presse-papiers.")
    return payload


class DecodedGraph(tuple):
    def __new__(cls, document, roots):
        result = super().__new__(cls, document.objects.values())
        result.document, result.roots = document, tuple(roots)
        return result

    def calculations(self):
        results, prefixes = {}, {}
        for obj in self:
            if isinstance(obj, BodyObject):
                state = body_result(self.document, obj.id)
                if state.status is not ResultStatus.READY:
                    raise ClipboardError(f"La copie ne se reconstruit pas : {obj.name} : {state.message}")
                results[obj.id] = state
                prefixes[obj.id] = dict(prefix_results(self.document, obj.id))
        return results, prefixes


def decode_graph(data, registry):
    driven = data.get('schema_version') == 3
    _fields(data, "format schema_version source_document length_unit required_plugins roots objects" + (" parameters" if driven else ""), "Construction copiée")
    if data["format"] != FORMAT or type(data["schema_version"]) is not int or data["schema_version"] not in (2, 3) or data["length_unit"] != "mm":
        raise ClipboardError("Format, version ou unité de construction invalide.")
    _uuid(data["source_document"])
    registry.check_requirements(data["required_plugins"])
    entries, roots = data["objects"], data["roots"]
    if (type(entries) is not list or not 0 < len(entries) <= MAX_OBJECTS or type(roots) is not list
            or not roots or any(type(key) is not str for key in roots) or len(set(roots)) != len(roots)):
        raise ClipboardError("Liste d’objets ou de corps racines invalide.")
    objects, records, ids, owners, total = [], [], set(), set(), 0

    def native(encoded, descriptor, kind):
        nonlocal total
        if type(encoded) is not str or len(encoded) > 4*((MAX_SHAPE+2)//3):
            raise ClipboardError("Source B-Rep invalide ou trop volumineuse.")
        payload = b64decode(encoded, validate=True)
        total += len(payload)
        if len(payload) > MAX_SHAPE or total > MAX_PAYLOAD*3//4 or sha256(payload).hexdigest() != descriptor["sha256"]:
            raise ClipboardError("Source B-Rep corrompue ou trop volumineuse.")
        return decode_shape(payload, kind)

    for entry in entries:
        _fields(entry, "object payload resources", "Objet de construction copié")
        record = entry["object"]
        kind, plane, color = _record(record, allow_extensions=True, appearance=True, logical=True, resources=True, equations=driven)
        if record["id"] in ids or type(entry["resources"]) is not dict or set(entry["resources"]) != set(record.get("resources", {})):
            raise ClipboardError("UUID dupliqué ou ressources natives incohérentes.")
        ids.add(record["id"])
        if record["brep"] is None:
            if entry["payload"] is not None:
                raise ClipboardError("Une recette logique ne contient pas de B-Rep finale.")
            shape = None
        else:
            shape = native(entry["payload"], record["brep"], kind)
        obj = SceneObject(record["name"], shape, color, plane, record["visible"], record["id"], record["revision"],
                          kind=kind, transparency=record["transparency"], material=decode_material(record.get('material')))
        if "extension" in record:
            obj, owner = registry.decode(obj, record["extension"])
            registry.check_clipboard_object(obj)
            owners.add(owner)
        obj = registry.restore_resources(obj, {key: native(value, record["resources"][key], ObjectType.BODY)
                                              for key, value in entry["resources"].items()})
        if 'expressions' in record:
            from .expressions import read_expression
            if type(record['expressions']) is not list:
                raise ClipboardError("Liste d’équations invalide.")
            obj = replace(obj, expressions=tuple(read_expression(e) for e in record['expressions']))
        if isinstance(obj, BodyObject) and shape is not None:
            raise ClipboardError("Un corps paramétrique conserve sa recette, jamais son résultat final.")
        objects.append(obj)
        records.append(record)
    by_id = {obj.id: obj for obj in objects}
    if (set(roots)-ids or any(by_id[key].kind is not ObjectType.BODY for key in roots)
            or owners != set(data["required_plugins"])):
        raise ClipboardError("Corps racines ou extensions requis incohérents.")
    for obj in objects:
        if set(references(registry, obj))-ids:
            raise ClipboardError("La copie contient une référence sortante non résolue.")
    registry.validate_objects(objects)
    id_map = {key: str(uuid4()) for key in ids}
    if driven:
        from .clipboard_parameters import read_metadata, expression, remap_state, rebuild
        variables, source, state = read_metadata(data['parameters'], data['source_document'], by_id)
        id_map.update((v.id, str(uuid4())) for v in variables)
    remapped = []
    for obj, record in zip(objects, records):
        base = SceneObject(obj.name, obj.shape, obj.color, obj.plane, obj.visible, id_map[obj.id], obj.revision,
                           kind=obj.kind, transparency=obj.transparency, material=obj.material)
        extension = registry.remap_clipboard_extension(record.get("extension"), id_map)
        if extension is not None:
            base, _ = registry.decode(base, extension)
        base = registry.restore_resources(base, {k: v for k, v in registry.native_resources(obj).items() if v is not None})
        if driven:
            base = replace(base, expressions=tuple(expression(e, id_map) for e in obj.expressions))
        if isinstance(base, BodyObject) and not base.features and base.source_shape is not None:
            base = replace(base, shape=base.source_shape)
        remapped.append(base)
    registry.validate_objects(remapped)
    if driven:
        variables = tuple(replace(v, id=id_map[v.id], expression=expression(v.expression, id_map)) for v in variables)
        if source is not None:
            source = replace(source, bindings=tuple((shared, id_map[local]) for shared, local in source.bindings))
        document_id = str(uuid4())
        state = remap_state(state, id_map, document_id)
        detached = rebuild({o.id: o for o in remapped}, variables, state, source, registry, document_id=document_id)
        result = DecodedGraph(detached, (id_map[key] for key in roots))
        result.parameters = True
        return result
    detached = Document("Construction copiée")
    detached._objects.update({obj.id: obj for obj in remapped})
    for obj in remapped:
        if isinstance(obj, BodyObject) and not obj.features and obj.source_shape is not None:
            detached._body_results[obj.id] = EvaluationResult(ResultStatus.READY, obj.source_shape)
    try:
        registry.restore_document(detached)
        result = DecodedGraph(detached, (id_map[key] for key in roots))
        result.calculations()  # Refuse unresolved names and failed rebuilds before insertion/cut.
        return result
    except BaseException:
        detached.deleteLater()
        raise


def cut_graph_ids(document, roots, registry):
    mandatory = set(roots)
    for key in roots:
        mandatory.update(getattr(document.objects[key], "features", ()))
    candidate, pending = set(mandatory), list(mandatory)
    while pending:
        obj = document.objects[pending.pop()]
        for key in references(registry, obj):
            dep = document.objects[key]
            if isinstance(dep, (SketchSourceObject, DatumPlaneObject)) and key not in candidate:
                candidate.add(key)
                pending.append(key)
    # Shared sketches/planes stay in the original; dependencies on a removed
    # body or feature must be explicitly included in the user's body selection.
    while True:
        keep = set()
        for obj in document.objects.values():
            if obj.id in candidate or type(obj) is SceneObject:
                continue
            try:
                registry.check_clipboard_object(obj)
                used = set(references(registry, obj)) & candidate
            except ValueError as exc:
                raise ClipboardError(f"Coupe refusée : les dépendances de {obj.name} ne peuvent pas être vérifiées : {exc}") from exc
            if used & mandatory:
                raise ClipboardError(f"Coupe refusée : {obj.name} dépend de la construction sélectionnée.")
            keep.update(used)
        if not keep:
            break
        candidate -= keep
    registry.validate_objects(obj for key, obj in document.objects.items() if key not in candidate)
    from .parameters import prepare
    from .configurations import prune
    from .configuration_codec import validate_targets
    remaining = {key: obj for key, obj in document.objects.items() if key not in candidate}
    prepare(remaining, document.variables)
    validate_targets(prune(document.configuration_state, remaining, document.variables), remaining, document.variables)
    return candidate
