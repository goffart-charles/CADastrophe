"""Closed equation/configuration transport and atomic destination staging."""
from dataclasses import replace
from uuid import uuid4

from ..document import Document
from .clipboard import ClipboardError, MAX_OBJECTS
from .expressions import Expression, Variable, expression_record, read_expression
from .configuration_codec import state_record, read_state, validate_targets
from .configuration_model import initial_state
from .configurations import bind_state, materialize
from .parameters import prepare
from .variable_files import VariableSource, MAX_VARIABLES
from .persistence import _fields


def target_id(target, mapping):
    owner, separator, key = target.partition(":")
    if owner not in mapping:
        raise ClipboardError("Référence d’équation absente de la construction copiée : " + target)
    return mapping[owner] + (separator + key if separator else "")


def expression(exp, mapping):
    return replace(exp, bindings=tuple((name, target_id(key, mapping)) for name, key in exp.bindings))


def remap_state(state, mapping, document_id):
    def setting(s):
        return replace(s, target=target_id(s.target, mapping),
                       value=expression(s.value, mapping) if isinstance(s.value, Expression) else s.value)
    configs = {c.id: (initial_state(document_id).default_id if c.id == state.default_id else str(uuid4()))
               for c in state.configurations}
    return replace(state, default_id=configs[state.default_id], active_id=configs[state.active_id],
        common_values=tuple(setting(s) for s in state.common_values), configurations=tuple(
            replace(c, id=configs[c.id], overrides=tuple(setting(s) for s in c.overrides)) for c in state.configurations))


def transport(document, roots, registry):
    """Follow geometric and bound equation edges, including inactive variants."""
    from .clipboard_graph import references
    objects, variables, _ = prepare(document.objects, document.variables)
    state = bind_state(document.configuration_state, objects, variables)
    by_id = {v.id: v for v in variables}
    settings = (*state.common_values, *(s for c in state.configurations for s in c.overrides))
    pending, ids, variable_ids = list(roots), set(), set()
    while pending:
        key = pending.pop().split(":", 1)[0]
        if key in ids or key in variable_ids:
            continue
        if key in objects:
            obj = objects[key]
            registry.check_clipboard_object(obj)
            ids.add(key)
            pending.extend(references(registry, obj))
            formulas = obj.expressions
        elif key in by_id:
            variable_ids.add(key)
            formulas = (by_id[key].expression,)
        else:
            raise ClipboardError("La construction contient une dépendance d’équation absente.")
        formulas = (*formulas, *(s.value for s in settings if s.target.split(":", 1)[0] == key
                                and isinstance(s.value, Expression)))
        pending.extend(target for exp in formulas for _, target in exp.bindings)
        if len(ids) > MAX_OBJECTS or len(variable_ids) > MAX_VARIABLES:
            raise ClipboardError("La construction dépasse les limites du presse-papiers.")
    belongs = lambda s: s.target.split(":", 1)[0] in ids | variable_ids
    state = replace(state, common_values=tuple(s for s in state.common_values if belongs(s)), configurations=tuple(
        replace(c, overrides=tuple(s for s in c.overrides if belongs(s))) for c in state.configurations))
    source = document.variable_source
    if source is not None:
        source = replace(source, bindings=tuple((shared, local) for shared, local in source.bindings if local in variable_ids))
        if not source.bindings:
            source = None
    metadata = dict(variables=[dict(id=v.id, name=v.name, unit=v.unit, expression=expression_record(v.expression))
                               for v in variables if v.id in variable_ids],
                    variable_source=None if source is None else dict(path=source.path, library_id=source.library_id,
                        digest=source.digest, bindings=[list(pair) for pair in source.bindings]),
                    configuration_state=state_record(state), configuration_scope="all")
    return ids, objects, metadata


def read_metadata(data, source_id, objects):
    _fields(data, "variables variable_source configuration_state configuration_scope", "Paramètres copiés")
    if data["configuration_scope"] != "all":
        raise ClipboardError("La copie doit conserver toutes les configurations de la construction.")
    records = data["variables"]
    if type(records) is not list or len(records) > MAX_VARIABLES:
        raise ClipboardError("Liste de variables copiées invalide.")
    variables = []
    for v in records:
        _fields(v, "id name unit expression", "Variable copiée")
        variables.append(Variable(v["name"], read_expression(v["expression"]), v["id"], v["unit"]))
    if {v.id for v in variables} & objects.keys():
        raise ClipboardError("Identité commune à une variable et un objet.")
    state = read_state(data["configuration_state"], source_id)
    validate_targets(state, objects, variables)
    prepare(objects, variables)
    source = data["variable_source"]
    if source is not None:
        _fields(source, "path library_id digest bindings", "Bibliothèque copiée")
        if type(source["bindings"]) is not list or any(type(p) is not list for p in source["bindings"]):
            raise ClipboardError("Correspondances de bibliothèque invalides.")
        source = VariableSource(**dict(source, bindings=tuple(tuple(p) for p in source["bindings"])))
        from pathlib import Path
        if not Path(source.path).is_absolute() or not {local for _, local in source.bindings} <= {v.id for v in variables}:
            raise ClipboardError("Lien de bibliothèque incomplet ou chemin relatif dans la copie.")
    return tuple(variables), source, state


def rebuild(objects, variables, state, source, registry, *, document_id=None):
    """Rebuild every variant in isolated documents; return the active candidate."""
    active = None
    try:
        validate_targets(state, objects, variables)
        for config in state.configurations:
            detached = Document("Construction copiée", document_id=document_id)
            try:
                configured = replace(state, active_id=config.id)
                effective, values = materialize(objects, variables, configured)
                effective, values, _ = prepare(effective, values)
                detached._objects.update(effective)
                detached._variables, detached._variable_source = values, source
                detached._configuration_state = configured
                from .feature_objects import BodyObject
                from .feature_model import EvaluationResult, ResultStatus
                for obj in effective.values():
                    if isinstance(obj, BodyObject) and not obj.features and obj.source_shape is not None:
                        detached._body_results[obj.id] = EvaluationResult(ResultStatus.READY, obj.source_shape)
                registry.restore_document(detached)
                from .clipboard_graph import DecodedGraph
                DecodedGraph(detached, ()).calculations()
                if config.id == state.active_id:
                    active = detached
                else:
                    detached.deleteLater()
            except BaseException:
                detached.deleteLater()
                raise
        return active
    except BaseException:
        if active is not None:
            active.deleteLater()
        raise


def paste(document, batch, renamed, registry):
    """Fresh local variables, shared identities reused only on exact compatibility."""
    incoming = batch.document
    source, previous = incoming.variable_source, document.variable_source
    if source and previous and (source.library_id != previous.library_id or source.digest != previous.digest):
        raise ClipboardError("Bibliothèques de variables incompatibles ; rechargez ou choisissez une même bibliothèque avant de coller.")
    shared = dict(previous.bindings) if source and previous else {}
    mapping = {o.id: o.id for o in batch}
    mapping.update((v.id, v.id) for v in incoming.variables)
    mapping.update((local, shared[key]) for key, local in (source.bindings if source else ()) if key in shared)
    linked = {local for _, local in source.bindings} if source else set()
    existing = {v.id: v for v in document.variables}
    names = {v.name for v in document.variables}
    added, reused, renames = [], set(), []
    for v in incoming.variables:
        value = replace(v, id=mapping[v.id], expression=expression(v.expression, mapping))
        if value.id in existing:
            # Compare common recipes, independently of either active variant.
            before = next((s.value for s in document.configuration_state.common_values if s.target == value.id), existing[value.id].expression)
            after = next((expression(s.value, mapping) for s in incoming.configuration_state.common_values if s.target == v.id), value.expression)
            if existing[value.id].unit != value.unit or before != after:
                raise ClipboardError("La variable partagée « " + value.name + " » diffère dans la cible ; rechargez la bibliothèque.")
            reused.add(value.id)
            continue
        name, suffix = value.name, 1
        if v.id in linked and name in names:
            raise ClipboardError("La variable partagée « " + name + " » entre en conflit avec une variable locale ; renommez-la avant de coller.")
        while name in names:
            name = value.name + "_copie" + (str(suffix) if suffix > 1 else "")
            suffix += 1
        names.add(name)
        if name != value.name:
            renames.append(value.name + " → " + name)
        added.append(replace(value, name=name))
    variables = (*document.variables, *added)
    if len(variables) > MAX_VARIABLES:
        raise ClipboardError("Le document dépasserait la limite de variables.")
    objects = {**document.objects, **{o.id: replace(o, expressions=tuple(expression(e, mapping) for e in o.expressions)) for o in renamed}}
    state = document.configuration_state
    common = {s.target: s for s in state.common_values}
    configurations = list(state.configurations)
    correspondences = []
    matched = set()
    for config in incoming.configurations:
        target = (state.configuration(state.default_id) if config.id == incoming.configuration_state.default_id else
                  next((c for c in configurations if c.name.casefold() == config.name.casefold()), None))
        if target is None:
            target = replace(config, id=str(uuid4()), overrides=())
            configurations.append(target)
        if target.id in matched:
            raise ClipboardError("Deux configurations sources correspondent à la même configuration cible ; renommez le conflit avant de coller.")
        matched.add(target.id)
        correspondences.append(config.name + " → " + target.name)
        overrides = {s.target: s for s in target.overrides}
        incoming_common = {s.target: s.value for s in incoming.configuration_state.common_values}
        incoming_overrides = {s.target: s.value for s in config.overrides}
        for variable in incoming.variables:
            key = mapping[variable.id]
            if key in reused:
                desired = incoming_overrides.get(variable.id, incoming_common.get(variable.id, variable.expression))
                if isinstance(desired, Expression):
                    desired = expression(desired, mapping)
                current = overrides.get(key, common.get(key))
                actual = current.value if current else existing[key].expression
                if actual != desired:
                    raise ClipboardError("Substitution incompatible de variable partagée dans « " + target.name + " ».")
        for s in config.overrides:
            key = target_id(s.target, mapping)
            value = expression(s.value, mapping) if isinstance(s.value, Expression) else s.value
            setting = replace(s, target=key, value=value)
            if key in reused:
                current = overrides.get(key, common.get(key))
                current_value = current.value if current else existing[key].expression
                if current_value != value:
                    raise ClipboardError("Substitution incompatible de variable partagée dans « " + target.name + " ».")
            else:
                overrides[key] = setting
        configurations[configurations.index(target)] = replace(target, overrides=tuple(overrides.values()))
    for s in incoming.configuration_state.common_values:
        key = target_id(s.target, mapping)
        if key not in reused:
            common[key] = replace(s, target=key, value=expression(s.value, mapping) if isinstance(s.value, Expression) else s.value)
    state = replace(state, configurations=tuple(configurations), common_values=tuple(common.values()))
    if source:
        bindings = dict(previous.bindings) if previous else {}
        bindings.update((key, mapping[local]) for key, local in source.bindings)
        source = replace(previous or source, bindings=tuple(bindings.items()))
    else:
        source = previous
    candidate = rebuild(objects, variables, state, source, registry, document_id=document.id)
    try:
        from .body_history import result_receipts
        from .document_commands import UpdateConstruction
        results, prefixes = batch.__class__(candidate, ()).calculations()
        # Native reconstruction can create equivalent wrappers for the existing
        # solids. Keep the destination's original objects, revisions and caches.
        for key, previous_object in document.objects.items():
            computed = candidate.objects[key]
            if replace(computed, shape=previous_object.shape, revision=previous_object.revision) != previous_object:
                raise ClipboardError("Le collage modifierait une construction existante ; résolvez le conflit de paramètres.")
        published = {**candidate.objects, **document.objects}
        changed = published.keys() - document.objects.keys()
        results = {key: value for key, value in results.items() if key in changed}
        prefixes = {key: value for key, value in prefixes.items() if key in changed}
        document.history.execute(UpdateConstruction(document, published.values(), changed, "Coller la construction",
            result_receipts(published, results, prefixes), selection=batch.roots, variables=candidate.variables,
            variable_source=source, configuration_state=state))
    finally:
        candidate.deleteLater()
    return ("Toutes les configurations : " + "; ".join(correspondences)
            + ". Configuration active conservée : " + document.active_configuration.name
            + (". Variables : " + "; ".join(renames) if renames else ""))
