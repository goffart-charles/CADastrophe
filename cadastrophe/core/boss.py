"""Checked additive boolean, independent of documents, Qt and rendering."""
from dataclasses import dataclass
from math import isfinite, ulp

from .extrusion import ExtrusionDiagnostic, _positive_finite


@dataclass(frozen=True)
class BossResult:
    success: bool
    shape: object = None
    diagnostics: tuple[ExtrusionDiagnostic, ...] = ()
    volume: float | None = None
    base_faces: tuple = ()  # Result faces descended from the original base.

    def __post_init__(self):
        if type(self.success) is not bool or self.success != (self.shape is not None):
            raise ValueError("Seule une fusion validée peut porter une forme.")
        if self.success:
            if self.diagnostics or not _positive_finite(self.volume):
                raise ValueError("Résultat de fusion incomplet.")
        elif not self.diagnostics or self.volume is not None:
            raise ValueError("Un échec exige un diagnostic sans résultat partiel.")

    @property
    def message(self):
        return "" if self.success else self.diagnostics[0].message


def _failure(code, message):
    return BossResult(False, diagnostics=(ExtrusionDiagnostic(code, message),))


def fuse_boss(base, prism, *, tolerance=1e-7, base_faces=None, trace=None, allow_disconnected=False):
    """Fuse closed solids without modifying them; accept face contact.

    Disconnected pieces, edge/point contact and a tool wholly inside the base
    never constitute an additive feature unless disconnected results were
    explicitly allowed. No automatic switch to new-body mode.
    """
    if not _positive_finite(tolerance):
        return _failure("invalid_tolerance", "La tolérance doit être positive et finie en mm.")
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Fuse
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps
    from OCC.Core.TopAbs import TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX
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
        return (isinstance(shape, TopoDS_Shape) and not shape.IsNull()
                and shape.ShapeType() == TopAbs_SOLID and BRepCheck_Analyzer(shape, True).IsValid()
                and bool(items(shape, TopAbs_SHELL))
                and all(BRep_Tool.IsClosed(topods.Shell(s)) for s in items(shape, TopAbs_SHELL)))

    def boolean(kind):
        # SetNonDestructive must precede Build: default constructors build early.
        args, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
        args.Append(base)
        tools.Append(prism)
        builder = kind()
        builder.SetArguments(args)
        builder.SetTools(tools)
        builder.SetNonDestructive(True)
        builder.Build()
        if not builder.IsDone() or builder.Shape().IsNull():
            raise ValueError("La fusion n’a pas produit de résultat valide.")
        return builder

    try:
        if allow_disconnected:
            from .operation_effects import checked_solids
            checked_solids(base)
        if (not allow_disconnected and not valid(base)) or not valid(prism):
            return _failure("invalid_input", "La fusion exige deux solides fermés et valides.")
        tolerance = max(float(tolerance), 1e-7,
                        *(BRep_Tool.Tolerance(topods.Vertex(v)) for s in (base, prism)
                          for v in items(s, TopAbs_VERTEX)))
        base_volume, tool_volume = volume(base), volume(prism)
        if any(not isfinite(v) or v <= tolerance**3 for v in (base_volume, tool_volume)):
            return _failure("invalid_volume", "Un des solides possède un volume nul, inversé ou non fini.")
        common = boolean(BRepAlgoAPI_Common).Shape()
        overlap = volume(common)
        builder = boolean(BRepAlgoAPI_Fuse)
        result = builder.Shape()
        solids = items(result, TopAbs_SOLID)
        # OCCT commonly returns a compound containing the single fused solid.
        # Reject stray faces/edges/vertices rather than hiding them on extraction.
        if not allow_disconnected and len(solids) != 1:
            return _failure("disconnected", "La fusion ne forme pas un seul solide. Décochez « Fusionner le résultat » pour autoriser plusieurs solides, ou rapprochez les occurrences.")
        if not solids or any(len(items(result, kind)) != sum(len(items(s, kind)) for s in solids)
                                   for kind in (TopAbs_SHELL, TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX)):
            return _failure("invalid_solid", "La fusion produit des composants parasites ou des contacts par une arête ou un sommet ; modifiez le profil ou l’espacement.")
        solid = topods.Solid(solids[0]) if len(solids) == 1 else result
        if not all(valid(s) for s in solids):
            return _failure("non_manifold", "Le contact produit une géométrie non manifold (arête ou sommet seul). Choisissez Nouveau corps ou modifiez le profil.")
        if allow_disconnected:
            checked_solids(solid)
        actual = volume(solid)
        expected = base_volume + tool_volume - overlap
        if not isfinite(expected):
            return _failure("invalid_volume", "Le volume attendu n’est pas fini.")
        accuracy = max(abs(expected)*1e-8, 64*ulp(expected), tolerance**3)
        if (not isfinite(overlap) or overlap < -accuracy or not isfinite(actual)
                or abs(actual-expected) > accuracy):
            return _failure("volume_mismatch", "Le volume de la fusion est incohérent ; opération refusée.")
        if actual-base_volume <= max(tolerance**3, 64*ulp(base_volume)):
            return _failure("no_added_material", "Aucune matière ajoutée au corps cible. Inversez la direction ou choisissez Nouveau corps.")
        originals = items(base, TopAbs_FACE) if base_faces is None else base_faces
        preserved = []
        final_faces = items(solid, TopAbs_FACE)
        for face in originals:
            descendants = (face, *builder.Modified(face))
            for descendant in descendants:
                for final in final_faces:
                    if final.IsSame(descendant) and not any(final.IsSame(old) for old in preserved):
                        preserved.append(final)
        if trace is not None:
            trace.operation(builder, (base, prism), solid)
        return BossResult(True, solid, volume=actual, base_faces=tuple(preserved))
    except (RuntimeError, ValueError, TypeError, ArithmeticError) as exc:
        return _failure("native_error", f"Fusion impossible : {exc}")
