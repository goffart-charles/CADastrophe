"""Validation, exact-prefix reconstruction and publication on the Document graph.

Adapters validate native results before returning READY. Failed prefixes carry
diagnostics, block their dependents and never retain an old published shape.
"""
from dataclasses import fields, replace
from types import MappingProxyType

from .feature_model import BodyStateRef, Operation, EvaluationResult, ResultStatus, EvaluationDiagnostic
from .feature_objects import BodyObject, ExtrusionFeature, SketchSourceObject, ConstructionFeature, EdgeFeature, ShellFeature, DraftFeature


def _failure_roots(failures):
    roots = []
    for failure in failures:
        causes = [key for diagnostic in failure.diagnostics
                  for key in (diagnostic.root_ids or (diagnostic.feature_id,))]
        roots.extend(causes or ([failure.feature_id] if failure.feature_id else []))
    return tuple(dict.fromkeys(roots))


def _inactive_result(result, objects):
    roots = _failure_roots((result,))
    return (result.status in (ResultStatus.UNAVAILABLE, ResultStatus.BLOCKED) and bool(roots)
            and all(isinstance(objects.get(key), ConstructionFeature) and not objects[key].enabled for key in roots))


def validate_graph(objects):
    objects = dict(objects)
    from .reference_graph import validate_datums
    from .construction_state import feature_dependencies
    validate_datums(objects)
    bodies = {key: obj for key, obj in objects.items() if isinstance(obj, BodyObject)}
    functions = {key: obj for key, obj in objects.items() if isinstance(obj, ConstructionFeature)}
    claimed, edges = set(), {}
    for body in bodies.values():
        if (body.source_kind is None) != (body.source_shape is None):
            raise ValueError("La base statique exige sa B-Rep source indépendante.")
        if not body.features and body.source_kind is None:
            raise ValueError("Un corps exige une base statique ou une extrusion initiale.")
        previous = None
        for index, key in enumerate(body.features):
            feature = functions.get(key)
            if feature is None or feature.body_id != body.id or key in claimed:
                raise ValueError("Ordre, propriétaire ou référence de fonction invalide.")
            claimed.add(key)
            first_extrusion = index == 0 and body.source_kind is None
            if (feature.operation is Operation.NEW_BODY) != first_extrusion:
                raise ValueError("new_body doit être la base initiale d'un nouveau corps.")
            for source, contours in feature.profile_sections:
                sketch = objects.get(source.sketch_id)
                if not isinstance(sketch, SketchSourceObject):
                    raise ValueError("La source ne désigne pas une esquisse existante.")
                # A missing contour is a repairable geometric reference, not
                # a corrupt dependency graph. The native evaluator reports it.
                if {ref.body_id for ref in source.supports} != set(sketch.body_dependencies):
                    raise ValueError("Les états producteurs des supports de l'esquisse doivent être explicites.")
                inputs = {ref.body_id: ref for ref in source.supports}
                if any(inputs.get(ref.body_id) != ref for ref in sketch.producer_refs):
                    raise ValueError("Le producteur enregistré sur l’esquisse doit être conservé dans la fonction.")
            inputs = feature_dependencies(objects, feature)
            deps = {inputs.predecessor.producer_id} if inputs.predecessor is not None and inputs.predecessor.producer_id is not None else set()
            if isinstance(feature, (ShellFeature, DraftFeature)) and feature.input_state != BodyStateRef(body.id, previous):
                raise ValueError("La fonction doit référencer l’état immédiatement précédent de son corps.")
            for ref in inputs.evaluation_states:
                target = bodies.get(ref.body_id)
                if target is None:
                    from ..document import ObjectType
                    static = objects.get(ref.body_id)
                    if (static is not None and static.kind is ObjectType.BODY
                            and static.shape is not None and ref.producer_id is None):
                        continue  # Existing primitive/import: explicit static producer.
                    raise ValueError("Corps de support absent ou non paramétrique.")
                if ref.producer_id is None:
                    if target.source_kind is None:
                        raise ValueError("Le support ne possède pas de base statique.")
                else:
                    if ref.producer_id not in target.features:
                        raise ValueError("Fonction productrice de support absente de ce corps.")
                    if target.id == body.id and target.features.index(ref.producer_id) >= index:
                        raise ValueError("Une fonction ne peut pas référencer un résultat futur.")
                    deps.add(ref.producer_id)
            edges[key] = deps
            previous = key
    if claimed != set(functions):
        raise ValueError("Fonction orpheline ou corps cible absent.")
    # Kahn's algorithm avoids recursion depth limits on long histories.
    followers = {key: set() for key in edges}
    for key, deps in edges.items():
        for dep in deps:
            followers[dep].add(key)
    pending = {key: len(deps) for key, deps in edges.items()}
    ready = [key for key, count in pending.items() if count == 0]
    count = 0
    while ready:
        key = ready.pop()
        count += 1
        for child in followers[key]:
            pending[child] -= 1
            if pending[child] == 0:
                ready.append(child)
    if count != len(edges):
        raise ValueError("Dépendance circulaire entre les fonctions et leurs esquisses.")
    return edges


def affected_bodies(objects, changed_ids):
    """Conservative transitive invalidation, including undo and redo."""
    changed = set(changed_ids)
    affected = set()
    progress = True
    while progress:
        progress = False
        for obj in objects.values():
            if not isinstance(obj, BodyObject) or obj.id in affected:
                continue
            dependencies = {obj.id, *obj.features}
            for key in obj.features:
                feature = objects.get(key)
                if isinstance(feature, ConstructionFeature):
                    dependencies.add(feature.sketch_id)
                    dependencies.update(ref.body_id for ref in feature.support_refs)
                    sketch = objects.get(feature.sketch_id)
                    if isinstance(sketch, SketchSourceObject):
                        dependencies.update(sketch.document_dependencies)
                    from .reference_graph import feature_inputs
                    try:
                        dependencies.update(feature_inputs(objects, feature)[2])
                    except ValueError:
                        pass  # Undo may briefly restore a partial graph.
            if dependencies & changed:
                affected.add(obj.id)
                changed.add(obj.id)
                progress = True
    return affected


def rollback_hidden_ids(objects, feature):
    """Bodies and dependent sketch presentations beyond an edited history step."""
    affected = affected_bodies(objects, (feature.id,))
    body = objects[feature.body_id]
    future = set(body.features[body.features.index(feature.id):])
    sketches = []
    for obj in objects.values():
        if not isinstance(obj, SketchSourceObject):
            continue
        if obj.producer_refs:
            hidden = any(ref.body_id in affected and
                         (ref.body_id != body.id or ref.producer_id in future) for ref in obj.producer_refs)
        else:
            hidden = bool(set(obj.body_dependencies) & affected)
        if hidden:
            sketches.append(obj.id)
    return (*affected, *sketches)


def sketch_history_display(document, sketch_id):
    """Display prefixes before the edited sketch's first consumers.

    This is transient viewport state; recipes, results and undo stay untouched.
    """
    objects, affected = document.objects, set()
    edges = validate_graph(objects)
    for key in edges:
        if any(source.sketch_id == sketch_id for source, _ in objects[key].profile_sections):
            affected.add(key)
    pending = True
    while pending:
        additions = {key for key, deps in edges.items() if key not in affected and deps & affected}
        pending = bool(additions)
        affected.update(additions)
    states = {}
    for body in objects.values():
        if not isinstance(body, BodyObject):
            continue
        index = next((i for i, key in enumerate(body.features) if key in affected), None)
        if index is not None:
            states[body.id] = (BodyStateRef(body.id, body.features[index-1]) if index else
                               BodyStateRef(body.id) if body.source_shape is not None else None)
    hidden = tuple(obj.id for obj in objects.values()
                   if isinstance(obj, SketchSourceObject) and obj.id != sketch_id
                   and (any(ref.producer_id in affected for ref in obj.producer_refs)
                        or not obj.producer_refs and bool(set(obj.body_dependencies) & set(states))))
    return states, hidden


def without_features(objects, object_ids):
    """Remove operations and explicit consumers, retaining every source sketch.

    Later operations on the surviving body are replayed against their new
    predecessor. A removed initial base also removes its now baseless body.
    """
    removed = set(object_ids)
    while True:
        before = set(removed)
        for obj in objects.values():
            if isinstance(obj, BodyObject):
                if (obj.id in removed or obj.source_shape is None and obj.features
                        and obj.features[0] in removed):
                    removed.add(obj.id)
                    removed.update(obj.features)
            elif isinstance(obj, ConstructionFeature):
                from .reference_graph import feature_inputs
                refs, _, inputs = feature_inputs(objects, obj)
                if obj.body_id in removed or bool(inputs & removed) or any(ref.body_id in removed or ref.producer_id in removed
                                                 for ref in refs):
                    removed.add(obj.id)
            else:
                from .reference_objects import DatumPlaneObject
                from .curve_model import CurveObject
                from .reference_graph import reference_inputs
                if isinstance(obj, (DatumPlaneObject, CurveObject)):
                    refs, _, inputs = reference_inputs(objects, obj.geometry_references)
                    if inputs & removed or any(r.producer_id in removed for r in refs):
                        removed.add(obj.id)
        if before == removed:
            break
    candidate = {}
    sources = {source.sketch_id for key, obj in objects.items()
               if key in removed and isinstance(obj, ConstructionFeature) for source, _ in obj.profile_sections}
    used = {source.sketch_id for key, obj in objects.items()
            if key not in removed and isinstance(obj, ConstructionFeature) for source, _ in obj.profile_sections}
    for key, obj in objects.items():
        if key in removed:
            continue
        if isinstance(obj, BodyObject):
            obj = replace(obj, features=tuple(fid for fid in obj.features if fid not in removed))
        elif isinstance(obj, SketchSourceObject) and key in sources-used:
            obj = replace(obj, visible=True)
        candidate[key] = obj
    validate_graph(candidate)
    return candidate


def invalidate(document, changed_ids, *, notify=True):
    affected = affected_bodies(document.objects, changed_ids)
    from .prefix_signatures import state_signatures
    try:
        signatures = state_signatures(document.objects)
    except ValueError:
        # ClearDocument undo can temporarily insert a partial graph. No prefix
        # is trusted until the complete graph is restored and evaluated.
        signatures = {}
    document._body_evaluation_cache = {ref: entry for ref, entry in document._body_evaluation_cache.items()
                                       if ref in signatures}
    document._body_prefix_signatures = {ref: signature for ref, signature in document._body_prefix_signatures.items()
                                        if ref in signatures}
    # Body UUIDs are conservative dependencies; consumers use exact prefixes.
    # An edit after a consumed prefix must not hide an unchanged consumer.
    unchanged = {key for key in affected if key in document._body_results
                 and (ref := BodyStateRef(key, document.objects[key].features[-1]
                                          if document.objects[key].features else None)) in signatures
                 and document._body_prefixes.get(key, {}).get(ref) is document._body_results[key]
                 and signatures[ref] == document._body_prefix_signatures.get(ref)}
    changed = []
    for key in document.objects.keys() & unchanged:
        body = document.objects[key]
        shape = document._body_results[key].shape
        if body.shape is not shape:
            document._objects[key] = replace(body, shape=shape)
            changed.append(key)
    affected -= unchanged
    for key in affected:
        document._body_results.pop(key, None)
        document._body_prefixes[key] = {
            ref: result for ref, result in document._body_prefixes.get(key, {}).items()
            if signatures.get(ref) is not None and signatures[ref] == document._body_prefix_signatures.get(ref)}
        obj = document.objects[key]
        shape = None
        if not obj.features and obj.source_shape is not None:
            shape = obj.source_shape
            document._body_results[key] = EvaluationResult(ResultStatus.READY, shape)
            document._body_last_valid_results[key] = document._body_results[key]
        if obj.shape is not shape:
            document._objects[key] = replace(obj, shape=shape, revision=obj.revision + 1)
            changed.append(key)
    if changed:
        document.select_objects(i for i in document.selected_ids if i not in changed)
    if notify:
        for key in changed:
            document.object_changed.emit(key, ("shape", "revision"))
        for key in affected:
            document.body_result_changed.emit(key)
    return changed, affected


def body_result(document, body_id):
    if not isinstance(document.objects[body_id], BodyObject):
        raise ValueError("Un corps de construction est requis.")
    return document._body_results.get(body_id, EvaluationResult(ResultStatus.DIRTY, message="Corps à évaluer."))


def last_valid_result(document, body_id):
    """Diagnostic snapshot only, never a certificate for the current recipes.

    It is not serialized, displayed automatically, or used as a rebuild fallback.
    """
    if not isinstance(document.objects[body_id], BodyObject):
        raise ValueError("Un corps de construction est requis.")
    return document._body_last_valid_results.get(body_id)


def result_at(document, ref):
    body = document.objects.get(ref.body_id)
    if not isinstance(body, BodyObject):
        from ..document import ObjectType
        if body is not None and body.kind is ObjectType.BODY and body.shape is not None and ref.producer_id is None:
            return EvaluationResult(ResultStatus.READY, body.shape)
        raise ValueError("Corps producteur absent.")
    if ref.producer_id is None:
        if body.source_shape is None:
            raise ValueError("Ce corps ne possède pas de base statique.")
        return EvaluationResult(ResultStatus.READY, body.source_shape)
    if ref.producer_id not in body.features:
        raise ValueError("Fonction productrice absente de ce corps.")
    return document._body_prefixes.get(body.id, {}).get(ref, EvaluationResult(ResultStatus.DIRTY, message="État producteur à évaluer."))


def prefix_results(document, body_id):
    """Read-only validated intermediate results, for atomic history publication."""
    if not isinstance(document.objects.get(body_id), BodyObject):
        raise ValueError("Un corps de construction est requis.")
    return MappingProxyType(dict(document._body_prefixes.get(body_id, {})))


def evaluate_body(document, body_id, adapter=None, *, reuse=False):
    from .computation import complete
    return complete(evaluate_body_steps(document, body_id, adapter, reuse=reuse))


def evaluate_body_steps(document, body_id, adapter=None, *, reuse=False):
    from .computation import ReservedComputation
    return ReservedComputation(document, _evaluate_body_steps(document, body_id, adapter, reuse=reuse))


def preview_feature(document, feature, adapter_factory, *, at_feature=False, strict=True):
    """Evaluate an edited recipe and its dependents without touching live state.

    The ordinary Document and evaluator are reused as a detached calculation
    context. No document manager, editor, renderer or undo command sees it.
    """
    previous = document.objects.get(feature.id)
    if not isinstance(previous, ConstructionFeature) or not isinstance(feature, ConstructionFeature):
        raise ValueError("Une fonction de construction existante est requise.")
    if (feature.body_id, feature.operation, feature.recipe_source) != (previous.body_id, previous.operation, previous.recipe_source):
        raise ValueError("L’édition doit conserver le propriétaire, l’opération et la source.")
    snapshot = dict(document.objects)
    candidate = {**snapshot, feature.id: feature}
    validate_graph(candidate)
    affected = affected_bodies(candidate, (feature.id,))
    return preview_construction(document, candidate, affected, adapter_factory,
                                stop_at=feature.id if at_feature else None, strict=strict)


def preview_construction(document, candidate, affected, adapter_factory, *, stop_at=None, strict=True, resolved_objects=None, materialized=False):
    """Calculate candidate recipes using the ordinary graph on a detached Document."""
    from ..document import Document
    snapshot = dict(document.objects)
    from .history_position import insertion_candidate
    candidate = insertion_candidate(document, candidate)
    variables = document.variables
    if not materialized and (len(document.configurations) > 1 or document.configuration_state.common_values):
        from .configurations import stage_edits
        from .parameters import prepare
        candidate, variables, _ = stage_edits(document, candidate, variables, document.parameter_scope)
        candidate, variables, _ = prepare(candidate, variables)
    validate_graph(candidate)
    detached = Document(document.name)
    try:
        detached._objects.update(candidate)
        detached._variables = variables
        detached._body_prefixes = {key: dict(states) for key, states in document._body_prefixes.items()}
        detached._body_prefix_signatures = dict(document._body_prefix_signatures)
        detached._body_evaluation_cache = dict(document._body_evaluation_cache)
        adapter = adapter_factory(detached)
        results, prefixes = {}, {}
        for key in candidate:
            if key not in affected:
                continue
            if stop_at is not None and stop_at not in candidate[key].features:
                continue
            result = _evaluate_body(detached, key, adapter, preserve_revision=True, reuse=True,
                                    stop_at=stop_at)
            if strict and result.status is not ResultStatus.READY and not _inactive_result(result, candidate):
                raise ValueError(f"{candidate[key].name} : {result.message}")
            results[key] = result
            prefixes[key] = dict(prefix_results(detached, key))
        if resolved_objects is not None:
            # Equation transactions also publish derived datum frames. Resolve
            # only datums whose recipes or referenced producers changed, using
            # the same reference resolver as ordinary plane tools/rebuilds.
            from .reference_objects import DatumPlaneObject
            from .reference_geometry import resolve_plane, plane_shape
            sources = set(affected) | {key for key, obj in candidate.items()
                                      if obj != snapshot.get(key)}
            datums = {key: obj for key, obj in candidate.items() if isinstance(obj, DatumPlaneObject)}
            pending = True
            while pending:
                additions = {key for key, obj in datums.items() if key not in sources and
                             any(ref.object_id in sources for ref in obj.parameters.references)}
                pending = bool(additions)
                sources.update(additions)
            for key in datums.keys() & sources:
                obj = datums[key]
                try:
                    plane = resolve_plane(detached, obj)
                except (ValueError, RuntimeError) as exc:
                    from .feature_model import InactiveReferenceError
                    if strict and not isinstance(exc, InactiveReferenceError):
                        raise
                    resolved_objects[key] = replace(obj, reference_error_message=str(exc))
                    continue
                if plane != obj.plane or obj.shape is None or obj.reference_error_message:
                    resolved_objects[key] = replace(obj, plane=plane, shape=plane_shape(plane), reference_error_message="")
        if set(document.objects) != set(snapshot) or any(document.objects[key] is not obj for key, obj in snapshot.items()):
            raise ValueError("Le document a changé pendant l’aperçu ; résultat abandonné.")
        return results, prefixes
    finally:
        detached.deleteLater()


def _evaluate_body(document, body_id, adapter, *, preserve_revision=False, reuse=False, stop_at=None):
    from .computation import complete
    return complete(_evaluate_body_steps(document, body_id, adapter, preserve_revision=preserve_revision,
                                         reuse=reuse, stop_at=stop_at))


def _evaluate_body_steps(document, body_id, adapter, *, preserve_revision=False, reuse=False, stop_at=None):
    """Evaluate detached prefixes; publish a final shape only on total success.

    adapter.evaluate(feature, sketch, previous_shape, read_only_support_shapes)
    returns EvaluationResult. No fallback to any previous final B-Rep.
    """
    objects = dict(document.objects)
    validate_graph(objects)
    body = objects[body_id]
    if not isinstance(body, BodyObject):
        raise ValueError("Un corps de construction est requis.")
    from .prefix_signatures import state_signatures
    signatures = state_signatures(objects)
    token = getattr(adapter, "cache_key", adapter)
    memo = {ref: result for ref, (owner, signature, result) in document._body_evaluation_cache.items()
            if reuse and adapter is not None and owner is token and signatures.get(ref) == signature
            and result.status is ResultStatus.READY}
    accepts = getattr(adapter, "accept_cached_result", None)
    if reuse and callable(accepts):
        for states in document._body_prefixes.values():
            for ref, state in states.items():
                if (ref not in memo and state.status is ResultStatus.READY
                        and signatures.get(ref) == document._body_prefix_signatures.get(ref)
                        and accepts(state)):
                    memo[ref] = state

    positions = {key: index for obj in objects.values() if isinstance(obj, BodyObject)
                 for index, key in enumerate(obj.features)}
    inactive_ids = {obj.id for obj in objects.values() if isinstance(obj, ConstructionFeature) and not obj.enabled}
    if stop_at is not None and stop_at not in body.features:
        raise ValueError("Fonction absente de l’historique du corps.")
    root = BodyStateRef(body_id, stop_at if stop_at is not None else body.features[-1] if body.features else None)
    pending = [root]
    resolved = set()
    while pending:
        ref = pending[-1]
        if ref in resolved:
            pending.pop()
            continue
        target = objects[ref.body_id]
        if ref.producer_id is None:
            result = EvaluationResult(ResultStatus.READY, target.source_shape if isinstance(target, BodyObject) else target.shape)
        else:
            feature = objects[ref.producer_id]
            from .construction_state import feature_dependencies
            inputs = feature_dependencies(objects, feature)
            previous_ref = inputs.predecessor
            effective_refs = inputs.evaluation_states if feature.enabled else ()
            dependencies = (() if previous_ref is None else (previous_ref,)) + effective_refs
            missing = tuple(dict.fromkeys(dep for dep in dependencies if dep not in resolved))
            if missing:
                pending.extend(reversed(missing))
                continue
            previous = memo.get(previous_ref)
            supports = {dep: memo[dep] for dep in effective_refs}
            failures = tuple(r for r in (previous, *supports.values()) if r is not None and r.status is not ResultStatus.READY)
            failed = failures[0] if failures else None
            if failed is not None:
                name = objects[failed.feature_id].name if failed.feature_id in objects else "producteur"
                message = f"Dépendance {name} bloquée : {failed.message[:500]}"
                result = EvaluationResult(ResultStatus.BLOCKED, message=message, feature_id=feature.id,
                    diagnostics=(EvaluationDiagnostic("dependency_blocked", message, feature.id, _failure_roots(failures)),))
            elif not feature.enabled:
                if previous is None:
                    message = f"{feature.name} est désactivée : le corps n'a pas de base disponible."
                    result = EvaluationResult(ResultStatus.UNAVAILABLE, message=message, feature_id=feature.id,
                        diagnostics=(EvaluationDiagnostic("disabled_without_base", message, feature.id, (feature.id,)),))
                else:
                    # READY describes the available prefix, not execution of
                    # this operation. Activation remains a separate recipe field.
                    result = EvaluationResult(ResultStatus.READY, previous.shape,
                                              feature_id=feature.id, topology=previous.topology)
            elif any(slot.path.startswith("parameters.sources.") and not objects[slot.state.producer_id].enabled
                     for slot in inputs.references):
                roots = tuple(dict.fromkeys(slot.state.producer_id for slot in inputs.references
                    if slot.path.startswith("parameters.sources.") and not objects[slot.state.producer_id].enabled))
                message = "Fonction source désactivée : " + ", ".join(objects[key].name for key in roots)
                result = EvaluationResult(ResultStatus.BLOCKED, message=message, feature_id=feature.id,
                    diagnostics=(EvaluationDiagnostic("inactive_source", message, feature.id, roots),))
            elif ref in memo:
                # A cached final result does not imply that all its source
                # prefixes are still cached (e.g. after an undo). Resolve those
                # dependencies too, so consumed sketches remain editable.
                result = memo[ref]
            elif adapter is None:
                result = EvaluationResult(ResultStatus.UNAVAILABLE, message="Service de reconstruction non installé.", feature_id=feature.id)
            else:
                try:
                    from .reference_geometry import resolve_topology
                    from .feature_model import InactiveReferenceError
                    for slot in inputs.references:
                        if (slot.topology is not None and slot.state is not None
                                and (slot.state.producer_id in inactive_ids or
                                     inactive_ids and not slot.topology.labels or
                                     any(key in label for key in inactive_ids for label in slot.topology.labels))):
                            try:
                                resolve_topology(document, slot.state, slot.topology, state=memo[slot.state])
                            except InactiveReferenceError as exc:
                                exc.reference = slot
                                raise
                    if isinstance(feature, EdgeFeature) and not feature.parameters.references:
                        before = objects[feature.body_id].features[:positions[feature.id]]
                        roots = tuple(key for key in before if key in inactive_ids)
                        if roots:
                            raise InactiveReferenceError("Ancienne sélection d'arêtes après désactivation : choisissez des arêtes nommées.", roots)
                    prepare = getattr(adapter, "prepare_feature", None)
                    if callable(prepare):
                        prepare(feature, previous, MappingProxyType(supports))
                    result = adapter.evaluate(feature, objects.get(feature.sketch_id),
                                              None if previous is None else previous.shape,
                                              MappingProxyType({dep: r.shape for dep, r in supports.items()}))
                    if not isinstance(result, EvaluationResult):
                        raise ValueError("Contrat de résultat du calcul invalide.")
                except Exception as exc:
                    message = f"Échec du calcul : {exc}"
                    reference = getattr(exc, "reference", None)
                    from .feature_model import ReferenceResolutionError
                    inactive = isinstance(exc, InactiveReferenceError)
                    reference = reference if isinstance(exc, (ReferenceResolutionError, InactiveReferenceError)) else None
                    result = EvaluationResult(ResultStatus.BLOCKED if inactive else ResultStatus.ERROR,
                        message=message, feature_id=feature.id,
                        diagnostics=(EvaluationDiagnostic("inactive_reference" if inactive else
                            "reference_invalid" if reference else "calculation_failed",
                            message, feature.id, exc.root_ids if inactive else (feature.id,), reference),))
            if result.status is not ResultStatus.READY and not result.diagnostics:
                code = "evaluator_unavailable" if result.status is ResultStatus.UNAVAILABLE else "calculation_failed"
                result = replace(result, feature_id=feature.id, diagnostics=(
                    EvaluationDiagnostic(code, result.message, feature.id, (feature.id,)),))
        memo[ref] = result
        resolved.add(ref)
        pending.pop()
        if ref.producer_id is not None:
            yield (sum(r.producer_id is not None for r in resolved), len(positions), objects[ref.producer_id].name)
    result = memo[root]
    # An adapter cannot publish a result computed from a changed document.
    if any(document.objects.get(key) is not obj for key, obj in objects.items()) or set(document.objects) != set(objects):
        raise RuntimeError("Le document a changé pendant l'évaluation ; résultat abandonné.")
    for ref, state in memo.items():
        if state.status is ResultStatus.READY and adapter is not None:
            document._body_evaluation_cache[ref] = (token, signatures[ref], state)
    document._body_prefix_signatures.update({ref: signatures[ref] for ref in memo})
    document._body_results[body_id] = result
    if result.status is ResultStatus.READY:
        document._body_last_valid_results[body_id] = result
    for key in {ref.body_id for ref in resolved}:
        document._body_prefixes.setdefault(key, {}).update({ref: memo[ref] for ref in resolved if ref.body_id == key})
    shape = result.shape
    if body.shape is not shape:
        document._objects[body_id] = replace(body, shape=shape, revision=body.revision + (0 if preserve_revision else 1))
        document.select_objects(key for key in document.selected_ids if key != body_id)
        document.object_changed.emit(body_id, ("shape", "revision"))
    document.body_result_changed.emit(body_id)
    return result


def result_signature(objects, body_id):
    """Semantic inputs of a cached result; appearance and replay revisions are irrelevant.

    Keep native sources by identity, logical immutable payloads by value. A redo
    may reuse a validated B-Rep only while all its actual inputs still match.
    """
    pending, seen, values = [body_id], set(), []
    cosmetic = {"name", "color", "visible", "transparency", "revision", "shape", "expressions", "material"}
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        obj = objects.get(key)
        if obj is None:
            values.append((str(key), None))
            continue
        from .reference_objects import DatumPlaneObject
        from .curve_model import CurveObject
        data = tuple((f.name, id(getattr(obj, f.name)) if f.name == "source_shape" else getattr(obj, f.name))
                     for f in fields(obj) if f.name not in cosmetic)
        if isinstance(obj, (DatumPlaneObject, CurveObject)):
            data = (("parameters", obj.parameters),)
        elif not isinstance(obj, (BodyObject, ConstructionFeature, SketchSourceObject)):
            data += (("native_shape", id(obj.shape)), ("revision", obj.revision))
        # Body revisions advance during atomic publication and undo. Their
        # immutable recipes/native sources, including explicit producer refs,
        # already describe their geometry; replay revisions are not inputs.
        values.append((str(key), type(obj), data))
        if isinstance(obj, BodyObject):
            pending.extend(obj.features)
        elif isinstance(obj, ConstructionFeature):
            pending.extend((obj.sketch_id,) if obj.sketch_id is not None else ())
            pending.extend(ref.body_id for ref in obj.support_refs)
            from .reference_graph import feature_inputs
            refs, _, inputs = feature_inputs(objects, obj)
            pending.extend(inputs)
            pending.extend(ref.body_id for ref in refs)
        elif isinstance(obj, SketchSourceObject):
            pending.extend(obj.document_dependencies)
        elif isinstance(obj, (DatumPlaneObject, CurveObject)):
            from .reference_graph import reference_inputs
            refs, _, inputs = reference_inputs(objects, obj.geometry_references)
            pending.extend(inputs)
            pending.extend(ref.body_id for ref in refs)
    return tuple(sorted(values, key=lambda item: item[0]))


def result_receipts(objects, results, prefixes=None):
    receipts = {}
    prefixes = prefixes or {}
    if set(prefixes) - set(results):
        raise ValueError("Les états intermédiaires exigent un résultat final validé.")
    for key, result in results.items():
        if not isinstance(objects.get(key), BodyObject) or not isinstance(result, EvaluationResult):
            raise ValueError("Un résultat calculé doit désigner un corps de construction.")
        body = objects[key]
        states = dict(prefixes.get(key, {}))
        if result.status is not ResultStatus.READY:
            required = {BodyStateRef(key, fid) for fid in body.features}
            if (result.feature_id not in body.features or not required <= set(states)
                    or result.status not in (ResultStatus.ERROR, ResultStatus.BLOCKED, ResultStatus.UNAVAILABLE)):
                raise ValueError("Un échec publié exige ses préfixes calculés et sa fonction responsable.")
        for ref, state in states.items():
            if (not isinstance(ref, BodyStateRef) or ref.body_id != key
                    or (ref.producer_id is None and body.source_shape is None)
                    or (ref.producer_id is not None and ref.producer_id not in body.features)
                    or not isinstance(state, EvaluationResult)):
                raise ValueError("État intermédiaire de construction invalide.")
        last = BodyStateRef(key, body.features[-1] if body.features else None)
        if last in states and states[last].shape is not result.shape:
            raise ValueError("L’état final ne correspond pas au résultat publié.")
        states[last] = result
        receipts[key] = result_signature(objects, key), result, states
    return receipts


def publish_result(document, body_id, result, *, notify=True, prefixes=None):
    """Publish a trusted evaluator result without changing the persisted recipe.

    No revision increment: restoring a derived cache must not invalidate face
    supports that were saved against this exact persisted body revision.
    """
    body = document.objects[body_id]
    if not isinstance(body, BodyObject) or not isinstance(result, EvaluationResult):
        raise ValueError("Résultat de corps invalide.")
    document._objects[body_id] = replace(body, shape=result.shape)
    document._body_results[body_id] = result
    if result.status is ResultStatus.READY:
        document._body_last_valid_results[body_id] = result
    ref = BodyStateRef(body_id, body.features[-1] if body.features else None)
    document._body_prefixes[body_id] = {**(prefixes or {}), ref: result}
    from .prefix_signatures import state_signatures
    signatures = state_signatures(document.objects)
    document._body_prefix_signatures.update({ref: signatures[ref] for ref in document._body_prefixes[body_id]})
    if notify:
        document.object_changed.emit(body_id, ("shape",))
        document.body_result_changed.emit(body_id)


class BodyHistory:
    """Plugin service facade; all state and undo still belong to Document."""
    def __init__(self):
        self.closed = False

    def _check(self):
        if self.closed:
            raise RuntimeError("Service de construction fermé.")

    def update(self, document, *, objects=(), remove=(), title="Modifier la construction"):
        self._check()
        document.update_construction(objects, remove=remove, title=title)

    def adopt(self, document, object_id, *, source_kind="static"):
        """Opt-in adoption, keeping the existing body UUID and exact static B-Rep."""
        from dataclasses import fields
        from ..document import SceneObject, ObjectType
        self._check()
        obj = document.objects[object_id]
        if type(obj) is not SceneObject or obj.kind is not ObjectType.BODY or obj.shape is None:
            raise ValueError("Seul un corps B-Rep existant sans historique peut être adopté.")
        body = BodyObject(**{f.name: getattr(obj, f.name) for f in fields(SceneObject)},
                          source_shape=obj.shape, source_kind=source_kind)
        document.update_construction((body,), title="Adopter une base statique")
        return document.objects[object_id]

    def evaluate(self, document, body_id, adapter=None):
        self._check()
        return evaluate_body(document, body_id, adapter)

    def result(self, document, body_id):
        self._check()
        return body_result(document, body_id)

    def last_valid(self, document, body_id):
        self._check()
        return last_valid_result(document, body_id)

    def result_at(self, document, ref):
        self._check()
        return result_at(document, ref)

    def dependencies(self, document, feature_id):
        self._check()
        from .construction_state import feature_dependencies
        return feature_dependencies(document.objects, document.objects[feature_id])

    def diagnostics(self, document, body_id):
        self._check()
        from .construction_state import construction_diagnostics
        return construction_diagnostics(document, body_id)

    def close(self):
        self.closed = True
