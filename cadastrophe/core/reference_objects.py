"""Parametric datum records in the ordinary document and undo history."""
from dataclasses import dataclass, field

from ..document import SceneObject, ObjectType
from .operation_model import PlaneParameters


@dataclass(frozen=True)
class DatumPlaneObject(SceneObject):
    parameters: PlaneParameters = field(kw_only=True)
    reference_error_message: str = field(default="", kw_only=True, compare=False)

    def __post_init__(self):
        super().__post_init__()
        if self.kind is not ObjectType.REFERENCE_PLANE or not isinstance(self.parameters, PlaneParameters):
            raise ValueError("Un plan paramétrique exige un repère et une définition de plan.")

    @property
    def geometry_references(self):
        return self.parameters.references
