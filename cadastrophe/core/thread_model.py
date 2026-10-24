"""Editable helical thread recipes, including frozen catalogue provenance."""
from dataclasses import dataclass
from math import sqrt, tan, radians, isfinite

from .operation_model import GeometryReference
from .fastener_catalog import CatalogChoice, VERSION, PROFILE_SOURCE, metric_threads


@dataclass(frozen=True)
class ThreadParameters:
    face: GeometryReference
    internal: bool = False
    diameter: float = 6.
    pitch: float = 1.
    length: float = 6.
    offset: float = 0.
    reversed: bool = False
    left_handed: bool = False
    starts: int = 1
    phase: float = 0.
    angle: float = 60.
    depth: float = 17 * sqrt(3) / 48
    root_flat: float = 0.
    root_radius: float = sqrt(3) / 12
    clearance: float = 0.
    lead_in: float = 0.
    lead_out: float = 0.
    catalog: CatalogChoice | None = None

    def __post_init__(self):
        if not isinstance(self.face, GeometryReference) or self.face.kind != "face":
            raise ValueError("Choisissez une face cylindrique nommée.")
        if any(type(v) is not bool for v in (self.internal, self.reversed, self.left_handed)):
            raise ValueError("Options de filetage invalides.")
        for key in ("diameter", "pitch", "length", "offset", "phase", "angle", "depth", "root_flat",
                    "root_radius", "clearance", "lead_in", "lead_out"):
            value = getattr(self, key)
            if type(value) not in (int, float) or not isfinite(value):
                raise ValueError("Les paramètres du filetage doivent être finis.")
        if (min(self.diameter, self.pitch, self.length, self.depth) <= 0
                or min(self.offset, self.root_flat, self.root_radius, self.clearance, self.lead_in, self.lead_out) < 0
                or not 10 <= self.angle <= 150 or self.depth + self.clearance >= self.diameter / 2
                or self.lead_in + self.lead_out > self.length):
            raise ValueError("Dimensions, angle ou entrées du filetage incompatibles.")
        if type(self.starts) is not int or not 1 <= self.starts <= 16:
            raise ValueError("Choisissez de 1 à 16 départs.")
        if self.root_flat and self.root_radius:
            raise ValueError("Choisissez un fond plat ou un fond arrondi.")
        if self.root_radius > self.depth:
            raise ValueError("Le rayon de fond dépasse la profondeur du filet.")
        if self.crest <= 1e-6:
            raise ValueError("Le profil croise le filet voisin : réduisez sa profondeur ou sa largeur.")
        if self.length / self.lead * self.starts > 512:
            raise ValueError("Le filetage dépasse 512 tours ; réduisez sa longueur ou augmentez le pas.")
        if self.catalog is not None and not isinstance(self.catalog, CatalogChoice):
            raise ValueError("Provenance du filetage invalide.")
        if self.catalog is not None and any(key not in {"diameter", "pitch", "angle", "depth", "root_width"}
                                            for key, _ in self.catalog.dimensions):
            raise ValueError("Le catalogue contient des dimensions incompatibles avec un filetage.")

    @property
    def lead(self):
        return self.pitch * self.starts

    @property
    def crest(self):
        from math import cos, sin
        a = radians(self.angle / 2)
        # Width of the cutting groove at the nominal crest cylinder.
        width = self.root_flat + 2 * self.depth * tan(a)
        if self.root_radius:
            width = 2 * (self.root_radius * cos(a) +
                         (self.depth - self.root_radius * (1 - sin(a))) * tan(a))
        return self.pitch - width

    @property
    def catalog_active(self):
        return (self.catalog is not None and self.catalog.matches(self)
                and self.catalog.family == ("metric_internal" if self.internal else "metric_external")
                and self.starts == 1 and self.clearance == 0)

    @property
    def root_width(self):
        return self.root_flat if self.internal else self.root_radius


def thread_choices(internal=False):
    result = []
    for diameter, pitch, _, source in metric_threads():
        height = sqrt(3) * pitch / 2
        dims = (("diameter", diameter), ("pitch", pitch), ("angle", 60.),
                ("depth", height * (5 / 8 if internal else 17 / 24)),
                ("root_width", pitch / 8 if internal else height / 6))
        result.append(CatalogChoice("metric_internal" if internal else "metric_external",
            f"M{diameter:g} × {pitch:g}", VERSION, source + " ; " + PROFILE_SOURCE, diameter, pitch, dims))
    return tuple(result)


def apply_thread_choice(parameters, choice):
    from dataclasses import replace
    if not isinstance(choice, CatalogChoice) or choice.family != ("metric_internal" if parameters.internal else "metric_external"):
        raise ValueError("Préréglage incompatible avec le type de filetage.")
    dims = dict(choice.dimensions)
    if set(dims) != {"diameter", "pitch", "angle", "depth", "root_width"}:
        raise ValueError("Préréglage de filetage incomplet ou incompatible.")
    width = dims.pop("root_width")
    return replace(parameters, catalog=choice, root_flat=width if parameters.internal else 0.,
                   root_radius=0. if parameters.internal else width, **dims)
