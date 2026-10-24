"""Immutable construction recipes. No Qt, native geometry or plugin imports."""
from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from uuid import UUID


def uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError("Une référence UUID canonique est requise.")
    return value


class Operation(str, Enum):
    NEW_BODY = "new_body"
    BOSS = "boss"
    CUT = "cut"
    FILLET = "fillet"
    CHAMFER = "chamfer"
    SHELL = "shell"
    DRAFT = "draft"
    MIRROR = "mirror"
    LINEAR_PATTERN = "linear_pattern"
    CIRCULAR_PATTERN = "circular_pattern"


@dataclass(frozen=True)
class EdgeParameters:
    edges: tuple[tuple[float, ...], ...]
    size: float = 2.
    size2: float = 2.
    mode: str = "symmetric"
    angle: float = 45.
    tangent: bool = True
    flip: bool = False
    references: tuple = ()

    def __post_init__(self):
        from .topology_refs import TopologyReference
        if (type(self.references) is not tuple or (self.references and len(self.references) != len(self.edges))
                or any(not isinstance(r, TopologyReference) or r.kind != "Edge" for r in self.references)):
            raise ValueError("Références persistantes d’arêtes invalides.")
        if (type(self.edges) is not tuple or not self.edges or len(self.edges) > 512
                or any(type(e) is not tuple for e in self.edges)
                or len(set(self.edges)) != len(self.edges)):
            raise ValueError("Sélectionnez des arêtes distinctes d’un même corps.")
        for edge in self.edges:
            if type(edge) is not tuple or len(edge) != 10 or any(type(v) not in (int, float) or not isfinite(v) for v in edge):
                raise ValueError("Référence géométrique d’arête invalide.")
        if any(type(v) not in (int, float) or not isfinite(v) or v <= 0 for v in (self.size, self.size2)):
            raise ValueError("Les distances et rayons doivent être positifs et finis.")
        if type(self.mode) is not str or self.mode not in {"symmetric", "two_distances", "distance_angle", "variable"}:
            raise ValueError("Méthode de congé/chanfrein inconnue.")
        if type(self.angle) not in (int, float) or not isfinite(self.angle) or not 0 < self.angle < 90:
            raise ValueError("L’angle doit être compris entre 0° et 90°.")
        if type(self.tangent) is not bool or type(self.flip) is not bool:
            raise ValueError("Options de propagation/direction invalides.")


@dataclass(frozen=True)
class BodyStateRef:
    """A particular prefix, never the body's implicitly changing final result.

    producer_id=None denotes the explicit static base, before any operation.
    """
    body_id: str
    producer_id: str | None = None

    def __post_init__(self):
        uuid(self.body_id)
        if self.producer_id is not None:
            uuid(self.producer_id)


@dataclass(frozen=True)
class SketchInput:
    sketch_id: str
    supports: tuple[BodyStateRef, ...] = ()

    def __post_init__(self):
        uuid(self.sketch_id)
        if (type(self.supports) is not tuple or
                any(not isinstance(ref, BodyStateRef) for ref in self.supports) or
                len({ref.body_id for ref in self.supports}) != len(self.supports)):
            raise ValueError("Les états de support doivent être explicites et uniques par corps.")


@dataclass(frozen=True)
class ExtrusionParameters:
    depth: float | None = 10.
    reversed: bool = False
    end_condition: str = "blind"
    merge_result: bool = True
    # Stable source entity IDs, never native wire indexes. Empty = whole sketch.
    contours: tuple[str, ...] = ()

    def __post_init__(self):
        if self.end_condition not in ("blind", "through_all"):
            raise ValueError("Condition de fin inconnue.")
        if self.end_condition == "blind":
            if type(self.depth) not in (int, float) or not isfinite(self.depth) or self.depth <= 0:
                raise ValueError("La profondeur doit être positive et finie, en mm.")
        elif self.depth is not None:
            raise ValueError("À travers tout ne stocke pas de profondeur de substitution.")
        if type(self.reversed) is not bool or type(self.merge_result) is not bool:
            raise ValueError("Les options de direction et de fusion doivent être booléennes.")
        if type(self.contours) is not tuple or len(set(self.contours)) != len(self.contours):
            raise ValueError("Sélection de contours invalide.")
        for key in self.contours:
            uuid(key)


class ResultStatus(str, Enum):
    DIRTY = "dirty"
    READY = "ready"
    ERROR = "error"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ReferenceSlot:
    """A recipe location, never a transient native selection or face index."""
    owner_id: str
    path: str
    target_id: str
    state: BodyStateRef | None = None
    topology: object = None
    entity_ids: tuple[str, ...] = ()

    def __post_init__(self):
        from .topology_refs import TopologyReference
        uuid(self.owner_id)
        uuid(self.target_id)
        if type(self.path) is not str or not self.path:
            raise ValueError("Emplacement de référence invalide.")
        if self.state is not None and (not isinstance(self.state, BodyStateRef) or self.state.body_id != self.target_id):
            raise ValueError("État producteur de référence incompatible.")
        if self.topology is not None and not isinstance(self.topology, TopologyReference):
            raise ValueError("Une référence topologique persistante est requise.")
        if type(self.entity_ids) is not tuple:
            raise ValueError("Identifiants d'entités invalides.")
        for key in self.entity_ids:
            uuid(key)


@dataclass(frozen=True)
class EvaluationDiagnostic:
    """Derived failure with its original causes, shared by host and plugins."""
    code: str
    message: str
    feature_id: str
    root_ids: tuple[str, ...] = ()
    reference: ReferenceSlot | None = None

    def __post_init__(self):
        uuid(self.feature_id)
        if type(self.code) is not str or not self.code or type(self.message) is not str:
            raise ValueError("Diagnostic de construction invalide.")
        if type(self.root_ids) is not tuple or len(set(self.root_ids)) != len(self.root_ids):
            raise ValueError("Causes de construction invalides.")
        for key in self.root_ids:
            uuid(key)
        if self.reference is not None and not isinstance(self.reference, ReferenceSlot):
            raise ValueError("Emplacement de diagnostic invalide.")


class InactiveReferenceError(ValueError):
    """A semantic input disappeared because its producing operation is inactive."""
    def __init__(self, message, root_ids):
        super().__init__(message)
        self.root_ids = tuple(dict.fromkeys(uuid(key) for key in root_ids))


class ReferenceResolutionError(ValueError):
    """Resolvers can attach the exact broken recipe location without Qt."""
    def __init__(self, message, reference):
        if not isinstance(reference, ReferenceSlot):
            raise ValueError("Emplacement de référence invalide.")
        super().__init__(message)
        self.reference = reference


@dataclass(frozen=True)
class EvaluationResult:
    status: ResultStatus
    shape: object = None
    message: str = ""
    feature_id: str | None = None
    # Derived naming history; rebuilt from recipes, never saved as project data.
    topology: object = field(default=None, compare=False, repr=False)
    effects: tuple = field(default=(), compare=False, repr=False)
    diagnostics: tuple[EvaluationDiagnostic, ...] = ()

    def __post_init__(self):
        if not isinstance(self.status, ResultStatus):
            raise ValueError("État de calcul invalide.")
        if type(self.message) is not str:
            raise ValueError("Le diagnostic doit être un texte.")
        if self.feature_id is not None:
            uuid(self.feature_id)
        if (self.status is ResultStatus.READY) != (self.shape is not None):
            raise ValueError("Seul un résultat disponible possède une forme courante.")
        if type(self.diagnostics) is not tuple or any(not isinstance(d, EvaluationDiagnostic) for d in self.diagnostics):
            raise ValueError("Diagnostics de résultat invalides.")
        if self.status is ResultStatus.READY and self.diagnostics:
            raise ValueError("Un résultat valide ne porte pas de diagnostic d'échec.")
