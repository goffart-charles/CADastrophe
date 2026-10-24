"""E6 atomic document commands, persistence, migration and malformed references."""
from dataclasses import replace
from copy import deepcopy

import pytest

from cadastrophe.core.persistence import save_document, load_document
from cadastrophe.document import SceneObject
from plugins.sketch.codec import CODEC
from plugins.sketch.commands import add_entities, set_constraints, remove_entities, replace_entities
from plugins.sketch.constraints import Constraint, Reference
from plugins.sketch.model import Circle, Vec2, Point, Segment
from test_sketch_document import registry, setup_document
from test_sketch_solver import rectangle


def test_fixed_point_identity_follows_persistent_coincidence_but_not_proximity():
    doc, obj = setup_document(native=True)
    a, b, c = (Point(Vec2(0, 0)) for _ in range(3))
    add_entities(doc, obj.id, (a, b, c), title="Trois points")
    first, second, third = (Reference(p.id, "position") for p in (a, b, c))
    joint = Constraint("coincident", (first, second))
    fixed = Constraint("fixed", (first,), Vec2(0, 0))
    set_constraints(doc, obj.id, (joint, fixed))
    with pytest.raises(ValueError, match="existe déjà"):
        set_constraints(doc, obj.id, (joint, fixed, Constraint("fixed", (second,), Vec2(0, 0))))
    # Equal coordinates alone do not make a persistent shared point.
    set_constraints(doc, obj.id, (joint, fixed, Constraint("fixed", (third,), Vec2(0, 0))))
    assert len(doc.objects[obj.id].sketch.constraints) == 3


@pytest.mark.parametrize("kind", ["fixed", "horizontal", "parallel", "coincident", "distance", "diameter"])
def test_duplicate_relations_are_refused_atomically_including_reversed_pairs(kind):
    doc, obj = setup_document(native=True)
    a, b = Segment(Vec2(0, 0), Vec2(10, 0)), Segment(Vec2(0, 5), Vec2(10, 5))
    circle = Circle(Vec2(30, 30), 3)
    add_entities(doc, obj.id, (a, b, circle), title="Géométrie")
    refs = {"fixed": (Reference(a.id, "start"),), "horizontal": (Reference(a.id),),
            "parallel": (Reference(a.id), Reference(b.id)),
            "coincident": (Reference(a.id, "start"), Reference(b.id, "start")),
            "distance": (Reference(a.id, "start"), Reference(b.id, "start")),
            "diameter": (Reference(circle.id),)}[kind]
    value = Vec2(0, 0) if kind == "fixed" else 5 if kind == "distance" else 6 if kind == "diameter" else None
    relation = Constraint(kind, refs, value)
    set_constraints(doc, obj.id, (relation,))
    doc.history.clear()
    old = doc.objects[obj.id]
    duplicate = Constraint(kind, tuple(reversed(refs)), value)
    with pytest.raises(ValueError, match="existe déjà"):
        set_constraints(doc, obj.id, (relation, duplicate))
    assert doc.objects[obj.id] is old and doc.history.count == 0 and not doc.is_modified
    if kind in {"fixed", "distance", "diameter"}:
        updated = replace(relation, value=Vec2(1, 2) if kind == "fixed" else value+1)
        set_constraints(doc, obj.id, (updated,))
        assert doc.objects[obj.id].sketch.constraints[0].id == relation.id
        doc.history.undo()
        assert doc.objects[obj.id].sketch == old.sketch


def prepare():
    doc, obj = setup_document(native=True)
    rect = rectangle()
    add_entities(doc, obj.id, rect.entities, title="Rectangle")
    set_constraints(doc, obj.id, rect.constraints)
    doc.history.clear()
    return doc, doc.objects[obj.id]


def test_dimension_change_undo_redo_and_native_roundtrip(registry, tmp_path):
    doc, original = prepare()
    constraints = tuple(replace(c, value=75) if c.id == original.sketch.constraints[8].id else c
                        for c in original.sketch.constraints)
    set_constraints(doc, original.id, constraints)
    changed = doc.objects[original.id].sketch
    assert changed.entities[0].end.u == pytest.approx(75)
    assert doc.history.count == 1
    doc.history.undo()
    assert doc.objects[original.id].sketch == original.sketch
    doc.history.redo()
    assert doc.objects[original.id].sketch == changed
    path = tmp_path / "e6-rectangle.cada"
    save_document(doc, path, registry=registry)
    loaded = load_document(path, registry=registry)
    assert loaded.objects[original.id].sketch == changed
    next_constraints = tuple(replace(c, value=30) if c.id == changed.constraints[8].id else c for c in changed.constraints)
    set_constraints(loaded, original.id, next_constraints)
    assert loaded.objects[original.id].sketch.entities[0].end.u == pytest.approx(30)


def test_failed_constraint_preserves_snapshot_revision_dirty_state_and_redo():
    doc, original = prepare()
    altered = tuple(replace(c, value=60) if c.kind == "length" else c for c in original.sketch.constraints)
    set_constraints(doc, original.id, altered)
    doc.history.undo()
    before = doc.objects[original.id]
    dirty = doc.is_modified
    bad = Constraint("length", (Reference(original.sketch.entities[0].id),), 999)
    with pytest.raises(ValueError, match="Échec"):
        set_constraints(doc, original.id, (*original.sketch.constraints, bad))
    assert doc.objects[original.id] is before and doc.history.count == 1 and doc.is_modified == dirty
    doc.history.redo()
    assert doc.objects[original.id].sketch.constraints == altered


def test_native_validation_failure_is_before_mutation():
    doc, original = prepare()
    constraints = tuple(replace(c, value=80) if c.kind == "length" else c for c in original.sketch.constraints)
    def invalid(entities):
        assert entities[0].end.u == pytest.approx(80)
        raise ValueError("OCCT refuse")
    with pytest.raises(ValueError, match="OCCT refuse"):
        set_constraints(doc, original.id, constraints, validate=invalid)
    assert doc.objects[original.id] is original and doc.history.count == 0


def test_delete_prunes_related_constraints_and_undo_restores_exact_references():
    doc, original = prepare()
    target = original.sketch.entities[0]
    remove_entities(doc, original.id, (target.id,))
    remaining = doc.objects[original.id].sketch
    assert all(ref.entity_id != target.id for c in remaining.constraints for ref in c.refs)
    assert len(remaining.entities) == 3 and len(remaining.constraints) < len(original.sketch.constraints)
    doc.history.undo()
    assert doc.objects[original.id].sketch == original.sketch


def test_entity_parameter_edit_cannot_break_persistent_dimensions():
    doc, original = prepare()
    edge = original.sketch.entities[0]
    replace_entities(doc, original.id, (replace(edge, end=Vec2(80, 10)),))
    changed = doc.objects[original.id].sketch
    assert changed.entities[0].end.u == pytest.approx(40, abs=1e-7)
    assert changed.entities[0].end.v == pytest.approx(0, abs=1e-7)
    assert changed.constraints == original.sketch.constraints


def test_legacy_v2_codec_is_rejected(registry):
    doc, original = prepare()
    # v2 never contained constraints. Its stored geometry must remain exact.
    unconstrained = replace(original, sketch=replace(original.sketch, constraints=()))
    extension, _ = registry.encode(unconstrained)
    extension["version"] = 2
    del extension["data"]["patterns"]
    del extension["data"]["support"]["producer_id"]
    del extension["data"]["support"]["reference"]
    del extension["data"]["support"]["attachment"]
    del extension["data"]["constraints"]
    del extension["data"]["external"]
    base = SceneObject(unconstrained.name, None, unconstrained.color, id=unconstrained.id,
                       kind=unconstrained.kind, revision=unconstrained.revision)
    with pytest.raises(ValueError, match="Version du type"):
        registry.decode(base, extension)


@pytest.mark.parametrize("damage", ["missing", "duplicate", "part", "type", "negative", "nan", "unknown", "extra", "list"])
def test_codec_rejects_malformed_or_dangling_constraints(registry, damage):
    doc, original = prepare()
    extension, _ = registry.encode(original)
    extension = deepcopy(extension)
    constraints = extension["data"]["constraints"]
    c = constraints[8]
    if damage == "missing": c["refs"][0]["entity_id"] = Circle(Vec2(0, 0), 2).id
    if damage == "duplicate": constraints.append(deepcopy(c))
    if damage == "part": c["refs"][0]["part"] = "start"
    if damage == "type": c["kind"] = "radius"
    if damage == "negative": c["value"] = -1
    if damage == "nan": c["value"] = float("nan")
    if damage == "unknown": c["kind"] = "magic"
    if damage == "extra": c["extra"] = 1
    if damage == "list": extension["data"]["constraints"] = {}
    base = SceneObject(original.name, None, original.color, id=original.id, kind=original.kind)
    with pytest.raises(ValueError):
        registry.decode(base, extension)
