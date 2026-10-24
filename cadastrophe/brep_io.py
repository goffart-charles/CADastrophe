"""The native OCCT codec used by document persistence, without view handles."""
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepTools import BRepTools_ShapeSet
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.TopoDS import TopoDS_Shape

from .document import ObjectType


def _validate(shape, kind):
    if not isinstance(shape, TopoDS_Shape) or shape.IsNull() or not BRepCheck_Analyzer(shape).IsValid():
        raise ValueError("Forme B-Rep absente ou géométrie invalide.")
    if kind is ObjectType.REFERENCE_PLANE and shape.ShapeType() != TopAbs_FACE:
        raise ValueError("La géométrie d’un plan doit être une face.")


def encode_shape(shape, kind):
    _validate(shape, kind)
    return BRepTools_ShapeSet.WriteToString(shape, True).encode("ascii")


def decode_shape(payload, kind):
    shape = BRepTools_ShapeSet.ReadFromString(payload.decode("ascii"))
    _validate(shape, kind)
    return shape
