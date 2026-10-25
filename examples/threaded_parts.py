"""Threaded variants of delivered parts, built through ordinary document recipes."""
from dataclasses import dataclass, replace
from pathlib import Path

from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Cylinder
from OCC.Core.TopoDS import topods

from cadastrophe.document import ObjectType
from cadastrophe.core.body_history import body_result
from cadastrophe.core.configurations import create, set_override
from cadastrophe.core.expressions import Expression, Variable
from cadastrophe.core.fastener_catalog import apply_hole_choice, hole_choices
from cadastrophe.core.feature_model import BodyStateRef, Operation, SketchInput
from cadastrophe.core.feature_objects import BodyObject, HoleFeature, ReplayFeature, ThreadFeature
from cadastrophe.core.hole_model import HoleParameters
from cadastrophe.core.native_naming import capture, subshapes
from cadastrophe.core.operation_model import GeometryReference, ReplayParameters
from cadastrophe.core.parameters import set_expression
from cadastrophe.core.persistence import load_document
from cadastrophe.core.thread_model import ThreadParameters, apply_thread_choice, thread_choices
from examples.parametric_example import registry
from plugins.modeling.extrusion_service import Extrusions
from plugins.sketch import commands
from plugins.sketch.model import Point, Vec2
from plugins.sketch.profile_service import SketchProfiles


ROOT = Path(__file__).resolve().parents[1] / "tests" / "pieces"
PARTS = ("equerre-fixation", "boitier")


@dataclass
class ThreadedPart:
    document: object
    service: object
    key: str
    body_id: str
    original_ids: frozenset
    original_features: tuple
    points_id: str
    hole_id: str
    thread_id: str
    replays: tuple
    finish_id: str | None
    length_variable: str
    height_variable: str
    configurations: tuple
    centers: tuple

    @property
    def codecs(self):
        return registry(self.service)


def build_threaded_part(key):
    """Preserve the original graph and add sourced bores and repeated real threads."""
    if key not in PARTS:
        raise ValueError("Pièce de qualification inconnue.")
    service = Extrusions(SketchProfiles())
    doc = load_document(ROOT / key / "01-piece.cada", registry=registry(service))
    doc.equation_evaluator = service.evaluator
    body, = (obj for obj in doc.objects.values() if isinstance(obj, BodyObject))
    original_ids, original_features = frozenset(doc.objects), body.features
    bracket = key == "equerre-fixation"
    centers = ((35., 15.), (35., 35.)) if bracket else ((8., 8.), (92., 8.), (8., 62.), (92., 62.))
    nominal, pitch = (6., 1.) if bracket else (4., .7)
    height = Variable("HauteurPiece", Expression("value", "=8" if bracket else "=30"))
    turns = Variable("NombrePasFiletes", Expression("value", "=3" if bracket else "=4"), unit="number")
    root = doc.objects[original_features[0]]
    doc.update_construction((set_expression(root, "depth", Expression("depth", "=HauteurPiece")),),
                            variables=(height, turns), scope="common")

    def append(feature):
        owner = doc.objects[body.id]
        doc.update_construction((replace(owner, features=(*owner.features, feature.id)), feature), scope="common")
        return feature

    xy = next(obj for obj in doc.objects.values() if obj.plane and obj.plane.name == "XY")
    points = commands.create_sketch(doc, xy.id, name="Axes des fixations taraudées")
    commands.add_entities(doc, points.id, tuple(Point(Vec2(*p)) for p in centers), title="Positions des fixations")
    choice = next(c for c in hole_choices("tap_drill") if c.nominal == nominal and c.pitch == pitch)
    hole = append(HoleFeature(f"Avant-trous M{nominal:g} × {pitch:g}", None, body.color,
        kind=ObjectType.FEATURE, body_id=body.id, operation=Operation.CUT, source=SketchInput(points.id),
        parameters=apply_hole_choice(HoleParameters(end_condition="through_all"), choice)))

    state = body_result(doc, body.id)
    candidates = []
    for face in subshapes(state.shape, "Face"):
        surface = BRepAdaptor_Surface(topods.Face(face))
        if surface.GetType() != GeomAbs_Cylinder:
            continue
        cylinder = surface.Cylinder()
        origin = cylinder.Location()
        if (abs(cylinder.Radius()-hole.parameters.diameter/2) < 1e-7
                and abs(origin.X()-centers[0][0]) < 1e-7 and abs(origin.Y()-centers[0][1]) < 1e-7):
            candidates.append(face)
    if len(candidates) != 1:
        raise ValueError("L’avant-trou de départ doit avoir une face cylindrique unique.")
    reference = GeometryReference("face", body.id, hole.id, capture(state.shape, candidates[0], "Face", state.topology))
    preset = next(c for c in thread_choices(True) if c.nominal == nominal and c.pitch == pitch)
    parameters = apply_thread_choice(ThreadParameters(reference, internal=True, reversed=True, length=3*pitch), preset)
    thread = ThreadFeature(f"Taraudage M{nominal:g} × {pitch:g}", None, body.color,
        kind=ObjectType.FEATURE, body_id=body.id, operation=Operation.CUT,
        input_state=BodyStateRef(body.id, hole.id), parameters=parameters)
    thread = set_expression(thread, "length", Expression("length", f"=NombrePasFiletes*{pitch:g}"))
    append(thread)
    replays = []
    for direction, spacing in (((0, 1, 0), 20.),) if bracket else (((1, 0, 0), 84.), ((0, 1, 0), 54.)):
        source = replays[-1] if replays else thread.id
        replay = append(ReplayFeature("Répétition des taraudages", None, body.color,
            kind=ObjectType.FEATURE, body_id=body.id, operation=Operation.LINEAR_PATTERN,
            parameters=ReplayParameters((source,), GeometryReference("axis", values=(0, 0, 0, *direction)),
                                        count=2, spacing=spacing)))
        replays.append(replay.id)

    finish = None
    if bracket:
        point = commands.create_sketch(doc, xy.id, name="Fixation à tête fraisée")
        commands.add_entity(doc, point.id, Point(Vec2(55., 25.)))
        preset = next(c for c in hole_choices("countersink") if c.nominal == 4.)
        finish = append(HoleFeature("Fixation fraisée M4", None, body.color,
            kind=ObjectType.FEATURE, body_id=body.id, operation=Operation.CUT, source=SketchInput(point.id),
            parameters=apply_hole_choice(HoleParameters(end_condition="through_all"), preset)))
    short = create(doc, "Taraudages courts")
    set_override(doc, short, turns.id, 2.)
    reinforced = create(doc, "Pièce renforcée")
    set_override(doc, reinforced, height.id, 10. if bracket else 35.)
    for obj in tuple(doc.objects.values()):
        if obj.kind is not ObjectType.BODY and obj.visible:
            doc.set_visible(obj.id, False)
    doc.history.clear()
    return ThreadedPart(doc, service, key, body.id, original_ids, original_features, points.id,
        hole.id, thread.id, tuple(replays), finish.id if finish else None, turns.id, height.id,
        (doc.default_configuration.id, short, reinforced), centers)
