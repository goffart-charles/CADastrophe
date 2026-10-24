"""Current body, extrusion and edge recipes with separately stored native sources."""
from dataclasses import fields, asdict

from cadastrophe.document import SceneObject
from cadastrophe.core.object_types import ObjectCodec
from cadastrophe.core.construction_codec import with_activation
from cadastrophe.core.feature_objects import BodyObject, ExtrusionFeature, EdgeFeature
from cadastrophe.core.feature_model import Operation, SketchInput, BodyStateRef, ExtrusionParameters, EdgeParameters
from cadastrophe.core.topology_refs import encode_reference, decode_reference
from cadastrophe.core.recipe_references import recipe_references, remap_recipe


def exact(data, keys):
    if type(data) is not dict or set(data) != set(keys.split()):
        raise ValueError("Description de construction : champs manquants ou inconnus.")


def base_fields(base):
    return {item.name: getattr(base, item.name) for item in fields(SceneObject)}


def encode_body(obj):
    return dict(features=list(obj.features), source_kind=obj.source_kind)


def decode_body(base, data):
    exact(data, "features source_kind")
    if type(data["features"]) is not list:
        raise ValueError("Ordre de fonctions invalide.")
    return BodyObject(**base_fields(base), features=tuple(data["features"]), source_kind=data["source_kind"])


def encode_feature(obj):
    return dict(operation=obj.operation.value, body_id=obj.body_id,
                source=dict(sketch_id=obj.source.sketch_id, supports=[asdict(ref) for ref in obj.source.supports]),
                parameters=dict(depth=obj.parameters.depth, reversed=obj.parameters.reversed,
                                end_condition=obj.parameters.end_condition, merge_result=obj.parameters.merge_result,
                                contours=list(obj.parameters.contours)))


def decode_feature(base, data):
    exact(data, "operation body_id source parameters")
    source, parameters = data["source"], data["parameters"]
    exact(source, "sketch_id supports")
    exact(parameters, "depth reversed end_condition merge_result contours")
    if type(source["supports"]) is not list or type(parameters["contours"]) is not list:
        raise ValueError("Références de source ou contours invalides.")
    supports = []
    for ref in source["supports"]:
        exact(ref, "body_id producer_id")
        supports.append(BodyStateRef(**ref))
    return ExtrusionFeature(**base_fields(base), operation=Operation(data["operation"]), body_id=data["body_id"],
                            source=SketchInput(source["sketch_id"], tuple(supports)),
                            parameters=ExtrusionParameters(**dict(parameters, contours=tuple(parameters["contours"]))))


BODY_CODEC = ObjectCodec("cadastrophe.modeling.body", BodyObject, 1, encode_body, decode_body,
                         native_resources=("source_shape",), clipboard_references=recipe_references, clipboard_remap=remap_recipe)
FEATURE_CODEC = ObjectCodec("cadastrophe.modeling.extrusion", ExtrusionFeature, 1, encode_feature, decode_feature,
                            clipboard_references=recipe_references, clipboard_remap=remap_recipe)


def encode_edges(obj):
    return dict(body_id=obj.body_id, operation=obj.operation.value,
                parameters=dict(asdict(obj.parameters), edges=[list(e) for e in obj.parameters.edges],
                                references=[encode_reference(r) for r in obj.parameters.references]))


def decode_edges(base, data):
    exact(data, "body_id operation parameters")
    params = data["parameters"]
    exact(params, "edges size size2 mode angle tangent flip references")
    if (type(params["edges"]) is not list or any(type(e) is not list for e in params["edges"])
            or type(params["references"]) is not list or len(params["references"]) > 512):
        raise ValueError("Références d’arêtes invalides.")
    return EdgeFeature(**base_fields(base), body_id=data["body_id"], operation=Operation(data["operation"]),
                       parameters=EdgeParameters(**dict(params, edges=tuple(tuple(e) for e in params["edges"]),
                                                        references=tuple(decode_reference(r) for r in params["references"]))))


EDGE_CODEC = ObjectCodec("cadastrophe.modeling.edges", EdgeFeature, 2, encode_edges, decode_edges,
                        clipboard_references=recipe_references, clipboard_remap=remap_recipe)

FEATURE_CODEC = with_activation(FEATURE_CODEC)
EDGE_CODEC = with_activation(EDGE_CODEC)
