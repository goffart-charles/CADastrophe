"""Public model commands; every commit uses the document's existing undo stack."""
from dataclasses import replace

from cadastrophe.plugin_api import ObjectType
from .model import Entity, PlaneSupport, Sketch
from .objects import SketchObject, support_status


def make_sketch(document, plane_id: str | PlaneSupport, *, name="Esquisse") -> SketchObject:
    """Build a detached snapshot so the host can roll back failed editor startup."""
    if isinstance(plane_id, PlaneSupport):
        support = plane_id
        if support.face_index is not None and support.reference is None:
            from .supports import from_selection, face_map, face_support_status
            from cadastrophe.document import SelectionItem
            target = document.objects.get(support.plane_id)
            if target is not None and not face_support_status(support, target):
                face = face_map(target.shape).FindKey(support.face_index)
                support = replace(from_selection(document, (SelectionItem(target.id, "Face", face, target.revision),)),
                                  frame=support.frame)
    else:
        plane = document.objects[plane_id]
        if plane.kind is not ObjectType.REFERENCE_PLANE:
            raise ValueError("Sélectionnez un plan de référence ou une face plane.")
        support = PlaneSupport(plane.id, plane.plane, plane.revision)
    sketch = Sketch(support)
    obj = SketchObject(name, None, (0.15, 0.35, 0.75), id=sketch.id, kind=ObjectType.FEATURE, sketch=sketch)
    if error := support_status(obj, document):
        raise ValueError(error)
    return obj


def create_sketch(document, plane_id: str | PlaneSupport, *, name="Esquisse") -> SketchObject:
    obj = make_sketch(document, plane_id, name=name)
    document.add(obj)
    return obj


def _current(document, sketch_id):
    obj = document.objects[sketch_id]
    if not isinstance(obj, SketchObject):
        raise ValueError("L’objet n’est pas une esquisse de ce plugin.")
    error = support_status(obj, document)
    if error:
        raise ValueError(error)
    from .construction_context import resolve_object
    return resolve_object(obj, document)


def add_entity(document, sketch_id: str, entity: Entity) -> None:
    obj = _current(document, sketch_id)
    document.replace_feature(replace(obj, sketch=obj.sketch.with_added(entity)), title="Ajouter une entité d’esquisse")


def add_entities(document, sketch_id: str, entities: tuple[Entity, ...], *, title: str, constraints=(), validate=None, validate_sketch=None) -> None:
    """One validated primitive, one undo. A rectangle atomically adds four sides."""
    obj = _current(document, sketch_id)
    if not entities:
        raise ValueError("Aucune entité à ajouter.")
    sketch = replace(obj.sketch, entities=(*obj.sketch.entities, *entities),
                     constraints=(*obj.sketch.constraints, *constraints))
    _commit(document, obj, sketch, title, validate, validate_sketch)


def replace_entity(document, sketch_id: str, entity: Entity) -> None:
    replace_entities(document, sketch_id, (entity,), title="Modifier une entité d’esquisse")


def apply_edit(document, sketch_id, sketch, *, title, validate=None, validate_sketch=None, expressions=None):
    """Publish a detached reshape through the same solver and history as drawing."""
    obj = _current(document, sketch_id)
    if sketch.id != obj.sketch.id or sketch.support != obj.sketch.support:
        raise ValueError("Une retouche doit conserver l’identité et le support de l’esquisse.")
    if expressions is not None:
        from cadastrophe.core.parameters import set_expression
        for key, exp in expressions.items():
            obj = set_expression(obj, key, exp)
    _commit(document, obj, sketch, title, validate, validate_sketch)


def remove_entity(document, sketch_id: str, entity_id: str) -> None:
    obj = _current(document, sketch_id)
    document.replace_feature(replace(obj, sketch=obj.sketch.without(entity_id)), title="Supprimer une entité d’esquisse")


def replace_entities(document, sketch_id, entities, *, title="Modifier les entités d’esquisse", validate=None, validate_sketch=None, annotation_positions=None, expressions=None):
    obj = _current(document, sketch_id)
    entities = tuple(entities)
    obj.sketch.require_editable(e.id for e in entities)
    if expressions is not None:
        from cadastrophe.core.parameters import set_expression
        for key, exp in expressions.items():
            obj = set_expression(obj, key, exp)
    values, current = {}, {entity.id: entity for entity in obj.sketch.entities}
    for entity in entities:
        if entity.id in values or entity.id not in current or type(entity) is not type(current[entity.id]):
            raise ValueError("Remplacement dupliqué, absent ou de type incompatible.")
        values[entity.id] = entity
    positions = {} if annotation_positions is None else dict(annotation_positions)
    if not positions.keys() <= {c.id for c in obj.sketch.constraints}:
        raise ValueError("Annotation absente de l’esquisse.")
    constraints = tuple(replace(c,label_position=positions[c.id]) if c.id in positions else c for c in obj.sketch.constraints)
    sketch = replace(obj.sketch, entities=tuple(values.get(entity.id, entity) for entity in obj.sketch.entities),constraints=constraints)
    _commit(document, obj, sketch, title, validate, validate_sketch)


def remove_entities(document, sketch_id, entity_ids):
    obj = _current(document, sketch_id)
    ids = set(entity_ids)
    obj.sketch.require_editable(ids)
    if not ids or not ids <= {entity.id for entity in obj.sketch.entities}:
        raise ValueError("Sélection d’entités invalide.")
    sketch = replace(obj.sketch, entities=tuple(entity for entity in obj.sketch.entities if entity.id not in ids),
                     constraints=tuple(c for c in obj.sketch.constraints if all(r.entity_id not in ids for r in c.refs)))
    used = {r.entity_id for c in sketch.constraints for r in c.refs}
    sketch = replace(sketch, external=tuple(e for e in sketch.external if e.geometry.id in used))
    document.replace_feature(replace(obj, sketch=sketch), title="Supprimer les entités d’esquisse")


def _commit(document, obj, sketch, title, validate=None, validate_sketch=None):
    from .constraints import reject_new_duplicates
    reject_new_duplicates(obj.sketch.constraints, sketch.constraints)
    if sketch.external:
        from .external import status
        from .construction_context import support_objects
        if error := status(sketch, support_objects(replace(obj, sketch=sketch), document)):
            raise ValueError(error)
    if sketch.constraints:
        from .solver import solve
        result = solve(sketch)
        if not result.success:
            raise ValueError(result.message)
        sketch = result.sketch
    if validate is not None:
        validate(sketch.entities)
    if validate_sketch is not None:
        validate_sketch(sketch)
    if sketch != obj.sketch or obj.expressions != document.objects[obj.id].expressions:
        document.replace_feature(replace(obj, sketch=sketch), title=title)


def set_constraints(document, sketch_id, constraints, *, title="Modifier les contraintes d’esquisse", validate=None, external=None, validate_sketch=None, expressions=None):
    obj = _current(document, sketch_id)
    if expressions is not None:
        from cadastrophe.core.parameters import set_expression
        for key, exp in expressions.items():
            obj = set_expression(obj, key, exp)
    sketch = replace(obj.sketch, constraints=tuple(constraints), external=obj.sketch.external if external is None else tuple(external))
    _commit(document, obj, sketch, title, validate, validate_sketch)


def place_constraint(document, sketch_id, key, position):
    obj = _current(document, sketch_id)
    if key not in {c.id for c in obj.sketch.constraints}:
        raise ValueError("Cote absente.")
    constraints = tuple(replace(c, label_position=position) if c.id == key else c for c in obj.sketch.constraints)
    sketch = replace(obj.sketch, constraints=constraints)
    if sketch != obj.sketch:
        document.replace_feature(replace(obj, sketch=sketch), title="Déplacer une annotation d’esquisse")


def remove_constraints(document, sketch_id, keys):
    # Removing a broken external relation must remain possible while its
    # source is missing. This operation only removes local logical data.
    obj = document.objects[sketch_id]
    if not isinstance(obj, SketchObject):
        raise ValueError("L’objet n’est pas une esquisse de ce plugin.")
    constraints = tuple(c for c in obj.sketch.constraints if c.id not in set(keys))
    used = {r.entity_id for c in constraints for r in c.refs}
    sketch = replace(obj.sketch, constraints=constraints, external=tuple(e for e in obj.sketch.external if e.geometry.id in used))
    if sketch != obj.sketch:
        document.replace_feature(replace(obj, sketch=sketch), title="Supprimer une relation d’esquisse")
