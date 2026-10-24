"""Construction records in the existing Document, without native imports."""
from dataclasses import dataclass, field, replace

from ..document import SceneObject, ObjectType
from .feature_model import Operation, SketchInput, ExtrusionParameters, EdgeParameters, BodyStateRef, uuid


class SketchSourceObject(SceneObject):
    """Public semantic contract, shared across isolated plugin namespaces."""
    @property
    def reference_slots(self):
        return ()

    @property
    def body_dependencies(self):
        return ()

    @property
    def profile_source_ids(self):
        return ()

    @property
    def document_dependencies(self):
        return self.body_dependencies

    @property
    def producer_refs(self):
        return ()

    def reference_error(self, document):
        return ""

    def resolved(self, document):
        return self


@dataclass(frozen=True)
class BodyObject(SceneObject):
    features: tuple[str, ...] = field(default=(), kw_only=True)
    # The original B-Rep of an explicitly adopted primitive/import. Not a cache.
    source_shape: object = field(default=None, kw_only=True, compare=False)
    source_kind: str | None = field(default=None, kw_only=True)

    def __post_init__(self):
        super().__post_init__()
        if self.kind is not ObjectType.BODY or self.plane is not None:
            raise ValueError("Un corps de construction doit être de type body.")
        if type(self.features) is not tuple or len(set(self.features)) != len(self.features):
            raise ValueError("Ordre de fonctions invalide ou dupliqué.")
        for key in self.features:
            uuid(key)
        if self.source_kind not in (None, "primitive", "import", "static"):
            raise ValueError("Origine de base statique inconnue.")
        # During codec decode the separately stored source is not attached yet.
        if self.source_kind is None and self.source_shape is not None:
            raise ValueError("Une B-Rep source exige une origine explicite.")


@dataclass(frozen=True)
class ConstructionFeature(SceneObject):
    enabled: bool = field(default=True, kw_only=True)
    replayable = False

    def __post_init__(self):
        super().__post_init__()
        if type(self.enabled) is not bool:
            raise ValueError("L'activation d'une fonction doit être booléenne.")

    @property
    def evaluation_inputs(self):
        """Effective values, including future configuration materialization.

        Configuration names/IDs and appearance are not geometric inputs.
        Equations/configurations must prepare existing numeric recipes first.
        """
        return self.enabled, self.operation, self.parameters, self.recipe_source

    @property
    def profile_sections(self):
        source = getattr(self, "source", None)
        return ((source, self.parameters.contours),) if isinstance(source, SketchInput) else ()

    def with_profile_supports(self, sketch_id, supports):
        if self.sketch_id == sketch_id:
            return replace(self, source=replace(self.source, supports=supports))
        return self

    @property
    def target_dependent_effect(self):
        return False
    @property
    def sketch_id(self):
        return self.source.sketch_id if hasattr(self, "source") else None

    @property
    def support_refs(self):
        return self.source.supports if hasattr(self, "source") else ()

    @property
    def recipe_source(self):
        return getattr(self, "source", None)


@dataclass(frozen=True)
class EdgeFeature(ConstructionFeature):
    body_id: str = field(kw_only=True)
    operation: Operation = field(kw_only=True)
    parameters: EdgeParameters = field(kw_only=True)

    def __post_init__(self):
        super().__post_init__()
        uuid(self.body_id)
        if self.kind is not ObjectType.FEATURE or not isinstance(self.operation, Operation) or self.operation not in (Operation.FILLET, Operation.CHAMFER):
            raise ValueError("Fonction de traitement d’arêtes invalide.")
        if not isinstance(self.parameters, EdgeParameters):
            raise ValueError("Paramètres d’arêtes invalides.")
        allowed = {"symmetric", "variable"} if self.operation is Operation.FILLET else {"symmetric", "two_distances", "distance_angle"}
        if self.parameters.mode not in allowed:
            raise ValueError("Méthode incompatible avec cette fonction.")


@dataclass(frozen=True)
class ShellFeature(ConstructionFeature):
    body_id: str = field(kw_only=True)
    operation: Operation = field(kw_only=True)
    input_state: BodyStateRef = field(kw_only=True)
    parameters: object = field(kw_only=True)

    def __post_init__(self):
        from .operation_model import ShellParameters
        super().__post_init__()
        uuid(self.body_id)
        if (self.kind is not ObjectType.FEATURE or self.operation is not Operation.SHELL
                or not isinstance(self.input_state, BodyStateRef) or self.input_state.body_id != self.body_id
                or not isinstance(self.parameters, ShellParameters)):
            raise ValueError("Description de coque invalide.")
        if any(r.object_id != self.body_id for r in self.parameters.faces):
            raise ValueError("Les faces à ouvrir doivent appartenir au corps de la coque.")

    @property
    def geometry_references(self):
        return self.parameters.faces

    @property
    def support_refs(self):
        return (self.input_state,)

    @property
    def recipe_source(self):
        return self.input_state


@dataclass(frozen=True)
class DraftFeature(ConstructionFeature):
    body_id: str = field(kw_only=True)
    operation: Operation = field(kw_only=True)
    input_state: BodyStateRef = field(kw_only=True)
    parameters: object = field(kw_only=True)

    def __post_init__(self):
        from .operation_model import DraftParameters
        super().__post_init__()
        uuid(self.body_id)
        if (self.kind is not ObjectType.FEATURE or self.operation is not Operation.DRAFT
                or not isinstance(self.input_state, BodyStateRef) or self.input_state.body_id != self.body_id
                or not isinstance(self.parameters, DraftParameters)):
            raise ValueError("Description de dépouille invalide.")
        if any(r.object_id != self.body_id for r in self.parameters.faces):
            raise ValueError("Les faces à dépouiller doivent appartenir au corps de la dépouille.")

    @property
    def geometry_references(self):
        return (*self.parameters.faces, self.parameters.neutral, self.parameters.direction)

    @property
    def support_refs(self):
        return (self.input_state,)

    @property
    def recipe_source(self):
        return self.input_state


@dataclass(frozen=True)
class ExtrusionFeature(ConstructionFeature):
    replayable = True
    operation: Operation = field(kw_only=True)
    body_id: str = field(kw_only=True)
    source: SketchInput = field(kw_only=True)
    parameters: ExtrusionParameters = field(default_factory=ExtrusionParameters, kw_only=True)

    @property
    def target_dependent_effect(self):
        return self.parameters.end_condition == "through_all"

    def __post_init__(self):
        super().__post_init__()
        if self.kind is not ObjectType.FEATURE:
            raise ValueError("Une opération est un objet logique sans B-Rep.")
        uuid(self.body_id)
        if (not isinstance(self.operation, Operation) or not isinstance(self.source, SketchInput)
                or not isinstance(self.parameters, ExtrusionParameters)):
            raise ValueError("Description de fonction invalide.")
        if self.operation is not Operation.CUT and self.parameters.end_condition != "blind":
            raise ValueError("À travers tout est réservé aux enlèvements de matière.")
        if self.operation is Operation.BOSS and not self.parameters.merge_result:
            raise ValueError("Un bossage dans un corps exige la fusion ; utiliser new_body sinon.")
        if self.operation not in (Operation.NEW_BODY, Operation.BOSS, Operation.CUT):
            raise ValueError("Opération incompatible avec une extrusion.")


@dataclass(frozen=True)
class RevolutionFeature(ConstructionFeature):
    replayable = True
    operation: Operation = field(kw_only=True)
    body_id: str = field(kw_only=True)
    source: SketchInput = field(kw_only=True)
    parameters: object = field(kw_only=True)

    def __post_init__(self):
        from .operation_model import RevolutionParameters
        super().__post_init__()
        uuid(self.body_id)
        if (self.kind is not ObjectType.FEATURE or self.operation not in (Operation.NEW_BODY, Operation.BOSS, Operation.CUT)
                or not isinstance(self.source, SketchInput) or not isinstance(self.parameters, RevolutionParameters)):
            raise ValueError("Description de révolution invalide.")

    @property
    def geometry_references(self):
        return (self.parameters.axis,)


@dataclass(frozen=True)
class SweepFeature(ConstructionFeature):
    replayable = True
    operation: Operation = field(kw_only=True)
    body_id: str = field(kw_only=True)
    source: SketchInput = field(kw_only=True)
    parameters: object = field(kw_only=True)

    def __post_init__(self):
        from .operation_model import SweepParameters
        super().__post_init__()
        uuid(self.body_id)
        if (self.kind is not ObjectType.FEATURE or self.operation not in (Operation.NEW_BODY, Operation.BOSS, Operation.CUT)
                or not isinstance(self.source, SketchInput) or not isinstance(self.parameters, SweepParameters)):
            raise ValueError("Description de balayage invalide.")

    @property
    def geometry_references(self):
        return self.parameters.path


@dataclass(frozen=True)
class LoftFeature(ConstructionFeature):
    replayable = True
    operation: Operation = field(kw_only=True)
    body_id: str = field(kw_only=True)
    parameters: object = field(kw_only=True)

    def __post_init__(self):
        from .operation_model import LoftParameters
        super().__post_init__()
        uuid(self.body_id)
        if (self.kind is not ObjectType.FEATURE or self.operation not in (Operation.NEW_BODY, Operation.BOSS, Operation.CUT)
                or not isinstance(self.parameters, LoftParameters)):
            raise ValueError("Description de lissage invalide.")

    @property
    def profile_sections(self):
        return tuple((s.source, s.contours) for s in self.parameters.sections)

    @property
    def support_refs(self):
        return tuple(dict.fromkeys(ref for source, _ in self.profile_sections for ref in source.supports))

    @property
    def recipe_source(self):
        return tuple(source for source, _ in self.profile_sections)

    def with_profile_supports(self, sketch_id, supports):
        sections = tuple(replace(s, source=replace(s.source, supports=supports)) if s.source.sketch_id == sketch_id else s
                         for s in self.parameters.sections)
        return replace(self, parameters=replace(self.parameters, sections=sections))


@dataclass(frozen=True)
class HoleFeature(ConstructionFeature):
    replayable = True
    operation: Operation = field(kw_only=True)
    body_id: str = field(kw_only=True)
    source: SketchInput = field(kw_only=True)
    parameters: object = field(kw_only=True)

    def __post_init__(self):
        from .hole_model import HoleParameters
        super().__post_init__()
        uuid(self.body_id)
        if (self.kind is not ObjectType.FEATURE or self.operation is not Operation.CUT
                or not isinstance(self.source, SketchInput) or not isinstance(self.parameters, HoleParameters)
                or any(r.object_id != self.source.sketch_id for r in self.parameters.positions)):
            raise ValueError("Description de perçage guidé invalide.")

    @property
    def profile_sections(self):
        # The same sketch dependency contract also carries point-only inputs.
        return ((self.source, ()),)

    @property
    def geometry_references(self):
        return self.parameters.positions

    @property
    def target_dependent_effect(self):
        return self.parameters.end_condition == "through_all"

    def equation_dimensions(self):
        from .parameters import Dimension
        p = self.parameters
        keys = ["diameter"]
        if p.end_condition == "blind":
            keys.append("depth")
        if p.form != "simple":
            keys += ["head_diameter", "head_depth" if p.form == "counterbore" else "angle"]
        return tuple(Dimension(self.id, key, f"D{i}@{self.name}", getattr(p, key),
                               "angle" if key == "angle" else "length") for i, key in enumerate(keys, 1))


@dataclass(frozen=True)
class ThreadFeature(ConstructionFeature):
    replayable = True
    body_id: str = field(kw_only=True)
    operation: Operation = field(kw_only=True)
    input_state: BodyStateRef = field(kw_only=True)
    parameters: object = field(kw_only=True)

    def __post_init__(self):
        from .thread_model import ThreadParameters
        super().__post_init__()
        uuid(self.body_id)
        if (self.kind is not ObjectType.FEATURE or self.operation is not Operation.CUT
                or not isinstance(self.input_state, BodyStateRef) or self.input_state.body_id != self.body_id
                or not isinstance(self.parameters, ThreadParameters) or self.parameters.face.object_id != self.body_id):
            raise ValueError("Description de filetage invalide.")

    @property
    def geometry_references(self):
        return (self.parameters.face,)

    @property
    def support_refs(self):
        return (self.input_state,)

    @property
    def recipe_source(self):
        return self.input_state

    def equation_dimensions(self):
        from .parameters import Dimension
        keys = ("diameter", "pitch", "length", "offset", "phase", "angle", "depth", "root_flat",
                "root_radius", "clearance", "lead_in", "lead_out", "starts")
        return tuple(Dimension(self.id, key, f"D{i}@{self.name}", getattr(self.parameters, key),
                               "angle" if key in {"phase", "angle"} else "number" if key == "starts" else "length",
                               key == "starts") for i, key in enumerate(keys, 1))


@dataclass(frozen=True)
class ReplayFeature(ConstructionFeature):
    replayable = True
    operation: Operation = field(kw_only=True)
    body_id: str = field(kw_only=True)
    parameters: object = field(kw_only=True)

    def __post_init__(self):
        from .operation_model import ReplayParameters
        super().__post_init__()
        uuid(self.body_id)
        if (self.kind is not ObjectType.FEATURE or not isinstance(self.parameters, ReplayParameters)
                or self.operation not in (Operation.MIRROR, Operation.LINEAR_PATTERN, Operation.CIRCULAR_PATTERN)):
            raise ValueError("Description de symétrie ou répétition invalide.")

    @property
    def geometry_references(self):
        return (self.parameters.reference,)

    @property
    def recipe_source(self):
        return self.parameters.sources
