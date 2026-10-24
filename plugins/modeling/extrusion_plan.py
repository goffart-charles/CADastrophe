"""Read-only multi-region planning; each result owns exactly one valid solid."""
from dataclasses import dataclass

from cadastrophe.core.boss import fuse_boss
from cadastrophe.core.extrusion import extrude_face, failure, validate_options
from cadastrophe.core.body_history import body_result
from cadastrophe.core.feature_model import ResultStatus
from cadastrophe.core.feature_objects import BodyObject
from cadastrophe.document import SceneObject, ObjectType
from cadastrophe.core.history_position import context_object


@dataclass(frozen=True)
class PlannedBody:
    target_id: str | None
    contours: tuple[str, ...]
    shape: object
    base_faces: tuple = ()


@dataclass(frozen=True)
class ExtrusionPlan:
    parts: tuple[PlannedBody, ...]
    shape: object
    translation: tuple[float, float, float]


def prisms(service, document, sketch_id, depth, *, reversed=False, contour_ids=(),
           tolerance=1e-7, support_shapes=None, trace=None):
    if service.closed:
        raise RuntimeError("Le service d’extrusion est fermé.")
    error = validate_options(depth, reversed, tolerance)
    if error:
        raise ValueError(error.message)
    faces = service.region_faces(document, sketch_id, contour_ids=contour_ids,
                                      tolerance=tolerance, support_shapes=support_shapes)
    results = []
    for region in faces.regions:
        result = extrude_face(region.face, depth, reversed=reversed, tolerance=faces.tolerance, trace=trace)
        if not result.success:
            raise ValueError(result.message)
        results.append((region.id, result))
    if not results:
        raise ValueError("Sélectionnez au moins une région fermée.")
    return tuple(results)


def combine(base, regions, *, trace=None):
    """One boss may consume multiple regions, but must stay a single solid."""
    from OCC.Core.TopAbs import TopAbs_COMPOUND
    shape, old_faces, added, last_success = base, None, False, None
    for _, prism in regions:
        result = fuse_boss(shape, prism.shape, tolerance=prism.tolerance, base_faces=old_faces, trace=trace,
                           allow_disconnected=base.ShapeType() == TopAbs_COMPOUND)
        if not result.success:
            if result.diagnostics[0].code == "no_added_material":
                continue
            return result
        shape, old_faces, added = result.shape, result.base_faces, True
        last_success = result
    if not added:
        return failure("no_added_material", "Aucune matière ajoutée au corps cible. Inversez la direction.")
    return last_success


def plan(service, document, sketch_id, depth, *, merge=True, target_id=None, **options):
    snapshots = dict(document.objects)
    regions = prisms(service, document, sketch_id, depth, **options)
    candidates = []
    if merge:
        for original in snapshots.values():
            obj = context_object(document, original)
            if (obj.kind is ObjectType.BODY and obj.shape is not None
                    and (type(obj) is SceneObject or isinstance(obj, BodyObject))
                    and (document.history_context.position is not None or not isinstance(obj, BodyObject) or body_result(document, obj.id).status is ResultStatus.READY)):
                candidates.append(obj)
        if target_id is not None:
            candidates = [body for body in candidates if body.id == target_id]
            if not candidates:
                raise ValueError("Sélectionnez un corps cible disponible.")
    touching = {}
    for body in candidates:
        attached = []
        for key, prism in regions:
            from OCC.Core.TopAbs import TopAbs_COMPOUND
            from cadastrophe.core.operation_effects import checked_solids
            collection = body.shape.ShapeType() == TopAbs_COMPOUND
            result = fuse_boss(body.shape, prism.shape, tolerance=prism.tolerance, allow_disconnected=collection)
            if collection and result.success and len(checked_solids(result.shape)) > len(checked_solids(body.shape)):
                continue
            if result.success or result.diagnostics[0].code == "no_added_material":
                attached.append((key, prism))
            elif result.diagnostics[0].code != "disconnected":
                raise ValueError(result.message)
        if attached:
            touching[body.id] = attached
    if len(touching) > 1:
        raise ValueError("Plusieurs corps peuvent fusionner : choisissez explicitement le corps cible.")
    parts, used = [], set()
    if touching:
        key, attached = next(iter(touching.items()))
        result = combine(context_object(document, snapshots[key]).shape, attached)
        if not result.success:
            raise ValueError(result.message)
        used = {region for region, _ in attached}
        parts.append(PlannedBody(key, tuple(region for region, _ in attached), result.shape, result.base_faces))
    parts.extend(PlannedBody(None, (key,), result.shape) for key, result in regions if key not in used)
    if set(document.objects) != set(snapshots) or any(document.objects[key] is not obj for key, obj in snapshots.items()):
        raise ValueError("Le document a changé pendant le calcul ; résultat abandonné.")
    if len(parts) == 1:
        shape = parts[0].shape
    else:
        from OCC.Core.BRep import BRep_Builder
        from OCC.Core.TopoDS import TopoDS_Compound
        shape, builder = TopoDS_Compound(), BRep_Builder()
        builder.MakeCompound(shape)
        for part in parts:
            builder.Add(shape, part.shape)
    return ExtrusionPlan(tuple(parts), shape, regions[0][1].translation)
