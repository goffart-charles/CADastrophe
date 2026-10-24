"""Strict JSON substitutions; geometry and effective result caches stay derived."""
from .configuration_model import Configuration, ConfigurationState, ParameterSetting
from .expressions import Expression, expression_record, read_expression


def _exact(value, keys):
    if type(value) is not dict or set(value) != set(keys.split()):
        raise ValueError("Configuration : champs manquants ou inconnus.")


def state_record(state):
    def setting(s):
        expression = isinstance(s.value, Expression)
        return dict(target=s.target, kind="expression" if expression else "value",
                    value=expression_record(s.value) if expression else s.value)
    return dict(active_id=state.active_id, default_id=state.default_id,
                common_values=[setting(s) for s in state.common_values],
                configurations=[dict(id=c.id, name=c.name, overrides=[setting(s) for s in c.overrides])
                                for c in state.configurations])


def read_state(data, document_id):
    _exact(data, "active_id default_id common_values configurations")
    def settings(values):
        if type(values) is not list or len(values) > 100000:
            raise ValueError("Liste de substitutions invalide.")
        result = []
        for record in values:
            _exact(record, "target kind value")
            if record["kind"] not in ("expression", "value"):
                raise ValueError("Type de substitution inconnu.")
            value = read_expression(record["value"]) if record["kind"] == "expression" else record["value"]
            result.append(ParameterSetting(record["target"], value))
        return tuple(result)
    if type(data["configurations"]) is not list or not 1 <= len(data["configurations"]) <= 256:
        raise ValueError("Liste de configurations invalide.")
    configs = []
    for record in data["configurations"]:
        _exact(record, "id name overrides")
        configs.append(Configuration(record["name"], record["id"], settings(record["overrides"])))
    from .configuration_model import initial_state
    if data["default_id"] != initial_state(document_id).default_id:
        raise ValueError("Identité de la configuration par défaut incohérente.")
    return ConfigurationState(tuple(configs), data["active_id"], data["default_id"], settings(data["common_values"]))


def validate_targets(state, objects, variables):
    from .configurations import settings, _dormant_parameter
    values = (*state.common_values, *(s for c in state.configurations for s in c.overrides))
    targets = {s.target for s in values} | {key for s in values if isinstance(s.value, Expression) for _, key in s.value.bindings}
    owners = {key.split(":", 1)[0] for key in targets if ":" in key}
    available = settings({key: obj for key, obj in objects.items() if key in owners}, variables)
    def known(target):
        if target in available:
            return True
        owner, _, key = target.partition(":")
        return owner in objects and _dormant_parameter(objects[owner], key)
    for s in values:
        if not known(s.target):
            raise ValueError("Paramètre de configuration absent : " + s.target)
        if isinstance(s.value, Expression) and any(not known(key) for _, key in s.value.bindings):
            raise ValueError("Référence d’équation de configuration absente : " + s.target)
