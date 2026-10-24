"""Related geometry moves as one rigid group, including in the preview."""
from dataclasses import replace

import pytest

from plugins.sketch.connected_drag import connected_entities, translated_group
from plugins.sketch.constraints import Constraint, Reference
from plugins.sketch.model import Segment, Vec2
from test_sketch_e6_bis import drawn_rectangle, check_rectangle


def test_dragging_one_dimensioned_rectangle_edge_translates_its_entire_component():
    source = drawn_rectangle()
    free = Segment(Vec2(200,0),Vec2(220,0))
    source = replace(source,entities=(*source.entities,free))
    group = connected_entities(source,(source.entities[1].id,))
    assert len(group) == 4 and free not in group
    moved = translated_group(source,group,Vec2(25,-15))
    for original,new in zip(source.entities,moved):
        assert new.id == original.id
        assert new.start == Vec2(original.start.u+25,original.start.v-15)
    check_rectangle(replace(source,entities=moved),100,60)
    assert source.entities[0].start == Vec2(0,0)


def test_fixed_component_cannot_preview_an_impossible_translation():
    source = drawn_rectangle()
    e = source.entities[0]
    fixed = Constraint("fixed",(Reference(e.id,"start"),),e.start)
    source = replace(source,constraints=(*source.constraints,fixed))
    with pytest.raises(ValueError,match="Déplacement bloqué.*Fixer"):
        translated_group(source,connected_entities(source,(e.id,)),Vec2(10,5))
    check_rectangle(source,100,60)
