"""Store curve recipes; native edges are always reconstructed."""
from dataclasses import asdict, fields

from cadastrophe.plugin_api import ObjectCodec, SceneObject, CurveObject, CurveParameters
from cadastrophe.geometry import Vec3
from cadastrophe.core.recipe_references import recipe_references, remap_recipe


def encode(obj):
    data = asdict(obj.parameters)
    data['points'] = [list(point.tuple()) for point in obj.parameters.points]
    return data


def decode(base, data):
    if type(data) is not dict or set(data) != {item.name for item in fields(CurveParameters)}:
        raise ValueError('Recette de courbe incomplète ou inconnue.')
    if type(data['points']) is not list or len(data['points']) > 256:
        raise ValueError('Liste de points invalide.')
    points = []
    for point in data['points']:
        if type(point) is not list or len(point) != 3:
            raise ValueError('Chaque point doit posséder trois coordonnées.')
        points.append(Vec3(*point))
    parameters = CurveParameters(**dict(data, points=tuple(points)))
    return CurveObject(**{item.name: getattr(base, item.name) for item in fields(SceneObject)}, parameters=parameters)


def validate(obj, objects):
    if obj.parameters.plane_id is not None:
        plane = objects.get(obj.parameters.plane_id)
        if plane is None or plane.plane is None:
            raise ValueError('Le plan de départ de la courbe est absent ou incompatible.')


CODEC = ObjectCodec('cadastrophe.curves.curve', CurveObject, 1, encode, decode,
                    validate_references=validate, clipboard_references=recipe_references, clipboard_remap=remap_recipe)
