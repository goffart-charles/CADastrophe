"""Versioned parameter bytes, not source documents, native owners or supports."""
from dataclasses import replace
import json

from .editing import translated
from .entity_codec import _fields, _encode_entity, _decode_entity
from .model import Vec2, new_id, valid_id

MIME_TYPE = "application/x-cadastrophe-sketch-entities+json"
MAX_PAYLOAD = 8 * 1024 * 1024
MAX_ENTITIES = 10_000


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Clé JSON dupliquée.")
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f"Constante JSON invalide : {value}")


def encode_entities(document_id, sketch_id, entities):
    valid_id(document_id)
    valid_id(sketch_id)
    data = dict(format="cadastrophe.sketch.entities", schema_version=1, length_unit="mm",
                source_document=document_id, source_sketch=sketch_id,
                entities=[_encode_entity(entity) for entity in entities])
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    decode_entities(payload, Vec2(0, 0))  # Validate the complete lot before publishing/cutting.
    return payload


def decode_entities(payload, offset: Vec2):
    if type(payload) is not bytes or not payload or len(payload) > MAX_PAYLOAD:
        raise ValueError("Presse-papiers d’esquisse vide ou trop volumineux.")
    try:
        data = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
        _fields(data, "format schema_version length_unit source_document source_sketch entities")
        if (data["format"] != "cadastrophe.sketch.entities" or type(data["schema_version"]) is not int
                or data["schema_version"] != 1 or data["length_unit"] != "mm"):
            raise ValueError("Format, version ou unité de presse-papiers d’esquisse incompatible.")
        valid_id(data["source_document"])
        valid_id(data["source_sketch"])
        records = data["entities"]
        if type(records) is not list or not 0 < len(records) <= MAX_ENTITIES:
            raise ValueError("Lot d’entités vide, invalide ou trop volumineux.")
        entities = tuple(_decode_entity(record) for record in records)
        ids = {entity.id for entity in entities}
        if len(ids) != len(entities) or data["source_sketch"] in ids:
            raise ValueError("Identifiant d’entité dupliqué ou confondu avec l’esquisse.")
        from .reshape import regroup_text
        return regroup_text(tuple(replace(translated(entity, offset), id=new_id()) for entity in entities))
    except (KeyError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
        raise ValueError(f"Presse-papiers d’esquisse invalide : {exc}") from exc
