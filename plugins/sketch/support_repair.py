"""Explicit support replacement with a detached, validated consumer graph."""
from dataclasses import replace

from cadastrophe.core.body_history import validate_graph
from cadastrophe.core.feature_objects import ConstructionFeature
from .objects import SketchObject


def reassociation_objects(document, sketch_id, support, *, preserve_frame=True, validate=True):
    obj = document.objects[sketch_id]
    if not isinstance(obj, SketchObject):
        raise ValueError("Sélectionnez une esquisse à rattacher.")
    old, new = obj.sketch.support.frame, support.frame
    attachment = support.attachment or new
    if preserve_frame:
        if (old.normal-new.normal).norm() > 1e-8:
            raise ValueError("Le nouveau support n’a pas la même orientation. "
                             "Décochez la conservation du repère pour accepter cette réorientation.")
        origin = old.origin+new.normal*(new.origin-old.origin).dot(new.normal)
        new = replace(old, name=new.name, origin=origin)
    support = replace(support, frame=new, attachment=attachment)
    fresh = replace(obj, sketch=replace(obj.sketch, support=support))
    objects = [fresh]
    for feature in document.objects.values():
        if isinstance(feature, ConstructionFeature) and any(s.sketch_id == sketch_id for s, _ in feature.profile_sections):
            objects.append(feature.with_profile_supports(sketch_id, fresh.producer_refs))
    if validate:
        validate_graph({**document.objects, **{o.id: o for o in objects}})
    return tuple(objects)
