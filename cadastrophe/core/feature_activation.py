"""Transactional activation of existing recipes using the common evaluator."""
from dataclasses import replace

from .feature_objects import ConstructionFeature
from .body_history import affected_bodies, preview_construction, validate_graph


def set_enabled(document, object_ids, enabled, evaluator):
    if type(enabled) is not bool:
        raise ValueError("L'activation doit être un booléen.")
    document.history._check_idle()
    keys = tuple(dict.fromkeys(object_ids))
    if not keys or any(not isinstance(document.objects.get(key), ConstructionFeature) for key in keys):
        raise ValueError("Sélectionnez uniquement des fonctions de construction.")
    edits = {key: replace(document.objects[key], enabled=enabled) for key in keys
             if document.objects[key].enabled != enabled}
    if not edits:
        return False
    title = "Réactiver les fonctions" if enabled else "Désactiver les fonctions"
    if document.equation_evaluator is not None and (document.variables or any(obj.expressions for obj in document.objects.values())):
        # The ordinary equation transaction already prepares, previews and
        # publishes the whole graph; do not run a second native preview.
        document.update_construction(tuple(edits.values()), title=title, allow_errors=True)
        return True
    candidate = {**document.objects, **edits}
    from .parameters import prepare
    candidate, _, _ = prepare(candidate, document.variables)
    validate_graph(candidate)
    resolved = {}
    results, prefixes = preview_construction(document, candidate, affected_bodies(candidate, edits),
        evaluator, strict=False, resolved_objects=resolved)
    candidate.update(resolved)
    updates = tuple(obj for key, obj in candidate.items()
                    if obj != document.objects[key] or key in resolved)
    document.update_construction(updates, title=title,
                                 results=results, prefixes=prefixes, allow_errors=True)
    return True
