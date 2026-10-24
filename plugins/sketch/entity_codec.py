"""Strict E1 entity parameters shared by project persistence and E4 clipboard."""
from dataclasses import replace
from .model import Arc, Circle, Point, Segment, Vec2, Ellipse, Spline


def _fields(data, names):
    if type(data) is not dict or set(data) != set(names.split()):
        raise ValueError("Champs d’esquisse manquants ou inconnus.")


def _vec2(value):
    if type(value) is not list or len(value) != 2:
        raise ValueError("Deux coordonnées locales sont requises.")
    return Vec2(*value)


def _coordinates(point):
    return [point.u, point.v]


def _encode_entity(entity):
    result = dict(id=entity.id, type=entity.kind, construction=entity.construction)
    if entity.text_group is not None:
        result["text_group"] = entity.text_group
    if type(entity) is Point:
        result["position"] = _coordinates(entity.position)
    elif type(entity) is Segment:
        result.update(start=_coordinates(entity.start), end=_coordinates(entity.end))
    elif type(entity) is Ellipse:
        result.update(center=_coordinates(entity.center), major_radius=entity.major_radius,
                      minor_radius=entity.minor_radius, rotation=entity.rotation)
    elif type(entity) is Spline:
        result.update(points=[_coordinates(p) for p in entity.points], closed=entity.closed)
        if entity.degree is not None:
            result.update(degree=entity.degree, knots=list(entity.knots), weights=list(entity.weights))
    else:
        result.update(center=_coordinates(entity.center), radius=entity.radius)
        if type(entity) is Arc:
            result.update(start_angle=entity.start_angle, sweep=entity.sweep)
    return result


def _decode_entity(data):
    if type(data) is not dict:
        raise ValueError("Une entité typée est requise.")
    construction = data.get("construction", False)
    if type(construction) is not bool:
        raise ValueError("Le statut de construction doit être booléen.")
    geometry = dict(data)
    geometry.pop("construction", None)
    text_group = geometry.pop("text_group", None)
    return replace(_decode_geometry(geometry), construction=construction, text_group=text_group)


def _decode_geometry(data):
    if type(data) is not dict or type(data.get("type")) is not str:
        raise ValueError("Une entité typée est requise.")
    kind = data["type"]
    parameters = {"point": "position", "segment": "start end", "circle": "center radius",
                  "arc": "center radius start_angle sweep", "ellipse": "center major_radius minor_radius rotation",
                  "spline": "points closed"}
    if kind not in parameters:
        raise ValueError(f"Type d’entité inconnu : {kind}.")
    explicit = kind == "spline" and "degree" in data
    _fields(data, "id type " + parameters[kind] + (" degree knots weights" if explicit else ""))
    if kind == "point":
        return Point(_vec2(data["position"]), id=data["id"])
    if kind == "segment":
        return Segment(_vec2(data["start"]), _vec2(data["end"]), id=data["id"])
    if kind == "circle":
        return Circle(_vec2(data["center"]), data["radius"], id=data["id"])
    if kind == "ellipse":
        return Ellipse(_vec2(data["center"]), data["major_radius"], data["minor_radius"], data["rotation"], id=data["id"])
    if kind == "spline":
        if type(data["points"]) is not list:
            raise ValueError("Les pôles doivent être une liste.")
        if explicit and (type(data["knots"]) is not list or type(data["weights"]) is not list):
            raise ValueError("Les nœuds et poids doivent être des listes.")
        return Spline(tuple(_vec2(p) for p in data["points"]), data["closed"],
                      data.get("degree"), tuple(data.get("knots", ())), tuple(data.get("weights", ())), id=data["id"])
    return Arc(_vec2(data["center"]), data["radius"], data["start_angle"], data["sweep"], id=data["id"])
