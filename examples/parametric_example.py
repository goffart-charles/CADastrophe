"""Reproducible native example, built with the application's recipe services.

No mesh, imported solid or test-only geometry evaluator is used here.
"""
from dataclasses import replace

from cadastrophe.document import Document, ObjectType, SelectionItem
from cadastrophe.geometry import REFERENCE_PLANES
from cadastrophe.scene import new_document
from cadastrophe.core.body_history import evaluate_body, result_at
from cadastrophe.core.feature_model import (
    BodyStateRef, EdgeParameters, ResultStatus, SketchInput,
)
from cadastrophe.core.feature_objects import BodyObject, EdgeFeature, ExtrusionFeature, ReplayFeature, RevolutionFeature
from cadastrophe.core.native_naming import subshapes
from cadastrophe.core.object_types import ObjectTypeRegistry
from cadastrophe.core.operation_model import PlaneParameters, ReplayParameters, RevolutionParameters
from cadastrophe.core.reference_geometry import capture_reference, plane_shape, resolve_plane
from cadastrophe.core.reference_objects import DatumPlaneObject
from plugins.modeling.codec import BODY_CODEC, EDGE_CODEC, FEATURE_CODEC
from plugins.modeling.datums import refresh_datums
from plugins.modeling.extrusion_service import Extrusions
from plugins.modeling.operation_codec import PLANE_CODEC, REPLAY_CODEC, REVOLUTION_CODEC, SHELL_CODEC, DRAFT_CODEC, SWEEP_CODEC, LOFT_CODEC, HOLE_CODEC, THREAD_CODEC
from plugins.modeling.restoration import restore_initial_extrusions
from plugins.sketch import commands
from plugins.sketch.codec import CODEC as SKETCH_CODEC
from plugins.sketch.constraints import Constraint, Reference
from plugins.sketch.model import Circle, Segment, Vec2
from plugins.sketch.profile_service import SketchProfiles


def registry(service):
    result = ObjectTypeRegistry()
    result.register("cadastrophe.sketch", SKETCH_CODEC)
    for codec in (BODY_CODEC, FEATURE_CODEC, EDGE_CODEC, PLANE_CODEC, REVOLUTION_CODEC, REPLAY_CODEC, SHELL_CODEC, DRAFT_CODEC, SWEEP_CODEC, LOFT_CODEC, HOLE_CODEC, THREAD_CODEC):
        result.register("cadastrophe.modeling", codec)
    result.register_document_restorer("cadastrophe.modeling", lambda doc: restore_initial_extrusions(doc, service))
    return result


def mass(shape):
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps
    props = GProp_GProps()
    brepgprop.VolumeProperties(shape, props, True)
    return props.Mass()


class ParametricExample:
    def __init__(self, name, *, color=(.18, .49, .52)):
        self.document = new_document(name)
        self.service = Extrusions(SketchProfiles())
        self.handles = {}
        self.body = BodyObject(name, None, color)

    def plane(self, name):
        return next(o for o in self.document.objects.values() if o.plane is not None and o.plane.name == name)

    def sketch(self, key, name, support, entities, constraints=()):
        sketch = commands.create_sketch(self.document, support.id, name=name)
        entities = tuple(entities)
        relations = list(constraints)
        sides = [e for e in entities if isinstance(e, Segment) and not e.construction]
        if sides and all(e.end == sides[(i+1) % len(sides)].start for i, e in enumerate(sides)):
            # Shared endpoints remain connected when dragged in the editor.
            for i, side in enumerate(sides):
                relations.append(Constraint("coincident", (Reference(side.id, "end"), Reference(sides[(i+1) % len(sides)].id, "start"))))
                direction = "horizontal" if side.start.v == side.end.v else "vertical" if side.start.u == side.end.u else None
                if direction:
                    relations.append(Constraint(direction, (Reference(side.id),)))
        commands.add_entities(self.document, sketch.id, entities, title=name, constraints=tuple(relations))
        self.handles[key] = sketch.id
        return self.document.objects[sketch.id]

    def circle(self, key, name, support, center, radius, *, hinge=False):
        circle = Circle(Vec2(*center), radius)
        constraints = (Constraint("fixed", (Reference(circle.id, "center"),), Vec2(*center)),
                       Constraint("diameter", (Reference(circle.id),), 2*radius, label_position=Vec2(center[0]+8, center[1]+8)))
        entities = [circle]
        if hinge:
            axis = Segment(Vec2(-70, 0), Vec2(70, 0), construction=True)
            entities.append(axis)
            self.handles["hinge"] = axis.id
            constraints += (Constraint("horizontal", (Reference(axis.id),)),)
        return self.sketch(key, name, support, entities, constraints)

    def datum(self, key, name, parameters):
        obj = DatumPlaneObject(name, plane_shape(REFERENCE_PLANES[0]), (.65, .55, .3),
                               plane=REFERENCE_PLANES[0], visible=False, parameters=parameters)
        frame = resolve_plane(self.document, obj)
        obj = replace(obj, plane=frame, shape=plane_shape(frame))
        self.document.add(obj)
        self.handles[key] = obj.id
        return obj

    def face_datum(self, key, name, producer, height):
        from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
        from OCC.Core.GeomAbs import GeomAbs_Plane
        from OCC.Core.TopoDS import topods
        state = result_at(self.document, BodyStateRef(self.body.id, producer.id))
        matches = []
        for face in subshapes(state.shape, "Face"):
            surface = BRepAdaptor_Surface(topods.Face(face))
            if surface.GetType() != GeomAbs_Plane:
                continue
            plane = surface.Plane()
            if abs(abs(plane.Axis().Direction().Z())-1) < 1e-9 and abs(plane.Location().Z()-height) < 1e-7:
                matches.append(face)
        if len(matches) != 1:
            raise ValueError(f"Face support non unique à Z={height} : {len(matches)} candidates.")
        reference = capture_reference(self.document, SelectionItem(self.body.id, "Face", matches[0], self.body.revision))
        return self.datum(key, name, PlaneParameters("offset", (reference,), distance=0))

    def append(self, key, name, operation, parameters, sketch=None):
        common = dict(kind=ObjectType.FEATURE, body_id=self.body.id, operation=operation, parameters=parameters)
        if isinstance(parameters, ReplayParameters):
            feature = ReplayFeature(name, None, self.body.color, **common)
        elif isinstance(parameters, EdgeParameters):
            feature = EdgeFeature(name, None, self.body.color, **common)
        else:
            source = SketchInput(sketch.id, sketch.producer_refs)
            cls = RevolutionFeature if isinstance(parameters, RevolutionParameters) else ExtrusionFeature
            feature = cls(name, None, self.body.color, source=source, **common)
        self.document.update_construction((replace(self.body, features=(*self.body.features, feature.id)), feature))
        self.body = self.document.objects[self.body.id]
        self.handles[key] = feature.id
        self.rebuild()
        return feature

    def rebuild(self):
        result = evaluate_body(self.document, self.body.id, self.service.evaluator(self.document), reuse=True)
        if result.status is not ResultStatus.READY:
            raise ValueError(f"{self.document.objects.get(result.feature_id, self.body).name} : {result.message}")
        refresh_datums(self.document)
        self.body = self.document.objects[self.body.id]
        return result


def polygon(points):
    points = tuple(Vec2(*p) for p in points)
    return tuple(Segment(p, points[(i+1) % len(points)]) for i, p in enumerate(points))


def independent_copy(model):
    """Allow baseline and modified examples to open in separate tabs."""
    copied = type(model).__new__(type(model))
    copied.document = Document(f"{model.document.name} · variante recalculée")
    # Bootstrap a complete graph with the same hook as the native file loader.
    # Clipboard insertion intentionally accepts bodies only, not whole recipes.
    for obj in model.document.objects.values():
        copied.document._insert(obj, len(copied.document.objects))
    copied.service = Extrusions(SketchProfiles())
    copied.handles = dict(model.handles)
    copied.body = copied.document.objects[model.body.id]
    copied.rebuild()
    copied.document.history.clear()
    return copied
