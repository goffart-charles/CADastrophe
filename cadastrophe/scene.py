"""Create a blank part with its three reference planes."""
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCC.Core.gp import gp_Ax3, gp_Dir, gp_Pln, gp_Pnt

from .document import Document, SceneObject
from .geometry import REFERENCE_PLANES


def new_document(name="Pièce") -> Document:
    doc = Document(name)
    _add_reference_planes(doc, visible=True)
    doc.history.clear()
    return doc


def _add_reference_planes(doc, *, visible=False):
    colors = ((0.45, 0.65, 0.90), (0.65, 0.85, 0.50), (0.90, 0.60, 0.45))
    for plane, color in zip(REFERENCE_PLANES, colors):
        frame = gp_Ax3(gp_Pnt(*plane.origin.tuple()), gp_Dir(*plane.normal.tuple()), gp_Dir(*plane.u.tuple()))
        face = BRepBuilderAPI_MakeFace(gp_Pln(frame), -105, 105, -85, 85).Face()
        doc.add(SceneObject(f"Plan {plane.name}", face, color, plane, visible=visible))
