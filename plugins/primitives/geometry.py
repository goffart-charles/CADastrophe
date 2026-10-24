"""Direct OCCT solids in world coordinates (mm); no viewport dependencies."""
from math import isfinite

from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepPrimAPI import (BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder,
                                 BRepPrimAPI_MakeSphere, BRepPrimAPI_MakeCone)
from OCC.Core.gp import gp_Ax2, gp_Dir, gp_Pnt

from cadastrophe.plugin_api import SceneObject


# key: (French name, dimensions: (key, label, default mm), origin convention)
PRIMITIVES = {
    "box": ("Boîte", (("length", "Longueur X", 40.), ("width", "Largeur Y", 30.),
                      ("height", "Hauteur Z", 25.)), "Coin inférieur (X, Y, Z)"),
    "cylinder": ("Cylindre", (("radius", "Rayon", 20.), ("height", "Hauteur Z", 40.)),
                 "Centre de la base (X, Y, Z), axe +Z"),
    "sphere": ("Sphère", (("radius", "Rayon", 20.),), "Centre (X, Y, Z)"),
    "cone": ("Cône", (("radius", "Rayon de base", 20.), ("height", "Hauteur Z", 40.)),
             "Centre de la base (X, Y, Z), pointe suivant +Z"),
}
MIN_SIZE, MAX_SIZE = 0.001, 1_000_000.


def _number(value, lower, upper):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Les coordonnées et dimensions doivent être numériques.")
    if not isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"Valeur attendue entre {lower:g} et {upper:g} mm.")
    return float(value)


def build_object(kind, name, origin, dimensions):
    """Validate first; return a standalone SceneObject, never mutate a document."""
    if kind not in PRIMITIVES:
        raise ValueError("Primitive inconnue.")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Le nom ne peut pas être vide.")
    if len(origin) != 3:
        raise ValueError("Trois coordonnées sont requises.")
    origin = tuple(_number(value, -MAX_SIZE, MAX_SIZE) for value in origin)
    expected = {key for key, _, _ in PRIMITIVES[kind][1]}
    if set(dimensions) != expected:
        raise ValueError("Dimensions incomplètes ou inconnues.")
    values = {key: _number(value, MIN_SIZE, MAX_SIZE) for key, value in dimensions.items()}
    point = gp_Pnt(*origin)
    if kind == "box":
        builder = BRepPrimAPI_MakeBox(point, values["length"], values["width"], values["height"])
    elif kind == "sphere":
        builder = BRepPrimAPI_MakeSphere(point, values["radius"])
    else:
        axis = gp_Ax2(point, gp_Dir(0, 0, 1))
        if kind == "cylinder":
            builder = BRepPrimAPI_MakeCylinder(axis, values["radius"], values["height"])
        else:
            builder = BRepPrimAPI_MakeCone(axis, values["radius"], 0., values["height"])
    shape = builder.Shape()
    if not builder.IsDone() or shape.IsNull() or not BRepCheck_Analyzer(shape).IsValid():
        raise ValueError("Impossible de construire un solide valide avec ces dimensions.")
    # Standard, self-contained B-Rep: loading and undo never require this plugin.
    return SceneObject(name.strip(), shape, (0.64, 0.73, 0.85))


def next_name(kind, document):
    prefix = PRIMITIVES[kind][0]
    names = {obj.name for obj in document.objects.values()}
    index = 1
    while f"{prefix}{index}" in names:
        index += 1
    return f"{prefix}{index}"
