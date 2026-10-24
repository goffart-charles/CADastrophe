"""Coordinate mathematics, deliberately independent of Qt and OCCT.

Lengths are millimetres. Frames are right handed and orthonormal.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt


@dataclass(frozen=True)
class Vec3:
    x: float
    y: float
    z: float

    def __post_init__(self):
        if not all(isfinite(v) for v in self.tuple()):
            raise ValueError("Coordinates must be finite")

    def tuple(self) -> tuple[float, float, float]:
        return self.x, self.y, self.z

    def __add__(self, other: Vec3) -> Vec3:
        return Vec3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: Vec3) -> Vec3:
        return Vec3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, factor: float) -> Vec3:
        return Vec3(self.x * factor, self.y * factor, self.z * factor)

    def dot(self, other: Vec3) -> float:
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other: Vec3) -> Vec3:
        return Vec3(self.y * other.z - self.z * other.y,
                    self.z * other.x - self.x * other.z,
                    self.x * other.y - self.y * other.x)

    def norm(self) -> float:
        return sqrt(self.dot(self))

    def normalized(self) -> Vec3:
        length = self.norm()
        if length < 1e-12:
            raise ValueError("Zero direction")
        return self * (1 / length)


@dataclass(frozen=True)
class Ray:
    origin: Vec3
    direction: Vec3

    def __post_init__(self):
        object.__setattr__(self, "direction", self.direction.normalized())


@dataclass(frozen=True)
class WorkPlane:
    name: str
    origin: Vec3
    u: Vec3
    v: Vec3

    def __post_init__(self):
        # Keep already-unit serialized axes bit-for-bit. Repeated normalization
        # otherwise drifts oblique frames and breaks immutable codec roundtrips.
        u, v = (axis if abs(axis.norm() - 1) <= 1e-15 else axis.normalized()
                for axis in (self.u, self.v))
        if abs(u.dot(v)) > 1e-10:
            raise ValueError("Plane axes must be perpendicular")
        object.__setattr__(self, "u", u)
        object.__setattr__(self, "v", v)

    @property
    def normal(self) -> Vec3:
        return self.u.cross(self.v)

    def to_world(self, u: float, v: float, normal: float = 0) -> Vec3:
        return self.origin + self.u * u + self.v * v + self.normal * normal

    def to_local(self, point: Vec3) -> tuple[float, float, float]:
        delta = point - self.origin
        return delta.dot(self.u), delta.dot(self.v), delta.dot(self.normal)

    def intersect(self, ray: Ray) -> Vec3 | None:
        denominator = self.normal.dot(ray.direction)
        if abs(denominator) < 1e-10:
            return None
        distance = self.normal.dot(self.origin - ray.origin) / denominator
        if distance < -1e-10:
            return None
        return ray.origin + ray.direction * max(0, distance)


ORIGIN = Vec3(0, 0, 0)
X, Y, Z = Vec3(1, 0, 0), Vec3(0, 1, 0), Vec3(0, 0, 1)


def natural_plane_up(normal: Vec3) -> Vec3:
    """Stable upright direction shared by face sketch frames and normal views."""
    vertical = Z
    up = vertical - normal * vertical.dot(normal)
    if up.norm() < 1e-6:
        vertical = Y
        up = vertical - normal * vertical.dot(normal)
    return up.normalized()


REFERENCE_PLANES = (
    WorkPlane("XY", ORIGIN, X, Y),
    WorkPlane("XZ", ORIGIN, X, Z),  # normal -Y; local vertical is +Z
    WorkPlane("YZ", ORIGIN, Y, Z),  # normal +X; local horizontal is +Y
)
