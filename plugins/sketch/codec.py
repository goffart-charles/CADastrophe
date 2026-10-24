"""Sketch v8: associative pattern recipes; derived occurrences are not stored."""
from dataclasses import fields

from cadastrophe.geometry import Vec3, WorkPlane
from cadastrophe.plugin_api import ObjectCodec, ObjectType, SceneObject
from .model import Arc, Circle, PlaneSupport, Point, Segment, Sketch, Vec2, finite, ExternalGeometry
from .objects import SketchObject
from .entity_codec import _fields, _encode_entity, _decode_entity
from .constraints import encode as encode_constraint, decode as decode_constraint
from cadastrophe.core.topology_refs import encode_reference, decode_reference
from cadastrophe.core.recipe_references import recipe_references, remap_recipe

TYPE_ID = "cadastrophe.sketch.sketch"


def _vec3(value):
    if type(value) is not list or len(value) != 3:
        raise ValueError("Trois coordonnées de repère sont requises.")
    for component in value:
        finite(component)
    return Vec3(*value)


def _encode_plane(frame):
    return None if frame is None else dict(name=frame.name, origin=list(frame.origin.tuple()),
                                          u=list(frame.u.tuple()), v=list(frame.v.tuple()))


def _decode_plane(data):
    if data is None:
        return None
    _fields(data, "name origin u v")
    if type(data["name"]) is not str or not data["name"].strip():
        raise ValueError("Nom de repère invalide.")
    origin, u, v = (_vec3(data[key]) for key in ("origin", "u", "v"))
    if abs(u.norm()-1) > 1e-10 or abs(v.norm()-1) > 1e-10:
        raise ValueError("Le repère doit être orthonormé.")
    return WorkPlane(data["name"], origin, u, v)


def encode(obj):
    support = obj.sketch.support
    frame = support.frame
    return dict(support=dict(plane_id=support.plane_id, revision=support.revision, face_index=support.face_index,
                             producer_id=support.producer_id, reference=encode_reference(support.reference),
                             attachment=_encode_plane(support.attachment),
                             frame=dict(name=frame.name, origin=list(frame.origin.tuple()),
                                        u=list(frame.u.tuple()), v=list(frame.v.tuple()))),
                entities=[_encode_entity(entity) for entity in obj.sketch.source_entities],
                patterns=[_encode_pattern(p) for p in obj.sketch.patterns],
                constraints=[encode_constraint(c) for c in obj.sketch.constraints],
                external=[dict(body_id=e.body_id, revision=e.revision, topology=e.topology, index=e.index,
                               geometry=_encode_entity(e.geometry), producer_id=e.producer_id,
                               reference=encode_reference(e.reference)) for e in obj.sketch.external])


def decode(base, data):
    _fields(data, "support entities constraints external patterns")
    if base.kind is not ObjectType.FEATURE or base.shape is not None:
        raise ValueError("Une esquisse est un objet logique sans B-Rep.")
    support = data["support"]
    _fields(support, "plane_id revision frame face_index producer_id reference attachment")
    frame = support["frame"]
    _fields(frame, "name origin u v")
    if type(frame["name"]) is not str or not frame["name"].strip():
        raise ValueError("Nom de repère invalide.")
    origin, u, v = (_vec3(frame[key]) for key in ("origin", "u", "v"))
    if abs(u.norm() - 1) > 1e-10 or abs(v.norm() - 1) > 1e-10:
        raise ValueError("Le repère doit être orthonormé.")
    plane = WorkPlane(frame["name"], origin, u, v)
    entities = data["entities"]
    if type(entities) is not list or len(entities) > 50_000:
        raise ValueError("Liste d’entités invalide ou trop volumineuse.")
    sketch = Sketch(PlaneSupport(support["plane_id"], plane, support["revision"], support["face_index"],
                                 support["producer_id"], decode_reference(support["reference"]), _decode_plane(support["attachment"])),
                    tuple(_decode_entity(entity) for entity in entities), id=base.id,
                    constraints=_decode_constraints(data["constraints"]), external=_decode_external(data["external"]),
                    patterns=_decode_patterns(data["patterns"]))
    return SketchObject(**{item.name: getattr(base, item.name) for item in fields(SceneObject)}, sketch=sketch)


def validate_references(obj, objects):
    target = objects.get(obj.sketch.support.plane_id)
    # A missing/changed support retains its frozen frame and explicit unresolved
    # status, rather than silently reattaching. An existing non-plane is corrupt.
    expected = ObjectType.REFERENCE_PLANE if obj.sketch.support.face_index is None else ObjectType.BODY
    if target is not None and target.kind is not expected:
        raise ValueError("La référence de support ne désigne pas un plan.")
    for e in obj.sketch.external:
        body = objects.get(e.body_id)
        if body is not None and body.kind is not ObjectType.BODY:
            raise ValueError("La référence externe ne désigne pas un solide.")


def _decode_constraints(data):
    if type(data) is not list or len(data) > 50_000:
        raise ValueError("Liste de contraintes invalide ou trop volumineuse.")
    return tuple(decode_constraint(c) for c in data)


def _decode_external(data):
    if type(data) is not list or len(data) > 50_000:
        raise ValueError("Liste de références externes invalide.")
    result = []
    for e in data:
        _fields(e, "body_id revision topology index geometry producer_id reference")
        result.append(ExternalGeometry(e["body_id"], e["revision"], e["topology"], e["index"],
                                       _decode_entity(e["geometry"]), e["producer_id"], decode_reference(e["reference"])))
    return tuple(result)


def _encode_pattern(pattern):
    return dict(id=pattern.id, entity_ids=list(pattern.entity_ids), mode=pattern.mode, count=pattern.count,
                du=pattern.du, dv=pattern.dv, center=[pattern.center.u, pattern.center.v], angle=pattern.angle)


def _decode_patterns(data):
    from .patterns import SketchPattern
    from .entity_codec import _vec2
    if type(data) is not list or len(data) > 256:
        raise ValueError("Liste de motifs d’esquisse invalide ou trop volumineuse.")
    result = []
    for item in data:
        _fields(item, "id entity_ids mode count du dv center angle")
        if type(item["entity_ids"]) is not list:
            raise ValueError("Liste de sources du motif invalide.")
        result.append(SketchPattern(**dict(item, center=_vec2(item["center"]))))
    return tuple(result)


def remap_sketch(data, id_map):
    result = remap_recipe(data, id_map)
    local_ids = {item['id'] for item in result['entities']}
    local_ids.update(item['geometry']['id'] for item in result['external'])
    for relation in result['constraints']:
        for ref in relation['refs']:
            if ref['entity_id'] not in local_ids and ref['entity_id'] in id_map:
                ref['entity_id'] = id_map[ref['entity_id']]
    return result


CODEC = ObjectCodec(TYPE_ID, SketchObject, 8, encode, decode, validate_references=validate_references,
                    clipboard_references=recipe_references, clipboard_remap=remap_sketch)
