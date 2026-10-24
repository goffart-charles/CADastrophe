"""Restore initial extrusions and additive prefixes when opening a document.

File restoration runs under the loader's reservation. Live changes are handled
separately by rebuild.py; both paths use the same exact-prefix evaluator.
"""
from cadastrophe.core.body_history import _evaluate_body_steps
from cadastrophe.core.feature_objects import BodyObject, EdgeFeature


def restore_initial_extrusions(document, extrusions):
    from cadastrophe.core.computation import complete
    return complete(restore_extrusions_steps(document, extrusions))


def restore_extrusions_steps(document, extrusions):
    # Called under the existing document-restorer history reservation.
    adapter = extrusions.evaluator(document)
    for obj in tuple(document.objects.values()):
        if isinstance(obj, BodyObject) and obj.features:
            yield from _evaluate_body_steps(document, obj.id, adapter, preserve_revision=True, reuse=True)
    migrate = getattr(extrusions._profiles, "migrate_references", None)
    migrated_sketches = callable(migrate) and migrate(document)
    migrated_edges = migrate_edge_references(document)
    migrated_names = migrate_named_references(document)
    if migrated_edges or migrated_sketches or migrated_names:
        # Capture against proven original producer states before the user can
        # edit upstream parameters. The same evaluator refreshes naming receipts.
        for obj in tuple(document.objects.values()):
            if isinstance(obj, BodyObject) and obj.features:
                yield from _evaluate_body_steps(document, obj.id, adapter, preserve_revision=True, reuse=True)
    from .datums import refresh_datums
    refresh_datums(document)


def migrate_edge_references(document):
    """Name v1 edge selections only after resolving their original exact prefix."""
    from dataclasses import replace
    from cadastrophe.core.body_history import result_at
    from cadastrophe.core.feature_model import BodyStateRef, ResultStatus
    from cadastrophe.core.native_naming import capture, canonical_reference
    from .edge_geometry import resolve_edges
    changed = False
    for feature in tuple(document.objects.values()):
        if not isinstance(feature, EdgeFeature):
            continue
        body = document.objects[feature.body_id]
        index = body.features.index(feature.id)
        if not feature.parameters.references and any(not document.objects[key].enabled for key in body.features[:index]):
            continue  # An inactive prefix cannot prove a legacy coordinate selection.
        ref = BodyStateRef(body.id, body.features[index-1] if index else None)
        state = result_at(document, ref)
        if state.status is not ResultStatus.READY:
            continue
        try:
            if feature.parameters.references:
                refs = tuple(canonical_reference(state.shape, r, state.topology) for r in feature.parameters.references)
            else:
                edges = resolve_edges(state.shape, feature.parameters.edges)
                refs = tuple(capture(state.shape, e, "Edge", state.topology) for e in edges)
        except (ValueError, RuntimeError):
            continue
        if refs != feature.parameters.references:
            document._objects[feature.id] = replace(feature, parameters=replace(feature.parameters, references=refs))
            changed = True
    return changed


def migrate_named_references(document):
    """Canonicalize geometric recipe inputs against proven producer states.

    Only loader-owned immutable parameters are walked. No index, nearest match,
    derived frame or failed reference is substituted by this migration.
    """
    from dataclasses import fields, is_dataclass, replace
    from cadastrophe.core.body_history import result_at
    from cadastrophe.core.feature_model import BodyStateRef, ResultStatus
    from cadastrophe.core.feature_objects import ConstructionFeature
    from cadastrophe.core.reference_objects import DatumPlaneObject
    from cadastrophe.core.operation_model import GeometryReference
    from cadastrophe.core.native_naming import canonical_reference

    def upgrade(value):
        if isinstance(value, GeometryReference):
            if value.topology is not None:
                try:
                    state = result_at(document, BodyStateRef(value.object_id, value.producer_id))
                    if state.status is ResultStatus.READY:
                        return replace(value, topology=canonical_reference(state.shape, value.topology, state.topology))
                except (ValueError, RuntimeError):
                    pass
            return value
        if isinstance(value, tuple):
            return tuple(upgrade(item) for item in value)
        if is_dataclass(value):
            return replace(value, **{f.name: upgrade(getattr(value, f.name)) for f in fields(value) if f.init})
        return value

    changed = False
    for obj in tuple(document.objects.values()):
        if isinstance(obj, (ConstructionFeature, DatumPlaneObject)):
            parameters = upgrade(obj.parameters)
            if parameters != obj.parameters:
                document._objects[obj.id] = replace(obj, parameters=parameters)
                changed = True
    return changed
