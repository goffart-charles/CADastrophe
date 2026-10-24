"""Opt-in clipboard contracts for the project's versioned recipe codecs."""
import re

_LINKS = {"body_id", "object_id", "sketch_id", "plane_id", "producer_id"}
_LISTS = {"features", "sources"}
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def recipe_references(data):
    result = []
    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in _LINKS and item is not None:
                    result.append(item)
                elif key in _LISTS:
                    result.extend(item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(data)
    return tuple(result)


def remap_recipe(data, id_map):
    def visit(value):
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                if key in _LINKS and item is not None:
                    out[key] = id_map[item]
                elif key in _LISTS:
                    out[key] = [id_map[old] for old in item]
                elif key == "labels":
                    # Topology labels contain producer UUIDs, including nested
                    # pattern/offset provenance. Local entity UUIDs stay local.
                    out[key] = [_UUID.sub(lambda m: id_map.get(m.group(), m.group()), label) for label in item]
                else:
                    out[key] = visit(item)
            return out
        return [visit(item) for item in value] if isinstance(value, list) else value
    return visit(data)
