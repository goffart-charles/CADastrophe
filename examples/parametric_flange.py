"""Reproducible Atlas example using the common native recipe builder."""
from dataclasses import replace

from cadastrophe.document import ObjectType
from cadastrophe.geometry import Vec3
from cadastrophe.core.body_history import result_at
from cadastrophe.core.feature_model import BodyStateRef, EdgeParameters, ExtrusionParameters, Operation
from cadastrophe.core.native_naming import capture, subshapes
from cadastrophe.core.operation_model import GeometryReference as Ref, PlaneParameters, ReplayParameters, RevolutionParameters
from plugins.modeling.edge_geometry import edge_reference
from plugins.sketch.constraints import Constraint, Reference
from plugins.sketch.model import Segment, Vec2

from examples.parametric_example import ParametricExample, independent_copy, mass, polygon, registry


class Flange(ParametricExample):
    def __init__(self):
        super().__init__("Atlas · bride paramétrique de démonstration")
        self.body = replace(self.body, name="Bride Atlas")


def build_demo():
    model = Flange()
    xy, xz, yz = (model.plane(name) for name in ("XY", "XZ", "YZ"))
    axis = Segment(Vec2(0, -5), Vec2(0, 48), construction=True)
    profile = model.sketch("turned_profile", "01 · Coupe du moyeu et axe de construction", xz,
        (*polygon(((10,0),(62,0),(62,8),(24,8),(24,30),(20,34),(10,34))), axis),
        (Constraint("vertical", (Reference(axis.id),)),))
    revolve_axis = Ref("sketch_line", profile.id, entity_id=axis.id)
    base = model.append("base", "01 · Flasque et moyeu · révolution 360°", Operation.NEW_BODY, RevolutionParameters(revolve_axis), profile)
    top = model.face_datum("flange_plane", "Plan A · face supérieure du flasque (suivi paramétrique)", base, 8)

    collar = model.sketch("collar_profile", "02 · Coupe de la couronne supérieure", xz, polygon(((10,34),(20,34),(20,38),(10,38))))
    model.append("collar", "02 · Couronne supérieure · révolution additive", Operation.BOSS, RevolutionParameters(revolve_axis), collar)
    groove = model.sketch("groove_profile", "03 · Section de la gorge annulaire", xz, polygon(((52,5),(58,5),(58,9),(52,9))))
    model.append("groove", "03 · Gorge annulaire · révolution soustractive", Operation.CUT, RevolutionParameters(revolve_axis), groove)

    plot = model.circle("plot_sketch", "04 · Plot Ø8 et charnière de construction", top, (33,0), 4, hinge=True)
    boss = model.append("plot", "04 · Plot radial · extrusion 6 mm", Operation.BOSS, ExtrusionParameters(depth=6), plot)
    plot_top = model.face_datum("plot_plane", "Plan B · sommet du plot (suit sa hauteur)", boss, 14)
    direction = Ref("axis", values=(0,0,0,1,0,0))
    zaxis = Ref("axis", values=(0,0,0,0,0,1))
    linear = model.append("linear_plots", "05 · Deux plots radiaux · répétition linéaire", Operation.LINEAR_PATTERN,
        ReplayParameters((boss.id,), direction, count=2, spacing=12))
    model.append("circular_plots", "06 · Six paires de plots · répétition circulaire du motif", Operation.CIRCULAR_PATTERN,
        ReplayParameters((linear.id,), zaxis, count=6))

    # Local coordinates, rather than a frozen world-space placement.
    point = plot_top.plane.to_local(Vec3(33,0,14))
    hole = model.circle("hole_sketch", "07 · Perçage pilote Ø4,2 (cote modifiable)", plot_top, point[:2], 2.1)
    drill = model.append("hole", "07 · Perçage pilote · à travers tout", Operation.CUT,
        ExtrusionParameters(depth=None, end_condition="through_all", reversed=True), hole)
    linear_holes = model.append("linear_holes", "08 · Deux perçages radiaux · répétition linéaire", Operation.LINEAR_PATTERN,
        ReplayParameters((drill.id,), direction, count=2, spacing=12))
    model.append("circular_holes", "09 · Douze perçages · répétition circulaire du motif", Operation.CIRCULAR_PATTERN,
        ReplayParameters((linear_holes.id,), zaxis, count=6))

    tab = model.sketch("tab_sketch", "10 · Patte de fixation", xy, polygon(((58,8),(74,8),(74,20),(58,20))))
    tab_feature = model.append("tab", "10 · Patte de fixation · extrusion 8 mm", Operation.BOSS, ExtrusionParameters(depth=8), tab)
    mirror = model.append("mirror_tab", "11 · Patte gauche · symétrie YZ", Operation.MIRROR,
        ReplayParameters((tab_feature.id,), Ref("plane", yz.id)))
    model.append("mirror_tabs", "12 · Pattes opposées · symétrie du motif", Operation.MIRROR,
        ReplayParameters((mirror.id,), Ref("plane", xz.id)))
    tab_hole = model.circle("tab_hole_sketch", "13 · Trou de fixation Ø5", xy, (67,14), 2.5)
    tab_drill = model.append("tab_hole", "13 · Perçage de fixation", Operation.CUT, ExtrusionParameters(depth=8), tab_hole)
    hole_mirror = model.append("mirror_tab_hole", "14 · Perçage gauche · symétrie YZ", Operation.MIRROR,
        ReplayParameters((tab_drill.id,), Ref("plane", yz.id)))
    model.append("mirror_tab_holes", "15 · Perçages opposés · symétrie du motif", Operation.MIRROR,
        ReplayParameters((hole_mirror.id,), Ref("plane", xz.id)))

    inclined = model.datum("inclined_plane", "Plan C · perçage incliné 15°", PlaneParameters("inclined",
        (Ref("plane", top.id), Ref("sketch_line", plot.id, entity_id=model.handles["hinge"])), angle=15))
    vent = model.circle("vent_sketch", "16 · Évent sur plan incliné Ø5", inclined, (0,40), 2.5)
    model.append("vent", "16 · Évent incliné · à travers tout", Operation.CUT,
        ExtrusionParameters(depth=None, end_condition="through_all", reversed=True), vent)

    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.GeomAbs import GeomAbs_Circle
    from OCC.Core.TopoDS import topods
    for key, name, operation, height, radius, size in (
        ("fillet", "17 · Congé supérieur R0,8", Operation.FILLET, 38, 20, .8),
        ("chamfer", "18 · Chanfrein d’entrée de l’alésage 0,6", Operation.CHAMFER, 0, 10, .6),
    ):
        state = result_at(model.document, BodyStateRef(model.body.id, model.body.features[-1]))
        matches = []
        for edge in subshapes(state.shape, "Edge"):
            curve = BRepAdaptor_Curve(topods.Edge(edge))
            if curve.GetType() == GeomAbs_Circle:
                circle = curve.Circle()
                if abs(circle.Radius()-radius) < 1e-7 and abs(circle.Location().Z()-height) < 1e-7:
                    matches.append(edge)
        if len(matches) != 1:
            raise ValueError(f"Arête de finition non unique : {name} ({len(matches)}).")
        edge = matches[0]
        parameters = EdgeParameters(edges=(edge_reference(state.shape, edge),), size=size,
                                    references=(capture(state.shape, edge, "Edge", state.topology),))
        model.append(key, name, operation, parameters)

    model.handles["body"] = model.body.id
    for obj in tuple(model.document.objects.values()):
        if obj.kind is not ObjectType.BODY and obj.visible:
            model.document.set_visible(obj.id, False)
    model.document.history.clear()
    return model
