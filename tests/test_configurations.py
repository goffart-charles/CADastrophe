"""Configurations exercise native recipes, exact volumes and shared history."""
from dataclasses import replace
from math import pi

import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer

from cadastrophe.core.configurations import create, activate, set_override, remove, rename, validate_all
from cadastrophe.core.feature_model import ExtrusionParameters
from test_parametric_operations import system, profile, append
from test_primitives import volume


def configured():
    doc, service = system()
    sketch = profile(doc, ((0, 0), (30, 0), (30, 20), (0, 20)))
    body, feature, _ = append(doc, service, sketch, parameters=ExtrusionParameters(depth=10.))
    doc.equation_evaluator = service.evaluator
    return doc, service, body, feature


def test_configurations_switch_real_volume_numeric_scopes_and_undo():
    doc, service, body, feature = configured()
    default = doc.active_configuration.id
    variant = create(doc, "Épaisse")
    activate(doc, variant)
    doc.update_construction((replace(feature, parameters=replace(feature.parameters, depth=20.)),))
    from cadastrophe.core.body_history import evaluate_body
    evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert volume(doc.objects[body.id].shape) == pytest.approx(12000.)
    assert doc.active_configuration.overrides
    activate(doc, default)
    assert doc.objects[feature.id].parameters.depth == 10.
    assert volume(doc.objects[body.id].shape) == pytest.approx(6000.)
    doc.set_parameter_scope("common")
    doc.update_construction((replace(doc.objects[feature.id], parameters=replace(feature.parameters, depth=12.)),))
    evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert volume(doc.objects[body.id].shape) == pytest.approx(7200.)
    activate(doc, variant)
    assert doc.objects[feature.id].parameters.depth == 20.
    doc.history.undo()
    assert doc.active_configuration.id == default
    assert volume(doc.objects[body.id].shape) == pytest.approx(7200.)
    doc.history.redo()
    assert doc.active_configuration.id == variant and BRepCheck_Analyzer(doc.objects[body.id].shape).IsValid()
    set_override(doc, variant, feature.id+":depth", None)
    assert doc.objects[feature.id].parameters.depth == 12.


def test_configurations_duplicate_delete_default_identity_and_validation():
    doc, service, body, feature = configured()
    default = doc.default_configuration
    variant = create(doc, "Courte")
    set_override(doc, variant, feature.id+":depth", 5.)
    duplicate = create(doc, "Copie", source_id=variant)
    rename(doc, duplicate, "Copie renommée")
    activate(doc, duplicate)
    assert volume(doc.objects[body.id].shape) == pytest.approx(3000.)
    assert all(not issue for issue in validate_all(doc).values())
    remove(doc, duplicate)
    assert doc.active_configuration.id == default.id and doc.default_configuration == default
    assert volume(doc.objects[body.id].shape) == pytest.approx(6000.)
    doc.history.undo()
    assert doc.active_configuration.id == duplicate and volume(doc.objects[body.id].shape) == pytest.approx(3000.)
    with pytest.raises(ValueError, match="par défaut"):
        remove(doc, default.id)


def test_configurations_failed_candidate_retains_every_live_state():
    doc, service, body, feature = configured()
    variant = create(doc, "Impossible")
    set_override(doc, variant, feature.id+":depth", -1.)
    snapshot = dict(doc.objects), doc.configuration_state, doc.variables, doc.history.count
    with pytest.raises(ValueError):
        activate(doc, variant)
    assert (dict(doc.objects), doc.configuration_state, doc.variables, doc.history.count) == snapshot
    assert validate_all(doc)[variant]
    assert (dict(doc.objects), doc.configuration_state, doc.variables, doc.history.count) == snapshot


def test_configurations_persistence_active_variants_and_schema8_migration(tmp_path):
    import json
    from zipfile import ZipFile, ZIP_DEFLATED
    from examples.parametric_example import registry
    from cadastrophe.core.persistence import save_document, load_document
    doc, service, body, feature = configured()
    default = doc.active_configuration.id
    variant = create(doc, "Épaisseur 25")
    set_override(doc, variant, feature.id+":depth", 25.)
    activate(doc, variant)
    path = tmp_path / "configurations.cada"
    types = registry(service)
    save_document(doc, path, registry=types)
    restored = load_document(path, registry=types)
    assert restored.configuration_state == doc.configuration_state
    assert set(restored.objects) == set(doc.objects)
    assert volume(restored.objects[body.id].shape) == pytest.approx(15000.)
    activate(restored, default, evaluator=service.evaluator)
    assert volume(restored.objects[body.id].shape) == pytest.approx(6000.)
    old = tmp_path / "livre-schema8.cada"
    with ZipFile(path) as source, ZipFile(old, "w", ZIP_DEFLATED) as destination:
        for name in source.namelist():
            payload = source.read(name)
            if name == "manifest.json":
                data = json.loads(payload)
                data["schema_version"] = 8
                del data["document"]["configuration_state"]
                payload = json.dumps(data).encode("utf-8")
            destination.writestr(name, payload)
    migrated = load_document(old, registry=types)
    assert len(migrated.configurations) == 1 and migrated.default_configuration.id == default
    assert migrated.objects[feature.id].parameters.depth == 25.
    assert volume(migrated.objects[body.id].shape) == pytest.approx(15000.)
    save_document(migrated, tmp_path / "converti.cada", registry=types)
    with ZipFile(tmp_path / "converti.cada") as archive:
        assert json.loads(archive.read("manifest.json"))["schema_version"] == 9


def test_configurations_one_default_numeric_edit_is_local_and_can_return_to_common():
    doc, service, body, feature = configured()
    doc.update_construction((replace(feature, parameters=replace(feature.parameters, depth=20.)),))
    assert doc.active_configuration.overrides[0].target == feature.id+":depth"
    variant = create(doc, "Valeurs communes")
    activate(doc, variant)
    assert doc.objects[feature.id].parameters.depth == 10.
    activate(doc, doc.default_configuration.id)
    assert doc.objects[feature.id].parameters.depth == 20.
    set_override(doc, doc.active_configuration.id, feature.id+":depth", None)
    assert doc.objects[feature.id].parameters.depth == 10.


def test_configurations_first_datum_numeric_edit_is_local():
    from test_parametric_operations import datum, plane
    from cadastrophe.core.operation_model import GeometryReference, PlaneParameters
    from cadastrophe.core.reference_geometry import resolve_plane
    doc, service, body, feature = configured()
    source = datum(doc, PlaneParameters("offset", (GeometryReference("plane", plane(doc).id),), distance=5.))
    doc.replace_feature(replace(source, parameters=replace(source.parameters, distance=12.)))
    assert doc.active_configuration.overrides[0].target == source.id+":distance"
    variant = create(doc, "Plan commun")
    activate(doc, variant)
    assert resolve_plane(doc, doc.objects[source.id]).origin.z == pytest.approx(5.)
    assert doc.objects[source.id].plane.origin.z == pytest.approx(5.)
    activate(doc, doc.default_configuration.id)
    assert resolve_plane(doc, doc.objects[source.id]).origin.z == pytest.approx(12.)
    assert doc.objects[source.id].plane.origin.z == pytest.approx(12.)


def assembly():
    from test_holes import setup_holes, hole
    from cadastrophe.core.hole_model import HoleParameters
    from cadastrophe.core.expressions import Variable, Expression
    from cadastrophe.core.parameters import set_expression
    from cadastrophe.core.feature_model import Operation
    doc, service, body, base, points = setup_holes(patterned=True)
    doc.equation_evaluator = service.evaluator
    bore, _ = hole(doc, service, body, points, HoleParameters(end_condition="through_all"))
    pocket_sketch = profile(doc, circle=(10., 30., 2.))
    _, pocket, _ = append(doc, service, pocket_sketch, body=body, operation=Operation.CUT,
                          parameters=ExtrusionParameters(depth=5.))
    thickness = Variable("Epaisseur", Expression("value", "=20"))
    diameter = Variable("Diametre", Expression("value", "=6"))
    doc.update_construction((set_expression(base, "depth", Expression("depth", "=Epaisseur")),
        set_expression(bore, "diameter", Expression("diameter", "=Diametre"))), variables=(thickness, diameter), scope="common")
    return doc, service, body, base, points, bore, pocket, thickness, diameter


def test_configurations_three_variants_holes_patterns_activation_common_rename_undo_reload(tmp_path):
    from math import pi
    from cadastrophe.core.expressions import Expression
    from cadastrophe.core.persistence import save_document, load_document
    from examples.parametric_example import registry
    import os
    from pathlib import Path
    doc, service, body, base, points, bore, pocket, thickness, diameter = assembly()
    default = doc.active_configuration.id
    count = points.id+":"+points.sketch.patterns[0].id+".count"
    heavy = create(doc, "Renforcée")
    light = create(doc, "Allégée")
    set_override(doc, heavy, thickness.id, Expression("value", "=30"))
    set_override(doc, heavy, diameter.id, Expression("value", "=8"))
    set_override(doc, heavy, count, 2)
    set_override(doc, heavy, pocket.id+":enabled", False)
    set_override(doc, light, thickness.id, Expression("value", "=15"))
    set_override(doc, light, diameter.id, Expression("value", "=4"))
    set_override(doc, light, count, 2)
    expected = {default: 48000-560*pi, heavy: 72000-960*pi, light: 36000-140*pi}
    ids = set(doc.objects)
    for _ in range(2):
        for key in (heavy, light, default):
            activate(doc, key)
            assert set(doc.objects) == ids
            assert volume(doc.objects[body.id].shape) == pytest.approx(expected[key])
            assert BRepCheck_Analyzer(doc.objects[body.id].shape, True).IsValid()
            assert doc.objects[bore.id].id == bore.id
    activate(doc, heavy)
    variables = tuple(replace(v, expression=Expression(v.expression.target, "=22")) if v.id == thickness.id else v
                      for v in doc.variables)
    doc.update_construction(variables=variables, scope="common")
    assert doc.objects[base.id].parameters.depth == 30.
    doc.set_variables(tuple(replace(v, name="Hauteur") if v.id == thickness.id else v for v in doc.variables))
    activate(doc, default)
    assert doc.objects[base.id].parameters.depth == 22.
    expected[default] = 52800-614*pi
    for key in (default, heavy, light):
        activate(doc, key)
        assert volume(doc.objects[body.id].shape) == pytest.approx(expected[key])
    snapshot = doc.configuration_state
    remove(doc, light)
    assert doc.active_configuration.id == default
    doc.history.undo()
    assert doc.configuration_state == snapshot
    doc.history.redo()
    doc.history.undo()
    path = tmp_path / "trois-configurations.cada"
    types = registry(service)
    save_document(doc, path, registry=types)
    restored = load_document(path, registry=types)
    assert restored.configuration_state == doc.configuration_state
    for key in (default, heavy, light):
        activate(restored, key, evaluator=service.evaluator)
        assert volume(restored.objects[body.id].shape) == pytest.approx(expected[key])
    assert all(not message for message in validate_all(restored, evaluator=service.evaluator).values())
    output = os.environ.get("CADASTROPHE_CONFIGURATION_EXAMPLE")
    if output:
        Path(output).mkdir(parents=True, exist_ok=True)
        activate(restored, default, evaluator=service.evaluator)
        save_document(restored, Path(output) / "01-trois-configurations.cada", registry=types)


def test_configurations_inactive_equation_cycle_retains_active_document():
    from cadastrophe.core.expressions import Expression, Variable
    doc, service, body, feature = configured()
    a, b = Variable("A", Expression("value", "=5")), Variable("B", Expression("value", "=8"))
    doc.set_variables((a, b))
    bad = create(doc, "Cycle")
    set_override(doc, bad, a.id, Expression("value", "=B"))
    set_override(doc, bad, b.id, Expression("value", "=A"))
    before = dict(doc.objects), doc.variables, doc.configuration_state, doc.history.count, doc.history.index
    with pytest.raises(ValueError, match="circulaire"):
        activate(doc, bad)
    assert (dict(doc.objects), doc.variables, doc.configuration_state, doc.history.count, doc.history.index) == before
    assert validate_all(doc)[bad]
    set_override(doc, bad, b.id, Expression("value", "=12"))
    activate(doc, bad)
    assert doc.active_configuration.id == bad


def test_configurations_inactive_expression_reference_blocks_source_deletion():
    from cadastrophe.core.expressions import Expression, Variable
    doc, service, body, feature = configured()
    source = Variable("Source", Expression("value", "=5"))
    doc.set_variables((source,))
    variant = create(doc, "Expression liée")
    set_override(doc, variant, feature.id+":depth", Expression("depth", "=Source"))
    before = dict(doc.objects), doc.variables, doc.configuration_state, doc.history.index
    with pytest.raises(ValueError, match="Référence.*absente"):
        doc.set_variables(())
    assert (dict(doc.objects), doc.variables, doc.configuration_state, doc.history.index) == before
    set_override(doc, variant, feature.id+":depth", None)
    doc.set_variables(())
    assert not doc.variables


def test_configurations_conditional_dimension_expression_stays_dormant():
    from test_holes import setup_holes, hole
    from cadastrophe.core.hole_model import HoleParameters
    from cadastrophe.core.expressions import Expression
    from cadastrophe.core.body_history import evaluate_body
    doc, service, body, _, points = setup_holes()
    doc.equation_evaluator = service.evaluator
    bore, _ = hole(doc, service, body, points, HoleParameters(depth=5.))
    variant = create(doc, "Profondeur pilotée")
    set_override(doc, variant, bore.id+":depth", Expression("depth", "=8"))
    activate(doc, variant)
    assert doc.objects[bore.id].parameters.depth == 8.
    current = doc.objects[bore.id]
    doc.update_construction((replace(current, parameters=replace(current.parameters, end_condition="through_all")),))
    assert not doc.objects[bore.id].expressions
    activate(doc, doc.default_configuration.id)
    activate(doc, variant)
    current = doc.objects[bore.id]
    doc.update_construction((replace(current, parameters=replace(current.parameters, end_condition="blind")),))
    evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert doc.objects[bore.id].parameters.depth == 8.
    assert doc.objects[bore.id].expressions[0].text == "=8"


def test_configurations_lost_finishing_reference_is_reported_then_can_be_disabled():
    from test_holes import setup_holes, hole
    from cadastrophe.core.feature_model import Operation, EdgeParameters
    from cadastrophe.core.feature_objects import EdgeFeature
    from cadastrophe.core.native_naming import subshapes, capture
    from plugins.modeling.edge_geometry import edge_reference
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.GeomAbs import GeomAbs_Circle
    from OCC.Core.TopoDS import topods
    from cadastrophe.document import ObjectType
    doc, service, body, _, points = setup_holes(patterned=True)
    doc.equation_evaluator = service.evaluator
    bore, result = hole(doc, service, body, points)
    edge = next(e for e in subshapes(result.shape, "Edge") if
        (c := BRepAdaptor_Curve(topods.Edge(e))).GetType() == GeomAbs_Circle and
        abs(c.Circle().Location().X()-45) < 1e-7 and abs(c.Circle().Location().Z()) < 1e-7)
    ref = capture(result.shape, edge, "Edge", result.topology)
    finish = EdgeFeature("Chanfrein du troisième trou", None, body.color, kind=ObjectType.FEATURE, body_id=body.id,
        operation=Operation.CHAMFER, parameters=EdgeParameters((edge_reference(result.shape, topods.Edge(edge)),), size=.5, references=(ref,)))
    owner = doc.objects[body.id]
    doc.update_construction((replace(owner, features=(*owner.features, finish.id)), finish))
    # Native rebuilding is explicit for recipes without equations.
    from cadastrophe.core.body_history import evaluate_body
    evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    small = create(doc, "Deux trous")
    set_override(doc, small, points.id+":"+points.sketch.patterns[0].id+".count", 2)
    before = dict(doc.objects), doc.configuration_state, doc.history.count
    with pytest.raises(ValueError, match="absent|ambigu|introuvable|perdue"):
        activate(doc, small)
    assert (dict(doc.objects), doc.configuration_state, doc.history.count) == before
    set_override(doc, small, finish.id+":enabled", False)
    activate(doc, small)
    assert doc.objects[finish.id].parameters.references == (ref,)
    assert not doc.objects[finish.id].enabled and BRepCheck_Analyzer(doc.objects[body.id].shape).IsValid()


def test_configurations_independent_body_cache(monkeypatch):
    from plugins.modeling.extrusion_service import _Evaluator
    doc, service, body, feature = configured()
    other_sketch = profile(doc, circle=(200., 0., 4.))
    other, _, _ = append(doc, service, other_sketch, parameters=ExtrusionParameters(depth=5.))
    independent = doc.objects[other.id].shape
    original = _Evaluator.evaluate
    calls = []
    def count(self, feature, *args):
        calls.append(feature.body_id)
        return original(self, feature, *args)
    monkeypatch.setattr(_Evaluator, "evaluate", count)
    variant = create(doc, "Haute")
    set_override(doc, variant, feature.id+":depth", 25.)
    activate(doc, variant)
    assert calls and other.id not in calls
    assert doc.objects[other.id].shape is independent
    calls.clear()
    rename(doc, variant, "Haute renommée")
    assert not calls and doc.objects[other.id].shape is independent


def test_configurations_shared_structure_deletion_clear_and_undo():
    from cadastrophe.core.body_history import evaluate_body
    doc, service, body, feature = configured()
    variant = create(doc, "Haute")
    set_override(doc, variant, feature.id+":depth", 25.)
    activate(doc, variant)
    sketch = profile(doc, circle=(200., 0., 4.))
    other, added, _ = append(doc, service, sketch, parameters=ExtrusionParameters(depth=5.))
    set_override(doc, variant, added.id+":depth", 8.)
    activate(doc, doc.default_configuration.id)
    assert added.id in doc.objects and doc.objects[added.id].parameters.depth == 5.
    assert volume(doc.objects[other.id].shape) == pytest.approx(80*pi)
    before = doc.configuration_state
    doc.remove_objects((other.id,))
    assert added.id not in doc.objects
    assert all(not s.target.startswith(added.id+":") for c in doc.configurations for s in c.overrides)
    doc.history.undo()
    assert doc.configuration_state == before and added.id in doc.objects
    activate(doc, variant)
    evaluate_body(doc, other.id, service.evaluator(doc), reuse=True)
    assert doc.objects[added.id].parameters.depth == 8.
    before = dict(doc.objects), doc.configuration_state
    doc.clear()
    assert not doc.objects and len(doc.configurations) == 1
    doc.history.undo()
    assert doc.configuration_state == before[1] and set(doc.objects) == set(before[0])
    evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    evaluate_body(doc, other.id, service.evaluator(doc), reuse=True)
    assert volume(doc.objects[body.id].shape) == pytest.approx(15000.)
    assert volume(doc.objects[other.id].shape) == pytest.approx(128*pi)


def test_configurations_positive_but_impossible_finishing_is_atomic():
    from cadastrophe.core.feature_model import Operation, EdgeParameters
    from cadastrophe.core.feature_objects import EdgeFeature
    from cadastrophe.core.body_history import evaluate_body
    from cadastrophe.document import ObjectType
    from cadastrophe.core.native_naming import subshapes
    from plugins.modeling.edge_geometry import edge_reference
    from OCC.Core.TopoDS import topods
    doc, service, body, feature = configured()
    edge = subshapes(doc.objects[body.id].shape, "Edge")[0]
    finish = EdgeFeature("Congé", None, body.color, kind=ObjectType.FEATURE, body_id=body.id,
        operation=Operation.FILLET, parameters=EdgeParameters((edge_reference(doc.objects[body.id].shape, topods.Edge(edge)),), size=1.))
    doc.update_construction((replace(doc.objects[body.id], features=(*body.features, finish.id)), finish))
    assert evaluate_body(doc, body.id, service.evaluator(doc), reuse=True).shape is not None
    variant = create(doc, "Congé impossible")
    set_override(doc, variant, finish.id+":size", 1000.)
    before = dict(doc.objects), doc.configuration_state, doc.history.count, doc.history.index
    with pytest.raises((ValueError, RuntimeError)):
        activate(doc, variant)
    assert (dict(doc.objects), doc.configuration_state, doc.history.count, doc.history.index) == before
    assert validate_all(doc)[variant]


@pytest.mark.parametrize("damage", ["duplicate", "missing_target", "unknown_field", "missing_active", "missing_binding"])
def test_configurations_corrupt_codec_rejected_without_replacing_document(tmp_path, damage):
    import json
    from zipfile import ZipFile, ZIP_DEFLATED
    from uuid import uuid4
    from cadastrophe.core.expressions import Expression
    from cadastrophe.core.persistence import save_document, load_document, DocumentFileError
    from examples.parametric_example import registry
    doc, service, body, feature = configured()
    variant = create(doc, "Variante")
    set_override(doc, variant, feature.id+":depth", 25.)
    path = tmp_path / "configurations.cada"
    types = registry(service)
    save_document(doc, path, registry=types)
    bad = tmp_path / "corrompu.cada"
    before = dict(doc.objects), doc.configuration_state, doc.history.index
    with ZipFile(path) as source, ZipFile(bad, "w", ZIP_DEFLATED) as destination:
        for name in source.namelist():
            payload = source.read(name)
            if name == "manifest.json":
                data = json.loads(payload)
                state = data["document"]["configuration_state"]
                override = state["configurations"][1]["overrides"][0]
                if damage == "duplicate":
                    state["configurations"][1]["overrides"].append(override)
                elif damage == "missing_target":
                    target = str(uuid4())+":depth"
                    override["target"] = state["common_values"][0]["target"] = target
                elif damage == "unknown_field":
                    state["inheritance"] = True
                elif damage == "missing_active":
                    state["active_id"] = str(uuid4())
                else:
                    from cadastrophe.core.expressions import expression_record
                    override["kind"] = "expression"
                    override["value"] = expression_record(Expression("depth", "=Perdu", (("Perdu", str(uuid4())),)))
                payload = json.dumps(data).encode("utf-8")
            destination.writestr(name, payload)
    with pytest.raises(DocumentFileError):
        load_document(bad, registry=types)
    assert (dict(doc.objects), doc.configuration_state, doc.history.index) == before
