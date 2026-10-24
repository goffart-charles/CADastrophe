"""New recipe codecs, using the existing .cada object extension mechanism."""
from dataclasses import asdict, replace

from cadastrophe.core.object_types import ObjectCodec
from cadastrophe.core.construction_codec import with_activation
from cadastrophe.core.operation_model import GeometryReference, PlaneParameters, RevolutionParameters, ReplayParameters, ShellParameters, DraftParameters, SweepParameters, LoftParameters, LoftSection
from cadastrophe.core.reference_objects import DatumPlaneObject
from cadastrophe.core.feature_objects import RevolutionFeature, ReplayFeature, ShellFeature, DraftFeature, SweepFeature, LoftFeature, HoleFeature
from cadastrophe.core.feature_model import Operation, SketchInput, BodyStateRef
from cadastrophe.core.topology_refs import encode_reference, decode_reference
from .codec import exact, base_fields
from cadastrophe.core.recipe_references import recipe_references, remap_recipe


def encode_geometry(ref):
    return dict(kind=ref.kind, object_id=ref.object_id, producer_id=ref.producer_id,
                topology=encode_reference(ref.topology), entity_id=ref.entity_id, part=ref.part, values=list(ref.values))


def decode_geometry(data):
    exact(data, "kind object_id producer_id topology entity_id part values")
    if type(data["values"]) is not list:
        raise ValueError("Coordonnées de référence invalides.")
    return GeometryReference(**dict(data, topology=decode_reference(data["topology"]), values=tuple(data["values"])))


def encode_plane(obj):
    return dict(error=obj.reference_error_message, parameters=dict(method=obj.parameters.method, distance=obj.parameters.distance,
                                angle=obj.parameters.angle, reversed=obj.parameters.reversed,
                                references=[encode_geometry(r) for r in obj.parameters.references]))


def decode_plane(base, data):
    exact(data, "parameters error")
    if type(data["error"]) is not str:
        raise ValueError("Diagnostic de plan invalide.")
    p = data["parameters"]
    exact(p, "method distance angle reversed references")
    if type(p["references"]) is not list or len(p["references"]) > 3:
        raise ValueError("Références de plan invalides.")
    return DatumPlaneObject(**base_fields(base), reference_error_message=data["error"], parameters=PlaneParameters(**dict(p, references=tuple(decode_geometry(r) for r in p["references"]))))


def encode_revolution(obj):
    p = obj.parameters
    return dict(body_id=obj.body_id, operation=obj.operation.value,
                source=dict(sketch_id=obj.source.sketch_id, supports=[asdict(r) for r in obj.support_refs]),
                parameters=dict(axis=encode_geometry(p.axis), angle=p.angle, reversed=p.reversed, contours=list(p.contours)))


def decode_revolution(base, data):
    exact(data, "body_id operation source parameters")
    source, p = data["source"], data["parameters"]
    exact(source, "sketch_id supports")
    exact(p, "axis angle reversed contours")
    if type(source["supports"]) is not list or type(p["contours"]) is not list:
        raise ValueError("Sources de révolution invalides.")
    supports = []
    for r in source["supports"]:
        exact(r, "body_id producer_id")
        supports.append(BodyStateRef(**r))
    return RevolutionFeature(**base_fields(base), body_id=data["body_id"], operation=Operation(data["operation"]),
        source=SketchInput(source["sketch_id"], tuple(supports)),
        parameters=RevolutionParameters(**dict(p, axis=decode_geometry(p["axis"]), contours=tuple(p["contours"]))))


def encode_replay(obj):
    p = obj.parameters
    return dict(body_id=obj.body_id, operation=obj.operation.value,
        parameters=dict(sources=list(p.sources), reference=encode_geometry(p.reference), count=p.count,
                        spacing=p.spacing, angle=p.angle, reversed=p.reversed, merge_result=p.merge_result))


def decode_replay(base, data):
    exact(data, "body_id operation parameters")
    p = data["parameters"]
    exact(p, "sources reference count spacing angle reversed merge_result")
    if type(p["sources"]) is not list or len(p["sources"]) > 128:
        raise ValueError("Fonctions sources invalides.")
    return ReplayFeature(**base_fields(base), body_id=data["body_id"], operation=Operation(data["operation"]),
        parameters=ReplayParameters(**dict(p, sources=tuple(p["sources"]), reference=decode_geometry(p["reference"]))))


PLANE_CODEC = ObjectCodec("cadastrophe.modeling.plane", DatumPlaneObject, 1, encode_plane, decode_plane,
                         clipboard_references=recipe_references, clipboard_remap=remap_recipe)
REVOLUTION_CODEC = ObjectCodec("cadastrophe.modeling.revolution", RevolutionFeature, 1, encode_revolution, decode_revolution,
                              clipboard_references=recipe_references, clipboard_remap=remap_recipe)
REPLAY_CODEC = ObjectCodec("cadastrophe.modeling.replay", ReplayFeature, 2, encode_replay, decode_replay,
                          clipboard_references=recipe_references, clipboard_remap=remap_recipe)


def encode_shell(obj):
    return dict(body_id=obj.body_id, operation=obj.operation.value, input_state=asdict(obj.input_state),
                parameters=dict(faces=[encode_geometry(r) for r in obj.parameters.faces],
                                thickness=obj.parameters.thickness, outward=obj.parameters.outward))


def decode_shell(base, data):
    exact(data, "body_id operation input_state parameters")
    exact(data["input_state"], "body_id producer_id")
    p = data["parameters"]
    exact(p, "faces thickness outward")
    if type(p["faces"]) is not list or not 1 <= len(p["faces"]) <= 512:
        raise ValueError("Sélection de faces à ouvrir invalide.")
    return ShellFeature(**base_fields(base), body_id=data["body_id"], operation=Operation(data["operation"]),
                        input_state=BodyStateRef(**data["input_state"]),
                        parameters=ShellParameters(tuple(decode_geometry(r) for r in p["faces"]), p["thickness"], p["outward"]))


SHELL_CODEC = ObjectCodec("cadastrophe.modeling.shell", ShellFeature, 1, encode_shell, decode_shell,
                         clipboard_references=recipe_references, clipboard_remap=remap_recipe)


def encode_draft(obj):
    p = obj.parameters
    return dict(body_id=obj.body_id, operation=obj.operation.value, input_state=asdict(obj.input_state),
                parameters=dict(faces=[encode_geometry(r) for r in p.faces], neutral=encode_geometry(p.neutral),
                                direction=encode_geometry(p.direction), angle=p.angle, reversed=p.reversed))


def decode_draft(base, data):
    exact(data, "body_id operation input_state parameters")
    exact(data["input_state"], "body_id producer_id")
    p = data["parameters"]
    exact(p, "faces neutral direction angle reversed")
    if type(p["faces"]) is not list or not 1 <= len(p["faces"]) <= 512:
        raise ValueError("Sélection de faces à dépouiller invalide.")
    return DraftFeature(**base_fields(base), body_id=data["body_id"], operation=Operation(data["operation"]),
                        input_state=BodyStateRef(**data["input_state"]),
                        parameters=DraftParameters(tuple(decode_geometry(r) for r in p["faces"]),
                            decode_geometry(p["neutral"]), decode_geometry(p["direction"]), p["angle"], p["reversed"]))


DRAFT_CODEC = ObjectCodec("cadastrophe.modeling.draft", DraftFeature, 1, encode_draft, decode_draft,
                         clipboard_references=recipe_references, clipboard_remap=remap_recipe)


def encode_sweep(obj):
    p = obj.parameters
    return dict(body_id=obj.body_id, operation=obj.operation.value,
                source=dict(sketch_id=obj.sketch_id, supports=[asdict(r) for r in obj.support_refs]),
                parameters=dict(path=[encode_geometry(r) for r in p.path], transport=p.transport, orientation=p.orientation,
                                transition=p.transition, roll=p.roll, reversed=p.reversed, contours=list(p.contours)))


def decode_sweep(base, data):
    exact(data, "body_id operation source parameters")
    source, p = data["source"], data["parameters"]
    exact(source, "sketch_id supports")
    exact(p, "path transport orientation transition roll reversed contours")
    if (type(source["supports"]) is not list or type(p["contours"]) is not list
            or type(p["path"]) is not list or not 1 <= len(p["path"]) <= 512):
        raise ValueError("Sources de balayage invalides.")
    supports = []
    for ref in source["supports"]:
        exact(ref, "body_id producer_id")
        supports.append(BodyStateRef(**ref))
    return SweepFeature(**base_fields(base), body_id=data["body_id"], operation=Operation(data["operation"]),
                        source=SketchInput(source["sketch_id"], tuple(supports)),
                        parameters=SweepParameters(**dict(p, path=tuple(decode_geometry(r) for r in p["path"]), contours=tuple(p["contours"]))))


SWEEP_CODEC = ObjectCodec("cadastrophe.modeling.sweep", SweepFeature, 1, encode_sweep, decode_sweep,
                         clipboard_references=recipe_references, clipboard_remap=remap_recipe)


def encode_loft(obj):
    p = obj.parameters
    return dict(body_id=obj.body_id, operation=obj.operation.value, parameters=dict(mode=p.mode, matching=p.matching,
        sections=[dict(source=dict(sketch_id=s.source.sketch_id, supports=[asdict(r) for r in s.source.supports]),
                       contours=list(s.contours), anchors=list(s.anchors), reversed=list(s.reversed)) for s in p.sections]))


def decode_loft(base, data):
    exact(data, "body_id operation parameters")
    p = data["parameters"]
    exact(p, "mode matching sections")
    if type(p["sections"]) is not list or not 2 <= len(p["sections"]) <= 64:
        raise ValueError("Sections de lissage invalides.")
    sections = []
    for s in p["sections"]:
        exact(s, "source contours anchors reversed")
        source = s["source"]
        exact(source, "sketch_id supports")
        if any(type(s[key]) is not list for key in ("contours", "anchors", "reversed")) or type(source["supports"]) is not list:
            raise ValueError("Références de section invalides.")
        supports = []
        for ref in source["supports"]:
            exact(ref, "body_id producer_id")
            supports.append(BodyStateRef(**ref))
        sections.append(LoftSection(SketchInput(source["sketch_id"], tuple(supports)), tuple(s["contours"]), tuple(s["anchors"]), tuple(s["reversed"])))
    return LoftFeature(**base_fields(base), body_id=data["body_id"], operation=Operation(data["operation"]),
                       parameters=LoftParameters(tuple(sections), p["mode"], p["matching"]))


LOFT_CODEC = ObjectCodec("cadastrophe.modeling.loft", LoftFeature, 1, encode_loft, decode_loft,
                        clipboard_references=recipe_references, clipboard_remap=remap_recipe)

REVOLUTION_CODEC = with_activation(REVOLUTION_CODEC)
REPLAY_CODEC = with_activation(REPLAY_CODEC)
SHELL_CODEC = replace(with_activation(SHELL_CODEC), version=3)
DRAFT_CODEC = replace(with_activation(DRAFT_CODEC), version=3)
SWEEP_CODEC = with_activation(SWEEP_CODEC)
LOFT_CODEC = with_activation(LOFT_CODEC)


def encode_hole(obj):
    p = obj.parameters
    return dict(body_id=obj.body_id, operation=obj.operation.value,
                source=dict(sketch_id=obj.sketch_id, supports=[asdict(r) for r in obj.support_refs]),
                parameters={**{key: getattr(p, key) for key in
                    ("form", "diameter", "depth", "end_condition", "reversed", "head_diameter", "head_depth", "angle")},
                    "positions": [encode_geometry(r) for r in p.positions]})


def decode_hole(base, data):
    from cadastrophe.core.hole_model import HoleParameters
    exact(data, "body_id operation source parameters")
    source, p = data["source"], data["parameters"]
    exact(source, "sketch_id supports")
    exact(p, "form diameter depth end_condition reversed head_diameter head_depth angle positions")
    if (type(source["supports"]) is not list or len(source["supports"]) > 512
            or type(p["positions"]) is not list or len(p["positions"]) > 512):
        raise ValueError("Sources de perçage invalides.")
    for item in source["supports"]:
        exact(item, "body_id producer_id")
    return HoleFeature(**base_fields(base), body_id=data["body_id"], operation=Operation(data["operation"]),
        source=SketchInput(source["sketch_id"], tuple(BodyStateRef(**r) for r in source["supports"])),
        parameters=HoleParameters(**dict(p, positions=tuple(decode_geometry(r) for r in p["positions"]))))


HOLE_CODEC = with_activation(ObjectCodec("cadastrophe.modeling.hole", HoleFeature, 1, encode_hole, decode_hole,
                             clipboard_references=recipe_references, clipboard_remap=remap_recipe))
