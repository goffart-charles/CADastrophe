"""Native activation: exact prefixes, dependencies, equations and undo ownership."""
from dataclasses import replace
from math import pi
from pathlib import Path
import os

import pytest
from PySide6.QtTest import QTest
from OCC.Core.BRepCheck import BRepCheck_Analyzer

from cadastrophe.core.body_history import body_result, result_at
from cadastrophe.core.feature_model import BodyStateRef, Operation, ResultStatus, SketchInput, ExtrusionParameters
from cadastrophe.core.feature_objects import BodyObject, ExtrusionFeature
from cadastrophe.core.expressions import Expression
from cadastrophe.document import ObjectType
from cadastrophe.core.topology_refs import TopologyReference
from cadastrophe.core.persistence import write_document
from test_extrusion_tool import extrusion_system, start, rectangle
from test_boss_tool import base_body, face_sketch
from test_parametric_rebuild import edge_operation, two_extrusions
from test_equations import variable
from test_primitives import volume


def flush(w):
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()


def toggle(w, keys, enabled):
    QTest.qWait(150)
    w.tree_selection_changed(list(keys))
    key = "cadastrophe.modeling." + ("enable_features" if enabled else "disable_features")
    assert w.command_registry.action(key).isEnabled()
    assert w.command_registry.execute(key)
    flush(w)


def new_body(w, sketch, depth=20.):
    doc = w.viewport.document
    body = BodyObject("Autre corps", None, (.5, .6, .7))
    supports = sketch.producer_refs or tuple(BodyStateRef(key, doc.objects[key].features[-1]) for key in sketch.body_dependencies)
    feature = ExtrusionFeature("Extrusion indépendante", None, body.color, kind=ObjectType.FEATURE,
        body_id=body.id, operation=Operation.NEW_BODY, source=SketchInput(sketch.id, supports),
        parameters=ExtrusionParameters(depth=depth))
    doc.update_construction((replace(body, features=(feature.id,)), feature))
    flush(w)
    return doc.objects[body.id]


def assert_solid(doc, body_id, expected):
    assert body_result(doc, body_id).status is ResultStatus.READY, body_result(doc, body_id).message
    shape = doc.objects[body_id].shape
    assert BRepCheck_Analyzer(shape).IsValid()
    assert volume(shape) == pytest.approx(expected)


@pytest.mark.parametrize("operation", ["fillet", "chamfer"])
def test_activation_finishing_native_ui_undo_and_reload(extrusion_system, tmp_path, operation):
    w, _, _ = extrusion_system
    body = base_body(extrusion_system)
    doc = w.viewport.document
    fid = edge_operation(w, body.id, operation, 0., 0., 2.)
    original = doc.objects[fid]
    expected = volume(doc.objects[body.id].shape)
    ids = set(doc.objects)
    prefix = result_at(doc, BodyStateRef(body.id, doc.objects[body.id].features[0]))
    doc.history.clear()
    toggle(w, (fid,), False)
    assert_solid(doc, body.id, 120000.)
    assert doc.objects[fid] == replace(original, enabled=False, revision=doc.objects[fid].revision)
    assert doc.objects[fid].visible == original.visible
    state = result_at(doc, BodyStateRef(body.id, fid))
    assert state.shape is prefix.shape and state.topology is prefix.topology and state.effects == ()
    assert doc.history.count == 1
    row = w.tree_adapter.items[fid]
    assert row.font(0).strikeOut() and "Désactivée" in row.toolTip(0)
    assert not w.command_registry.action("cadastrophe.modeling.edit_feature").isEnabled()
    assert "Désactiver" in [a.text() for a in w.object_context_menu().actions()]
    doc.history.undo(); flush(w)
    assert_solid(doc, body.id, expected)
    assert not w.tree_adapter.items[fid].font(0).strikeOut()
    doc.history.redo(); flush(w)
    assert_solid(doc, body.id, 120000.)
    output = os.environ.get("CADASTROPHE_ACTIVATION_EXAMPLE")
    if output and operation == "fillet":
        Path(output).mkdir(parents=True, exist_ok=True)
        write_document(doc, Path(output) / "01-desactive.cada", registry=w.object_types)
    path = tmp_path / "inactive.cada"
    assert w.save_document(path=path) and w.close_document() and w.open_document(path) is not None
    doc = w.viewport.document
    assert set(doc.objects) == ids and not doc.objects[fid].enabled
    assert_solid(doc, body.id, 120000.)
    toggle(w, (fid,), True)
    assert_solid(doc, body.id, expected)
    assert doc.objects[fid].parameters == original.parameters
    if output and operation == "fillet":
        write_document(doc, Path(output) / "02-reactive.cada", registry=w.object_types)


def test_activation_initial_no_base_updates_equations_without_building(extrusion_system, monkeypatch):
    w, _, _ = extrusion_system
    body = base_body(extrusion_system)
    doc = w.viewport.document
    fid = doc.objects[body.id].features[0]
    doc.set_variables((variable("profondeur", "20"),))
    doc.set_equation(fid, "depth", "=profondeur")
    expressions = doc.objects[fid].expressions
    native = w.plugins._record("cadastrophe.modeling").instance.extrusions
    factory, calls = native.evaluator, []
    def evaluator(document):
        adapter = factory(document)
        evaluate = adapter.evaluate
        def tracked(feature, *args):
            calls.append(feature.id)
            return evaluate(feature, *args)
        adapter.evaluate = tracked
        return adapter
    monkeypatch.setattr(native, "evaluator", evaluator)
    monkeypatch.setattr(doc, "equation_evaluator", evaluator)
    toggle(w, (fid,), False)
    assert body_result(doc, body.id).status is ResultStatus.UNAVAILABLE
    assert doc.objects[body.id].shape is None
    doc.set_variables((replace(doc.variables[0], expression=Expression("value", "=35")),)); flush(w)
    assert doc.objects[fid].parameters.depth == 35.
    assert doc.objects[fid].expressions == expressions and not calls
    before, count = dict(doc.objects), doc.history.count
    with pytest.raises(ValueError):
        doc.set_variables((replace(doc.variables[0], expression=Expression("value", "=1/0")),))
    assert dict(doc.objects) == before and doc.history.count == count
    toggle(w, (fid,), True)
    assert_solid(doc, body.id, 210000.)
    assert calls == [fid]  # Reused publication does not rebuild again.


@pytest.mark.parametrize("legacy", [False, "geometry", "index"])
def test_activation_interbody_created_face_blocks_but_inherited_face_survives(extrusion_system, tmp_path, legacy):
    w, _, _ = extrusion_system
    body_id, _ = two_extrusions(extrusion_system)
    doc = w.viewport.document
    source = doc.objects[body_id]
    boss_id = source.features[-1]
    created = face_sketch(extrusion_system, source, height=30)
    created_body = new_body(w, created)
    inherited = face_sketch(extrusion_system, source, center=(20, 20), height=20)
    if legacy:
        support = inherited.sketch.support
        inherited = replace(inherited, sketch=replace(inherited.sketch, support=replace(support,
                            reference=None if legacy == "index" else replace(support.reference, labels=()))))
        doc.replace_feature(inherited)
    inherited_body = new_body(w, inherited)
    independent = new_body(w, rectangle(extrusion_system))
    flush(w)
    independent_shape = doc.objects[independent.id].shape
    expected = {key: volume(doc.objects[key].shape) for key in (body_id, created_body.id, inherited_body.id)}
    refs = {key: doc.objects[key].sketch.support.reference for key in (created.id, inherited.id)}
    toggle(w, (boss_id,), False)
    assert_solid(doc, body_id, 120000.)
    blocked = body_result(doc, created_body.id)
    assert blocked.status is ResultStatus.BLOCKED and doc.objects[created_body.id].shape is None
    assert boss_id in blocked.diagnostics[0].root_ids and "désactiv" in blocked.message.lower()
    blocked_row = w.tree_adapter.items[created_body.features[0]]
    assert blocked_row.foreground(0).color().name() == "#ad6800" and not blocked_row.font(0).strikeOut()
    if legacy:
        assert body_result(doc, inherited_body.id).status is ResultStatus.BLOCKED
    else:
        assert_solid(doc, inherited_body.id, expected[inherited_body.id])
    assert doc.objects[independent.id].shape is independent_shape
    assert all(doc.objects[key].sketch.support.reference == ref for key, ref in refs.items())
    path = tmp_path / "interbody.cada"
    assert w.save_document(path=path) and w.close_document() and w.open_document(path) is not None
    doc = w.viewport.document
    assert body_result(doc, created_body.id).status is ResultStatus.BLOCKED
    toggle(w, (boss_id,), True)
    for key, value in expected.items():
        assert_solid(doc, key, value)


def test_activation_disabled_invalid_recipe_is_skipped_then_error_is_explicit(extrusion_system):
    w, _, _ = extrusion_system
    body = base_body(extrusion_system)
    doc = w.viewport.document
    fid = edge_operation(w, body.id, "fillet", 0., 0., 2.)
    original = doc.objects[fid]
    toggle(w, (fid,), False)
    bad = replace(doc.objects[fid], parameters=replace(original.parameters, size=100.,
                  references=(TopologyReference("Edge", ("missing:edge",)),)))
    doc.replace_feature(bad); flush(w)
    assert_solid(doc, body.id, 120000.)
    toggle(w, (fid,), True)
    assert body_result(doc, body.id).status is ResultStatus.ERROR
    assert doc.objects[body.id].shape is None and doc.objects[fid].parameters.size == 100.
    assert "Erreur" in w.tree_adapter.items[fid].toolTip(0)
    assert w.tree_adapter.items[fid].foreground(0).color().name() == "#c0392b"
    assert not w.tree_adapter.items[fid].font(0).strikeOut()
    doc.replace_feature(replace(original, revision=doc.objects[fid].revision)); flush(w)
    assert BRepCheck_Analyzer(doc.objects[body.id].shape).IsValid()


def test_activation_multi_selection_atomic_and_hiding_is_independent(extrusion_system):
    w, _, _ = extrusion_system
    bodies = (base_body(extrusion_system), new_body(w, rectangle(extrusion_system)))
    doc = w.viewport.document
    keys = tuple(doc.objects[b.id].features[0] for b in bodies)
    doc.set_visible(keys[0], False)
    doc.history.clear()
    observed = []
    def observe(*args):
        observed.append(tuple(doc.objects[key].enabled for key in keys))
        assert all(doc.objects[b.id].shape is None for b in bodies)
    doc.object_changed.connect(observe)
    toggle(w, keys, False)
    doc.object_changed.disconnect(observe)
    assert observed and all(value == (False, False) for value in observed)
    assert doc.history.count == 1 and not doc.objects[keys[0]].visible
    w.tree_selection_changed([keys[0], bodies[0].id])
    assert not w.command_registry.action("cadastrophe.modeling.enable_features").isEnabled()
    doc.history.undo(); flush(w)
    for body in bodies:
        assert_solid(doc, body.id, 120000.)
    doc.history.redo(); flush(w)
    assert all(not doc.objects[key].enabled for key in keys)
    toggle(w, keys, True)
    assert not doc.objects[keys[0]].visible
    for body in bodies:
        assert_solid(doc, body.id, 120000.)


def test_activation_static_base_survives_first_disabled_function(extrusion_system):
    w, _, _ = extrusion_system
    source = base_body(extrusion_system, static=True)
    sketch = face_sketch(extrusion_system, source)
    tool = start(w, sketch)
    tool.depth.set_internal_value(10.)
    assert w.tool_sessions.commit()
    doc = w.viewport.document
    body = doc.objects[source.id]
    fid = body.features[0]
    expected = volume(body.shape)
    toggle(w, (fid,), False)
    assert_solid(doc, body.id, 120000.)
    assert doc.objects[body.id].shape is source.shape
    toggle(w, (fid,), True)
    assert_solid(doc, body.id, expected)


def test_activation_pattern_effect_and_inactive_source(extrusion_system):
    w, _, _ = extrusion_system
    path = Path(__file__).parent / "fonctions/repetition-lineaire/02-apres.cada"
    assert w.open_document(path) is not None
    QTest.qWait(150)
    doc = w.viewport.document
    from cadastrophe.core.feature_objects import ReplayFeature
    pattern = next(obj for obj in doc.objects.values() if isinstance(obj, ReplayFeature))
    body = doc.objects[pattern.body_id]
    expected = volume(body.shape)
    previous = body.features[body.features.index(pattern.id)-1]
    before = result_at(doc, BodyStateRef(body.id, previous))
    toggle(w, (pattern.id,), False)
    assert_solid(doc, body.id, volume(before.shape))
    assert result_at(doc, BodyStateRef(body.id, pattern.id)).effects == ()
    toggle(w, (pattern.id,), True)
    assert_solid(doc, body.id, expected)
    source = pattern.parameters.sources[0]
    toggle(w, (source,), False)
    blocked = body_result(doc, body.id)
    assert blocked.status is ResultStatus.BLOCKED and source in blocked.diagnostics[0].root_ids
    assert "désactiv" in blocked.message.lower() and doc.objects[body.id].shape is None
    toggle(w, (source,), True)
    assert_solid(doc, body.id, expected)


def test_activation_skips_failed_semantic_input_of_disabled_boss(extrusion_system):
    w, _, _ = extrusion_system
    a_id, _ = two_extrusions(extrusion_system)
    doc = w.viewport.document
    source = face_sketch(extrusion_system, doc.objects[a_id], height=30.)
    body = new_body(w, rectangle(extrusion_system), depth=30.)
    boss = ExtrusionFeature("Ajout dépendant", None, body.color, kind=ObjectType.FEATURE,
        body_id=body.id, operation=Operation.BOSS, source=SketchInput(source.id, source.producer_refs))
    doc.update_construction((replace(body, features=(*body.features, boss.id)), boss)); flush(w)
    assert_solid(doc, body.id, 180000.+250*pi)
    first = doc.objects[a_id].features[0]
    toggle(w, (first,), False)
    assert body_result(doc, body.id).status is ResultStatus.BLOCKED
    toggle(w, (boss.id,), False)
    assert_solid(doc, body.id, 180000.)
    assert body_result(doc, a_id).status is ResultStatus.UNAVAILABLE or body_result(doc, a_id).status is ResultStatus.BLOCKED
    toggle(w, (first,), True)
    assert_solid(doc, body.id, 180000.)
    toggle(w, (boss.id,), True)
    assert_solid(doc, body.id, 180000.+250*pi)


def test_activation_datums_are_invalidated_and_restored_atomically(extrusion_system, tmp_path):
    w, commands, m = extrusion_system
    from cadastrophe.core.reference_objects import DatumPlaneObject
    from cadastrophe.core.operation_model import GeometryReference as Ref, PlaneParameters
    from cadastrophe.core.reference_geometry import resolve_plane, plane_shape
    body_id, _ = two_extrusions(extrusion_system)
    doc = w.viewport.document
    body = doc.objects[body_id]
    source = face_sketch(extrusion_system, body, height=30.)
    support = source.sketch.support
    first = DatumPlaneObject("Plan porté", plane_shape(support.frame), body.color, plane=support.frame,
        parameters=PlaneParameters("offset", (Ref("face", body_id, body.features[-1], support.reference),), distance=5.))
    plane = resolve_plane(doc, first)
    first = replace(first, plane=plane, shape=plane_shape(plane))
    doc.update_construction((first,))
    second = DatumPlaneObject("Plan dérivé", plane_shape(plane), body.color, plane=plane,
        parameters=PlaneParameters("offset", (Ref("plane", first.id),), distance=7.))
    plane = resolve_plane(doc, second)
    second = replace(second, plane=plane, shape=plane_shape(plane))
    doc.update_construction((second,)); flush(w)
    sketch = commands.create_sketch(doc, second.id)
    commands.add_entity(doc, sketch.id, m.Circle(m.Vec2(0., 0.), 5.))
    consumer = new_body(w, doc.objects[sketch.id])
    seen = []
    def observe(*args):
        seen.append(tuple(bool(doc.objects[key].reference_error_message) for key in (first.id, second.id)))
    doc.object_changed.connect(observe)
    toggle(w, (body.features[-1],), False)
    doc.object_changed.disconnect(observe)
    assert seen and all(value == (True, True) for value in seen)
    assert body_result(doc, consumer.id).status is ResultStatus.BLOCKED
    assert body.features[-1] in body_result(doc, consumer.id).diagnostics[0].root_ids
    for key in (first.id, second.id):
        assert "désactiv" in doc.objects[key].reference_error_message.lower()
        presentation = w.viewport.presentations.get(key)
        assert presentation is None or not w.viewport.context.IsDisplayed(presentation)
    path = tmp_path / "datums.cada"
    assert w.save_document(path=path) and w.close_document() and w.open_document(path) is not None
    doc = w.viewport.document
    assert all(doc.objects[key].reference_error_message for key in (first.id, second.id))
    toggle(w, (body.features[-1],), True)
    assert not doc.objects[first.id].reference_error_message and not doc.objects[second.id].reference_error_message
    assert doc.objects[first.id].plane.origin.z == pytest.approx(35.)
    assert doc.objects[second.id].plane.origin.z == pytest.approx(42.)
    assert_solid(doc, consumer.id, 500*pi)


def test_activation_keeps_independent_prefix_cached_and_refuses_bad_selection(extrusion_system, monkeypatch):
    w, _, _ = extrusion_system
    body_id, _ = two_extrusions(extrusion_system)
    doc = w.viewport.document
    body = doc.objects[body_id]
    base = doc.objects[body.features[0]]
    independent_source = face_sketch(extrusion_system, doc.objects[body_id], center=(20,20), height=20.)
    support = independent_source.sketch.support
    independent_source = replace(independent_source, sketch=replace(independent_source.sketch,
        support=replace(support, producer_id=base.id)))
    doc.replace_feature(independent_source)
    independent = new_body(w, independent_source)
    shape = doc.objects[independent.id].shape
    native = w.plugins._record("cadastrophe.modeling").instance.extrusions
    factory, calls = native.evaluator, []
    def evaluator(document):
        adapter = factory(document)
        evaluate = adapter.evaluate
        def tracked(feature, *args):
            calls.append(feature.id)
            return evaluate(feature, *args)
        adapter.evaluate = tracked
        return adapter
    monkeypatch.setattr(native, "evaluator", evaluator)
    from cadastrophe.core.feature_activation import set_enabled
    before, count = dict(doc.objects), doc.history.count
    with pytest.raises(ValueError):
        set_enabled(doc, (body.features[-1], body_id), False, evaluator)
    assert dict(doc.objects) == before and doc.history.count == count
    toggle(w, (body.features[-1],), False)
    assert doc.objects[independent.id].shape is shape and not calls
    count = doc.history.count
    assert not set_enabled(doc, (body.features[-1],), False, evaluator)
    assert doc.history.count == count
    toggle(w, (body.features[-1],), True)
    assert not calls  # The original enabled prefix also has a valid receipt.
    assert_solid(doc, body_id, 120000.+250*pi)
    assert doc.objects[independent.id].shape is shape
    toggle(w, (body.features[-1],), False)
    boss = doc.objects[body.features[-1]]
    doc.replace_feature(replace(boss, parameters=replace(boss.parameters, depth=40.))); flush(w)
    assert not calls
    assert_solid(doc, body_id, 120000.)
    toggle(w, (boss.id,), True)
    assert calls == [boss.id]
    assert_solid(doc, body_id, 120000.+1000*pi)
    assert doc.objects[independent.id].shape is shape
