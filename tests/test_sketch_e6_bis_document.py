"""Atomic repeated dimension edits, rollback, undo/redo and angular persistence."""
from dataclasses import replace
from math import radians

import pytest

from cadastrophe.core.persistence import save_document, load_document
from plugins.sketch.commands import add_entities, set_constraints
from plugins.sketch.constraints import Constraint as C, Reference as R
from plugins.sketch.model import Segment, Vec2
from plugins.sketch.dimension_geometry import measure
from test_sketch_document import registry, setup_document
from test_sketch_e6_bis import drawn_rectangle, check_rectangle


def test_f_successive_driving_dimensions_entire_undo_redo_and_roundtrip(registry,tmp_path):
    doc,obj = setup_document(native=True)
    rect = drawn_rectangle()
    add_entities(doc,obj.id,rect.entities,constraints=rect.constraints,title="Rectangle avec ses relations")
    edges = rect.entities
    width = C("line_distance",(R(edges[1].id,"start"),R(edges[3].id)),100)
    height = C("line_distance",(R(edges[0].id,"start"),R(edges[2].id)),60)
    set_constraints(doc,obj.id,(*rect.constraints,width,height))
    doc.history.clear()
    snapshots = [doc.objects[obj.id].sketch]
    for w,h in [(150,60),(150,90),(20,7),(120,60),(60,40),(100,60)]:
        source = doc.objects[obj.id].sketch
        constraints = tuple(replace(c,value=w if c.id == width.id else h) if c.id in {width.id,height.id} else c
                            for c in source.constraints)
        set_constraints(doc,obj.id,constraints)
        check_rectangle(doc.objects[obj.id].sketch,w,h)
        snapshots.append(doc.objects[obj.id].sketch)
    assert doc.history.count == 6
    for expected in reversed(snapshots[:-1]):
        doc.history.undo()
        assert doc.objects[obj.id].sketch == expected
    before = doc.objects[obj.id]
    bad = C("length",(R(edges[0].id),),99)
    with pytest.raises(ValueError,match="Échec"):
        set_constraints(doc,obj.id,(*before.sketch.constraints,bad))
    assert doc.objects[obj.id] is before and doc.history.count == 6
    for expected in snapshots[1:]:
        doc.history.redo()
        assert doc.objects[obj.id].sketch == expected
    path = tmp_path / "e6-bis.cada"
    save_document(doc,path,registry=registry)
    loaded = load_document(path,registry=registry)
    assert loaded.objects[obj.id].sketch == snapshots[-1]


def test_angle_and_oriented_rays_persist_then_edit_same_constraint(registry,tmp_path):
    doc,obj = setup_document(native=True)
    a,b = Segment(Vec2(0,0),Vec2(30,0)),Segment(Vec2(0,0),Vec2(15,25))
    add_entities(doc,obj.id,(a,b),title="Lignes")
    angle = C("angle",(R(a.id,"start"),R(b.id,"end")),radians(120),label_position=Vec2(-15,20))
    set_constraints(doc,obj.id,(angle,))
    path = tmp_path / "angle.cada"
    save_document(doc,path,registry=registry)
    loaded = load_document(path,registry=registry)
    source = loaded.objects[obj.id].sketch
    assert source == doc.objects[obj.id].sketch and source.constraints == (angle,)
    set_constraints(loaded,obj.id,(replace(angle,value=radians(100)),))
    changed = loaded.objects[obj.id].sketch
    assert len(changed.constraints) == 1 and changed.constraints[0].id == angle.id
    assert measure(changed,"angle",angle.refs) == pytest.approx(radians(100),abs=1e-9)
    loaded.history.undo()
    assert loaded.objects[obj.id].sketch == source
