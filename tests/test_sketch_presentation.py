"""Native derived curves on all deterministic support frames, without widgets."""
from math import pi
from uuid import uuid4

import pytest
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.GProp import GProp_GProps

from cadastrophe.geometry import REFERENCE_PLANES, Vec3
from plugins.sketch.model import Arc, Circle, PlaneSupport, Segment, Sketch, Vec2
from plugins.sketch.presentation import entity_shape


@pytest.mark.parametrize("frame", REFERENCE_PLANES)
@pytest.mark.parametrize("entity,length", [
    (Segment(Vec2(1, 2), Vec2(4, 6)), 5),
    (Circle(Vec2(-3, 4), 7), 14*pi),
    (Arc(Vec2(1, 2), 7, pi/4, pi/2), 7*pi/2),
    (Arc(Vec2(1, 2), 7, pi/4, -pi), 7*pi),
])
def test_derived_native_edges_preserve_local_geometry(frame, entity, length):
    sketch = Sketch(PlaneSupport(str(uuid4()), frame))
    shape = entity_shape(sketch, entity)
    props = GProp_GProps()
    brepgprop.LinearProperties(shape, props)
    assert props.Mass() == pytest.approx(length)
    curve = BRepAdaptor_Curve(shape)
    for parameter in (curve.FirstParameter(), curve.LastParameter()):
        point = Vec3(*curve.Value(parameter).Coord())
        assert frame.to_local(point)[2] == pytest.approx(0, abs=1e-9)
    if isinstance(entity, Arc):
        points = [Vec3(*curve.Value(value).Coord()) for value in (curve.FirstParameter(), curve.LastParameter())]
        expected = [sketch.to_world(entity.point_at(value)) for value in (0, 1)]
        assert all(min((point - endpoint).norm() for point in points) < 1e-7 for endpoint in expected)
