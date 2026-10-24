"""Equation bindings onto existing recipe fields and sketch driving constraints."""
from dataclasses import dataclass, replace
from math import isclose

from .expressions import Expression, ExpressionError, Node, bind, evaluate


@dataclass(frozen=True)
class Dimension:
    owner: str
    key: str
    label: str
    value: float
    unit: str = "length"
    integer: bool = False

    @property
    def id(self):
        return self.owner + ":" + self.key


def dimensions(objects):
    result = []
    for obj in objects.values():
        if callable(getattr(obj, "equation_dimensions", None)):
            result.extend(obj.equation_dimensions())
            continue
        params = getattr(obj, "parameters", None)
        if params is None:
            continue
        # Explicit recipe fields: topology indices, tolerances, revisions and
        # booleans must never accidentally become public driving dimensions.
        keys = ("depth", "size", "size2", "angle", "thickness", "distance", "roll", "count", "spacing")
        number = 0
        for key in keys:
            value = getattr(params, key, None)
            if type(value) not in (int, float):
                continue
            if key == "size2" and getattr(params, "mode", "") not in ("variable", "two_distances"):
                continue
            if key == "angle" and hasattr(params, "size") and params.mode != "distance_angle":
                continue
            from .feature_model import Operation
            operation = getattr(obj, "operation", None)
            if hasattr(params, "sources"):
                if operation is Operation.MIRROR or key == "spacing" and operation is not Operation.LINEAR_PATTERN or key == "angle" and operation is not Operation.CIRCULAR_PATTERN:
                    continue
            if hasattr(params, "method") and ((key == "distance" and params.method != "offset") or
                                               (key == "angle" and params.method != "inclined")):
                continue
            unit = "angle" if key in ("angle", "roll") else "number" if key == "count" else "length"
            number += 1
            result.append(Dimension(obj.id, key, f"D{number}@{obj.name}", value, unit, key == "count"))
    return tuple(result)


def graph(objects, variables):
    from .expressions import Variable
    if any(not isinstance(v, Variable) for v in variables):
        raise ExpressionError("Une liste de variables est requise.")
    dims = dimensions(objects)
    aliases = {}
    for variable in variables:
        aliases.setdefault(variable.name, []).append(variable.id)
    if len({v.name for v in variables}) != len(variables) or len({v.id for v in variables}) != len(variables):
        raise ExpressionError("Nom ou identifiant de variable dupliqué.")
    for dim in dims:
        aliases.setdefault(dim.label, []).append(dim.id)
        aliases.setdefault(dim.id, []).append(dim.id)
    # Dn labels are conveniences for first binding only. Persisted references
    # use owner UUID + recipe field/constraint UUID, even after a rename.
    for obj in objects.values():
        for i, dim in enumerate((d for d in dims if d.owner == obj.id), 1):
            aliases.setdefault(f"D{i}@{obj.name}", []).append(dim.id)
    nodes, bound_variables, bound_objects = [], [], dict(objects)
    for variable in variables:
        exp = bind(variable.expression, aliases)
        variable = replace(variable, expression=exp)
        bound_variables.append(variable)
        nodes.append(Node(variable.id, variable.name, 0., variable.unit, exp))
    available = {d.id: d for d in dims}
    formulas = {}
    for obj in objects.values():
        bound = []
        for exp in obj.expressions:
            key = obj.id + ":" + exp.target
            if key not in available:
                raise ExpressionError(f"Dimension absente ou inactive : {obj.name} / {exp.target}.")
            if key in formulas:
                raise ExpressionError("Plusieurs équations pilotent la même dimension.")
            exp = bind(exp, aliases)
            formulas[key] = exp
            bound.append(exp)
        if tuple(bound) != obj.expressions:
            bound_objects[obj.id] = replace(obj, expressions=tuple(bound))
    nodes.extend(Node(d.id, d.label, d.value, d.unit, formulas.get(d.id)) for d in dims)
    return tuple(nodes), tuple(bound_variables), bound_objects, dims


def prepare(objects, variables):
    """Return detached, solved numeric recipes; publish only after success."""
    nodes, variables, objects, dims = graph(objects, variables)
    values = evaluate(nodes)
    edits = {}
    for dim in dims:
        value = values[dim.id]
        if dim.integer:
            if not float(value).is_integer():
                raise ExpressionError(f"{dim.label} exige un entier.")
            value = int(value)
        if value != dim.value:
            edits.setdefault(dim.owner, {})[dim.key] = value
    for key, changes in edits.items():
        obj = objects[key]
        if callable(getattr(obj, "with_equation_values", None)):
            objects[key] = obj.with_equation_values(changes)
        else:
            objects[key] = replace(obj, parameters=replace(obj.parameters, **changes))
    # A solver may move geometry; driving dimensions must still agree with
    # their requested values. No iterative equation/solver feedback loop.
    final_dims = dimensions(objects)
    # Non-driving entity coordinates are solver results. Re-read those leaves
    # once and check every equation against the solved geometry, rather than
    # iterating an equation/solver loop or silently accepting a shifted target.
    final_nodes, _, _, _ = graph(objects, variables)
    final_values = evaluate(final_nodes)
    driven = {n.id for n in nodes if n.expression is not None}
    for dim in final_dims:
        if dim.id in driven and not isclose(dim.value, final_values[dim.id], rel_tol=1e-10, abs_tol=1e-9):
            raise ExpressionError(f"Résolution incompatible avec l’équation de {dim.label}.")
    return objects, variables, final_values


def set_expression(obj, key, expression):
    old = next((e for e in obj.expressions if e.target == key), None)
    if expression is not None and old is not None and not expression.bindings:
        expression = replace(expression, bindings=old.bindings)
    expressions = tuple(e for e in obj.expressions if e.target != key)
    if expression is not None:
        expressions += (expression,)
    return replace(obj, expressions=expressions)
