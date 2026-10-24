"""Thin document adapter; domain parameters remain in the pure model."""
from dataclasses import dataclass, field

from cadastrophe.plugin_api import ObjectType, SceneObject
from .model import Sketch, Segment, Circle, Arc, Ellipse, Spline
from cadastrophe.core.feature_objects import SketchSourceObject


@dataclass(frozen=True)
class SketchObject(SketchSourceObject):
    sketch: Sketch = field(kw_only=True)

    def equation_dimensions(self):
        from .equation_parameters import dimensions
        return dimensions(self)

    def with_equation_values(self, changes):
        from .equation_parameters import with_values
        return with_values(self, changes)

    def __post_init__(self):
        super().__post_init__()
        if self.kind is not ObjectType.FEATURE or not isinstance(self.sketch, Sketch) or self.id != self.sketch.id:
            raise ValueError("Une esquisse documentaire exige le même UUID et le type logique feature.")

    @property
    def body_dependencies(self):
        support = self.sketch.support
        ids = (() if support.face_index is None else (support.plane_id,))
        return tuple(dict.fromkeys((*ids, *(e.body_id for e in self.sketch.external))))

    @property
    def profile_source_ids(self):
        from .profile_regions import additional_region_ids
        return (tuple(entity.id for entity in self.sketch.entities if isinstance(entity, (Segment, Circle, Arc, Ellipse, Spline)))
                + additional_region_ids(self.sketch))

    @property
    def document_dependencies(self):
        return tuple(dict.fromkeys((self.sketch.support.plane_id, *self.body_dependencies)))

    @property
    def reference_slots(self):
        from cadastrophe.core.feature_model import BodyStateRef, ReferenceSlot
        support = self.sketch.support
        state = BodyStateRef(support.plane_id, support.producer_id) if support.face_index is not None else None
        slots = [ReferenceSlot(self.id, "sketch.support", support.plane_id, state, support.reference)]
        slots.extend(ReferenceSlot(self.id, f"sketch.external.{i}", e.body_id,
                                   BodyStateRef(e.body_id, e.producer_id), e.reference)
                     for i, e in enumerate(self.sketch.external))
        return tuple(slots)

    @property
    def producer_refs(self):
        from cadastrophe.core.feature_model import BodyStateRef
        support = self.sketch.support
        refs = ([BodyStateRef(support.plane_id, support.producer_id)]
                if support.reference is not None else [])
        refs.extend(BodyStateRef(e.body_id, e.producer_id) for e in self.sketch.external if e.reference is not None)
        return tuple(dict.fromkeys(refs))

    def reference_error(self, document):
        return support_status(self, document)

    def resolved(self, document):
        from .construction_context import resolve_object
        return resolve_object(self, document)


def support_status(obj: SketchObject, objects) -> str:
    if hasattr(objects, "objects"):
        from .construction_context import support_objects, resolve_object
        try:
            obj = resolve_object(obj, objects)
            objects = support_objects(obj, objects)
        except ValueError as exc:
            return str(exc)
    support = obj.sketch.support
    target = objects.get(support.plane_id)
    if target is None:
        return "Support non résolu : corps absent." if support.face_index is not None else "Support non résolu : plan absent."
    if support.face_index is not None:
        from .supports import face_support_status
        return face_support_status(support, target)
    if target.kind is not ObjectType.REFERENCE_PLANE:
        return "Support non résolu : l’objet n’est pas un plan de référence."
    from cadastrophe.core.reference_objects import DatumPlaneObject
    if isinstance(target, DatumPlaneObject):
        # Document callers resolved the recipe above; mapping callers receive
        # a detached, already-resolved object from the profile provider.
        return ""
    if support.attachment is not None:
        if (target.revision != support.revision or target.plane != support.attachment
                or (target.plane.normal-support.frame.normal).norm() > 1e-10
                or abs((support.frame.origin-target.plane.origin).dot(target.plane.normal)) > 1e-7):
            return "Support non résolu : repère d’attachement au plan modifié."
        return ""
    if target.revision != support.revision or target.plane != support.frame:
        return "Support non résolu : plan modifié."
    return ""
