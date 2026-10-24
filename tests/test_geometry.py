import math
import pytest
from cadastrophe.geometry import Vec3, WorkPlane, Ray, REFERENCE_PLANES, ORIGIN, X, Y, Z


@pytest.mark.parametrize("plane", REFERENCE_PLANES)
def test_reference_frame(plane):
    assert plane.origin == ORIGIN
    assert plane.u.norm() == pytest.approx(1)
    assert plane.v.norm() == pytest.approx(1)
    assert plane.u.dot(plane.v) == pytest.approx(0)
    assert plane.u.cross(plane.v) == plane.normal
    assert plane.normal.norm() == pytest.approx(1)


def test_reference_normals_are_deterministic():
    assert [p.normal for p in REFERENCE_PLANES] == [Z, Y * -1, X]


@pytest.mark.parametrize("plane", REFERENCE_PLANES)
@pytest.mark.parametrize("coords", [(0, 0, 0), (-12.5, 33.8, 0), (1e6, -0.002, 17)])
def test_roundtrip(plane, coords):
    assert plane.to_local(plane.to_world(*coords)) == pytest.approx(coords)


def test_translated_oblique_frame():
    plane = WorkPlane("oblique", Vec3(12, -30, 8), Vec3(1, 1, 0), Vec3(-1, 1, 2))
    assert plane.to_local(plane.to_world(-5, 7, 11)) == pytest.approx((-5, 7, 11))
    hit = plane.intersect(Ray(plane.to_world(3, 4, 10), plane.normal * -2))
    assert hit is not None
    assert plane.to_local(hit) == pytest.approx((3, 4, 0))


def test_parallel_and_behind_ray():
    plane = REFERENCE_PLANES[0]
    assert plane.intersect(Ray(Vec3(0, 0, 10), X)) is None
    assert plane.intersect(Ray(Vec3(0, 0, 10), Z)) is None
    assert plane.intersect(Ray(ORIGIN, Z)) == ORIGIN
    assert plane.intersect(Ray(Vec3(0, 0, 10), Vec3(1, 0, -1e-12))) is None


@pytest.mark.parametrize("u,v", [(ORIGIN, Y), (X, X), (X, Vec3(1, 1, 0))])
def test_invalid_frame(u, v):
    with pytest.raises(ValueError):
        WorkPlane("bad", ORIGIN, u, v)


def test_invalid_numbers_and_ray():
    with pytest.raises(ValueError):
        Vec3(math.inf, 0, 0)
    with pytest.raises(ValueError):
        Ray(ORIGIN, ORIGIN)
