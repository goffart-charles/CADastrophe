"""Native repair workflows: exact choices, temporary previews and document undo."""
from dataclasses import replace
from math import pi
from uuid import uuid4
import os
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from OCC.Core.BRepCheck import BRepCheck_Analyzer

from cadastrophe.core.body_history import body_result, result_at
from cadastrophe.core.feature_model import BodyStateRef, ResultStatus
from cadastrophe.core.feature_objects import ConstructionFeature
from cadastrophe.core.native_naming import capture, subshapes
from cadastrophe.core.operation_model import GeometryReference as Ref
from cadastrophe.core.reference_repair import reference_entries, entry_issue, related_objects, error_sources
from cadastrophe.core.topology_refs import TopologyReference
from cadastrophe.core.persistence import save_document, write_document
from test_extrusion_tool import extrusion_system, start
from test_boss_tool import base_body, face_sketch
from test_support_repair import damaged_boss, top_view, screen, module
from test_parametric_rebuild import edge_operation
from test_primitives import volume


def begin(w, key):
    QTest.qWait(150)
    w.tree_selection_changed([key])
    assert w.command_registry.execute("cadastrophe.modeling.repair_references")
    tool = w.tool_sessions.tool
    assert tool is not None and tool.editor.objectName() == "referenceRepairEditor", w.tool_sessions.message
    return tool


def row(tool, predicate):
    for i in range(tool.references.topLevelItemCount()):
        item = tool.references.topLevelItem(i)
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if predicate(entry):
            tool.references.setCurrentItem(item)
            return entry
    raise AssertionError("Référence attendue absente du panneau.")


def apply(w, tool, entry, value):
    tool.set_replacement(entry, value)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message


def flush(w):
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()


def test_reference_repair_support_native_click_cancel_then_atomic_commit_and_reload(extrusion_system, tmp_path):
    w, _, _ = extrusion_system
    doc, body_id, sketch_id = damaged_boss(extrusion_system)
    snapshots, ids = dict(doc.objects), set(doc.objects)
    entities, expressions = doc.objects[sketch_id].sketch.entities, doc.objects[sketch_id].expressions
    output = os.environ.get("CADASTROPHE_REPAIR_EXAMPLE")
    if output:
        destination = Path(output)
        destination.mkdir(parents=True, exist_ok=True)
        write_document(doc, destination / "01-avant.cada", registry=w.object_types)
    tool = begin(w, sketch_id)
    assert not w.tool_sessions.can_commit
    row(tool, lambda e: e.role == "support")
    top_view(w)
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=screen(w, (50., 30., 20.)))
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert tool.references.currentItem().text(2) == "Résolue"
    assert dict(doc.objects) == snapshots
    assert w.tool_sessions.cancel()
    assert dict(doc.objects) == snapshots and doc.history.count == 0
    tool = begin(w, sketch_id)
    row(tool, lambda e: e.role == "support")
    top_view(w)
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=screen(w, (50., 30., 20.)))
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert set(doc.objects) == ids and doc.history.count == 1
    assert doc.objects[sketch_id].sketch.entities == entities and doc.objects[sketch_id].expressions == expressions
    assert volume(doc.objects[body_id].shape) == pytest.approx(120000+250*pi)
    assert BRepCheck_Analyzer(doc.objects[body_id].shape).IsValid()
    doc.history.undo()
    flush(w)
    assert body_result(doc, body_id).status is ResultStatus.ERROR
    doc.history.redo()
    flush(w)
    assert body_result(doc, body_id).status is ResultStatus.READY
    path = tmp_path / "reference-repaired.cada"
    assert w.save_document(path=path) and w.close_document() and w.open_document(path) is not None
    assert set(w.viewport.document.objects) == ids
    assert volume(w.viewport.document.objects[body_id].shape) == pytest.approx(120000+250*pi)
    if output:
        write_document(w.viewport.document, destination / "02-apres.cada", registry=w.object_types)


def test_reference_repair_context_actions_and_original_cause(extrusion_system):
    w, _, _ = extrusion_system
    doc, body_id, sketch_id = damaged_boss(extrusion_system)
    boss = doc.objects[body_id].features[-1]
    roots = error_sources(doc, boss)
    assert roots == (boss,)
    diagnostic = body_result(doc, body_id).diagnostics[0]
    assert diagnostic.reference.path == "sketch.support"
    assert diagnostic.reference.owner_id == sketch_id
    parents, consumers = related_objects(doc, sketch_id)
    assert boss in consumers and body_id in parents
    w.tree_selection_changed([sketch_id])
    commands = {a.text(): a for a in w.object_context_menu().actions()}
    assert "Réparer les références" in commands
    assert w.command_registry.execute("cadastrophe.modeling.select_consumers")
    assert boss in doc.selected_ids
    assert w.command_registry.execute("cadastrophe.modeling.select_error_sources")
    assert doc.selected_ids == roots
    assert not doc.history.count


def test_reference_repair_old_valid_geometry_is_not_a_selectable_fallback(extrusion_system, tmp_path):
    w, _, _ = extrusion_system
    doc, body_id, sketch_id = damaged_boss(extrusion_system)
    last = w.services.service("cadastrophe.modeling.history").last_valid(doc, body_id)
    assert last is not None and doc.objects[body_id].shape is None
    path = tmp_path / "failed.cada"
    save_document(doc, path, registry=w.object_types)
    tool = begin(w, sketch_id)
    assert "Dernier résultat valide conservé pour diagnostic" in tool.summary.text()
    for i in range(tool.producer.count()):
        ref = tool.producer.itemData(i)
        if ref is not None:
            assert result_at(doc, ref).status is ResultStatus.READY
            assert ref.producer_id != doc.objects[body_id].features[-1]
    assert not w.tool_sessions.can_commit
    assert w.tool_sessions.cancel()


def test_reference_repair_static_body_support_uses_actual_source(extrusion_system):
    w, _, _ = extrusion_system
    body = base_body(extrusion_system, static=True)
    sketch = face_sketch(extrusion_system, body)
    doc = w.viewport.document
    support = replace(sketch.sketch.support, reference=TopologyReference("Face", ("missing:static",)))
    doc.replace_feature(replace(sketch, sketch=replace(sketch.sketch, support=support)))
    doc.history.clear()
    tool = begin(w, sketch.id)
    row(tool, lambda e: e.role == "support")
    top_view(w)
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=screen(w, (50., 30., 20.)))
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert doc.objects[body.id].shape is body.shape
    assert doc.objects[sketch.id].sketch.entities == sketch.sketch.entities
    profiles = w.services.service("cadastrophe.sketch.profiles")
    assert all(not entry_issue(doc, e, profiles) for e in reference_entries(doc, sketch.id))
    doc.history.undo()
    flush(w)
    assert any(entry_issue(doc, e, profiles) for e in reference_entries(doc, sketch.id))
    doc.history.redo()
    flush(w)
    assert all(not entry_issue(doc, e, profiles) for e in reference_entries(doc, sketch.id))


def test_reference_repair_edge_preserves_parameters_and_rejects_wrong_state(extrusion_system):
    w, _, _ = extrusion_system
    body = base_body(extrusion_system)
    doc = w.viewport.document
    fillet_id = edge_operation(w, body.id, "fillet", 0., 0., 2.)
    original = doc.objects[fillet_id]
    expected = volume(doc.objects[body.id].shape)
    doc.replace_feature(replace(original, parameters=replace(original.parameters,
        references=(TopologyReference("Edge", ("missing:edge",)),))))
    flush(w)
    assert body_result(doc, body.id).status is ResultStatus.ERROR
    doc.history.clear()
    tool = begin(w, fillet_id)
    entry = row(tool, lambda e: e.role == "edge")
    with pytest.raises(ValueError, match="précédant"):
        tool.set_replacement(entry, Ref("edge", body.id, fillet_id, original.parameters.references[0]))
    value = Ref("edge", body.id, entry.slot.state.producer_id, original.parameters.references[0])
    apply(w, tool, entry, value)
    assert doc.objects[body.id].shape is None
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert doc.objects[fillet_id].parameters.size == original.parameters.size
    assert volume(doc.objects[body.id].shape) == pytest.approx(expected)
    assert doc.history.count == 1


def test_reference_repair_partial_equation_model_retains_geometric_error_and_can_recover(extrusion_system):
    w, _, _ = extrusion_system
    from test_equations import variable
    body = base_body(extrusion_system)
    doc = w.viewport.document
    fillet_id = edge_operation(w, body.id, "fillet", 0., 0., 2.)
    original = doc.objects[fillet_id]
    expected = volume(doc.objects[body.id].shape)
    broken = replace(original, parameters=replace(original.parameters, size=100.,
        references=(TopologyReference("Edge", ("missing:edge",)),)))
    doc.update_construction((broken,), variables=(variable("largeur", "100"),), allow_errors=True)
    assert body_result(doc, body.id).status is ResultStatus.ERROR
    doc.history.clear()
    tool = begin(w, fillet_id)
    entry = row(tool, lambda e: e.role == "edge")
    apply(w, tool, entry, Ref("edge", body.id, entry.slot.state.producer_id, original.parameters.references[0]))
    assert tool.rebuild_warning  # Reference fixed, independently impossible radius remains explicit.
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert doc.objects[fillet_id].parameters.references == original.parameters.references
    assert doc.objects[fillet_id].parameters.size == 100.
    assert body_result(doc, body.id).status is ResultStatus.ERROR and doc.objects[body.id].shape is None
    assert doc.history.count == 1
    doc.history.undo()
    flush(w)
    assert doc.objects[fillet_id].parameters.references == broken.parameters.references
    doc.history.redo()
    flush(w)
    assert doc.objects[fillet_id].parameters.references == original.parameters.references
    doc.replace_feature(original)
    flush(w)
    assert body_result(doc, body.id).status is ResultStatus.READY
    assert volume(doc.objects[body.id].shape) == pytest.approx(expected)
    assert BRepCheck_Analyzer(doc.objects[body.id].shape).IsValid()


def test_reference_repair_contour_missing_is_an_error_then_replaced_explicitly(extrusion_system, tmp_path):
    w, commands, m = extrusion_system
    body = base_body(extrusion_system)
    doc = w.viewport.document
    feature = doc.objects[body.features[0]]
    circle = m.Circle(m.Vec2(50, 30), 10)
    commands.add_entity(doc, feature.source.sketch_id, circle)
    doc.replace_feature(replace(feature, parameters=replace(feature.parameters, contours=(circle.id,))))
    commands.remove_entity(doc, feature.source.sketch_id, circle.id)
    flush(w)
    assert body_result(doc, body.id).status is ResultStatus.ERROR
    path = tmp_path / "missing-contour.cada"
    assert w.save_document(path=path) and w.close_document() and w.open_document(path) is not None
    doc = w.viewport.document
    tool = begin(w, feature.id)
    entry = row(tool, lambda e: e.role == "contour")
    source = doc.objects[feature.source.sketch_id]
    contour = w.services.service("cadastrophe.sketch.profiles").selectable_contours(doc, source.id)[0]
    apply(w, tool, entry, contour)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert volume(doc.objects[body.id].shape) == pytest.approx(120000)


def test_reference_repair_profile_replacement_keeps_feature_id_and_dimensions(extrusion_system):
    w, commands, m = extrusion_system
    body = base_body(extrusion_system)
    doc = w.viewport.document
    feature = doc.objects[body.features[0]]
    from test_equations import variable
    doc.set_variables((variable("hauteur", "20"),))
    doc.set_equation(feature.id, "depth", "=hauteur")
    feature = doc.objects[feature.id]
    variables = doc.variables
    xy = next(o for o in doc.objects.values() if o.plane and o.plane.name == "XY")
    source = commands.create_sketch(doc, xy.id)
    commands.add_entity(doc, source.id, m.Circle(m.Vec2(50, 30), 10))
    doc.history.clear()
    tool = begin(w, feature.id)
    entry = row(tool, lambda e: e.role == "sketch")
    apply(w, tool, entry, (source.id, ()))
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(2000*pi)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert doc.objects[feature.id].source.sketch_id == source.id
    assert doc.objects[feature.id].parameters.depth == feature.parameters.depth
    assert doc.objects[feature.id].expressions == feature.expressions
    assert doc.variables == variables
    assert doc.history.count == 1
    doc.history.undo()
    flush(w)
    assert volume(doc.objects[body.id].shape) == pytest.approx(120000)
    doc.history.redo()
    flush(w)
    assert volume(doc.objects[body.id].shape) == pytest.approx(2000*pi)


def test_reference_repair_future_support_refused_without_document_mutation(extrusion_system):
    w, _, _ = extrusion_system
    doc, body_id, sketch_id = damaged_boss(extrusion_system)
    tool = begin(w, sketch_id)
    entry = row(tool, lambda e: e.role == "support")
    snapshot = dict(doc.objects)
    # A named face from the consumer's former result must not be remapped to a prefix.
    feature_id = doc.objects[body_id].features[-1]
    value = Ref("face", body_id, feature_id, TopologyReference("Face", ("future:face",)))
    with pytest.raises(ValueError):
        tool.set_replacement(entry, value)
    assert dict(doc.objects) == snapshot and doc.history.count == 0
    assert w.tool_sessions.cancel()


def test_reference_repair_plane_cycle_stays_temporary(extrusion_system):
    w, _, _ = extrusion_system
    from cadastrophe.core.reference_objects import DatumPlaneObject
    from cadastrophe.core.operation_model import PlaneParameters
    from cadastrophe.core.reference_geometry import plane_shape
    from cadastrophe.document import ObjectType
    doc = w.viewport.document
    xy = next(o for o in doc.objects.values() if o.plane and o.plane.name == "XY")
    a = DatumPlaneObject("A", plane_shape(xy.plane), (.3,.4,.5), plane=xy.plane, kind=ObjectType.REFERENCE_PLANE,
                         parameters=PlaneParameters("offset", (Ref("plane", xy.id),), distance=5))
    doc.update_construction((a,))
    b = DatumPlaneObject("B", plane_shape(xy.plane), (.3,.4,.5), plane=xy.plane, kind=ObjectType.REFERENCE_PLANE,
                         parameters=PlaneParameters("offset", (Ref("plane", a.id),), distance=5))
    doc.update_construction((b,))
    flush(w)
    doc.history.clear()
    snapshot = dict(doc.objects)
    tool = begin(w, a.id)
    entry = row(tool, lambda e: e.slot.owner_id == a.id)
    tool.set_replacement(entry, Ref("plane", b.id))
    w.tool_sessions.update_preview()
    assert not w.tool_sessions.can_commit and "circulaire" in w.tool_sessions.message
    assert not w.tool_sessions.commit()
    assert dict(doc.objects) == snapshot and doc.history.count == 0
    assert w.tool_sessions.cancel()


def test_reference_repair_projection_preserves_constraint_and_entity_ids(extrusion_system):
    w, commands, m = extrusion_system
    from cadastrophe.document import SelectionItem
    body = base_body(extrusion_system)
    doc = w.viewport.document
    xy = next(o for o in doc.objects.values() if o.plane and o.plane.name == "XY")
    sketch = commands.create_sketch(doc, xy.id)
    external_api = module(w, "external")
    constraint_api = module(w, "constraints")
    vertex = external_api.shape_map(body.shape, "Vertex").FindKey(1)
    external = external_api.capture(doc, sketch.sketch, SelectionItem(body.id, "Vertex", vertex, body.revision))
    local = m.Point(m.Vec2(-5, -5))
    commands.add_entity(doc, sketch.id, local)
    constraint = constraint_api.Constraint("coincident", (constraint_api.Reference(local.id, "position"),
                                                          constraint_api.Reference(external.geometry.id, "position")))
    commands.set_constraints(doc, sketch.id, (constraint,), external=(external,))
    obj = doc.objects[sketch.id]
    damaged = replace(external, reference=TopologyReference("Vertex", ("missing:vertex",)))
    doc.replace_feature(replace(obj, sketch=replace(obj.sketch, external=(damaged,))))
    doc.history.clear()
    tool = begin(w, sketch.id)
    entry = row(tool, lambda e: e.role == "external")
    ref = BodyStateRef(body.id, body.features[-1])
    state = result_at(doc, ref)
    next_vertex = external_api.shape_map(body.shape, "Vertex").FindKey(3)
    value = Ref("vertex", body.id, ref.producer_id, capture(state.shape, next_vertex, "Vertex", state.topology))
    apply(w, tool, entry, value)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    fresh = doc.objects[sketch.id].sketch
    assert fresh.external[0].geometry.id == external.geometry.id
    assert fresh.constraints == (constraint,) and fresh.entities[0].id == local.id
    assert fresh.entities[0].position == fresh.external[0].geometry.position
    assert doc.history.count == 1


def test_reference_repair_shared_support_updates_both_consumers_atomically(extrusion_system):
    w, _, _ = extrusion_system
    from cadastrophe.core.feature_objects import BodyObject, ExtrusionFeature
    from cadastrophe.core.feature_model import Operation, SketchInput, ExtrusionParameters
    from cadastrophe.document import ObjectType
    doc, body_id, sketch_id = damaged_boss(extrusion_system)
    source = doc.objects[sketch_id]
    second = BodyObject("Second corps", None, (.5,.6,.7))
    feature = ExtrusionFeature("Second ajout", None, second.color, kind=ObjectType.FEATURE, body_id=second.id,
        operation=Operation.NEW_BODY, source=SketchInput(sketch_id, source.producer_refs), parameters=ExtrusionParameters(depth=10))
    doc.update_construction((replace(second, features=(feature.id,)), feature))
    flush(w)
    doc.history.clear()
    observations = []
    def observe(*_):
        observations.append((body_result(doc, body_id).status, body_result(doc, second.id).status))
    doc.object_changed.connect(observe)
    tool = begin(w, sketch_id)
    row(tool, lambda e: e.role == "support")
    top_view(w)
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=screen(w, (50.,30.,20.)))
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert observations and all(a is b for a, b in observations)
    assert volume(doc.objects[body_id].shape) == pytest.approx(120000+250*pi)
    assert volume(doc.objects[second.id].shape) == pytest.approx(250*pi)
    assert doc.history.count == 1


def test_reference_repair_named_face_preserves_shell_recipe(extrusion_system):
    w, _, _ = extrusion_system
    from cadastrophe.core.feature_objects import ShellFeature
    from cadastrophe.core.feature_model import Operation
    from cadastrophe.core.operation_model import ShellParameters
    from cadastrophe.core.reference_geometry import face_plane
    from cadastrophe.document import ObjectType
    body = base_body(extrusion_system)
    doc = w.viewport.document
    before = BodyStateRef(body.id, body.features[-1])
    state = result_at(doc, before)
    face = next(f for f in subshapes(state.shape, "Face") if face_plane(f).normal.z > .9)
    ref = Ref("face", body.id, before.producer_id, capture(state.shape, face, "Face", state.topology))
    shell = ShellFeature("Coque", None, body.color, kind=ObjectType.FEATURE, body_id=body.id,
        operation=Operation.SHELL, input_state=before, parameters=ShellParameters((ref,), thickness=2))
    doc.update_construction((replace(body, features=(*body.features, shell.id)), shell))
    flush(w)
    expected = volume(doc.objects[body.id].shape)
    bad = replace(ref, topology=TopologyReference("Face", ("missing:shell-face",)))
    doc.replace_feature(replace(shell, parameters=replace(shell.parameters, faces=(bad,))))
    flush(w)
    assert body_result(doc, body.id).status is ResultStatus.ERROR
    tool = begin(w, shell.id)
    entry = row(tool, lambda e: e.role == "face")
    apply(w, tool, entry, ref)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert doc.objects[shell.id].parameters == shell.parameters
    assert volume(doc.objects[body.id].shape) == pytest.approx(expected)


def test_reference_repair_axis_of_revolution_rebuilds_real_solid(extrusion_system):
    w, commands, m = extrusion_system
    from cadastrophe.core.feature_objects import BodyObject, RevolutionFeature
    from cadastrophe.core.feature_model import Operation, SketchInput
    from cadastrophe.core.operation_model import RevolutionParameters
    from cadastrophe.document import ObjectType
    doc = w.viewport.document
    xy = next(o for o in doc.objects.values() if o.plane and o.plane.name == "XY")
    source = commands.create_sketch(doc, xy.id)
    points = tuple(m.Vec2(*p) for p in ((5,-5),(10,-5),(10,5),(5,5)))
    commands.add_entities(doc, source.id, tuple(m.Segment(p, points[(i+1)%4]) for i, p in enumerate(points)), title="Profil")
    body = BodyObject("Corps tourné", None, (.5,.6,.7))
    feature = RevolutionFeature("Révolution", None, body.color, kind=ObjectType.FEATURE, body_id=body.id,
        operation=Operation.NEW_BODY, source=SketchInput(source.id),
        parameters=RevolutionParameters(Ref("sketch_line", source.id, entity_id=str(uuid4()))))
    doc.update_construction((replace(body, features=(feature.id,)), feature))
    flush(w)
    assert body_result(doc, body.id).status is ResultStatus.ERROR
    tool = begin(w, feature.id)
    entry = row(tool, lambda e: e.role == "axis")
    apply(w, tool, entry, Ref("axis", values=(0,0,0,0,1,0)))
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert volume(doc.objects[body.id].shape) == pytest.approx(750*pi)
    assert BRepCheck_Analyzer(doc.objects[body.id].shape).IsValid()


def test_reference_repair_split_face_requires_explicit_choice_and_named_neighbors():
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.gp import gp_Pnt
    from cadastrophe.core.native_naming import NamedTopology, NamingTrace, resolve
    from cadastrophe.core.reference_geometry import face_plane
    from cadastrophe.core.feature_model import EvaluationResult
    from cadastrophe.core.topology_refs import encode_reference, decode_reference
    def build(width):
        source = BRepPrimAPI_MakeBox(width, 20., 30.).Shape()
        def name(face):
            n = face_plane(face).normal
            return "source:" + ("right" if n.x > .9 else "left" if n.x < -.9 else
                                 "back" if n.y > .9 else "front" if n.y < -.9 else "top" if n.z > .9 else "bottom")
        names = NamedTopology(tuple((name(f), f) for f in subshapes(source, "Face")))
        cap = next(f for f in subshapes(source, "Face") if name(f) == "source:top")
        old = capture(source, cap, "Face", names)
        slot = BRepPrimAPI_MakeBox(gp_Pnt(8., -1., 25.), 4., 22., 10.).Shape()
        maker = BRepAlgoAPI_Cut(source, slot)
        result = maker.Shape()
        trace = NamingTrace("slot", EvaluationResult(ResultStatus.READY, source, topology=names))
        trace.operation(maker, (source, slot), result)
        return result, trace.topology, old
    shape, names, old = build(20.)
    with pytest.raises(ValueError, match="ambigu"):
        resolve(shape, old, names)
    caps = [f for f in subshapes(shape, "Face") if "source:top" in names.labels(f, "Face")]
    assert len(caps) == 2
    selected = caps[0]
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps
    selected_properties = GProp_GProps()
    brepgprop.SurfaceProperties(selected, selected_properties)
    selected_left = selected_properties.CentreOfMass().X() < 10.
    chosen = capture(shape, selected, "Face", names)
    assert any(label.startswith("@boundary:") for label in chosen.labels)
    assert resolve(shape, chosen, names).IsSame(selected)
    saved = decode_reference(encode_reference(chosen))
    for width in (20., 24., 21.):
        rebuilt, topology, legacy = build(width)
        assert BRepCheck_Analyzer(rebuilt).IsValid()
        restored = resolve(rebuilt, saved, topology)
        properties = GProp_GProps()
        brepgprop.SurfaceProperties(restored, properties)
        assert (properties.CentreOfMass().X() < 10.) == selected_left
        assert properties.Mass() == pytest.approx(20.*(8. if selected_left else width-12.))
        with pytest.raises(ValueError, match="ambigu"):
            resolve(rebuilt, legacy, topology)


def test_reference_repair_symmetric_named_neighborhood_is_still_ambiguous():
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from cadastrophe.core.native_naming import NamedTopology
    shape = BRepPrimAPI_MakeBox(20., 20., 20.).Shape()
    faces = subshapes(shape, "Face")
    names = NamedTopology(tuple(("same-provenance", face) for face in faces))
    with pytest.raises(ValueError, match="ambigu"):
        capture(shape, faces[0], "Face", names)


@pytest.mark.parametrize("case,relative", [
    ("curve", "balayage/droit/02-apres.cada"),
    ("anchor", "lissage/correspondance-explicite/02-apres.cada"),
    ("function", "repetition-lineaire/02-apres.cada"),
])
def test_reference_repair_existing_sweep_loft_and_pattern_recipes(extrusion_system, discard_document_changes, case, relative):
    w, _, _ = extrusion_system
    from cadastrophe.core.feature_objects import SweepFeature, LoftFeature, ReplayFeature
    path = Path(__file__).parent / "fonctions" / relative
    assert w.open_document(path) is not None
    QTest.qWait(150)
    doc = w.viewport.document
    cls = {"curve": SweepFeature, "anchor": LoftFeature, "function": ReplayFeature}[case]
    feature = next(obj for obj in doc.objects.values() if isinstance(obj, cls))
    expected = volume(doc.objects[feature.body_id].shape)
    ids = set(doc.objects)
    if case == "curve":
        original = feature.parameters.path[0]
        bad = replace(original, entity_id=str(uuid4()))
        parameters = replace(feature.parameters, path=(bad, *feature.parameters.path[1:]))
    elif case == "anchor":
        original = feature.parameters.sections[0].anchors[0]
        first = feature.parameters.sections[0]
        first = replace(first, anchors=(str(uuid4()), *first.anchors[1:]))
        parameters = replace(feature.parameters, sections=(first, *feature.parameters.sections[1:]))
    else:
        original = feature.parameters.sources[0]
        base_id = doc.objects[feature.body_id].features[0]
        assert base_id != original
        parameters = replace(feature.parameters, sources=(base_id,))
    doc.replace_feature(replace(feature, parameters=parameters))
    flush(w)
    doc.history.clear()
    tool = begin(w, feature.id)
    entry = row(tool, lambda e: e.role == case and e.slot.owner_id == feature.id)
    apply(w, tool, entry, original)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert set(doc.objects) == ids and doc.objects[feature.id].parameters == feature.parameters
    assert body_result(doc, feature.body_id).status is ResultStatus.READY
    assert BRepCheck_Analyzer(doc.objects[feature.body_id].shape).IsValid()
    assert volume(doc.objects[feature.body_id].shape) == pytest.approx(expected)
    assert doc.history.count == 1
    doc.history.undo()
    flush(w)
    assert doc.objects[feature.id].parameters == parameters
    doc.history.redo()
    flush(w)
    assert doc.objects[feature.id].parameters == feature.parameters
    assert volume(doc.objects[feature.body_id].shape) == pytest.approx(expected)
    assert w.close_document(doc.id)
    assert doc.history.count == 0  # Release native receipts before Qt ownership teardown.
