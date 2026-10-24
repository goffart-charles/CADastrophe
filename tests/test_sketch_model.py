"""E1 domain tests: no QApplication, document, AIS or OCCT dependency."""
from dataclasses import FrozenInstanceError, replace
from math import nan, inf, pi
import subprocess
import sys
from uuid import uuid4

import pytest

from cadastrophe.geometry import Ray, REFERENCE_PLANES, Vec3, WorkPlane, X, Y, Z
from plugins.sketch.model import Arc, Circle, PlaneSupport, Point, Segment, Sketch, Vec2


def sketch(frame=REFERENCE_PLANES[0]):
    return Sketch(PlaneSupport(str(uuid4()), frame))


def test_domain_import_does_not_import_qt_or_occt():
    subprocess.run([sys.executable, "-c", "import sys; import plugins.sketch.model; "
                    "assert not any(n.startswith(('PySide6', 'OCC')) for n in sys.modules)"], check=True)


@pytest.mark.parametrize("frame", (*REFERENCE_PLANES,
    WorkPlane("Oblique", Vec3(10, -20, 30), Vec3(1, 1, 0), Vec3(-1, 1, 2))))
@pytest.mark.parametrize("point", [Vec2(0, 0), Vec2(-12.5, 23), Vec2(1e6, -0.002)])
def test_local_world_ray_roundtrip(frame, point):
    model = sketch(frame)
    world = model.to_world(point)
    local = model.to_local(world)
    assert (local.u, local.v) == pytest.approx((point.u, point.v))
    ray = Ray(world + frame.normal * 50, frame.normal * -1)
    local = model.project_ray(ray)
    assert (local.u, local.v) == pytest.approx((point.u, point.v))
    assert model.project_ray(Ray(world + frame.normal * 5, frame.u)) is None
    assert model.project_ray(Ray(world + frame.normal * 5, frame.normal)) is None


def test_normals_and_off_plane_rejection_are_explicit():
    assert [plane.normal for plane in REFERENCE_PLANES] == [Z, Y * -1, X]
    with pytest.raises(ValueError, match="n’appartient"):
        sketch().to_local(Vec3(2, 3, 0.1))


def test_domain_snapshots_and_ids_are_immutable_and_distinct():
    entities = [Point(Vec2(1, 2)), Segment(Vec2(0, 0), Vec2(5, 0)),
                Circle(Vec2(2, 3), 5), Arc(Vec2(2, 3), 5, 0, pi / 2)]
    model = replace(sketch(), entities=entities)
    entities.clear()
    assert len(model.entities) == 4 and len({entity.id for entity in model.entities}) == 4
    with pytest.raises(FrozenInstanceError):
        model.entities[0].position = Vec2(3, 4)
    modified = model.with_replaced(replace(model.entities[0], position=Vec2(4, 5)))
    assert model.entities[0].position == Vec2(1, 2)
    assert modified.entities[0].id == model.entities[0].id
    assert len(modified.without(model.entities[0].id).entities) == 3


@pytest.mark.parametrize("value", [nan, inf, -inf, True, "1", None])
def test_non_numeric_or_nonfinite_parameters_are_rejected(value):
    with pytest.raises(ValueError):
        Vec2(value, 0)
    with pytest.raises(ValueError):
        Circle(Vec2(0, 0), value)


@pytest.mark.parametrize("factory", [
    lambda: Point((1, 2)), lambda: Segment(Vec2(0, 0), Vec2(0, 0)),
    lambda: Circle(Vec2(0, 0), 0), lambda: Circle(Vec2(0, 0), -2),
    lambda: Arc(Vec2(0, 0), 2, 0, 0), lambda: Arc(Vec2(0, 0), 2, 0, 2*pi),
    lambda: Point(Vec2(1, 2), id="bad"), lambda: PlaneSupport(str(uuid4()), None),
    lambda: PlaneSupport(str(uuid4()), REFERENCE_PLANES[0], True),
])
def test_invalid_entities_and_supports_are_rejected(factory):
    with pytest.raises(ValueError):
        factory()


@pytest.mark.parametrize("sweep", [pi / 2, -pi / 2])
def test_arc_uses_signed_local_radians(sweep):
    arc = Arc(Vec2(10, 20), 5, 0, sweep)
    assert arc.point_at(0) == Vec2(15, 20)
    assert arc.point_at(1).u == pytest.approx(10)
    assert arc.point_at(1).v == pytest.approx(25 if sweep > 0 else 15)


def test_duplicate_ids_unknown_types_and_missing_entities_fail_without_mutation():
    point = Point(Vec2(1, 2))
    model = sketch().with_added(point)
    for action in (lambda: model.with_added(point), lambda: model.with_added(object()),
                   lambda: model.with_added(Point(Vec2(0, 0), id=model.id)),
                   lambda: model.with_replaced(Circle(Vec2(0, 0), 2, id=point.id))):
        with pytest.raises(ValueError):
            action()
    with pytest.raises(KeyError):
        model.without(str(uuid4()))
    assert model.entities == (point,)
