"""Measurements of the selected B-Rep itself, never of its displayed mesh."""
from dataclasses import dataclass
from math import acos, asin, degrees, isfinite

from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.GeomAbs import GeomAbs_Line, GeomAbs_Circle, GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Sphere
from OCC.Core.GProp import GProp_GProps
from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID, TopAbs_SHELL
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopoDS import TopoDS_Shape, topods

MODES = {
    "distance": ("Distance minimale", "Sélectionnez exactement deux éléments (Ctrl+clic) : objets, faces, arêtes ou sommets."),
    "length": ("Longueur d’arête", "Sélectionnez exactement une arête en cliquant près de son tracé."),
    "radius": ("Rayon", "Sélectionnez une arête circulaire, une face cylindrique ou une face sphérique."),
    "angle": ("Angle minimal", "Sélectionnez deux arêtes droites, deux faces planes ou une de chaque. Angle non orienté entre 0 et 90°."),
    "area": ("Aire", "Sélectionnez exactement une face ou un objet contenant des faces."),
    'volume': ('Volume', 'Sélectionnez un ou plusieurs corps solides fermés.'),
    'mass': ('Masse', 'Sélectionnez des corps solides auxquels un matériau a été affecté.'),
}


class MeasurementError(ValueError):
    pass


@dataclass(frozen=True)
class Measurement:
    value: float
    dimension: str  # length: mm, area: mm², angle: degrees
    explanation: str

    def formatted(self, units):
        if self.dimension == 'volume':
            return units.format_volume(self.value)
        if self.dimension == 'mass':
            return units.format_mass(self.value)
        if self.dimension == "area":
            return units.format_area(self.value)
        if self.dimension == "angle":
            return f"{self.value:.{units.decimals}f} °"
        return units.format_length(self.value)


def _curve(shape):
    if shape.ShapeType() != TopAbs_EDGE:
        raise MeasurementError("La sélection doit être une arête.")
    edge = topods.Edge(shape)
    if BRep_Tool.Degenerated(edge):
        raise MeasurementError("Cette arête est dégénérée ; elle ne définit pas de courbe mesurable.")
    return BRepAdaptor_Curve(edge)


def _direction(shape):
    if shape.ShapeType() == TopAbs_EDGE:
        curve = _curve(shape)
        if curve.GetType() == GeomAbs_Line:
            return "line", curve.Line().Direction()
    elif shape.ShapeType() == TopAbs_FACE:
        surface = BRepAdaptor_Surface(topods.Face(shape), True)
        if surface.GetType() == GeomAbs_Plane:
            return "plane", surface.Plane().Axis().Direction()
    raise MeasurementError("L’angle exige des arêtes droites ou des faces planes ; les courbes et surfaces non planes sont exclues.")


def measure(mode, shapes, *, densities=None):
    """Pure read-only operation; coordinates/areas remain canonical mm/mm²."""
    try:
        if mode not in MODES:
            raise MeasurementError("Type de mesure inconnu.")
        shapes = tuple(shapes)
        physical = mode in {'volume', 'mass'}
        count = 2 if mode in ("distance", "angle") else 1
        if (not shapes if physical else len(shapes) != count):
            raise MeasurementError(MODES[mode][1])
        if any(not isinstance(shape, TopoDS_Shape) or shape.IsNull() for shape in shapes):
            raise MeasurementError("La sélection ne contient pas de forme B-Rep valide.")
        if any(shape.IsSame(other) for index, shape in enumerate(shapes) for other in shapes[:index]):
            raise MeasurementError("Sélectionnez deux éléments distincts.")
        shape = shapes[0]
        if physical:
            volumes = []
            for solid_shape in shapes:
                if not TopExp_Explorer(solid_shape, TopAbs_SOLID).More() or not BRepCheck_Analyzer(solid_shape, True).IsValid():
                    raise MeasurementError('Le volume exige des corps solides valides ; les faces, coques ouvertes et courbes sont exclues.')
                shells = TopExp_Explorer(solid_shape, TopAbs_SHELL)
                while shells.More():
                    if not BRep_Tool.IsClosed(shells.Current()):
                        raise MeasurementError('Le corps contient une coque ouverte et ne définit pas un volume fermé.')
                    shells.Next()
                from cadastrophe.core.mass_properties import precise_volume
                value = precise_volume(solid_shape)
                if not isfinite(value) or value <= 0:
                    raise MeasurementError('Le corps ne définit pas un volume positif valide.')
                volumes.append(value)
            if mode == 'mass':
                if (densities is None or len(densities) != len(shapes) or any(
                        type(d) not in (int, float) or not isfinite(d) or d <= 0 for d in densities)):
                    raise MeasurementError('Affectez un matériau avec une masse volumique positive à chaque corps sélectionné.')
                result = Measurement(sum(v*d/1_000_000. for v, d in zip(volumes, densities)), 'mass',
                                     'Masse calculée à partir du volume exact et du matériau de chaque corps. Les volumes superposés de corps distincts sont additionnés.')
            else:
                result = Measurement(sum(volumes), 'volume', 'Volume exact des corps sélectionnés. Les volumes de corps distincts sont additionnés, même en cas de recouvrement.')
        elif mode == "distance":
            solver = BRepExtrema_DistShapeShape(*shapes)
            if not solver.IsDone() or solver.NbSolution() < 1:
                raise MeasurementError("Impossible de calculer la distance entre ces éléments.")
            result = Measurement(solver.Value(), "length", "Distance minimale entre les formes sélectionnées, nulle si elles se touchent ou se recouvrent.")
        elif mode == "length":
            _curve(shape)
            props = GProp_GProps()
            brepgprop.LinearProperties(shape, props, False, False)
            result = Measurement(props.Mass(), "length", "Longueur de l’arête sélectionnée, avec ses limites réelles, y compris pour un arc.")
        elif mode == "radius":
            if shape.ShapeType() == TopAbs_EDGE:
                curve = _curve(shape)
                if curve.GetType() != GeomAbs_Circle:
                    raise MeasurementError("Cette arête n’est pas un cercle ou un arc de cercle.")
                value, detail = curve.Circle().Radius(), "Rayon du cercle support de l’arête sélectionnée."
            elif shape.ShapeType() == TopAbs_FACE:
                surface = BRepAdaptor_Surface(topods.Face(shape), True)
                if surface.GetType() == GeomAbs_Cylinder:
                    value, detail = surface.Cylinder().Radius(), "Rayon de la face cylindrique sélectionnée."
                elif surface.GetType() == GeomAbs_Sphere:
                    value, detail = surface.Sphere().Radius(), "Rayon de la face sphérique sélectionnée."
                else:
                    raise MeasurementError("Cette face n’a pas de rayon unique pris en charge : choisissez une face cylindrique ou sphérique.")
            else:
                raise MeasurementError(MODES[mode][1])
            result = Measurement(value, "length", detail)
        elif mode == "angle":
            first_kind, first = _direction(shapes[0])
            second_kind, second = _direction(shapes[1])
            dot = max(0., min(1., abs(first.Dot(second))))
            value = degrees(acos(dot) if first_kind == second_kind else asin(dot))
            result = Measurement(value, "angle", "Angle minimal non orienté (0–90°), indépendant du sens des arêtes et des normales de faces.")
        else:
            if not TopExp_Explorer(shape, TopAbs_FACE).More():
                raise MeasurementError("L’aire exige une face ou un objet contenant des faces ; une arête ou un sommet n’a pas d’aire.")
            props = GProp_GProps()
            brepgprop.SurfaceProperties(shape, props, True, False)
            result = Measurement(props.Mass(), "area", "Aire de la face sélectionnée ou aire totale des surfaces de l’objet.")
        if not isfinite(result.value) or result.value < 0:
            raise MeasurementError("Le calcul a retourné une valeur invalide.")
        return result
    except MeasurementError:
        raise
    except (RuntimeError, ValueError, TypeError, OverflowError) as exc:
        raise MeasurementError(f"Mesure impossible : {exc}") from exc
