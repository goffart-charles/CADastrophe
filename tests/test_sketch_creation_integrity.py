"""Tool-created contours keep their joints and fixed origin after real edits."""
from dataclasses import replace
from math import hypot

import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer

from cadastrophe.core.persistence import load_document, save_document
from cadastrophe.scene import new_document
from plugins.sketch.commands import add_entities, set_constraints, create_sketch, replace_entity
from plugins.sketch.constraints import Constraint, Reference, point
from plugins.sketch.creation_relations import creation_relations
from plugins.sketch.drawing import DrawingController
from plugins.sketch.model import Arc, Circle, Point, Vec2
from plugins.sketch.profile_faces import to_faces
from plugins.sketch.solver import solve
from test_sketch_document import registry
from test_sketch_model import sketch


def setup_document():
    document = new_document()
    return document, create_sketch(document, next(iter(document.objects)))


def build_faces(sketch):
    from plugins.sketch.native_regions import extended, report, faces
    return faces(sketch, report(sketch)) if extended(sketch) else to_faces(sketch)


def draft_entities(tool):
    draft = DrawingController()
    draft.select(tool)
    draft.click(Vec2(0., 0.))
    if tool == 'spline':
        draft.spline_closed = True
        for p in (Vec2(30., 0.), Vec2(30., 20.), Vec2(0., 20.)):
            draft.click(p)
        return draft.candidate(Vec2(0., 20.))
    if tool in {'slot', 'ellipse'}:
        draft.click(Vec2(40., 0.))
        return draft.click(Vec2(30., 8.))
    return draft.click(Vec2(30., 20.))


@pytest.mark.parametrize('tool', ['rectangle', 'polygon', 'slot', 'circle', 'ellipse', 'spline'])
def test_closed_drawing_tools_have_valid_native_faces_before_and_after_edit(tool, registry, tmp_path):
    document, obj = setup_document()
    entities = draft_entities(tool)
    relations = creation_relations(obj.sketch, entities, rectangle=tool == 'rectangle')
    add_entities(document, obj.id, entities, title='Dessiner', constraints=relations)
    original = document.objects[obj.id].sketch
    faces = build_faces(original)
    assert len(faces.faces) == 1 and BRepCheck_Analyzer(faces.faces[0], True).IsValid()
    if tool in {'rectangle', 'polygon', 'slot'}:
        relation = Constraint('length', (Reference(entities[0].id),), 55.)
        set_constraints(document, obj.id, (*original.constraints, relation))
        changed = document.objects[obj.id].sketch
        actual = changed.entity(entities[0].id)
        assert hypot(actual.end.u-actual.start.u, actual.end.v-actual.start.v) == pytest.approx(55.)
    elif tool == 'circle':
        relation = Constraint('radius', (Reference(entities[0].id),), 55.)
        set_constraints(document, obj.id, (*original.constraints, relation))
        changed = document.objects[obj.id].sketch
    else:
        entity = original.entities[0]
        changes = dict(major_radius=60., minor_radius=20.) if tool == 'ellipse' else dict(
            points=tuple(Vec2(p.u*1.3, p.v*1.2) for p in entity.points))
        replace_entity(document, obj.id, replace(entity, **changes))
        changed = document.objects[obj.id].sketch
    rebuilt = build_faces(changed)
    assert len(rebuilt.faces) == 1 and BRepCheck_Analyzer(rebuilt.faces[0], True).IsValid()
    if tool == 'slot':
        assert sum(c.kind == 'coincident' and any(r.part in {'start', 'end'} and isinstance(changed.entity(r.entity_id), Arc) for r in c.refs)
                   for c in changed.constraints) == 4
    path = tmp_path/(tool+'.cada')
    save_document(document, path, registry=registry)
    reopened = load_document(path, registry=registry)
    restored = reopened.objects[obj.id].sketch
    assert tuple(e.id for e in restored.entities) == tuple(e.id for e in entities)
    assert BRepCheck_Analyzer(build_faces(restored).faces[0], True).IsValid()
    if changed is not original:
        document.history.undo()
        assert document.objects[obj.id].sketch == original
        document.history.redo()
        assert document.objects[obj.id].sketch == changed
    export_example(document, registry, tool)


def test_origin_is_a_fixed_reference_without_profile_geometry_and_can_drive_dimensions(registry, tmp_path):
    document, obj = setup_document()
    entity = Point(Vec2(0., 0.))
    relations = creation_relations(obj.sketch, (entity,))
    assert relations[0].refs[0] == Reference(obj.id, 'position')
    add_entities(document, obj.id, (entity,), title='Point à l’origine', constraints=relations)
    actual = document.objects[obj.id].sketch
    moved = replace(actual, entities=(replace(entity, position=Vec2(11., 19.)),))
    result = solve(moved)
    assert result.success and result.sketch.entity(entity.id).position == Vec2(0., 0.)
    with pytest.raises(ValueError, match='origine'):
        actual.without(actual.id)
    other = Point(Vec2(12., 7.))
    add_entities(document, obj.id, (other,), title='Autre point')
    relation = Constraint('horizontal_distance', (Reference(other.id, 'position'), Reference(obj.id, 'position')), 25.)
    set_constraints(document, obj.id, (*actual.constraints, relation))
    actual = document.objects[obj.id].sketch
    assert actual.entity(other.id).position.u == pytest.approx(25.)
    assert actual.origin.position == Vec2(0., 0.) and len(actual.entities) == 2
    path = tmp_path/'origine.cada'
    save_document(document, path, registry=registry)
    restored = load_document(path, registry=registry).objects[obj.id].sketch
    assert restored.entity(other.id).position.u == pytest.approx(25.)
    assert solve(restored).success
    export_example(document, registry, 'origine_cotee')
    from cadastrophe.core.clipboard import encode_objects, decode_objects
    from test_parametric_operations import append
    from plugins.modeling.extrusion_service import Extrusions
    from plugins.sketch.profile_service import SketchProfiles
    from examples.parametric_example import registry as graph_registry
    service = Extrusions(SketchProfiles())
    add_entities(document, obj.id, (Circle(Vec2(40., 30.), 5.),), title='Profil')
    body, _, _ = append(document, service, document.objects[obj.id])
    types = graph_registry(service)
    copied = decode_objects(encode_objects(document, [body.id], types), types)
    copied_sketch, = [o.sketch for o in copied if hasattr(o, 'sketch')]
    assert copied_sketch.id != obj.id
    assert any(ref.entity_id == copied_sketch.id for c in copied_sketch.constraints for ref in c.refs)
    assert all(ref.entity_id != obj.id for c in copied_sketch.constraints for ref in c.refs)
    assert solve(copied_sketch).success


def export_example(document, registry, name):
    import os
    from pathlib import Path
    root = os.environ.get('CADASTROPHE_ISSUES_EXAMPLES')
    if root:
        path = Path(root)/'esquisses_liees'/(name+'.cada')
        path.parent.mkdir(parents=True, exist_ok=True)
        save_document(document, path, registry=registry)
