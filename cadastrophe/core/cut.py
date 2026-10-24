"""Checked subtractive boolean and directional through-all extent. No Qt/AIS."""
from dataclasses import dataclass
from itertools import product
from math import isfinite, ulp

from .extrusion import ExtrusionDiagnostic, _positive_finite


@dataclass(frozen=True)
class CutResult:
    success: bool
    shape: object = None
    diagnostics: tuple[ExtrusionDiagnostic, ...] = ()
    volume: float | None = None
    removed_volume: float | None = None
    base_faces: tuple = ()

    def __post_init__(self):
        if type(self.success) is not bool or self.success != (self.shape is not None):
            raise ValueError("Seule une coupe validée peut porter une forme.")
        if self.success:
            if self.diagnostics or not all(_positive_finite(v) for v in (self.volume, self.removed_volume)):
                raise ValueError("Résultat de coupe incomplet.")
        elif not self.diagnostics or self.volume is not None or self.removed_volume is not None or self.base_faces:
            raise ValueError("Un échec exige un diagnostic sans résultat partiel.")

    @property
    def message(self):
        return "" if self.success else self.diagnostics[0].message


def failure(code, message):
    return CutResult(False, diagnostics=(ExtrusionDiagnostic(code, message),))


def cut_solid(base, tool, *, tolerance=1e-7, base_faces=None, trace=None, allow_disconnected=False):
    """Remove positive volume; permit multiple solids only when opted in."""
    if not _positive_finite(tolerance):
        return failure("invalid_tolerance", "La tolérance doit être positive et finie en mm.")
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps
    from OCC.Core.TopAbs import TopAbs_COMPOUND, TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX
    from OCC.Core.TopExp import topexp
    from OCC.Core.TopTools import TopTools_IndexedMapOfShape, TopTools_ListOfShape
    from OCC.Core.TopoDS import TopoDS_Shape, topods

    def items(shape, kind):
        mapping = TopTools_IndexedMapOfShape()
        topexp.MapShapes(shape, kind, mapping)
        return tuple(mapping.FindKey(i) for i in range(1, mapping.Size()+1))

    def volume(shape):
        props = GProp_GProps()
        brepgprop.VolumeProperties(shape, props, True)
        return props.Mass()

    def valid(shape):
        return (isinstance(shape, TopoDS_Shape) and not shape.IsNull() and shape.ShapeType() == TopAbs_SOLID
                and BRepCheck_Analyzer(shape, True).IsValid() and bool(items(shape, TopAbs_SHELL))
                and all(BRep_Tool.IsClosed(topods.Shell(s)) for s in items(shape, TopAbs_SHELL)))

    def boolean(kind):
        args, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
        args.Append(base)
        tools.Append(tool)
        builder = kind()
        builder.SetArguments(args)
        builder.SetTools(tools)
        builder.SetNonDestructive(True)
        builder.Build()
        if not builder.IsDone() or builder.Shape().IsNull():
            raise ValueError("La soustraction n’a pas produit de résultat valide.")
        return builder

    try:
        tool_valid = valid(tool)
        if isinstance(tool, TopoDS_Shape) and not tool.IsNull() and tool.ShapeType() == TopAbs_COMPOUND:
            tools = items(tool, TopAbs_SOLID)
            tool_valid = (bool(tools) and all(valid(s) for s in tools) and BRepCheck_Analyzer(tool, True).IsValid()
                and all(len(items(tool, kind)) == sum(len(items(s, kind)) for s in tools)
                        for kind in (TopAbs_SHELL, TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX)))
        if allow_disconnected:
            from .operation_effects import checked_solids
            checked_solids(base)
        if (not allow_disconnected and not valid(base)) or not tool_valid:
            return failure("invalid_input", "L’enlèvement exige deux solides fermés et valides.")
        tolerance = max(float(tolerance), 1e-7,
            *(BRep_Tool.Tolerance(topods.Vertex(v)) for s in (base, tool) for v in items(s, TopAbs_VERTEX)))
        initial, tool_volume = volume(base), volume(tool)
        if any(not isfinite(v) or v <= tolerance**3 for v in (initial, tool_volume)):
            return failure("invalid_volume", "Un des solides possède un volume nul, inversé ou non fini.")
        precision = max(tolerance**3, 64*ulp(initial))
        overlap = volume(boolean(BRepAlgoAPI_Common).Shape())
        if not isfinite(overlap) or overlap < -precision:
            return failure("invalid_volume", "Le volume d’intersection n’est pas valide.")
        if overlap <= precision:
            return failure("no_removed_material", "Aucune matière enlevée. Vérifiez le corps cible, la profondeur et le sens.")
        builder = boolean(BRepAlgoAPI_Cut)
        result = builder.Shape()
        solids = items(result, TopAbs_SOLID)
        if not solids:
            return failure("empty_result", "La coupe supprimerait entièrement le corps ; opération refusée.")
        if len(solids) != 1 and not allow_disconnected:
            return failure("split_result", "La coupe séparerait le corps en plusieurs solides ; opération refusée.")
        solid = topods.Solid(solids[0]) if len(solids) == 1 else result
        if any(len(items(result, kind)) != sum(len(items(s, kind)) for s in solids) for kind in
               (TopAbs_SHELL, TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX)) or not all(valid(s) for s in solids):
            return failure("invalid_solid", "La coupe produit un solide ouvert, non manifold ou des composants parasites.")
        if allow_disconnected:
            checked_solids(solid)
        actual = volume(solid)
        removed = initial-actual
        if not isfinite(actual) or actual <= precision or removed <= precision:
            return failure("invalid_volume", "La coupe ne produit pas un enlèvement positif et un solide restant valide.")
        if abs(removed-overlap) > max(initial*1e-8, precision):
            return failure("volume_mismatch", "Le bilan de volume de la coupe est incohérent.")
        originals = items(base, TopAbs_FACE) if base_faces is None else base_faces
        preserved, final_faces = [], items(solid, TopAbs_FACE)
        for face in originals:
            for descendant in (face, *builder.Modified(face)):
                for final in final_faces:
                    if final.IsSame(descendant) and not any(final.IsSame(old) for old in preserved):
                        preserved.append(final)
        if trace is not None:
            trace.operation(builder, (base, tool), solid)
        return CutResult(True, solid, volume=actual, removed_volume=removed, base_faces=tuple(preserved))
    except (RuntimeError, ValueError, TypeError, ArithmeticError) as exc:
        return failure("native_error", f"Enlèvement impossible : {exc}")


def through_all_depth(base, face, *, reversed=False, tolerance=1e-7):
    """Bound the current target in the oriented profile direction, plus margin.

    This derived length is never a stored replacement for the through_all recipe.
    It is one-sided: material behind the sketch plane is not silently selected.
    """
    if type(reversed) is not bool or not _positive_finite(tolerance):
        raise ValueError("Sens ou tolérance de traversée invalide.")
    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.BRepBndLib import brepbndlib
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.GeomAbs import GeomAbs_Plane
    from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCC.Core.TopoDS import TopoDS_Shape, topods
    if (not isinstance(base, TopoDS_Shape) or base.IsNull() or not isinstance(face, TopoDS_Shape)
            or face.IsNull() or face.ShapeType() != TopAbs_FACE):
        raise ValueError("Une cible native et une face plane sont requises.")
    surface = BRepAdaptor_Surface(topods.Face(face), True)
    if surface.GetType() != GeomAbs_Plane:
        raise ValueError("La traversée exige un profil plan.")
    bounds = Bnd_Box()
    brepbndlib.Add(base, bounds)
    coordinates = bounds.Get()
    if not all(isfinite(value) for value in coordinates):
        raise ValueError("Les limites du corps cible ne sont pas finies.")
    origin = surface.Plane().Location().Coord()
    sign = (-1 if face.Orientation() == TopAbs_REVERSED else 1)*(-1 if reversed else 1)
    normal = tuple(sign*v for v in surface.Plane().Axis().Direction().Coord())
    far = max(sum((p[i]-origin[i])*normal[i] for i in range(3))
              for p in product(*((coordinates[i], coordinates[i+3]) for i in range(3))))
    if far <= max(tolerance, 1e-7)*2:
        raise ValueError("Le corps cible est derrière le plan de coupe ; inversez la direction.")
    span = max(coordinates[i+3]-coordinates[i] for i in range(3))
    margin = max(100*max(tolerance, 1e-7), span*1e-6,
                 64*ulp(max(abs(value) for value in (*coordinates, *origin))))
    depth = far+margin
    if not _positive_finite(depth):
        raise ValueError("La profondeur de traversée n’est pas représentable.")
    return depth
