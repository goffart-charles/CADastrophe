"""Reference dependencies folded into the existing exact-prefix feature graph."""
from .feature_model import BodyStateRef
from .feature_objects import BodyObject, SketchSourceObject, ConstructionFeature, ReplayFeature
from .reference_objects import DatumPlaneObject
from .curve_model import CurveObject


def reference_inputs(objects, references=(), sketch_id=None):
    states, payload, ids, visiting = [], [], set(), set()

    def visit(key):
        if key in visiting:
            raise ValueError("Dépendance circulaire entre les références géométriques.")
        obj = objects.get(key)
        if obj is None:
            raise ValueError("Référence géométrique supprimée ou absente.")
        ids.add(key)
        visiting.add(key)
        if isinstance(obj, (DatumPlaneObject, CurveObject)):
            payload.append((key, obj.parameters))
            for ref in obj.geometry_references:
                reference(ref)
        elif isinstance(obj, SketchSourceObject):
            payload.append((key, getattr(obj, "sketch", None)))
            states.extend(obj.producer_refs)
            for dep in obj.document_dependencies:
                if dep not in obj.body_dependencies:
                    visit(dep)
        elif obj.plane is not None:
            payload.append((key, obj.plane, obj.revision, id(obj.shape)))
        else:
            raise ValueError("Cette référence n’est ni un plan, ni une esquisse, ni une courbe de construction.")
        visiting.remove(key)

    def reference(ref):
        if ref.kind in {"axis", "point"}:
            return
        ids.add(ref.object_id)
        if ref.kind in {"face", "edge", "vertex"}:
            target = objects.get(ref.object_id)
            from ..document import ObjectType
            if target is None or target.kind is not ObjectType.BODY:
                raise ValueError("Le corps producteur de la référence est absent.")
            states.append(BodyStateRef(ref.object_id, ref.producer_id))
        else:
            visit(ref.object_id)

    if sketch_id is not None:
        visit(sketch_id)
    for ref in references:
        reference(ref)
    return tuple(dict.fromkeys(states)), tuple(payload), ids


def feature_inputs(objects, feature):
    states, payload, ids = reference_inputs(objects, getattr(feature, "geometry_references", ()), feature.sketch_id)
    states = [*feature.support_refs, *states]
    for source, _ in feature.profile_sections:
        if source.sketch_id != feature.sketch_id:
            extra, data, keys = reference_inputs(objects, sketch_id=source.sketch_id)
            states.extend(extra)
            payload += data
            ids.update(keys)
    if isinstance(feature, ReplayFeature):
        owner = objects.get(feature.body_id)
        for key in feature.parameters.sources:
            source = objects.get(key)
            if not isinstance(source,ConstructionFeature) or not source.replayable:
                raise ValueError("Choisissez des extrusions, révolutions, balayages, lissages, perçages, répétitions ou symétries rejouables.")
            if source.body_id != feature.body_id or not isinstance(owner, BodyObject) or key not in owner.features:
                raise ValueError("Les fonctions sources doivent appartenir au même corps.")
            if feature.id in owner.features and owner.features.index(key) >= owner.features.index(feature.id):
                raise ValueError("Une répétition ne peut pas référencer une fonction future.")
            index = owner.features.index(key)
            states.append(BodyStateRef(source.body_id, key))
            if index or owner.source_kind is not None:
                states.append(BodyStateRef(owner.id, owner.features[index-1] if index else None))
            source_states, source_payload, source_ids = feature_inputs(objects, source)
            states.extend(source_states)
            payload += source_payload
            ids.update(source_ids)
            ids.add(key)
    return tuple(dict.fromkeys(states)), payload, ids


def validate_datums(objects):
    for obj in objects.values():
        if isinstance(obj, (DatumPlaneObject, CurveObject)):
            states, _, _ = reference_inputs(objects, obj.geometry_references)
            for ref in states:
                target = objects[ref.body_id]
                if isinstance(target, BodyObject):
                    if (ref.producer_id is None and target.source_kind is None
                            or ref.producer_id is not None and ref.producer_id not in target.features):
                        raise ValueError("État producteur du plan absent du corps référencé.")
