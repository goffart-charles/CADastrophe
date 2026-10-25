"""Bound arithmetic expressions. No GUI, geometry or executable user code.

Names are resolved once, to stable node IDs. Subsequent evaluations never
reinterpret a saved name against a different object or variable.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from math import isfinite
import operator
import re
from uuid import UUID, uuid4


class ExpressionError(ValueError):
    pass


@dataclass(frozen=True)
class Expression:
    target: str
    text: str
    bindings: tuple[tuple[str, str], ...] = ()
    scale: float = 1.0

    def __post_init__(self):
        if not isinstance(self.target, str) or not self.target:
            raise ExpressionError("Cible d’équation absente.")
        if not isinstance(self.text, str) or not self.text.startswith("="):
            raise ExpressionError("Une équation commence par =.")
        if type(self.bindings) is not tuple or any(type(b) is not tuple or len(b) != 2 or
                any(not isinstance(s, str) or not s for s in b) for b in self.bindings):
            raise ExpressionError("Références d’équation invalides.")
        if len(dict(self.bindings)) != len(self.bindings):
            raise ExpressionError("Référence d’équation dupliquée.")
        if type(self.scale) not in (int, float) or not isfinite(self.scale) or self.scale <= 0:
            raise ExpressionError("Unité d’équation invalide.")


@dataclass(frozen=True)
class Variable:
    name: str
    expression: Expression
    id: str = field(default_factory=lambda: str(uuid4()))
    unit: str = "length"

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.isidentifier():
            raise ExpressionError("Le nom d’une variable doit être un identifiant (ex. largeur).")
        if str(UUID(self.id)) != self.id or self.unit not in ("length", "angle", "number"):
            raise ExpressionError("Identifiant ou unité de variable invalide.")


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    value: float
    unit: str = "length"
    expression: Expression | None = None


_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub,
           ast.Mult: operator.mul, ast.Div: operator.truediv}
_DIMENSION = re.compile(r"\[([^\[\]\n]+)\]|([^\W\d]\w*@[^\W\d]\w*)", re.UNICODE)


def parse(text):
    """Return a deliberately small arithmetic AST and its public name tokens."""
    if not isinstance(text, str) or len(text) > 4096:
        raise ExpressionError("Expression trop longue ou invalide.")
    source = text[1:] if text.startswith("=") else text
    tokens = {}
    def dimension(match):
        alias = f"__dimension_{len(tokens)}"
        while alias in source:
            alias += "_"
        tokens[alias] = match.group(1) or match.group(2)
        return alias
    source = _DIMENSION.sub(dimension, source).strip()
    source = re.sub(r"(?<=\d),(?=\d)", ".", source)
    try:
        tree = ast.parse(source, mode="eval")
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise ExpressionError("Syntaxe d’équation invalide.") from exc
    if len(list(ast.walk(tree))) > 512:
        raise ExpressionError("Expression trop complexe.")
    names = {}
    def check(node, depth=0):
        if depth > 64:
            raise ExpressionError("Expression trop imbriquée.")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            try:
                finite = isfinite(node.value)
            except OverflowError:
                finite = False
            if not finite:
                raise ExpressionError("Nombre non fini dans l’équation.")
        elif isinstance(node, ast.Name):
            names[node.id] = tokens.get(node.id, node.id)
        elif isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            check(node.left, depth+1)
            check(node.right, depth+1)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            check(node.operand, depth+1)
        else:
            raise ExpressionError("Seuls les nombres, références, parenthèses et + − * / sont acceptés.")
    check(tree.body)
    return tree.body, names


def bind(expression, aliases):
    _, names = parse(expression.text)
    previous = dict(expression.bindings)
    bindings = []
    for name in dict.fromkeys(names.values()):
        if name in previous:
            target = previous[name]
        else:
            candidates = tuple(dict.fromkeys(aliases.get(name, ())))
            if len(candidates) != 1:
                reason = "inconnue" if not candidates else "ambiguë"
                raise ExpressionError(f"Référence {reason} : {name}.")
            target = candidates[0]
        bindings.append((name, target))
    from dataclasses import replace
    return replace(expression, bindings=tuple(bindings))


def evaluate(nodes):
    """Evaluate an acyclic bound graph; values use mm, degrees, or scalars.

    Bare arithmetic is scalar until assigned; its field's saved display scale
    then applies. Addition to a quantity coerces a literal in that same context.
    Multiplication/division by a scalar preserve the quantity's dimension.
    """
    nodes = tuple(nodes)
    by_id = {n.id: n for n in nodes}
    if len(by_id) != len(nodes):
        raise ExpressionError("Identifiant de paramètre dupliqué.")
    values, visiting = {}, []
    def visit(key):
        if key in values:
            return values[key]
        if key not in by_id:
            raise ExpressionError(f"Référence absente : {key}.")
        node = by_id[key]
        if key in visiting:
            path = visiting[visiting.index(key):] + [key]
            raise ExpressionError("Dépendance circulaire : " + " → ".join(by_id[k].label for k in path))
        visiting.append(key)
        try:
            if node.expression is None:
                result = node.value, node.unit
            else:
                exp = node.expression
                tree, names = parse(exp.text)
                refs = dict(exp.bindings)
                def calc(tree):
                    if isinstance(tree, ast.Constant):
                        return float(tree.value), "number"
                    if isinstance(tree, ast.Name):
                        name = names[tree.id]
                        if name not in refs:
                            raise ExpressionError(f"Référence inconnue : {name}.")
                        if refs[name] not in by_id:
                            raise ExpressionError(f"Référence absente : {name}.")
                        return visit(refs[name])
                    if isinstance(tree, ast.UnaryOp):
                        value, unit = calc(tree.operand)
                        return (-value if isinstance(tree.op, ast.USub) else value), unit
                    left, lu = calc(tree.left)
                    right, ru = calc(tree.right)
                    if isinstance(tree.op, (ast.Add, ast.Sub)):
                        if lu == "number" and ru != "number":
                            left, lu = left*exp.scale, ru
                        if ru == "number" and lu != "number":
                            right, ru = right*exp.scale, lu
                        if lu != ru:
                            raise ExpressionError("Addition de longueurs et d’angles incompatible.")
                        unit = lu
                    elif isinstance(tree.op, ast.Mult):
                        if lu != "number" and ru != "number":
                            raise ExpressionError("Multiplier deux dimensions n’est pas pris en charge.")
                        unit = ru if lu == "number" else lu
                    else:
                        if right == 0:
                            raise ExpressionError("Division par zéro.")
                        if ru != "number" and lu != ru:
                            raise ExpressionError("Unités incompatibles dans la division.")
                        unit = "number" if lu == ru else lu
                    return _BINARY[type(tree.op)](left, right), unit
                value, unit = calc(tree)
                if unit == "number" and node.unit != "number":
                    value, unit = value*exp.scale, node.unit
                if unit != node.unit:
                    raise ExpressionError(f"Unité incompatible pour {node.label}.")
                result = value, unit
            if not isfinite(result[0]):
                raise ExpressionError("Résultat non fini.")
            values[key] = result
            return result
        except (ArithmeticError, RecursionError) as exc:
            raise ExpressionError(f"Calcul impossible pour {node.label} : {exc}") from exc
        finally:
            visiting.pop()
    for node in nodes:
        visit(node.id)
    return {key: value for key, (value, _) in values.items()}


def expression_record(expression):
    return dict(target=expression.target, text=expression.text,
                bindings=[list(pair) for pair in expression.bindings], scale=expression.scale)


def read_expression(record):
    if type(record) is not dict or set(record) != {"target", "text", "bindings", "scale"} or type(record["bindings"]) is not list:
        raise ExpressionError("Enregistrement d’équation invalide.")
    if any(type(pair) is not list for pair in record["bindings"]):
        raise ExpressionError("Références d’équation invalides.")
    result = Expression(record["target"], record["text"], tuple(tuple(p) for p in record["bindings"]), record["scale"])
    parse(result.text)
    return result
