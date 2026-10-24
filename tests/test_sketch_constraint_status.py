"""Display classification reflects entity/point mobility in a mixed sketch."""
from dataclasses import replace
import pytest
from plugins.sketch.model import Segment, Circle, Vec2
from plugins.sketch.constraints import Constraint as C, Reference as R
from plugins.sketch.constraint_status import classify
from test_sketch_model import sketch


def test_fixed_vertex_is_black_while_its_line_and_unrelated_geometry_stay_free():
    a = Segment(Vec2(0,0),Vec2(40,0))
    b = Segment(Vec2(0,20),Vec2(40,20))
    s = replace(sketch(),entities=(a,b),constraints=(C("fixed",(R(a.id,"start"),),a.start),))
    state = classify(s)
    assert state.valid and not state.entities
    assert (a.id,"start") in state.points and (a.id,"end") not in state.points
    assert not any(key == b.id for key,_ in state.points)


@pytest.mark.parametrize("circle",[False,True])
def test_fully_defined_entity_is_identified_even_with_unrelated_free_geometry(circle):
    a = Circle(Vec2(0,0),20) if circle else Segment(Vec2(0,0),Vec2(40,0))
    b = Segment(Vec2(0,50),Vec2(40,50))
    constraints = (C("fixed",(R(a.id,"center"),),a.center),C("radius",(R(a.id),),20)) if circle else (
        C("fixed",(R(a.id,"start"),),a.start),C("horizontal",(R(a.id),)),C("length",(R(a.id),),40))
    s = replace(sketch(),entities=(a,b),constraints=constraints)
    assert classify(s).entities == {a.id}


def test_invalid_constraints_cannot_be_presented_as_fully_defined():
    a = Segment(Vec2(0,0),Vec2(40,0))
    s = replace(sketch(),entities=(a,),constraints=(C("fixed",(R(a.id,"start"),),Vec2(4,0)),))
    assert not classify(s).valid and not classify(s).entities
