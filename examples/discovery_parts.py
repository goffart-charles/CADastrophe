"""Editable mechanical examples for the README's native discovery screenshots."""
from math import cos, sin, pi
from pathlib import Path

from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.GeomAbs import GeomAbs_Line
from OCC.Core.TopoDS import topods
from OCC.Core.TopAbs import TopAbs_SOLID
from OCC.Core.TopExp import topexp
from OCC.Core.TopTools import TopTools_IndexedMapOfShape

from cadastrophe.core.configurations import create, activate, set_override, rename
from cadastrophe.core.expressions import Variable, Expression
from cadastrophe.core.feature_model import Operation, ExtrusionParameters, EdgeParameters
from cadastrophe.core.native_naming import capture, subshapes
from cadastrophe.core.operation_model import GeometryReference as Ref, PlaneParameters, ReplayParameters
from cadastrophe.core.persistence import save_document, load_document
from examples.parametric_example import ParametricExample, polygon, registry, mass
from plugins.modeling.edge_geometry import edge_reference
from plugins.sketch.model import Circle, Vec2


OUTPUT = Path(__file__).resolve().parent/'decouverte'


def offset(model, key, name, plane, distance):
    return model.datum(key, name, PlaneParameters('offset', (Ref('plane', plane.id),), distance=distance))


def circles(model, key, name, plane, centers, radius):
    return model.sketch(key, name, plane, tuple(Circle(Vec2(*center), radius) for center in centers))


def finish(model, key, name, operation, predicate, size, expected):
    state = model.rebuild()
    edges = [edge for edge in subshapes(state.shape, 'Edge') if predicate(BRepAdaptor_Curve(topods.Edge(edge)))]
    if len(edges) != expected:
        raise ValueError(f'{name} : {len(edges)} arêtes au lieu de {expected}.')
    return model.append(key, name, operation, EdgeParameters(
        tuple(edge_reference(state.shape, edge) for edge in edges), size=size,
        references=tuple(capture(state.shape, edge, 'Edge', state.topology) for edge in edges)))


def bearing_support():
    model = ParametricExample('Support de palier à embase nervurée', color=(.22, .49, .56))
    xy, xz = model.plane('XY'), model.plane('XZ')
    outline = model.sketch('outline', '01 · Semelle 140 × 80', xy,
                          polygon(((-70,-40), (70,-40), (70,40), (-70,40))))
    model.append('base', '01 · Semelle · épaisseur 10', Operation.NEW_BODY, ExtrusionParameters(depth=10), outline)

    def vertical(curve):
        if curve.GetType() != GeomAbs_Line:
            return False
        a, b = curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())
        return abs(a.X()-b.X()) < 1e-7 and abs(a.Y()-b.Y()) < 1e-7 and abs(abs(a.Z()-b.Z())-10) < 1e-7
    finish(model, 'corners', '02 · Quatre angles de semelle R5', Operation.FILLET, vertical, 5., 4)
    top = offset(model, 'base_top', 'Dessus de la semelle', xy, 10.)
    pedestal = model.sketch('pedestal_profile', '03 · Socle du palier', top,
                            polygon(((-28,-20), (28,-20), (28,20), (-28,20))))
    model.append('pedestal', '03 · Socle · hauteur 14', Operation.BOSS, ExtrusionParameters(depth=14), pedestal)
    front = offset(model, 'front', 'Face avant du logement', xz, -20.)
    outer = model.circle('housing_profile', '04 · Logement extérieur Ø60', front, (0, 40), 30.)
    model.append('housing', '04 · Logement cylindrique · largeur 40', Operation.BOSS, ExtrusionParameters(depth=40), outer)
    rib_plane = offset(model, 'rib_plane', 'Plan médian des nervures', xz, -3.)
    ribs = model.sketch('rib_profile', '05 · Deux nervures triangulaires', rib_plane,
        (*polygon(((24,10), (56,10), (24,38))), *polygon(((-24,10), (-24,38), (-56,10)))))
    model.append('ribs', '05 · Nervures latérales · épaisseur 6', Operation.BOSS, ExtrusionParameters(depth=6), ribs)
    bore = model.circle('bore_profile', '06 · Alésage de palier Ø36', front, (0, 40), 18.)
    model.append('bore', '06 · Alésage traversant', Operation.CUT,
                 ExtrusionParameters(depth=None, end_condition='through_all'), bore)
    mounts = ((-52,-26), (52,-26), (52,26), (-52,26))
    mounting = circles(model, 'mount_profile', '07 · Quatre fixations Ø11', xy, mounts, 5.5)
    model.append('mounting', '07 · Fixations traversantes', Operation.CUT,
                 ExtrusionParameters(depth=None, end_condition='through_all'), mounting)
    seats = circles(model, 'seat_profile', '08 · Quatre lamages Ø18', top, mounts, 9.)
    model.append('seats', '08 · Lamages de fixation · profondeur 4', Operation.CUT,
                 ExtrusionParameters(depth=4, reversed=True), seats)
    cover = tuple((24*cos(i*pi/3), 40+24*sin(i*pi/3)) for i in range(6))
    holes = circles(model, 'cover_profile', '09 · Six vis de couvercle Ø5', front, cover, 2.5)
    model.append('cover', '09 · Six perçages axiaux du logement', Operation.CUT,
                 ExtrusionParameters(depth=40), holes)

    return model


def ribbed_flange():
    model = ParametricExample('Platine à moyeu et six raidisseurs', color=(.48, .57, .67))
    xy, xz = model.plane('XY'), model.plane('XZ')
    base = model.circle('outline', '01 · Semelle circulaire Ø116', xy, (0, 0), 58.)
    model.append('base', '01 · Semelle · épaisseur 8', Operation.NEW_BODY, ExtrusionParameters(depth=8), base)
    top = offset(model, 'base_top', 'Dessus de la semelle', xy, 8.)
    tower = model.circle('hub_profile', '02 · Moyeu extérieur Ø54', top, (0, 0), 27.)
    hub = model.append('hub', '02 · Moyeu · hauteur variable', Operation.BOSS, ExtrusionParameters(depth=22), tower)
    hub_top = model.face_datum('hub_top', 'Dessus associatif du moyeu', hub, 30.)
    centers = tuple((43*cos(i*pi/3), 43*sin(i*pi/3)) for i in range(6))
    pads = circles(model, 'pad_profile', '03 · Six bossages Ø16', top, centers, 8.)
    model.append('pads', '03 · Bossages de fixation · hauteur 6', Operation.BOSS, ExtrusionParameters(depth=6), pads)
    rib_plane = offset(model, 'rib_plane', 'Plan de la nervure source', xz, -2.5)
    ribs = model.sketch('rib_profile', '04 · Nervure triangulaire source', rib_plane,
                        polygon(((25,8), (43,8), (25,25))))
    rib = model.append('rib', '04 · Nervure source · épaisseur 5', Operation.BOSS, ExtrusionParameters(depth=5), ribs)
    model.append('ribs', '05 · Six raidisseurs · motif circulaire', Operation.CIRCULAR_PATTERN,
                 ReplayParameters((rib.id,), Ref('axis', values=(0,0,0,0,0,1)), count=6))
    bore = model.circle('bore_profile', '06 · Alésage central Ø26', xy, (0, 0), 13.)
    model.append('bore', '06 · Alésage central traversant', Operation.CUT,
                 ExtrusionParameters(depth=None, end_condition='through_all'), bore)
    windows = tuple((38*cos(pi/6+i*2*pi/3), 38*sin(pi/6+i*2*pi/3)) for i in range(3))
    lightening = circles(model, 'window_profile', '07 · Trois évidements Ø18', xy, windows, 9.)
    model.append('windows', '07 · Évidements traversants', Operation.CUT,
                 ExtrusionParameters(depth=None, end_condition='through_all'), lightening)
    fixing = circles(model, 'fixing_profile', '08 · Six fixations Ø6,6', xy, centers, 3.3)
    model.append('fixings', '08 · Fixations traversantes', Operation.CUT,
                 ExtrusionParameters(depth=None, end_condition='through_all'), fixing)
    pad_top = offset(model, 'pad_top', 'Dessus des bossages', xy, 14.)
    seats = circles(model, 'seat_profile', '09 · Six lamages Ø11', pad_top, centers, 5.5)
    model.append('seats', '09 · Lamages · profondeur 3', Operation.CUT, ExtrusionParameters(depth=3, reversed=True), seats)
    groove = model.sketch('groove_profile', '10 · Gorge annulaire Ø36–44', hub_top,
                         (Circle(Vec2(0,0), 22.), Circle(Vec2(0,0), 18.)))
    model.append('groove', '10 · Gorge annulaire · profondeur 2', Operation.CUT,
                 ExtrusionParameters(depth=2, reversed=True), groove)
    doc = model.document
    doc.equation_evaluator = model.service.evaluator
    doc.set_parameter_scope('common')
    height = Variable('HauteurMoyeu', Expression('value', '=22'))
    doc.set_variables((height,))
    doc.set_equation(hub.id, 'depth', '=HauteurMoyeu')
    rename(doc, doc.default_configuration.id, 'Standard')
    reinforced = create(doc, 'Moyeu haut')
    set_override(doc, reinforced, height.id, Expression('value', '=32'))
    light = create(doc, 'Sans raidisseurs')
    set_override(doc, light, height.id, Expression('value', '=18'))
    for key in ('rib', 'ribs'):
        set_override(doc, light, model.handles[key]+':enabled', False)
    model.rebuild()
    return model


def validate_and_save(model, path):
    doc, codecs = model.document, registry(model.service)
    volumes = {}
    default = doc.active_configuration.id
    for configuration in doc.configurations:
        activate(doc, configuration.id, evaluator=model.service.evaluator)
        state = model.rebuild()
        solids = TopTools_IndexedMapOfShape()
        topexp.MapShapes(state.shape, TopAbs_SOLID, solids)
        if not BRepCheck_Analyzer(state.shape, True).IsValid() or solids.Size() != 1:
            raise ValueError('La pièce doit être un solide valide et connexe : '+configuration.name)
        volumes[configuration.name] = mass(state.shape)
    activate(doc, default, evaluator=model.service.evaluator)
    for obj in tuple(doc.objects.values()):
        if obj.id != model.body.id and obj.visible:
            doc.set_visible(obj.id, False)
    model.rebuild()
    doc.history.clear()
    save_document(doc, path, registry=codecs)
    reopened = load_document(path, registry=codecs)
    if set(reopened.objects) != set(doc.objects) or reopened.configuration_state != doc.configuration_state:
        raise ValueError('La réouverture a modifié les identités ou les configurations.')
    for configuration in reopened.configurations:
        activate(reopened, configuration.id, evaluator=model.service.evaluator)
        shape = reopened.objects[model.body.id].shape
        if (not BRepCheck_Analyzer(shape, True).IsValid()
                or abs(mass(shape)-volumes[configuration.name]) > 1e-5):
            raise ValueError('Reconstruction différente après réouverture : '+configuration.name)
    print(path.name, len(model.body.features), 'fonctions ; volumes', volumes, flush=True)


def main():
    OUTPUT.mkdir(exist_ok=True)
    validate_and_save(bearing_support(), OUTPUT/'support-palier.cada')
    validate_and_save(ribbed_flange(), OUTPUT/'platine-nervuree.cada')


if __name__ == '__main__':
    main()
