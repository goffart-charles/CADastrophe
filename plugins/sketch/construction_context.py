"""Resolve sketch placement/projections at their exact producing history states.

Resolved objects are detached derived data; the document's local recipe remains
unchanged. Missing/ambiguous names never fall back to the body's final B-Rep.
"""
from dataclasses import replace

from cadastrophe.core.body_history import result_at
from cadastrophe.core.feature_objects import ConstructionFeature
from cadastrophe.core.feature_model import ResultStatus


def _check_legacy_activation(obj, document):
    from cadastrophe.core.reference_geometry import inactive_ancestors
    from cadastrophe.core.feature_model import BodyStateRef, InactiveReferenceError
    for key in obj.body_dependencies:
        body = document.objects.get(key)
        refs = tuple(source.supports for f in document.objects.values() if isinstance(f, ConstructionFeature)
                     for source, _ in f.profile_sections if source.sketch_id == obj.id)
        producer = next((ref for context in refs for ref in context if ref.body_id == key), None)
        if producer is None and getattr(body, "features", ()):
            producer = BodyStateRef(key, body.features[-1])
        roots = inactive_ancestors(document, producer) if producer is not None else ()
        if roots:
            raise InactiveReferenceError("Ancien support après désactivation : réassociez son support et ses projections.", roots)


def _legacy_support_objects(obj, document, support_shapes=None):
    _check_legacy_activation(obj, document)
    objects = document.objects
    if support_shapes is None:
        contexts = {source.supports for feature in objects.values() if isinstance(feature, ConstructionFeature)
                    for source, _ in feature.profile_sections if source.sketch_id == obj.id}
        if len(contexts) > 1:
            raise ValueError("Support ambigu : l’esquisse est consommée à plusieurs états producteurs.")
        if not contexts or not obj.body_dependencies:
            return objects
        support_shapes = {}
        for ref in contexts.pop():
            state = result_at(document, ref)
            if state.status is not ResultStatus.READY:
                raise ValueError("Support introuvable : état producteur à reconstruire ou en erreur.")
            support_shapes[ref.body_id] = state.shape
    if set(support_shapes) != set(obj.body_dependencies):
        raise ValueError("Les états des corps supports doivent être explicites et complets.")
    objects = dict(objects)
    for key, shape in support_shapes.items():
        revisions = {e.revision for e in obj.sketch.external if e.body_id == key}
        support = obj.sketch.support
        if support.face_index is not None and support.plane_id == key:
            revisions.add(support.revision)
        if len(revisions) != 1 or key not in objects:
            raise ValueError("Références de support incohérentes.")
        objects[key] = replace(objects[key], shape=shape, revision=next(iter(revisions)))
        if support.face_index is not None and support.plane_id == key:
            # The legacy support stores an index and an exact plane frame, not
            # a general topological name. Coplanar split faces cannot be proved
            # to be the original face: explicitly refuse that ambiguity.
            from .supports import face_map, face_frame
            faces, matches = face_map(shape), 0
            for index in range(1, faces.Size()+1):
                try:
                    frame = face_frame(faces.FindKey(index), support.frame.name)
                except ValueError:
                    continue
                normal = support.frame.normal
                if ((frame.normal-normal).norm() <= 1e-10
                        and abs((frame.origin-support.frame.origin).dot(normal)) <= 1e-7):
                    matches += 1
            if matches > 1:
                raise ValueError("Support ambigu : plusieurs faces coplanaires dans l’état producteur ; réassociation nécessaire.")
    return objects


def input_states(obj, document, support_shapes=None):
    from cadastrophe.core.feature_model import BodyStateRef, EvaluationResult
    if support_shapes is not None and set(support_shapes) != set(obj.body_dependencies):
        raise ValueError("Les états des corps supports doivent être explicites et complets.")
    contexts = {source.supports for f in document.objects.values() if isinstance(f, ConstructionFeature)
                for source, _ in f.profile_sections if source.sketch_id == obj.id}
    if len(contexts) > 1:
        raise ValueError("Support ambigu : plusieurs états producteurs pour cette esquisse.")
    refs = next(iter(contexts)) if contexts else obj.producer_refs
    if len({r.body_id for r in refs}) != len(refs):
        raise ValueError("Support ambigu : plusieurs états d’un même corps.")
    states = {}
    for ref in refs:
        provided = getattr(document, "rebuild_states", {})
        if ref in provided:
            state = provided[ref]
        elif support_shapes is not None and ref.body_id in support_shapes:
            state = result_at(document, ref)
            shape = support_shapes[ref.body_id]
            if state.shape is None or not state.shape.IsSame(shape):
                state = EvaluationResult(ResultStatus.READY, shape)
        else:
            state = result_at(document, ref)
        if state.status is not ResultStatus.READY:
            raise ValueError("Support non reconstruit : " + state.message)
        states[ref.body_id] = state
    return states


def support_objects(obj, document, support_shapes=None):
    if not obj.producer_refs:
        return _legacy_support_objects(obj, document, support_shapes)
    objects = dict(document.objects)
    for key, state in input_states(obj, document, support_shapes).items():
        revisions = {e.revision for e in obj.sketch.external if e.body_id == key}
        if obj.sketch.support.plane_id == key:
            revisions.add(obj.sketch.support.revision)
        objects[key] = replace(objects[key], shape=state.shape,
                               revision=next(iter(revisions)) if revisions else objects[key].revision)
    return objects


def attached_frame(frame, anchor, native):
    """Transport a user frame by its proven attachment, without reprojection."""
    if anchor == native:
        return frame
    def direction(value):
        return native.u*value.dot(anchor.u)+native.v*value.dot(anchor.v)+native.normal*value.dot(anchor.normal)
    from cadastrophe.geometry import WorkPlane
    return WorkPlane(frame.name, native.to_world(*anchor.to_local(frame.origin)), direction(frame.u), direction(frame.v))


def resolve_object(obj, document, support_shapes=None):
    """Derived placement/projections; preserve the persisted local sketch recipe."""
    from cadastrophe.core.reference_objects import DatumPlaneObject
    from cadastrophe.document import ObjectType
    target = document.objects.get(obj.sketch.support.plane_id)
    if target is not None and target.kind is ObjectType.REFERENCE_PLANE and (isinstance(target, DatumPlaneObject) or obj.sketch.support.face_index is None and obj.sketch.support.attachment is not None):
        from cadastrophe.core.reference_geometry import resolve_plane
        native = resolve_plane(document, target)
        old = obj.sketch.support
        frame = attached_frame(old.frame, old.attachment, native) if old.attachment is not None else native
        support = replace(old, frame=frame, revision=target.revision,
                          attachment=native if old.attachment is not None else None)
        obj = replace(obj, sketch=replace(obj.sketch, support=support))
    if not obj.producer_refs:
        if obj.body_dependencies:
            # Preserve typed dependency failures before profile analysis turns
            # ordinary validation messages into display diagnostics.
            _check_legacy_activation(obj, document)
        return obj  # Legacy inputs retain their strict revision/frame checks.
    from cadastrophe.core.reference_geometry import resolve_topology
    from cadastrophe.core.feature_model import BodyStateRef
    from .supports import face_frame, face_map
    from .external import project, shape_map
    states = input_states(obj, document, support_shapes)
    sketch, support = obj.sketch, obj.sketch.support
    from cadastrophe.core.reference_geometry import inactive_ancestors
    from cadastrophe.core.feature_model import InactiveReferenceError
    legacy = ((BodyStateRef(support.plane_id, support.producer_id),) if support.face_index is not None and support.reference is None else ())
    legacy += tuple(BodyStateRef(item.body_id, item.producer_id) for item in sketch.external if item.reference is None)
    roots = tuple(key for ref in legacy for key in inactive_ancestors(document, ref))
    if roots:
        raise InactiveReferenceError("Ancien support après désactivation : réassociez son support et ses projections.", roots)
    if support.reference is not None:
        state = states.get(support.plane_id)
        if state is None:
            raise ValueError("Face support perdue : producteur absent de la recette.")
        face = resolve_topology(document, BodyStateRef(support.plane_id, support.producer_id), support.reference, state=state)
        native = face_frame(face, support.frame.name, natural=False)
        if support.attachment == native:
            # An unchanged attachment is an exact identity. Repeated dot
            # products drift oblique axes and invalidate the active editor.
            frame = support.frame
        elif support.attachment is not None:
            frame = attached_frame(support.frame, support.attachment, native)
        else:
            # Legacy native or natural frames can be proved on unchanged faces.
            old = support.frame
            frame = native if (old.u-native.u).norm() < 1e-10 and (old.v-native.v).norm() < 1e-10 else face_frame(face, old.name)
        support = replace(support, frame=frame, attachment=native, face_index=face_map(state.shape).FindIndex(face))
    external = []
    for item in sketch.external:
        if item.reference is None:
            external.append(item)
            continue
        state = states.get(item.body_id)
        if state is None:
            raise ValueError("Référence externe perdue : producteur absent de la recette.")
        part = resolve_topology(document, BodyStateRef(item.body_id, item.producer_id), item.reference, state=state)
        geometry = replace(project(part, item.topology, support.frame), id=item.geometry.id)
        external.append(replace(item, geometry=geometry, index=shape_map(state.shape, item.topology).FindIndex(part)))
    fresh = replace(sketch, support=support, external=tuple(external))
    if fresh.external != sketch.external and fresh.constraints:
        from .solver import solve
        result = solve(fresh)
        if not result.success:
            raise ValueError("Relations de références externes impossibles : "+result.message)
        fresh = result.sketch
    return replace(obj, sketch=fresh)


def freeze_legacy_references(document):
    """Loader migration only: name legacy selections after verifying old geometry.

    Never infer a moved legacy face from its index. A selection that cannot be
    proved against its frozen frame/projection remains explicitly unresolved.
    """
    from cadastrophe.core.feature_model import BodyStateRef
    from cadastrophe.core.reference_geometry import inactive_ancestors
    from cadastrophe.core.feature_objects import BodyObject
    from cadastrophe.core.native_naming import capture, canonical_reference
    from .objects import SketchObject
    from .supports import face_map, face_support_status
    from .external import shape_map, project
    from .solver import parameters
    changed = False
    for obj in tuple(document.objects.values()):
        if not isinstance(obj, SketchObject):
            continue
        consumers = {source.supports for f in document.objects.values() if isinstance(f, ConstructionFeature)
                     for source, _ in f.profile_sections if source.sketch_id == obj.id}
        if len(consumers) > 1:
            continue
        refs = {ref.body_id: ref for ref in next(iter(consumers), ())}
        for key in obj.body_dependencies:
            body = document.objects.get(key)
            if key not in refs and body is not None:
                refs[key] = BodyStateRef(key, body.features[-1] if isinstance(body, BodyObject) and body.features else None)
        sketch, support, external = obj.sketch, obj.sketch.support, []
        if support.reference is not None:
            try:
                state = result_at(document, BodyStateRef(support.plane_id, support.producer_id))
                if state.status is ResultStatus.READY:
                    support = replace(support, reference=canonical_reference(state.shape, support.reference, state.topology))
            except (ValueError, RuntimeError):
                pass  # An unproven name must remain unresolved, never guessed.
        elif support.face_index is not None:
            try:
                ref = refs[support.plane_id]
                if inactive_ancestors(document, ref):
                    raise ValueError("Un état désactivé ne prouve pas une ancienne sélection par indice.")
                state = result_at(document, ref)
                body = replace(document.objects[support.plane_id], shape=state.shape, revision=support.revision)
                if state.status is ResultStatus.READY and not face_support_status(support, body):
                    face = face_map(state.shape).FindKey(support.face_index)
                    from .supports import face_frame
                    support = replace(support, producer_id=ref.producer_id,
                                      attachment=face_frame(face, support.frame.name, natural=False),
                                      reference=capture(state.shape, face, "Face", state.topology))
            except (ValueError, RuntimeError, KeyError):
                pass
        for item in sketch.external:
            if item.reference is not None:
                try:
                    state = result_at(document, BodyStateRef(item.body_id, item.producer_id))
                    if state.status is ResultStatus.READY:
                        item = replace(item, reference=canonical_reference(state.shape, item.reference, state.topology))
                except (ValueError, RuntimeError):
                    pass
            else:
                try:
                    ref = refs[item.body_id]
                    if inactive_ancestors(document, ref):
                        raise ValueError("Un état désactivé ne prouve pas une ancienne projection par indice.")
                    state = result_at(document, ref)
                    shapes = shape_map(state.shape, item.topology)
                    if item.index > shapes.Size():
                        raise ValueError("Sous-forme absente")
                    part = shapes.FindKey(item.index)
                    geometry = project(part, item.topology, support.frame)
                    actual, frozen = parameters(geometry), parameters(item.geometry)
                    if type(geometry) is type(item.geometry) and len(actual) == len(frozen) and all(abs(a-b) <= 1e-7 for a, b in zip(actual, frozen)):
                        item = replace(item, producer_id=ref.producer_id,
                                       reference=capture(state.shape, part, item.topology, state.topology))
                except (ValueError, RuntimeError, KeyError, TypeError):
                    pass
            external.append(item)
        fresh = replace(obj, sketch=replace(sketch, support=support, external=tuple(external)))
        if fresh != obj:
            document._objects[obj.id] = fresh
            changed = True
    return changed
