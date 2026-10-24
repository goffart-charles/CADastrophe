"""Bounded, offline fastener data and immutable provenance stored in recipes.

Dimensions in mm; the snapshot, never today's catalogue, governs saved parts.
Counterbore depth is deliberately user-defined (the cutter table gives diameters).
Tap drills apply to cutting taps, not forming taps. No tolerance class is implied.
"""
from dataclasses import dataclass
from math import isfinite

VERSION = "2026-10-24.1"
PASSAGE_SOURCE = "https://www.tbksoft.com/fileadmin/dokumente/tbk/etc/HTMLHandbuch/de/HTMLHandbuchch13.html"
BORE_SOURCE = "https://www.gwg-gabrovo.com/products.php?id=813"
SINK_SOURCE = "https://catalogue.atagroup.com/international/2022/696/"
TAP_SOURCE = "https://www.guhring.com.mx/site/image/products/machuelos/catalogo-machuelos/catalogo-machuelos-threading-tools.pdf"
FINE_SOURCE = "https://www.guhring.co.jp/cms/wordpress/wp-content/plugins/guhring/pdf/catalog_137.pdf"
PROFILE_SOURCE = "https://website-assets.bossard.com/Metric-Thread-Profile/Metric-thread-profile-calc-en.html"


@dataclass(frozen=True)
class CatalogChoice:
    family: str
    designation: str
    version: str
    source: str
    nominal: float
    pitch: float
    dimensions: tuple[tuple[str, float], ...]
    unit: str = "mm"

    def __post_init__(self):
        if any(type(v) is not str or not v.strip() or len(v) > 2048
               for v in (self.family, self.designation, self.version, self.source)) or self.unit != "mm":
            raise ValueError("Provenance du catalogue invalide.")
        if any(type(v) not in (int, float) or not isfinite(v) or v <= 0 for v in (self.nominal, self.pitch)):
            raise ValueError("Diamètre nominal ou pas du catalogue invalide.")
        allowed = {"diameter", "head_diameter", "angle", "pitch", "depth", "crest", "root_width"}
        if (type(self.dimensions) is not tuple or not self.dimensions
                or any(type(item) is not tuple or len(item) != 2 or item[0] not in allowed
                       or type(item[1]) not in (int, float) or not isfinite(item[1]) or item[1] <= 0
                       for item in self.dimensions)
                or len(dict(self.dimensions)) != len(self.dimensions)):
            raise ValueError("Dimensions du catalogue invalides.")

    def matches(self, parameters):
        return all(type(actual := getattr(parameters, key, None)) in (int, float)
                   and abs(actual - value) <= 1e-9 for key, value in self.dimensions)


# nominal, coarse pitch, cutting drill, close/medium/loose clearance,
# DIN 373 m cutter diameter, DIN 74 Bf 90-degree countersink diameter.
_ROWS = (
    (3., .5, 2.5, (3.2, 3.4, 3.6), 6., 6.3),
    (4., .7, 3.3, (4.3, 4.5, 4.8), 8., 8.3),
    (5., .8, 4.2, (5.3, 5.5, 5.8), 10., 10.4),
    (6., 1., 5., (6.4, 6.6, 7.), 11., 12.4),
    (8., 1.25, 6.8, (8.4, 9., 10.), 15., 16.5),
    (10., 1.5, 8.5, (10.5, 11., 12.), 18., 20.5),
    (12., 1.75, 10.2, (13., 13.5, 14.5), 20., 25.),
)
_FINE = ((8., 1., 7.), (10., 1., 9.), (10., 1.25, 8.8), (12., 1.5, 10.5))
FAMILIES = (("clearance", "Passage métrique · jeu moyen"),
            ("clearance_close", "Passage métrique · jeu réduit"),
            ("clearance_loose", "Passage métrique · jeu large"),
            ("counterbore", "Lamage métrique · profondeur libre"),
            ("countersink", "Fraisure métrique · 90°"),
            ("tap_drill", "Avant-trou de taraudage métrique"))


def metric_threads():
    """Nominal, pitch, recommended cutting drill and its primary source."""
    return tuple((d, p, drill, TAP_SOURCE) for d, p, drill, *_ in _ROWS) + tuple(
        (d, p, drill, FINE_SOURCE) for d, p, drill in _FINE)


def hole_choices(family):
    if family not in dict(FAMILIES):
        raise ValueError("Famille de perçage absente du catalogue.")
    if family == "tap_drill":
        return tuple(CatalogChoice(family, f"M{d:g} × {p:g}", VERSION, source, d, p,
                                   (("diameter", drill),)) for d, p, drill, source in metric_threads())
    result = []
    for d, p, _, clearance, bore, sink in _ROWS:
        index = 0 if family == "clearance_close" else 2 if family == "clearance_loose" else 1
        diameter = clearance[index]
        dims, source = (("diameter", diameter),), PASSAGE_SOURCE
        if family == "counterbore":
            # This is the pilot of the selected DIN 373 m cutter, including M12=14.
            dims = (("diameter", 14. if d == 12 else diameter), ("head_diameter", bore))
            source = BORE_SOURCE
        elif family == "countersink":
            dims = (("diameter", diameter), ("head_diameter", sink), ("angle", 90.))
            source = PASSAGE_SOURCE + " ; " + SINK_SOURCE
        result.append(CatalogChoice(family, f"M{d:g}", VERSION, source, d, p, dims))
    return tuple(result)


def hole_form(choice):
    return choice.family if choice.family in {"counterbore", "countersink"} else "simple"


def apply_hole_choice(parameters, choice):
    from dataclasses import replace
    if not isinstance(choice, CatalogChoice) or choice.family not in dict(FAMILIES):
        raise ValueError("Choix de perçage incompatible.")
    return replace(parameters, form=hole_form(choice), catalog=choice, **dict(choice.dimensions))
