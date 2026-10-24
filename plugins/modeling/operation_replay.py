"""One generating-volume contract for ordinary and transformed operations."""
from dataclasses import replace
from hashlib import sha256
from cadastrophe.core.feature_model import BodyStateRef, Operation
from cadastrophe.core.feature_objects import ExtrusionFeature, RevolutionFeature, ReplayFeature, SweepFeature, LoftFeature, HoleFeature
from cadastrophe.core.operation_effects import VolumeEffect, apply_effects, occurrence_transforms, transform_effect
from cadastrophe.core.reference_geometry import resolve_reference, resolve_axis_reference, state_for
from cadastrophe.core.native_naming import NamingTrace


def generator_trace(service, document, feature):
    provider = getattr(service._profiles, "source_edges", None)
    edges = provider(document, feature.sketch_id) if callable(provider) and feature.sketch_id is not None else ()
    return NamingTrace(feature.id, sources=edges)


def generating_volumes(service, document, feature, previous, trace):
    if not feature.enabled:
        from cadastrophe.core.feature_model import InactiveReferenceError
        raise InactiveReferenceError("La fonction source est désactivée : " + feature.name, (feature.id,))
    p = feature.parameters
    if isinstance(feature, HoleFeature):
        from .hole_geometry import generating_holes
        return generating_holes(service, document, feature, previous, trace)
    if isinstance(feature, LoftFeature):
        from .loft_geometry import generating_loft
        shape = generating_loft(service, document, feature, trace)
        return (VolumeEffect(feature.operation, shape, trace.topology, (feature.id, "0"), feature.id),)
    # References of the generating sketch retain their own exact producer states.
    # Other axis/plane/source states are available through rebuild_states, but
    # must not be passed as unrelated sketch-support substitutions.
    states = getattr(document, "rebuild_states", {})
    support_shapes = {ref.body_id: states[ref].shape for ref in feature.support_refs if ref in states}
    faces = service.region_faces(document, feature.sketch_id, contour_ids=p.contours, support_shapes=support_shapes)
    if not faces.regions:
        raise ValueError("Sélectionnez au moins un profil fermé valide.")
    effects = []
    if isinstance(feature, SweepFeature):
        from cadastrophe.core.sweep import sweep_face
        from .sweep_path import resolve_path
        if len(faces.regions) != 1:
            raise ValueError("Choisissez une seule région fermée pour le balayage.")
        for region in faces.regions:
            path = resolve_path(service._profiles, document, p, region.face)
            shape = sweep_face(region.face, path, p, tolerance=faces.tolerance, trace=trace)
            effects.append(VolumeEffect(feature.operation, shape, trace.topology, (feature.id, str(len(effects))), feature.id))
    elif isinstance(feature, RevolutionFeature):
        from cadastrophe.core.revolution import revolve_face
        axis = resolve_axis_reference(document, p.axis)
        for region in faces.regions:
            shape = revolve_face(region.face, axis, p.angle, reversed=p.reversed, tolerance=faces.tolerance, trace=trace)
            effects.append(VolumeEffect(feature.operation, shape, trace.topology, (feature.id, str(len(effects))), feature.id))
    elif isinstance(feature, ExtrusionFeature):
        from cadastrophe.core.extrusion import extrude_face
        from cadastrophe.core.cut import through_all_depth
        for region in faces.regions:
            depth = p.depth if p.end_condition == "blind" else through_all_depth(previous, region.face, reversed=p.reversed, tolerance=faces.tolerance)
            result = extrude_face(region.face, depth, reversed=p.reversed, tolerance=faces.tolerance, trace=trace)
            if not result.success:
                raise ValueError(result.message)
            effects.append(VolumeEffect(feature.operation, result.shape, trace.topology, (feature.id, str(len(effects))), feature.id))
    else:
        raise ValueError("Cette fonction ne fournit pas de volume générateur compatible.")
    return tuple(effects)


def replay(service, document, feature, previous, trace):
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
    params = feature.parameters
    owner = document.objects[feature.body_id]
    selected = set(params.sources)
    sources = [document.objects[key] for key in owner.features if key in selected]
    reference = resolve_reference(document, params.reference) if feature.operation is Operation.MIRROR else resolve_axis_reference(document, params.reference)
    transforms = occurrence_transforms(feature.operation, params, reference)
    cached = {}

    def source_effects(source):
        if not source.enabled:
            from cadastrophe.core.feature_model import InactiveReferenceError
            raise InactiveReferenceError("La fonction source est désactivée : " + source.name, (source.id,))
        if source.id in cached:
            return cached[source.id]
        state = state_for(document, BodyStateRef(owner.id, source.id))
        effects = state.effects
        if not effects:
            index = owner.features.index(source.id)
            before = state_for(document, BodyStateRef(owner.id, owner.features[index-1] if index else None)).shape if index or owner.source_kind else None
            source_trace = generator_trace(service, document, source)
            if isinstance(source, ReplayFeature):
                _, effects = replay(service, document, source, before, source_trace)
            else:
                effects = generating_volumes(service, document, source, before, source_trace)
        if isinstance(source, ReplayFeature):
            # A pattern represents its complete motif, including its seed, but
            # never the surrounding body. Shared ancestors are included once.
            seeds = set(source.parameters.sources)
            effects = (*tuple(e for key in owner.features if key in seeds
                              for e in source_effects(document.objects[key])), *effects)
        cached[source.id] = tuple({e.key: e for e in effects}.values())
        return cached[source.id]

    effects = tuple({e.key: e for source in sources for e in source_effects(source)}.values())
    shape = previous
    generated = []
    for index, transform in enumerate(transforms, 1):
        for effect in effects:
            source = document.objects[effect.generator_id]
            current = effect
            placement = transform if effect.placement is None else transform.Multiplied(effect.placement)
            if source.target_dependent_effect:
                # Through-all remains directional and parametric for each copy:
                # bound the current target in the source coordinate system.
                target = BRepBuilderAPI_Transform(shape, placement.Inverted(), True).Shape()
                source_trace = generator_trace(service, document, source)
                if isinstance(source, HoleFeature):
                    from .hole_geometry import generating_holes
                    # Rebuild only this position. Earlier occurrences may have
                    # already removed material at another position of the seed.
                    current, = generating_holes(service, document, source, target, source_trace,
                                                position_id=effect.key[-1])
                else:
                    base = generating_volumes(service, document, source, target, source_trace)
                    current = base[int(effect.key[-1])]
            # A changing inner count must not rename another generator's face
            # by shifting its position in this flattened list.
            occurrence = f"{index}:" + sha256(repr(effect.key).encode("utf-8")).hexdigest()
            transformed = transform_effect(current, placement if source.target_dependent_effect else transform,
                                           trace, occurrence)
            if source.target_dependent_effect:
                transformed = replace(transformed, key=(feature.id, occurrence, *effect.key))
            try:
                shape = apply_effects(shape, (transformed,), trace=trace, merge_result=params.merge_result)
            except (ValueError, RuntimeError) as exc:
                raise ValueError(f"Occurrence {index+1}, {source.name} : {exc}") from exc
            generated.append(transformed)
    return shape, tuple(generated)
