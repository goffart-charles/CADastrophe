"""Exact planar faces derived from E7 profiles, without document/UI mutation.

One face per material region. Holes remain holes; nested islands and disjoint
regions remain separate faces. No prism, solid, boolean or healing is built here.
Native imports are lazy, as in profile_native.
"""
from dataclasses import dataclass
from math import fsum, hypot, isfinite, pi, ulp

from .model import Arc, Circle, Segment
from .profile_geometry import ends
from .profile_native import ProfileError, to_wires
from .profiles import Diagnostic, analyze


@dataclass(frozen=True)
class FaceRegion:
    id: str  # Stable outer contour ID, not a native topology index.
    hole_ids: tuple[str, ...]
    face: object
    area: float  # mm², checked against analytic area including all holes.


@dataclass(frozen=True)
class NativeFaces:
    regions: tuple[FaceRegion, ...]
    tolerance: float  # Effective native linear tolerance, mm.

    @property
    def faces(self):
        return tuple(region.face for region in self.regions)

    @property
    def area(self):
        return fsum(region.area for region in self.regions)


def _fail(code, message, analysis, entity_ids=()):
    from dataclasses import replace
    report = replace(analysis, diagnostics=(*analysis.diagnostics,
                     Diagnostic(code, message, tuple(entity_ids))))
    return ProfileError(message, report)


def _native_analysis(sketch, report):
    """Respect both local accuracy and loss of precision in world coordinates."""
    magnitude = 0.
    for entity in sketch.profile_entities:
        if type(entity) is Segment:
            magnitude = max(magnitude, *(abs(value) for point in ends(entity) for value in (point.u, point.v)))
        elif isinstance(entity, Circle):
            magnitude = max(magnitude, abs(entity.center.u) + entity.radius, abs(entity.center.v) + entity.radius)
    bound = max(abs(value) for value in sketch.support.frame.origin.tuple()) + 2*magnitude
    if not isfinite(bound):
        raise _fail("native_precision", "Le placement dépasse la précision native disponible.", report)
    tolerance = max(1e-7, report.tolerance, 8*ulp(bound))
    if tolerance > report.tolerance:
        if report.profile_sketch is None:
            report = analyze(sketch, tolerance=tolerance)
        else:
            from .profile_regions import analyze_regions
            report = analyze_regions(sketch, tolerance=tolerance)
        if not report.valid:
            raise ProfileError("Profil non résolu à la précision du placement 3D : " +
                               next(d.message for d in report.diagnostics if d.severity == "error"), report)
    return report


def _length(entity):
    if type(entity) is Segment:
        return hypot(entity.end.u - entity.start.u, entity.end.v - entity.start.v)
    return entity.radius * (abs(entity.sweep) if type(entity) is Arc else 2*pi)


def to_faces(sketch, analysis=None, *, tolerance=1e-7, contour_ids=()):
    """Build all regions, or explicitly selected outer contour UUIDs.

    Selecting an outer always retains its direct holes. Selecting a hole as an
    independent filled disk is refused. Validate the entire sketch first, even
    when selecting only a subset. No partial result is returned after failure.
    """
    report = analyze(sketch, tolerance=tolerance) if analysis is None else analysis
    if report.sketch != sketch:
        raise ProfileError("Analyse périmée : vérifiez à nouveau l’esquisse.", report)
    if not report.valid:
        raise ProfileError(next((d.message for d in report.diagnostics if d.severity == "error"),
                                "Aucun contour fermé valide."), report)
    report = _native_analysis(sketch, report)
    if (type(contour_ids) not in (tuple, list) or any(type(key) is not str for key in contour_ids)
            or len(set(contour_ids)) != len(contour_ids)):
        raise _fail("region_selection", "La sélection de régions doit contenir des identifiants uniques.", report)
    wanted = set(contour_ids)
    available = {outer.id for outer, _ in report.regions}
    if wanted - available:
        raise _fail("region_selection", "Région absente ou contour intérieur sélectionné sans sa région.", report)

    native = to_wires(sketch, report)
    sketch = report.geometry
    contours = {contour.id: contour for contour in report.contours}
    entities = {entity.id: entity for entity in sketch.entities}
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps
    from OCC.Core.GeomAbs import GeomAbs_Plane
    from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_WIRE
    from OCC.Core.TopExp import topexp
    from OCC.Core.TopTools import TopTools_IndexedMapOfShape
    from OCC.Core.gp import gp_Ax3, gp_Dir, gp_Pln, gp_Pnt

    frame = sketch.support.frame
    plane = gp_Pln(gp_Ax3(gp_Pnt(*frame.origin.tuple()), gp_Dir(*frame.normal.tuple()), gp_Dir(*frame.u.tuple())))
    regions = []
    for outer, holes in native.regions:
        if wanted and outer.id not in wanted:
            continue
        ids = (outer.id, *(hole.id for hole in holes))
        entity_ids = tuple(use.entity_id for key in ids for use in contours[key].edges)
        expected = fsum(contours[key].signed_area for key in ids)
        perimeter = fsum(_length(entities[key]) for key in entity_ids)
        area_tolerance = max(abs(expected)*1e-8, 4*report.tolerance*perimeter,
                             32*ulp(fsum(abs(contours[key].signed_area) for key in ids)))
        try:
            maker = BRepBuilderAPI_MakeFace(plane, outer.wire, True)
            if not maker.IsDone():
                raise _fail("native_face", f"Construction du contour extérieur impossible : {maker.Error()}.", report, entity_ids)
            for hole in holes:
                maker.Add(hole.wire)
                if not maker.IsDone():
                    raise _fail("native_face", f"Construction d’un trou impossible : {maker.Error()}.", report, entity_ids)
            face = maker.Face()
            if face.IsNull() or face.ShapeType() != TopAbs_FACE or not BRepCheck_Analyzer(face, True).IsValid():
                raise _fail("invalid_face", "La face construite n’est pas valide.", report, entity_ids)
            wires = TopTools_IndexedMapOfShape()
            topexp.MapShapes(face, TopAbs_WIRE, wires)
            if wires.Size() != 1 + len(holes):
                raise _fail("missing_hole", "La face ne conserve pas toutes les frontières de la région.", report, entity_ids)
            surface = BRepAdaptor_Surface(face, True)
            if surface.GetType() != GeomAbs_Plane:
                raise _fail("non_planar", "Le résultat n’est pas une face plane.", report, entity_ids)
            if surface.Plane().Distance(gp_Pnt(*frame.origin.tuple())) > report.tolerance:
                raise _fail("non_planar", "La face n’appartient pas au plan du support.", report, entity_ids)
            normal = surface.Plane().Axis().Direction()
            sign = -1 if face.Orientation() == TopAbs_REVERSED else 1
            if sign*normal.Dot(gp_Dir(*frame.normal.tuple())) < 1 - 1e-10:
                raise _fail("face_orientation", "La normale de la face ne respecte pas le support de l’esquisse.", report, entity_ids)
            properties = GProp_GProps()
            brepgprop.SurfaceProperties(face, properties)
            area = properties.Mass()
            if (not isfinite(area) or not isfinite(expected) or expected <= report.tolerance**2
                    or area <= report.tolerance**2 or abs(area - expected) > area_tolerance):
                raise _fail("face_area", "L’aire native ne correspond pas à la région et à ses trous.", report, entity_ids)
            regions.append(FaceRegion(outer.id, tuple(hole.id for hole in holes), face, area))
        except (RuntimeError, ValueError) as exc:
            if isinstance(exc, ProfileError):
                raise
            raise _fail("native_face", f"Construction de la face impossible : {exc}", report, entity_ids) from exc
    return NativeFaces(tuple(regions), report.tolerance)
