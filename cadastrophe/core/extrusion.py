"""Blind prismatic extrusion of one planar face, in mm. No Qt, AIS or document.

Native imports are lazy. Inputs are never healed or modified; OCCT builds with
Copy=True. Failure returns diagnostics and no shape, including on validation.
"""
from dataclasses import dataclass
from math import isfinite, ulp


def _positive_finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return value > 0 and isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True)
class ExtrusionDiagnostic:
    code: str
    message: str
    region_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtrusionResult:
    success: bool
    shape: object = None
    diagnostics: tuple[ExtrusionDiagnostic, ...] = ()
    volume: float | None = None  # mm³
    expected_volume: float | None = None
    translation: tuple[float, float, float] | None = None
    tolerance: float | None = None  # effective native linear tolerance, mm

    def __post_init__(self):
        if type(self.success) is not bool or self.success != (self.shape is not None):
            raise ValueError("Seule une extrusion réussie peut porter une forme.")
        if self.success:
            if (self.diagnostics or self.translation is None or
                    any(not _positive_finite(value) for value in (self.volume, self.expected_volume, self.tolerance))):
                raise ValueError("Résultat d'extrusion incomplet ou non valide.")
        elif (not self.diagnostics or any(value is not None for value in
                                        (self.volume, self.expected_volume, self.translation, self.tolerance))):
            raise ValueError("Un échec exige un diagnostic et ne conserve aucun résultat partiel.")

    @property
    def message(self):
        return "" if self.success else self.diagnostics[0].message


def failure(code, message, region_ids=()):
    return ExtrusionResult(False, diagnostics=(ExtrusionDiagnostic(code, message, tuple(region_ids)),))


def validate_options(depth, reversed=False, tolerance=1e-7):
    """Return an input diagnostic, or None; no geometry or native import needed."""
    if not _positive_finite(depth):
        return failure("invalid_depth", "La profondeur doit être un nombre positif fini en mm.")
    if type(reversed) is not bool:
        return failure("invalid_direction", "Le sens inversé doit être booléen.")
    if not _positive_finite(tolerance):
        return failure("invalid_tolerance", "La tolérance doit être positive et finie en mm.")
    return None


def extrude_face(face, depth, *, reversed=False, tolerance=1e-7, trace=None):
    """Return a checked single solid along the face's oriented plane normal.

    The input must be one bounded planar face, typically produced by X2. Multiple
    regions must be selected/split explicitly by the caller; no compound input
    is accepted. A reversed extrusion changes the translation, never its sketch.
    """
    error = validate_options(depth, reversed, tolerance)
    if error is not None:
        return error

    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.BRepBndLib import brepbndlib
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCC.Core.GeomAbs import GeomAbs_Plane
    from OCC.Core.GProp import GProp_GProps
    from OCC.Core.TopAbs import (TopAbs_FACE, TopAbs_FORWARD, TopAbs_REVERSED,
                                 TopAbs_SHELL, TopAbs_SOLID, TopAbs_VERTEX, TopAbs_WIRE)
    from OCC.Core.TopExp import topexp
    from OCC.Core.TopTools import TopTools_IndexedMapOfShape
    from OCC.Core.TopoDS import TopoDS_Shape, topods
    from OCC.Core.gp import gp_Pnt, gp_Vec

    def items(shape, kind):
        mapping = TopTools_IndexedMapOfShape()
        topexp.MapShapes(shape, kind, mapping)
        return tuple(mapping.FindKey(index) for index in range(1, mapping.Size()+1))

    try:
        if not isinstance(face, TopoDS_Shape) or face.IsNull() or face.ShapeType() != TopAbs_FACE:
            return failure("invalid_face", "Une seule face plane non nulle est requise.")
        face = topods.Face(face)
        if face.Orientation() not in (TopAbs_FORWARD, TopAbs_REVERSED) or not BRepCheck_Analyzer(face, True).IsValid():
            return failure("invalid_face", "La face de départ n'est pas une frontière orientée valide.")
        surface = BRepAdaptor_Surface(face, True)
        if surface.GetType() != GeomAbs_Plane:
            return failure("non_planar", "L'extrusion exige une face de profil plane.")
        wires = items(face, TopAbs_WIRE)
        if not wires or any(not BRep_Tool.IsClosed(topods.Wire(wire)) for wire in wires):
            return failure("open_profile", "Le profil doit posséder des frontières fermées et bornées.")
        bounds = Bnd_Box()
        brepbndlib.Add(face, bounds)
        coordinates = bounds.Get()
        if not all(isfinite(value) for value in coordinates):
            return failure("numerical_error", "Le profil possède des limites non finies.")
        linear_tolerance = max(float(tolerance), 1e-7, 8*ulp(max(abs(value) for value in coordinates)),
                               *(BRep_Tool.Tolerance(topods.Vertex(vertex)) for vertex in items(face, TopAbs_VERTEX)))
        if depth <= linear_tolerance:
            return failure("depth_precision", "La profondeur est trop faible pour la précision native de ce profil.")
        normal = surface.Plane().Axis().Direction()
        sign = (-1 if face.Orientation() == TopAbs_REVERSED else 1) * (-1 if reversed else 1)
        translation = tuple(sign*depth*value for value in normal.Coord())
        if not all(isfinite(value + translation[index % 3]) for index, value in enumerate(coordinates)):
            return failure("numerical_error", "La profondeur dépasse les coordonnées natives représentables.")
        # A nearby origin stabilizes mass integration for translated profiles.
        center = gp_Pnt(*(coordinates[index]/2 + coordinates[index+3]/2 for index in range(3)))
        area_properties = GProp_GProps(center)
        brepgprop.SurfaceProperties(face, area_properties)
        area = area_properties.Mass()
        expected = area*depth
        if not isfinite(area) or area <= linear_tolerance**2 or not isfinite(expected) or expected <= 0:
            return failure("profile_area", "L'aire ou le volume attendu est nul, non fini ou trop petit.")

        builder = BRepPrimAPI_MakePrism(face, gp_Vec(*translation), True, True)
        if not builder.IsDone():
            return failure("prism_failed", "La construction de l'extrusion n'a pas abouti.")
        shape = builder.Shape()
        if not isinstance(shape, TopoDS_Shape) or shape.IsNull():
            return failure("null_result", "La construction a produit un résultat vide.")
        if shape.ShapeType() != TopAbs_SOLID or len(items(shape, TopAbs_SOLID)) != 1:
            return failure("solid_count", "L'extrusion doit produire exactement un solide, sans compound ni composant masqué.")
        solid = topods.Solid(shape)
        if not BRepCheck_Analyzer(solid, True).IsValid():
            return failure("invalid_solid", "Le solide extrudé n'est pas valide.")
        shells = items(solid, TopAbs_SHELL)
        if not shells or any(not BRep_Tool.IsClosed(topods.Shell(shell)) for shell in shells):
            return failure("open_solid", "Le solide extrudé n'est pas entièrement fermé.")
        volume_properties = GProp_GProps(center)
        brepgprop.VolumeProperties(solid, volume_properties, True)
        volume = volume_properties.Mass()
        if not isfinite(volume) or volume <= linear_tolerance**3:
            return failure("invalid_volume", "Le volume du solide est nul, inversé ou non fini.")
        if abs(volume - expected) > max(expected*1e-7, 64*ulp(expected)):
            # Multi-span spline boundaries require adaptive integration. Keep
            # the same geometric validation and accuracy target for all faces.
            area_properties, volume_properties = GProp_GProps(center), GProp_GProps(center)
            brepgprop.SurfaceProperties(face, area_properties, 1e-10)
            brepgprop.VolumeProperties(solid, volume_properties, 1e-10, True)
            area, volume = area_properties.Mass(), volume_properties.Mass()
            expected = area*depth
            if not all(isfinite(v) and v > 0 for v in (area, volume, expected)):
                return failure("invalid_volume", "L’intégration du profil ne fournit pas un volume positif fini.")
        if abs(volume - expected) > max(expected*1e-7, 64*ulp(expected)):
            return failure("volume_mismatch", "Le volume natif ne correspond pas à l'aire du profil multipliée par la profondeur.")
        if trace is not None:
            trace.prism(builder, face, solid)
        return ExtrusionResult(True, solid, volume=volume, expected_volume=expected,
                               translation=translation, tolerance=linear_tolerance)
    except (RuntimeError, ValueError, TypeError, ArithmeticError) as exc:
        return failure("native_error", f"Extrusion impossible : {exc}")
