"""Catalogue choices drive real holes; saved dimensions do not depend on lookup."""
from dataclasses import replace
import json
from pathlib import Path
import os

import pytest

from cadastrophe.core.fastener_catalog import hole_choices, apply_hole_choice, FAMILIES, CatalogChoice
from cadastrophe.core.hole_model import HoleParameters
from cadastrophe.core.feature_model import ResultStatus
from cadastrophe.core.persistence import save_document, load_document
from examples.parametric_example import registry
from test_holes import setup_holes, hole, removed
from test_primitives import volume


@pytest.mark.parametrize("family", [key for key, _ in FAMILIES])
def test_fastener_catalog_real_holes_and_frozen_offline_roundtrip(family, tmp_path, monkeypatch):
    choice = next(c for c in hole_choices(family) if c.nominal == 6)
    p = apply_hole_choice(HoleParameters(depth=15.), choice)
    assert p.catalog_active
    doc, service, body, _, positions = setup_holes()
    feature, result = hole(doc, service, body, positions, p)
    assert result.status is ResultStatus.READY
    assert volume(result.shape) == pytest.approx(48000 - removed(p))
    types = registry(service)
    path = tmp_path / "catalogue.cada"
    save_document(doc, path, registry=types)
    import cadastrophe.core.fastener_catalog as catalog
    monkeypatch.setattr(catalog, "_ROWS", ())
    restored = load_document(path, registry=types)
    assert restored.objects[feature.id].parameters == p
    assert volume(restored.objects[body.id].shape) == pytest.approx(volume(result.shape))
    custom = replace(p, diameter=p.diameter + .1)
    assert not custom.catalog_active and custom.catalog == choice
    root = os.environ.get("CADASTROPHE_FASTENER_EXAMPLES")
    if root:
        target = Path(root) / "percages"
        target.mkdir(parents=True, exist_ok=True)
        save_document(doc, target / (family + ".cada"), registry=types)


def test_fastener_catalog_source_oracles_and_explicit_old_hole_migration():
    from plugins.modeling.operation_codec import HOLE_CODEC
    from cadastrophe.document import SceneObject
    from cadastrophe.core.object_types import ObjectTypeRegistry
    doc, service, body, _, positions = setup_holes()
    feature, _ = hole(doc, service, body, positions)
    types = registry(service)
    extension, _ = types.encode(feature)
    assert extension["version"] == 3
    extension["version"] = 2
    del extension["data"]["parameters"]["catalog"]
    base = SceneObject(**{f.name: getattr(feature, f.name) for f in __import__("dataclasses").fields(SceneObject)})
    old, _ = types.decode(base, extension)
    assert old.parameters == HoleParameters() and old.enabled
    extension["version"] = 1
    with pytest.raises(ValueError, match="migrable"):
        types.decode(base, extension)
    taps = {(c.nominal, c.pitch): dict(c.dimensions)["diameter"] for c in hole_choices("tap_drill")}
    assert taps[6., 1.] == 5.
    assert taps[8., 1.25] == 6.8 and taps[8., 1.] == 7.
    assert taps[10., 1.25] == 8.8
    assert next(c for c in hole_choices("clearance") if c.nominal == 6).dimensions == (("diameter", 6.6),)
    with pytest.raises(ValueError):
        hole_choices("absent")
    with pytest.raises(ValueError):
        CatalogChoice("tap_drill", "M6", "1", "source", 6., 1., (("diameter", float("nan")),))


def test_fastener_catalog_rejects_incompatible_recipe_snapshots():
    from cadastrophe.core.thread_model import thread_choices, ThreadParameters, apply_thread_choice
    from test_threads import support
    with pytest.raises(ValueError, match="incompatibles"):
        HoleParameters(catalog=thread_choices()[0])
    _, _, p = support()
    with pytest.raises(ValueError, match="incompatibles"):
        replace(p, catalog=hole_choices("counterbore")[0])
    partial = replace(thread_choices()[0], dimensions=(("diameter", 6.),))
    with pytest.raises(ValueError, match="incomplet"):
        apply_thread_choice(p, partial)


def test_fastener_catalog_equations_and_configurations_become_custom_explicitly():
    from cadastrophe.core.expressions import Expression
    from cadastrophe.core.parameters import set_expression
    from cadastrophe.core.configurations import create, activate, set_override
    p = apply_hole_choice(HoleParameters(), next(c for c in hole_choices("tap_drill") if c.nominal == 6))
    doc, service, body, _, positions = setup_holes()
    doc.equation_evaluator = service.evaluator
    feature, _ = hole(doc, service, body, positions, p)
    default = doc.active_configuration.id
    variant = create(doc, "Perçage personnalisé")
    set_override(doc, variant, feature.id + ":diameter", 5.2)
    activate(doc, variant)
    assert not doc.objects[feature.id].parameters.catalog_active
    assert volume(doc.objects[body.id].shape) == pytest.approx(48000 - removed(replace(p, diameter=5.2)))
    activate(doc, default)
    assert doc.objects[feature.id].parameters.catalog_active
    doc.update_construction((set_expression(doc.objects[feature.id], "diameter", Expression("diameter", "=5.1")),))
    assert not doc.objects[feature.id].parameters.catalog_active
