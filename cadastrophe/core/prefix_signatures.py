"""Semantic fingerprints of exact construction prefixes, without native imports.

Unlike final-body receipts these keys exclude future operations. They describe
the whole dependency DAG, including sketch data, planes and explicit supports.
Derived geometry, appearance and replay revision counters are not inputs.
"""
from dataclasses import fields
from hashlib import sha256

from .feature_model import BodyStateRef
from .feature_objects import BodyObject


def state_signatures(objects):
    from .body_history import validate_graph
    edges = validate_graph(objects)
    signatures, previous = {}, {}
    cosmetic = {"name", "color", "visible", "transparency", "revision", "shape", "expressions", "material"}

    def digest(value):
        return sha256(repr(value).encode("utf-8")).digest()

    def static(key):
        ref = BodyStateRef(key)
        if ref not in signatures:
            obj = objects[key]
            shape = obj.source_shape if isinstance(obj, BodyObject) else obj.shape
            signatures[ref] = digest((key, id(shape)))
        return signatures[ref]

    for body in objects.values():
        if isinstance(body, BodyObject):
            before = None if body.source_kind is None else BodyStateRef(body.id)
            if before is not None:
                static(body.id)
            for key in body.features:
                previous[key] = before
                before = BodyStateRef(body.id, key)
    followers = {key: [] for key in edges}
    pending = {key: len(deps) for key, deps in edges.items()}
    for key, deps in edges.items():
        for dep in deps:
            followers[dep].append(key)
    ready = [key for key, count in pending.items() if count == 0]
    while ready:
        key = ready.pop()
        feature = objects[key]
        before = previous[key]
        predecessor = None if before is None else signatures[before]
        if not feature.enabled:
            # Geometry of a skipped operation depends only on the predecessor.
            # Stored parameters/equations still participate in document receipts.
            value = (key, False, predecessor)
        else:
            sketch = objects.get(feature.sketch_id)
            payload = () if sketch is None else tuple((f.name, getattr(sketch, f.name)) for f in fields(sketch) if f.name not in cosmetic)
            from .reference_graph import feature_inputs
            refs, planes, _ = feature_inputs(objects, feature)
            supports = tuple((ref, static(ref.body_id) if ref.producer_id is None else signatures[ref]) for ref in refs)
            value = (key, feature.evaluation_inputs, payload, planes, predecessor, supports)
        signatures[BodyStateRef(feature.body_id, key)] = digest(value)
        for child in followers[key]:
            pending[child] -= 1
            if pending[child] == 0:
                ready.append(child)
    return signatures
