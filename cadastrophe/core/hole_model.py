"""Persistent hole dimensions and sketch point selection, without native caches."""
from dataclasses import dataclass

from .operation_model import GeometryReference, number


@dataclass(frozen=True)
class HoleParameters:
    form: str = "simple"
    diameter: float = 6.
    depth: float = 10.
    end_condition: str = "blind"
    reversed: bool = False
    head_diameter: float = 10.
    head_depth: float = 3.
    angle: float = 90.
    # Empty means all point entities, including associative occurrences.
    positions: tuple[GeometryReference, ...] = ()

    def __post_init__(self):
        if type(self.form) is not str or self.form not in {"simple", "counterbore", "countersink"}:
            raise ValueError("Forme de perçage inconnue.")
        if (type(self.end_condition) is not str or self.end_condition not in {"blind", "through_all"}
                or type(self.reversed) is not bool):
            raise ValueError("Condition de fin ou sens du perçage invalide.")
        for key in ("diameter", "depth", "head_diameter", "head_depth", "angle"):
            number(getattr(self, key), key)
        if min(self.diameter, self.depth, self.head_diameter, self.head_depth) <= 0 or not 0 < self.angle < 180:
            raise ValueError("Les diamètres et profondeurs doivent être positifs ; l’angle est compris entre 0° et 180°.")
        if self.form != "simple" and self.head_diameter <= self.diameter:
            raise ValueError("Le diamètre d’entrée doit dépasser le diamètre du trou.")
        if (type(self.positions) is not tuple or len(self.positions) > 512
                or any(not isinstance(r, GeometryReference) or r.kind != "sketch_point" or r.part != "position"
                       for r in self.positions) or len(set(self.positions)) != len(self.positions)):
            raise ValueError("Choisissez des points d’esquisse distincts (512 maximum).")
