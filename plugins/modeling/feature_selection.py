"""Resolve a picked face to its operation, using proven naming provenance."""
from cadastrophe.core.body_history import body_result, result_at
from cadastrophe.core.feature_model import BodyStateRef, ResultStatus
from cadastrophe.core.feature_objects import BodyObject, ConstructionFeature, ReplayFeature
from cadastrophe.core.native_naming import subshapes


def face_sources(document, item, point=None, *, before=None):
    body = document.objects.get(item.object_id)
    if not isinstance(body, BodyObject) or item.topology != "Face":
        raise ValueError("Cliquez une face de la fonction à reproduire dans la vue 3D.")
    if body.revision != item.revision:
        raise ValueError("La face sélectionnée a changé ; sélectionnez-la à nouveau.")
    state = body_result(document, body.id)
    if state.status is not ResultStatus.READY or state.topology is None:
        raise ValueError("Reconstruisez le corps avant de sélectionner une fonction.")
    labels = state.topology.labels(item.shape, "Face")
    roots = {label.split(":", 1)[0] for label in labels if ":untracked:" not in label}
    candidates = [document.objects[key] for key in body.features if key in roots]
    if len(candidates) > 1 and point is not None:
        from OCC.Core.BRepClass3d import BRepClass3d_SolidClassifier
        from OCC.Core.gp import gp_Pnt
        from OCC.Core.TopAbs import TopAbs_ON
        local = []
        for feature in candidates:
            effects = result_at(document, BodyStateRef(body.id, feature.id)).effects
            if any(BRepClass3d_SolidClassifier(effect.shape, gp_Pnt(*point.tuple()), 1e-6).State() == TopAbs_ON
                   for effect in effects):
                local.append(feature)
        if local:
            candidates = local
    if len(candidates) != 1:
        raise ValueError("Cette face ne désigne pas une fonction unique. Cliquez une autre face de la fonction ou utilisez l’arbre en complément.")
    feature = candidates[0]
    if not isinstance(feature, ConstructionFeature) or not feature.replayable:
        raise ValueError(f"{feature.name} ne fournit pas d’opération rejouable. Cliquez une face d’extrusion, de révolution, de balayage, de lissage ou de motif.")
    if before is not None and feature.body_id == before.body_id and body.features.index(feature.id) >= body.features.index(before.id):
        raise ValueError("Choisissez une fonction antérieure au motif en cours de modification.")
    return feature.id


def selected_faces(document, keys):
    """Include a pattern's seeds for the visual feedback of its complete motif."""
    closure = set(keys)
    pending = list(keys)
    while pending:
        feature = document.objects[pending.pop()]
        if isinstance(feature, ReplayFeature):
            for key in feature.parameters.sources:
                if key not in closure:
                    closure.add(key)
                    pending.append(key)
    result = []
    bodies = {document.objects[key].body_id for key in closure}
    for key in bodies:
        state = body_result(document, key)
        if state.shape is None or state.topology is None:
            continue
        result.extend(face for face in subshapes(state.shape, "Face")
                      if any(label.split(":", 1)[0] in closure for label in state.topology.labels(face, "Face")))
    return tuple(result)
