"""Reconstructible construction curves independent of their native display."""
from dataclasses import dataclass, field
from math import isfinite, radians, tan

from ..document import SceneObject, ObjectType
from ..geometry import Vec3
from .feature_model import uuid


@dataclass(frozen=True)
class CurveParameters:
    kind: str = 'helix'
    plane_id: str | None = None
    radius: float = 10.
    pitch: float = 10.
    turns: float = 3.
    conical_angle: float = 0.
    start_angle: float = 0.
    clockwise: bool = False
    radial_pitch: float = 5.
    center_u: float = 0.
    center_v: float = 0.
    points: tuple[Vec3, ...] = ()
    closed: bool = False

    def __post_init__(self):
        if self.kind not in {'helix', 'spiral', 'points'}:
            raise ValueError('Type de courbe inconnu.')
        if self.plane_id is not None:
            uuid(self.plane_id)
        if self.kind != 'points' and self.plane_id is None:
            raise ValueError('Choisissez le plan de départ de la courbe.')
        for name in ('radius', 'pitch', 'turns', 'conical_angle', 'start_angle', 'radial_pitch', 'center_u', 'center_v'):
            value = getattr(self, name)
            if type(value) not in (float, int) or not isfinite(value):
                raise ValueError('Les paramètres de courbe doivent être des nombres finis.')
        if type(self.clockwise) is not bool or type(self.closed) is not bool:
            raise ValueError('Sens ou fermeture de courbe invalide.')
        if self.kind == 'helix' and (self.radius <= 0 or self.pitch <= 0 or not -80. < self.conical_angle < 80.
                or self.radius+self.pitch*self.turns*tan(radians(self.conical_angle)) <= 1e-7):
            raise ValueError('Une hélice exige un rayon et un pas positifs ; le rayon final du cône doit rester positif.')
        if self.kind == 'spiral' and (self.radius < 0 or self.radial_pitch == 0
                or self.radius+self.radial_pitch*self.turns < 0):
            raise ValueError('La spirale exige un écartement non nul et des rayons positifs ou nuls.')
        if self.kind != 'points' and not 0 < self.turns <= 100:
            raise ValueError('Le nombre de tours doit être compris entre 0 et 100.')
        if type(self.points) is not tuple or any(type(p) is not Vec3 for p in self.points):
            raise ValueError('Les points de la courbe doivent être des coordonnées 3D.')
        if any(not isfinite(v) for point in self.points for v in point.tuple()):
            raise ValueError('Les coordonnées des points doivent être finies.')
        if self.kind == 'points':
            if not (3 if self.closed else 2) <= len(self.points) <= 256:
                raise ValueError('Une courbe par points exige de 2 à 256 points, au moins 3 pour être fermée.')
            if any((a-b).norm() <= 1e-7 for a, b in zip(self.points, self.points[1:])):
                raise ValueError('Deux points successifs doivent être distincts.')
            if self.closed and (self.points[-1]-self.points[0]).norm() <= 1e-7:
                raise ValueError('Ne répétez pas le premier point pour fermer la courbe.')


@dataclass(frozen=True)
class CurveObject(SceneObject):
    parameters: CurveParameters = field(kw_only=True)

    def __post_init__(self):
        super().__post_init__()
        if self.kind is not ObjectType.FEATURE or type(self.parameters) is not CurveParameters:
            raise ValueError('Une courbe est une recette géométrique sans solide persistant.')

    @property
    def geometry_references(self):
        from .operation_model import GeometryReference
        return (GeometryReference('plane', self.parameters.plane_id),) if self.parameters.plane_id is not None else ()

    def reference_error(self, document):
        try:
            from .curve_geometry import curve_edge
            curve_edge(document, self)
        except (ValueError, RuntimeError) as exc:
            return str(exc)
        return ''
