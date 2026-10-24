"""Independent constraint islands; identities and local recipes stay unchanged."""
from dataclasses import replace


def components(sketch):
    parents = {}

    def root(key):
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for relation in sketch.constraints:
        keys = [ref.entity_id for ref in relation.refs if ref.entity_id != sketch.id]
        if not keys:
            continue
        first = root(keys[0])
        for key in keys[1:]:
            parents[root(key)] = first
    groups = {}
    for relation in sketch.constraints:
        key = next((ref.entity_id for ref in relation.refs if ref.entity_id != sketch.id), sketch.id)
        groups.setdefault(root(key), []).append(relation)
    islands = []
    for relations in groups.values():
        ids = {ref.entity_id for relation in relations for ref in relation.refs}
        islands.append(replace(sketch, entities=tuple(e for e in sketch.entities if e.id in ids),
                               constraints=tuple(relations)))
    return tuple(islands)
