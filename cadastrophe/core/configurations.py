"""Materialize existing numeric recipes, then use the common evaluator/commands."""
from dataclasses import replace

from .configuration_model import Configuration, ParameterSetting
from .expressions import Expression, bind
from .parameters import dimensions, prepare, set_expression


def _dormant_parameter(obj, key):
    # Public dimensions whose availability depends on the operation's method.
    conditional = {"depth", "size2", "angle", "distance", "count", "spacing",
                   "head_diameter", "head_depth"}
    return key in conditional and key in getattr(getattr(obj, "parameters", None), "__dataclass_fields__", {})


def settings(objects, variables):
    from .feature_objects import ConstructionFeature
    result = {v.id: ParameterSetting(v.id, v.expression) for v in variables}
    for d in dimensions(objects):
        formula = next((e for e in objects[d.owner].expressions if e.target == d.key), None)
        result[d.id] = ParameterSetting(d.id, formula if formula is not None else d.value)
    result.update((obj.id+":enabled", ParameterSetting(obj.id+":enabled", obj.enabled))
                  for obj in objects.values() if isinstance(obj, ConstructionFeature))
    return result


def _map(values):
    return {s.target: s for s in values}


def _ordered(values):
    return tuple(values[key] for key in sorted(values))


def bind_state(state, objects, variables):
    if not any(isinstance(s.value, Expression) for s in state.common_values) and not any(
            isinstance(s.value, Expression) for c in state.configurations for s in c.overrides):
        return state
    aliases = {}
    for variable in variables:
        aliases.setdefault(variable.name, []).append(variable.id)
    dims = dimensions(objects)
    for dim in dims:
        aliases.setdefault(dim.label, []).append(dim.id)
        aliases.setdefault(dim.id, []).append(dim.id)
    for obj in objects.values():
        for i, dim in enumerate((d for d in dims if d.owner == obj.id), 1):
            aliases.setdefault(f"D{i}@{obj.name}", []).append(dim.id)
    def bound(values):
        return tuple(replace(s, value=bind(s.value, aliases)) if isinstance(s.value, Expression) else s for s in values)
    return replace(state, common_values=bound(state.common_values), configurations=tuple(
        replace(c, overrides=bound(c.overrides)) for c in state.configurations))


def materialize(objects, variables, state, *, previous_common=()):
    """Apply all values of an object together; never build an invalid midpoint."""
    from .feature_objects import ConstructionFeature
    selected = {**_map(previous_common), **_map(state.common_values),
                **_map(state.configuration().overrides)}
    if not selected:
        return objects, tuple(variables)
    owners = {key.split(":", 1)[0] for key in selected if ":" in key}
    dims = {d.id: d for d in dimensions({key: obj for key, obj in objects.items() if key in owners})}
    variables = tuple(variables)
    variable_ids = {v.id for v in variables}
    result, edits, formulas, enabled = dict(objects), {}, {}, {}
    for target, setting in selected.items():
        owner, _, key = target.partition(":")
        if not key:
            if owner not in variable_ids:
                raise ValueError("Variable de configuration absente : " + owner)
            continue
        obj = objects.get(owner)
        if obj is None:
            raise ValueError("Producteur de configuration absent : " + owner)
        if key == "enabled":
            if not isinstance(obj, ConstructionFeature):
                raise ValueError("La substitution d’activation exige une fonction.")
            enabled[owner] = setting.value
            continue
        dim = dims.get(target)
        if dim is None:
            # A conditional parameter (depth of a through-all, for example)
            # remains dormant until that parameter is exposed again.
            if _dormant_parameter(obj, key):
                formulas.setdefault(owner, {})[key] = None
                continue
            raise ValueError("Dimension de configuration absente : " + target)
        value = setting.value
        formulas.setdefault(owner, {})[key] = value if isinstance(value, Expression) else None
        if not isinstance(value, Expression):
            if dim.integer:
                if not float(value).is_integer():
                    raise ValueError("La dimension exige un entier : " + dim.label)
                value = int(value)
            if value != dim.value:
                edits.setdefault(owner, {})[key] = value
    for owner in edits.keys() | formulas.keys() | enabled.keys():
        obj = result[owner]
        for key, expression in formulas.get(owner, {}).items():
            obj = set_expression(obj, key, expression)
        changes = edits.get(owner, {})
        if changes:
            obj = (obj.with_equation_values(changes) if callable(getattr(obj, "with_equation_values", None))
                   else replace(obj, parameters=replace(obj.parameters, **changes)))
        if owner in enabled:
            obj = replace(obj, enabled=enabled[owner])
        result[owner] = obj
    variables = tuple(replace(v, expression=(selected[v.id].value if isinstance(selected[v.id].value, Expression)
        else Expression(v.expression.target, "="+repr(selected[v.id].value)))) if v.id in selected else v for v in variables)
    return result, variables


def prune(state, objects, variables):
    """Structural deletion removes its substitutions in every configuration."""
    if not state.common_values:
        return state
    owners = {s.target.split(":", 1)[0] for s in state.common_values}
    available = settings({key: obj for key, obj in objects.items() if key in owners}, variables)
    def keep(s):
        owner, _, key = s.target.partition(":")
        if not key:
            return any(v.id == owner for v in variables)
        obj = objects.get(owner)
        return s.target in available or obj is not None and _dormant_parameter(obj, key)
    configurations = tuple(replace(c, overrides=tuple(s for s in c.overrides if keep(s))) for c in state.configurations)
    used = {s.target for c in configurations for s in c.overrides}
    common = tuple(s for s in state.common_values if s.target in used and keep(s))
    return replace(state, configurations=configurations, common_values=common)


def stage_edits(document, objects, variables, scope):
    if scope not in ("active", "common"):
        raise ValueError("Portée attendue : configuration active ou valeurs communes.")
    state = document.configuration_state
    edited = {key: obj for key, obj in objects.items() if key in document.objects and obj != document.objects[key]}
    before = settings({key: document.objects[key] for key in edited}, document.variables)
    after = settings(edited, variables)
    common = _map(state.common_values)
    changes = {}
    for key, value in after.items():
        if key in before:
            if value != before[key]:
                changes[key] = value
        elif key in common:
            owner, _, field = key.partition(":")
            previous = getattr(getattr(document.objects.get(owner), "parameters", None), field, None)
            # Revealing a conditional dimension is a structural change. Keep
            # its dormant expression unless the caller actually edits it.
            if value.value != previous:
                changes[key] = value
    active = state.configuration()
    overrides = _map(active.overrides)
    if scope == "active":
        for key, value in changes.items():
            if key not in common:
                common[key] = before[key]
            overrides[key] = value
    elif scope == "common":
        for key, value in changes.items():
            if key in common:
                common[key] = value
    state = replace(state, common_values=_ordered(common), configurations=tuple(
        replace(c, overrides=_ordered(overrides)) if c.id == active.id else c for c in state.configurations))
    state = prune(state, objects, variables)
    state = bind_state(state, objects, variables)
    objects, variables = materialize(objects, variables, state)
    return objects, variables, state


def _check_idle(document):
    document.history._check_idle()
    if document.configuration_editing:
        raise RuntimeError("Terminez l’édition active avant de changer de configuration.")


def candidate(document, state):
    state = bind_state(state, document.objects, document.variables)
    objects, variables = materialize(document.objects, document.variables, state,
                                      previous_common=document.configuration_state.common_values)
    objects, variables, _ = prepare(objects, variables)
    return objects, variables, state


def publish(document, state, title, *, evaluator=None):
    from .computation import complete
    return complete(publish_steps(document, state, title, evaluator=evaluator))


def publish_steps(document, state, title, *, evaluator=None):
    _check_idle(document)
    from .computation import ReservedComputation
    owner = object()
    return ReservedComputation(document, _publish_steps(document, state, title, evaluator, owner), owner=owner)


def _publish_steps(document, state, title, evaluator, owner):
    objects, variables, state = candidate(document, state)
    changed = {key for key, obj in objects.items() if obj != document.objects[key]}
    results, prefixes, resolved = {}, {}, {}
    from .body_history import affected_bodies, preview_construction_steps, result_receipts
    affected = affected_bodies(objects, changed)
    evaluator = evaluator or document.equation_evaluator
    if affected and evaluator is None:
        raise ValueError("Le service de recalcul doit être disponible pour activer cette configuration.")
    if changed:
        results, prefixes = yield from preview_construction_steps(document, objects, affected,
            evaluator or (lambda _: None), resolved_objects=resolved, materialized=True)
        objects.update(resolved)
        changed.update(resolved)
    from .document_commands import UpdateConstruction
    state = prune(state, objects, variables)
    with document.history.commit_reserved(owner, title):
        document.history.execute(UpdateConstruction(document, objects.values(), changed, title,
            result_receipts(objects, results, prefixes), variables=variables, configuration_state=state))


def create(document, name, *, source_id=None):
    _check_idle(document)
    state = document.configuration_state
    overrides = () if source_id is None else state.configuration(source_id).overrides
    config = Configuration(name.strip(), overrides=overrides)
    publish(document, replace(state, configurations=(*state.configurations, config)), "Créer la configuration")
    return config.id


def rename(document, key, name):
    state = document.configuration_state
    state.configuration(key)
    publish(document, replace(state, configurations=tuple(replace(c, name=name.strip()) if c.id == key else c
        for c in state.configurations)), "Renommer la configuration")


def remove(document, key, *, evaluator=None, incremental=False):
    state = document.configuration_state
    state.configuration(key)
    if key == state.default_id:
        raise ValueError("La configuration par défaut doit être conservée.")
    state = replace(state, configurations=tuple(c for c in state.configurations if c.id != key),
                    active_id=state.default_id if state.active_id == key else state.active_id)
    return (publish_steps if incremental else publish)(document, state, "Supprimer la configuration", evaluator=evaluator)


def activate(document, key, *, evaluator=None, incremental=False):
    _check_idle(document)
    state = document.configuration_state
    state.configuration(key)
    if key != state.active_id:
        return (publish_steps if incremental else publish)(document, replace(state, active_id=key),
            "Activer la configuration", evaluator=evaluator)


def set_override(document, key, target, value, *, evaluator=None, incremental=False):
    _check_idle(document)
    state = document.configuration_state
    config = state.configuration(key)
    common, overrides = _map(state.common_values), _map(config.overrides)
    if value is None:
        overrides.pop(target, None)
    else:
        available = settings(document.objects, document.variables)
        if target not in available:
            raise ValueError("Paramètre de configuration absent ou inactif.")
        common.setdefault(target, available[target])
        overrides[target] = ParameterSetting(target, value)
    state = replace(state, common_values=_ordered(common), configurations=tuple(
        replace(c, overrides=_ordered(overrides)) if c.id == key else c for c in state.configurations))
    return (publish_steps if incremental else publish)(document, state, "Modifier les substitutions", evaluator=evaluator)


def validate_all(document, *, evaluator=None, incremental=False):
    """Return per-configuration diagnostics without changing active state/history."""
    _check_idle(document)
    from .computation import ReservedComputation, complete
    steps = ReservedComputation(document, _validate_all_steps(document, evaluator))
    return steps if incremental else complete(steps)


def _validate_all_steps(document, evaluator):
    from .body_history import preview_construction_steps
    from .feature_objects import BodyObject
    evaluator = evaluator or document.equation_evaluator
    reports = {}
    cache = {}
    for index, config in enumerate(document.configurations, 1):
        try:
            objects, _, _ = candidate(document, replace(document.configuration_state, active_id=config.id))
            bodies = {key for key, obj in objects.items() if isinstance(obj, BodyObject)}
            if bodies and evaluator is None:
                raise ValueError("Le service de recalcul est indisponible.")
            yield from preview_construction_steps(document, objects, bodies, evaluator or (lambda _: None),
                resolved_objects={}, materialized=True, cache=cache)
            reports[config.id] = ""
        except (ValueError, RuntimeError) as exc:
            reports[config.id] = str(exc)
        yield index, len(document.configurations), config.name
    return reports
