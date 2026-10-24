"""Native geometry used by development scenarios; excluded from installations."""
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCC.Core.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCC.Core.TopAbs import TopAbs_EDGE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopoDS import topods
from OCC.Core.gp import gp_Ax2, gp_Ax3, gp_Dir, gp_Pln, gp_Pnt

from .document import Document, SceneObject
from .geometry import REFERENCE_PLANES


from .scene import _add_reference_planes


def demo_document(name="Document de démonstration") -> Document:
    doc = Document(name)
    doc.add(SceneObject("Cube", BRepPrimAPI_MakeBox(gp_Pnt(-75, -55, 0), 40, 40, 40).Shape(), (0.64, 0.73, 0.85)))
    cylinder = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, -35, 0), gp_Dir(0, 0, 1)), 22, 52).Shape()
    doc.add(SceneObject("Cylindre", cylinder, (0.75, 0.76, 0.79)))
    block = BRepPrimAPI_MakeBox(gp_Pnt(40, -55, 0), 55, 42, 26).Shape()
    drill = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(67, -34, -2), gp_Dir(0, 0, 1)), 10, 30).Shape()
    cut = BRepAlgoAPI_Cut(block, drill)
    cut.Build()
    if not cut.IsDone():
        raise RuntimeError("Échec du perçage")
    doc.add(SceneObject("Bloc percé Ø20", cut.Shape(), (0.78, 0.66, 0.47)))
    rounded = BRepPrimAPI_MakeBox(gp_Pnt(-32, 20, 0), 64, 40, 24).Shape()
    fillet = BRepFilletAPI_MakeFillet(rounded)
    explorer = TopExp_Explorer(rounded, TopAbs_EDGE)
    while explorer.More():
        fillet.Add(3.0, topods.Edge(explorer.Current()))
        explorer.Next()
    fillet.Build()
    if not fillet.IsDone():
        raise RuntimeError("Échec des congés")
    doc.add(SceneObject("Bloc avec congés R3", fillet.Shape(), (0.50, 0.72, 0.68)))
    _add_reference_planes(doc)
    doc.history.clear()  # Generated scene is a baseline, not seven user edits.
    return doc

