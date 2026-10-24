"""Persistent inputs for datum planes, swept volumes and transformed operations."""
from dataclasses import dataclass
from math import isfinite

from .feature_model import uuid, SketchInput
from .topology_refs import TopologyReference


def number(value, label):
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"{label} doit être un nombre fini.")


@dataclass(frozen=True)
class GeometryReference:
    kind: str
    object_id: str | None = None
    producer_id: str | None = None
    topology: TopologyReference | None = None
    entity_id: str | None = None
    part: str | None = None
    values: tuple = ()

    def __post_init__(self):
        if self.kind not in {"plane", "face", "edge", "vertex", "sketch_line", "sketch_curve", "sketch_point", "axis", "point", 'curve'}:
            raise ValueError("Type de référence géométrique inconnu.")
        literal = self.kind in {"axis", "point"}
        if literal:
            if any(v is not None for v in (self.object_id, self.producer_id, self.topology, self.entity_id, self.part)):
                raise ValueError("Une référence explicite ne contient pas de lien documentaire.")
            if type(self.values) is not tuple or len(self.values) != (6 if self.kind == "axis" else 3):
                raise ValueError("Coordonnées de référence invalides.")
            for value in self.values:
                number(value, "La coordonnée")
            if self.kind == "axis" and sum(v*v for v in self.values[3:]) < 1e-20:
                raise ValueError("La direction de l’axe doit être non nulle.")
            return
        uuid(self.object_id)
        if self.values:
            raise ValueError("Une référence liée ne stocke pas de géométrie de substitution.")
        if self.producer_id is not None:
            uuid(self.producer_id)
        if self.kind in {"face", "edge", "vertex"}:
            expected = {"face": "Face", "edge": "Edge", "vertex": "Vertex"}[self.kind]
            if not isinstance(self.topology, TopologyReference) or self.topology.kind != expected:
                raise ValueError("Une référence topologique persistante est requise.")
        elif self.topology is not None or self.producer_id is not None:
            raise ValueError("Producteur ou topologie incompatible avec cette référence.")
        if self.kind.startswith("sketch_"):
            uuid(self.entity_id)
            if self.kind == "sketch_point" and self.part not in {"start", "end", "center", "position"}:
                raise ValueError("Choisissez un point de l’entité.")
        elif self.entity_id is not None or self.part is not None:
            raise ValueError("Entité d’esquisse incompatible avec cette référence.")


@dataclass(frozen=True)
class ShellParameters:
    faces: tuple[GeometryReference, ...]
    thickness: float = 2.
    outward: bool = False

    def __post_init__(self):
        number(self.thickness, "L’épaisseur")
        if self.thickness <= 0 or type(self.outward) is not bool:
            raise ValueError("L’épaisseur doit être positive et le sens doit être booléen.")
        if (type(self.faces) is not tuple or not 1 <= len(self.faces) <= 512
                or any(not isinstance(r, GeometryReference) or r.kind != "face" or not r.topology.labels for r in self.faces)
                or len(set(self.faces)) != len(self.faces)):
            raise ValueError("Sélectionnez des faces distinctes à ouvrir (1 à 512).")


@dataclass(frozen=True)
class DraftParameters:
    faces: tuple[GeometryReference, ...]
    neutral: GeometryReference
    direction: GeometryReference
    angle: float = 3.
    reversed: bool = False

    def __post_init__(self):
        number(self.angle, "L’angle")
        if not 0 < self.angle < 90 or type(self.reversed) is not bool:
            raise ValueError("L’angle doit être compris strictement entre 0° et 90° ; le sens doit être booléen.")
        if (type(self.faces) is not tuple or not 1 <= len(self.faces) <= 512
                or any(not isinstance(r, GeometryReference) or r.kind != "face" or not r.topology.labels for r in self.faces)
                or len(set(self.faces)) != len(self.faces)):
            raise ValueError("Sélectionnez des faces distinctes à dépouiller (1 à 512).")
        if (not isinstance(self.neutral, GeometryReference) or self.neutral.kind not in {"plane", "face"}
                or self.neutral.kind == "face" and not self.neutral.topology.labels):
            raise ValueError("Choisissez un plan ou une face plane nommée comme référence neutre.")
        if (not isinstance(self.direction, GeometryReference)
                or self.direction.kind not in {"axis", "plane", "face", "edge", "sketch_line"}
                or self.direction.kind in {"face", "edge"} and not self.direction.topology.labels):
            raise ValueError("Choisissez une direction persistante ou un axe explicite.")


@dataclass(frozen=True)
class PlaneParameters:
    method: str
    references: tuple[GeometryReference, ...]
    distance: float = 10.
    angle: float = 45.
    reversed: bool = False

    def __post_init__(self):
        expected = {"offset": 1, "inclined": 2, "three_points": 3}
        if self.method not in expected or type(self.references) is not tuple or len(self.references) != expected[self.method]:
            raise ValueError("Références incomplètes pour cette méthode de création de plan.")
        if any(not isinstance(r, GeometryReference) for r in self.references):
            raise ValueError("Références de plan invalides.")
        number(self.distance, "La distance")
        number(self.angle, "L’angle")
        if type(self.reversed) is not bool:
            raise ValueError("Le sens doit être booléen.")


@dataclass(frozen=True)
class LoftSection:
    source: SketchInput
    contours: tuple[str, ...] = ()
    anchors: tuple[str, ...] = ()
    reversed: tuple[bool, ...] = ()

    def __post_init__(self):
        if not isinstance(self.source, SketchInput):
            raise ValueError("Une section de lissage exige une esquisse et ses producteurs.")
        if type(self.contours) is not tuple or len(self.contours) > 1:
            raise ValueError("Choisissez une seule région fermée pour chaque section.")
        if (type(self.anchors) is not tuple or len(self.anchors) > 128 or len(set(self.anchors)) != len(self.anchors)
                or type(self.reversed) is not tuple or len(self.reversed) != len(self.anchors)
                or any(type(value) is not bool for value in self.reversed)):
            raise ValueError("Ancres ou orientations des contours invalides.")
        for key in (*self.contours, *self.anchors):
            uuid(key)


@dataclass(frozen=True)
class LoftParameters:
    sections: tuple[LoftSection, ...]
    mode: str = "smooth"
    matching: str = "automatic"

    def __post_init__(self):
        if (type(self.sections) is not tuple or not 2 <= len(self.sections) <= 64
                or any(not isinstance(s, LoftSection) for s in self.sections)
                or len({s.source.sketch_id for s in self.sections}) != len(self.sections)):
            raise ValueError("Choisissez de 2 à 64 sections d’esquisses distinctes et ordonnées.")
        if self.mode not in {"smooth", "ruled"} or self.matching not in {"automatic", "explicit"}:
            raise ValueError("Mode de lissage ou de correspondance inconnu.")
        if self.matching == "explicit" and any(not s.anchors for s in self.sections):
            raise ValueError("La correspondance explicite exige une ancre pour chaque contour.")


@dataclass(frozen=True)
class SweepParameters:
    path: tuple[GeometryReference, ...]
    transport: str = "corrected"
    orientation: str = "normal"
    transition: str = "right"
    roll: float = 0.
    reversed: bool = False
    contours: tuple[str, ...] = ()

    def __post_init__(self):
        if (type(self.path) is not tuple or not 1 <= len(self.path) <= 512 or len(set(self.path)) != len(self.path)
                or any(not isinstance(r, GeometryReference) or r.kind not in {"edge", "sketch_curve", 'curve'}
                       or r.kind == "edge" and not r.topology.labels for r in self.path)):
            raise ValueError("Choisissez un parcours de courbes distinctes et nommées (1 à 512).")
        if (self.transport not in {"corrected", "frenet", "fixed"} or self.orientation not in {"normal", "keep"}
                or self.transition not in {"right", "round"}):
            raise ValueError("Mode de transport, orientation ou raccord de balayage inconnu.")
        if self.transition == "round" and self.orientation != "normal":
            raise ValueError("Le raccord par rotation exige une section normale au parcours.")
        number(self.roll, "La rotation du profil")
        if not -360 <= self.roll <= 360 or type(self.reversed) is not bool:
            raise ValueError("Rotation ou sens de parcours invalide.")
        if type(self.contours) is not tuple or len(set(self.contours)) != len(self.contours):
            raise ValueError("Sélection de contours invalide.")
        for key in self.contours:
            uuid(key)


@dataclass(frozen=True)
class RevolutionParameters:
    axis: GeometryReference
    angle: float = 360.
    reversed: bool = False
    contours: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.axis, GeometryReference):
            raise ValueError("Choisissez un axe de révolution.")
        number(self.angle, "L’angle")
        if not 0 < self.angle <= 360 or type(self.reversed) is not bool:
            raise ValueError("L’angle doit être supérieur à 0° et inférieur ou égal à 360°.")
        if type(self.contours) is not tuple or len(set(self.contours)) != len(self.contours):
            raise ValueError("Sélection de contours invalide.")
        for key in self.contours:
            uuid(key)


@dataclass(frozen=True)
class ReplayParameters:
    sources: tuple[str, ...]
    reference: GeometryReference
    count: int = 2
    spacing: float = 20.
    angle: float = 360.
    reversed: bool = False
    merge_result: bool = True

    def __post_init__(self):
        if type(self.sources) is not tuple or not self.sources or len(self.sources) > 128 or len(set(self.sources)) != len(self.sources):
            raise ValueError("Sélectionnez des fonctions sources distinctes (128 maximum).")
        for key in self.sources:
            uuid(key)
        if not isinstance(self.reference, GeometryReference):
            raise ValueError("Une direction, un axe ou un plan est requis.")
        if type(self.count) is not int or not 2 <= self.count <= 256:
            raise ValueError("Le nombre total d’occurrences doit être compris entre 2 et 256.")
        number(self.spacing, "L’espacement")
        number(self.angle, "L’angle")
        if (self.spacing <= 0 or not 0 < self.angle <= 360 or type(self.reversed) is not bool
                or type(self.merge_result) is not bool):
            raise ValueError("Espacement, angle ou sens de répétition invalide.")
