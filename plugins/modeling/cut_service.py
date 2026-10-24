"""Read-only subtractive extrusion over public sketch profiles and core OCCT."""
from cadastrophe.core.cut import cut_solid, through_all_depth, failure
from cadastrophe.core.extrusion import extrude_face, validate_options
from cadastrophe.core.body_history import body_result
from cadastrophe.core.feature_objects import BodyObject
from cadastrophe.core.feature_model import ResultStatus
from cadastrophe.document import SceneObject, ObjectType
from .extrusion_plan import PlannedBody, ExtrusionPlan
from cadastrophe.core.history_position import context_object


def target_body(document, target_id):
    candidates = [obj for original in document.objects.values() if (obj := context_object(document, original))
        if obj.kind is ObjectType.BODY and obj.shape is not None
        and (type(obj) is SceneObject or isinstance(obj, BodyObject))
        and (getattr(document, "history_context", None) is not None and document.history_context.position is not None
             or not isinstance(obj, BodyObject) or body_result(document, obj.id).status is ResultStatus.READY)]
    if target_id is not None:
        candidates = [body for body in candidates if body.id == target_id]
    if len(candidates) != 1:
        raise ValueError("Choisissez un corps cible disponible pour l’enlèvement." if not candidates else
                         "Plusieurs corps sont disponibles ; choisissez explicitement le corps cible de l’enlèvement.")
    return candidates[0]


def cut_shape(service, document, sketch_id, base, depth, *, end_condition="blind", reversed=False,
              contour_ids=(), tolerance=1e-7, support_shapes=None, trace=None):
    if service.closed:
        raise RuntimeError("Le service d’extrusion est fermé.")
    if end_condition not in ("blind", "through_all") or (end_condition == "through_all" and depth is not None):
        return failure("invalid_condition", "La traversée conserve sa condition paramétrique et aucune profondeur fixe."), None
    error = validate_options(depth if end_condition == "blind" else 1., reversed, tolerance)
    if error:
        return failure(error.diagnostics[0].code, error.message), None
    try:
        faces = service.region_faces(document, sketch_id, contour_ids=contour_ids,
                                          tolerance=tolerance, support_shapes=support_shapes)
        if not faces.regions:
            return failure("missing_profile", "Sélectionnez une région fermée à enlever."), None
        tools = []
        for region in faces.regions:
            extent = depth if end_condition == "blind" else through_all_depth(
                base, region.face, reversed=reversed, tolerance=faces.tolerance)
            prism = extrude_face(region.face, extent, reversed=reversed, tolerance=faces.tolerance, trace=trace)
            if not prism.success:
                return failure(prism.diagnostics[0].code, prism.message), None
            tools.append(prism)
        tool = tools[0].shape
        if len(tools) > 1:
            from OCC.Core.BRep import BRep_Builder
            from OCC.Core.TopoDS import TopoDS_Compound
            tool, builder = TopoDS_Compound(), BRep_Builder()
            builder.MakeCompound(tool)
            for prism in tools:
                builder.Add(tool, prism.shape)
        # One boolean for the whole selected feature; inspect the final topology.
        from OCC.Core.TopAbs import TopAbs_COMPOUND
        result = cut_solid(base, tool, tolerance=max(prism.tolerance for prism in tools), trace=trace,
                           allow_disconnected=base.ShapeType() == TopAbs_COMPOUND)
        return result, tools[0].translation if result.success else None
    except (ValueError, RuntimeError, TypeError, ArithmeticError) as exc:
        return failure("cut_failed", str(exc)), None


def build_cut(service, document, sketch_id, target_id, depth, **options):
    snapshots = dict(document.objects)
    try:
        target = target_body(document, target_id)
    except ValueError as exc:
        return failure("missing_target", str(exc))
    result, _ = cut_shape(service, document, sketch_id, target.shape, depth, **options)
    if set(document.objects) != set(snapshots) or any(document.objects[key] is not obj for key, obj in snapshots.items()):
        return failure("stale_input", "Le document a changé pendant la coupe ; résultat abandonné.")
    return result


def cut_plan(service, document, sketch_id, depth, *, target_id=None, **options):
    snapshots = dict(document.objects)
    target = target_body(document, target_id)
    result, translation = cut_shape(service, document, sketch_id, target.shape, depth, **options)
    if set(document.objects) != set(snapshots) or any(document.objects[key] is not obj for key, obj in snapshots.items()):
        raise ValueError("Le document a changé pendant la coupe ; résultat abandonné.")
    if not result.success:
        raise ValueError(result.message)
    contours = tuple(options.get("contour_ids", ()))
    return ExtrusionPlan((PlannedBody(target.id, contours, result.shape, result.base_faces),), result.shape, translation)
