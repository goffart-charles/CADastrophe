"""X7: exact prefix caching and reconstruction with real isolated providers."""
from dataclasses import replace
from importlib import import_module
from math import pi

import pytest
from PySide6.QtTest import QTest
from OCC.Core.BRepCheck import BRepCheck_Analyzer

from cadastrophe.core.body_history import evaluate_body, body_result, result_at
from cadastrophe.core.feature_model import BodyStateRef, Operation, ResultStatus, ExtrusionParameters, SketchInput
from cadastrophe.core.feature_objects import BodyObject, ExtrusionFeature
from cadastrophe.document import ObjectType
from cadastrophe.core.persistence import save_document, load_document
from test_modeling_history import sketch_document, construction, TraceAdapter
from test_extrusion_service import setup
from test_plugins import harness, plugin_root
from test_primitives import volume
from test_extrusion_tool import extrusion_system


def test_cache_reuses_only_unchanged_prefix_and_never_a_different_adapter():
    doc, sketch = sketch_document()
    body, functions = construction(doc, sketch)
    adapter = TraceAdapter()
    evaluate_body(doc, body.id, adapter, reuse=True)
    initial = result_at(doc, BodyStateRef(body.id, functions[0].id)).shape
    adapter.calls.clear()
    revised = replace(functions[1], parameters=ExtrusionParameters(depth=25))
    doc.replace_feature(revised)
    assert result_at(doc, BodyStateRef(body.id, functions[0].id)).shape is initial
    assert result_at(doc, BodyStateRef(body.id, functions[1].id)).status is ResultStatus.DIRTY
    evaluate_body(doc, body.id, adapter, reuse=True)
    assert [c[0] for c in adapter.calls] == [f.id for f in functions[1:]]
    other = TraceAdapter()
    evaluate_body(doc, body.id, other, reuse=True)
    assert [c[0] for c in other.calls] == [f.id for f in functions]
    adapter.calls.clear()
    doc.history.undo()
    evaluate_body(doc, body.id, adapter, reuse=True)
    assert body_result(doc, body.id).status is ResultStatus.READY
    # An upstream failure overwrites the base cache, while an older valid final
    # result may remain cached. Undo must recover every missing source prefix.
    doc.replace_feature(replace(functions[0], parameters=ExtrusionParameters(depth=30)))
    adapter.fail = functions[1].id
    evaluate_body(doc, body.id, adapter, reuse=True)
    doc.history.undo()
    adapter.fail = None
    adapter.calls.clear()
    evaluate_body(doc, body.id, adapter, reuse=True)
    assert [call[0] for call in adapter.calls] == [functions[0].id]
    assert all(result_at(doc, BodyStateRef(body.id, f.id)).status is ResultStatus.READY for f in functions)


def flush(h):
    QTest.qWait(180)


def base(h):
    doc, sketch, commands, m, records = setup(h)
    body, functions = construction(doc, sketch, (Operation.NEW_BODY,))
    doc.replace_feature(replace(functions[0], parameters=ExtrusionParameters(depth=20)))
    flush(h)
    assert body_result(doc, body.id).status is ResultStatus.READY
    assert volume(doc.objects[body.id].shape) == pytest.approx(120000)
    return doc, sketch, commands, m, records, doc.objects[body.id]


def face_source(h, data, body, *, center=(50, 30), height=20):
    doc, _, commands, m, records, _ = data
    supports = import_module(records['cadastrophe.sketch'].namespace + '.supports')
    from cadastrophe.geometry import Vec3
    faces = supports.face_map(body.shape)
    for index in range(1, faces.Size()+1):
        try:
            frame = supports.face_frame(faces.FindKey(index), 'Support')
        except ValueError:
            continue
        if frame.normal.z > .99 and abs(frame.origin.z-height) < 1e-7:
            break
    else:
        raise AssertionError('Face absente')
    source = commands.create_sketch(doc, m.PlaneSupport(body.id, frame, body.revision, index))
    u, v, _ = frame.to_local(Vec3(*center, height))
    commands.add_entity(doc, source.id, m.Circle(m.Vec2(u, v), 5))
    return doc.objects[source.id]


def append(h, data, source, operation, depth, *, reversed=False, through=False, new=False):
    from uuid import uuid4
    doc, _, _, _, _, old = data
    target = doc.objects[old.id]
    body_id = str(uuid4()) if new else target.id
    producer = BodyStateRef(target.id, target.features[-1])
    feature = ExtrusionFeature('Fonction', None, (.5,.6,.7), kind=ObjectType.FEATURE,
        operation=operation, body_id=body_id, source=SketchInput(source.id, (producer,)),
        parameters=ExtrusionParameters(depth=None if through else depth,
            reversed=reversed, end_condition='through_all' if through else 'blind'))
    body = (BodyObject('Second corps', None, (.5,.6,.7), id=body_id, features=(feature.id,)) if new else
            replace(target, features=(*target.features, feature.id)))
    doc.update_construction((body, feature))
    flush(h)
    assert body_result(doc, body_id).status is ResultStatus.READY, body_result(doc, body_id).message
    return doc.objects[body_id], feature


def widen(data, width):
    doc, original, commands, m, _, _ = data
    source = doc.objects[original.id]
    if source.sketch.constraints:
        key = next(c.id for c in source.sketch.constraints if c.kind == 'length')
        commands.set_constraints(doc, source.id, tuple(replace(c, value=width) if c.id == key else c
                                                     for c in source.sketch.constraints))
        return
    right = max(e.start.u for e in source.sketch.entities)
    entities = tuple(replace(e, start=m.Vec2(width if e.start.u == right else e.start.u, e.start.v),
                             end=m.Vec2(width if e.end.u == right else e.end.u, e.end.v))
                     for e in source.sketch.entities)
    doc.replace_feature(replace(source, sketch=replace(source.sketch, entities=entities)), title='Largeur')


def test_base_boss_cut_width_radius_depth_rebuild_and_undo_redo(harness, tmp_path):
    h = harness
    data = base(h)
    doc, initial, commands, m, records, body = data
    relations = import_module(records['cadastrophe.sketch'].namespace + '.constraints')
    C, R = relations.Constraint, relations.Reference
    edges = initial.sketch.entities
    constraints = tuple(C('coincident', (R(e.id,'end'), R(edges[(i+1)%4].id,'start')))
                        for i,e in enumerate(edges))
    constraints += tuple(C('horizontal' if i%2 == 0 else 'vertical', (R(e.id),)) for i,e in enumerate(edges))
    constraints += (C('length', (R(edges[0].id),),100), C('length',(R(edges[1].id),),60),
                    C('fixed',(R(edges[0].id,'start'),),m.Vec2(0,0)))
    commands.set_constraints(doc, initial.id, constraints)
    flush(h)
    body = doc.objects[body.id]
    boss_source = face_source(h, data, body)
    radius = C('radius', (R(boss_source.sketch.entities[0].id),), 5)
    commands.set_constraints(doc, boss_source.id, (radius,))
    boss_source = doc.objects[boss_source.id]
    body, boss = append(h, data, boss_source, Operation.BOSS, 10)
    cut_source = face_source(h, data, body, center=(80, 30))
    body, cut = append(h, data, cut_source, Operation.CUT, 5, reversed=True)
    assert volume(body.shape) == pytest.approx(120000+125*pi)
    original_ids = set(doc.objects)
    widen(data, 150)
    assert doc.objects[body.id].shape is None  # Never claim stale geometry is current.
    flush(h)
    assert volume(doc.objects[body.id].shape) == pytest.approx(180000+125*pi)
    current = doc.objects[boss_source.id]
    commands.set_constraints(doc, current.id, (replace(radius, value=8),))
    flush(h)
    assert volume(doc.objects[body.id].shape) == pytest.approx(180000+640*pi-125*pi)
    count = doc.history.count
    revised = replace(doc.objects[cut.id], parameters=replace(cut.parameters, depth=8))
    doc.replace_feature(revised)
    flush(h)
    assert doc.history.count == count+1
    assert volume(doc.objects[body.id].shape) == pytest.approx(180000+640*pi-200*pi)
    for action, expected in ((doc.history.undo, 180000+640*pi-125*pi),
                             (doc.history.redo, 180000+640*pi-200*pi)):
        action()
        flush(h)
        assert volume(doc.objects[body.id].shape) == pytest.approx(expected)
        assert BRepCheck_Analyzer(doc.objects[body.id].shape).IsValid()
    assert set(doc.objects) == original_ids
    assert [c.id for c in doc.objects[initial.id].sketch.constraints] == [c.id for c in constraints]
    path = tmp_path / 'rebuild.cada'
    save_document(doc, path, registry=h.types)
    loaded = load_document(path, registry=h.types)
    assert not loaded.is_modified and loaded.history.count == 0
    assert volume(loaded.objects[body.id].shape) == pytest.approx(180000+440*pi)
    assert loaded.objects[initial.id].sketch == doc.objects[initial.id].sketch


def test_traversing_cut_recomputes_extent_after_base_depth_change(harness):
    data = base(harness)
    doc, source, commands, m, records, body = data
    # Global-plane sketch: the support itself does not move with the upper face.
    hole = commands.create_sketch(doc, source.sketch.support.plane_id)
    commands.add_entity(doc, hole.id, m.Circle(m.Vec2(50,30), 5))
    cut = ExtrusionFeature('Trou', None, (.5,.6,.7), kind=ObjectType.FEATURE,
        body_id=body.id, operation=Operation.CUT, source=SketchInput(hole.id),
        parameters=ExtrusionParameters(depth=None, end_condition='through_all'))
    doc.update_construction((replace(body, features=(*body.features, cut.id)), cut))
    flush(harness)
    for depth in (20, 120, 7):
        first = doc.objects[body.features[0]]
        doc.replace_feature(replace(first, parameters=replace(first.parameters, depth=depth)))
        flush(harness)
        assert volume(doc.objects[body.id].shape) == pytest.approx((6000-25*pi)*depth)
        assert doc.objects[cut.id].parameters.depth is None
    blind = replace(cut, parameters=replace(cut.parameters, end_condition='blind', depth=3))
    doc.replace_feature(blind)
    flush(harness)
    assert volume(doc.objects[body.id].shape) == pytest.approx(42000-75*pi)
    doc.replace_feature(cut)
    flush(harness)
    assert volume(doc.objects[body.id].shape) == pytest.approx(42000-175*pi)
    assert doc.objects[cut.id].parameters.depth is None


@pytest.mark.parametrize('fault', ['open_profile', 'moved_face', 'wrong_direction'])
def test_upstream_failure_reports_error_blocks_downstream_and_recovers(harness, fault):
    data = base(harness)
    doc, source, commands, m, records, body = data
    boss_source = face_source(harness, data, body)
    body, boss = append(harness, data, boss_source, Operation.BOSS, 10)
    previous = body.shape
    doc.history.clear()
    if fault == 'open_profile':
        current = doc.objects[source.id]
        doc.replace_feature(replace(current, sketch=replace(current.sketch, entities=current.sketch.entities[:-1])))
    else:
        first = doc.objects[body.features[0]]
        parameters = replace(first.parameters, **({'depth':30} if fault == 'moved_face' else {'reversed':True}))
        doc.replace_feature(replace(first, parameters=parameters))
    flush(harness)
    state = body_result(doc, body.id)
    if fault == 'moved_face':
        assert state.status is ResultStatus.READY
        assert volume(doc.objects[body.id].shape) == pytest.approx(180000+250*pi)
    else:
        assert state.status in (ResultStatus.ERROR, ResultStatus.BLOCKED) and state.message
        assert doc.objects[body.id].shape is None
    assert BRepCheck_Analyzer(previous).IsValid()
    doc.history.undo()
    flush(harness)
    assert body_result(doc, body.id).status is ResultStatus.READY
    assert volume(doc.objects[body.id].shape) == pytest.approx(120000+250*pi)


def test_inter_body_explicit_support_and_closed_document_isolation(harness):
    data = base(harness)
    doc, source, commands, m, records, body = data
    upper = face_source(harness, data, body)
    child, _ = append(harness, data, upper, Operation.NEW_BODY, 10, new=True)
    other = harness.doc
    unchanged = dict(other.objects)
    widen(data, 130)
    harness.documents.activate(other.id)
    flush(harness)
    assert volume(doc.objects[body.id].shape) == pytest.approx(156000)
    assert volume(doc.objects[child.id].shape) == pytest.approx(250*pi)
    assert dict(other.objects) == unchanged
    widen(data, 140)
    harness.documents.remove(doc.id)
    flush(harness)
    assert body_result(doc, body.id).status is ResultStatus.DIRTY
    harness.documents.add(doc)
    flush(harness)
    # A subscriber may close the document from the first body's publication.
    # The next body must not be evaluated after that synchronous callback.
    closed = []
    def close_after_publication():
        if (not doc.history.blocked and not closed
                and body_result(doc, body.id).status is ResultStatus.READY
                and body_result(doc, child.id).status is ResultStatus.DIRTY):
            closed.append(True)
            harness.documents.remove(doc.id)
    doc.history.changed.connect(close_after_publication)
    widen(data, 145)
    flush(harness)
    doc.history.changed.disconnect(close_after_publication)
    assert closed and body_result(doc, child.id).status is ResultStatus.DIRTY


def test_commands_coalesce_and_reservation_defers_native_evaluation(harness, monkeypatch):
    data = base(harness)
    doc, _, _, _, _, body = data
    service = harness.services.service('cadastrophe.modeling.extrusions')
    cls = type(service.evaluator(doc))
    native = cls.evaluate
    calls = []
    def traced(self, feature, *args):
        calls.append(feature.id)
        return native(self, feature, *args)
    monkeypatch.setattr(cls, 'evaluate', traced)
    widen(data, 110)
    widen(data, 120)
    owner = object()
    doc.history.reserve(owner)
    flush(harness)
    assert body_result(doc, body.id).status is ResultStatus.DIRTY
    assert calls == []
    doc.history.release(owner)
    flush(harness)
    assert volume(doc.objects[body.id].shape) == pytest.approx(144000)
    assert calls == list(body.features)
    count, shape = doc.history.count, doc.objects[body.id].shape
    doc.rename(body.id, 'Renommé')
    flush(harness)
    assert doc.objects[body.id].shape is shape and doc.history.count == count+1
    assert calls == list(body.features)


def test_consumed_face_sketch_stays_editable_during_rebuild_and_undo(extrusion_system, monkeypatch):
    from test_boss_tool import base_body, face_sketch
    from test_extrusion_tool import start
    w, commands, _ = extrusion_system
    doc = w.viewport.document
    body = base_body(extrusion_system)
    source = face_sketch(extrusion_system, body)
    tool = start(w, source)
    tool.depth.setValue(10)
    assert w.tool_sessions.commit()
    service = w.services.service('cadastrophe.modeling.extrusions')
    cls = type(service.evaluator(doc))
    native, calls = cls.evaluate, []
    def traced(self, feature, *args):
        calls.append(feature.id)
        return native(self, feature, *args)
    monkeypatch.setattr(cls, 'evaluate', traced)
    doc.set_visible(source.id, True)
    w.tree_selection_changed([source.id])
    assert w.command_registry.execute('cadastrophe.sketch.edit')
    assert w.tool_sessions.editing
    original = doc.objects[source.id].sketch
    commands.replace_entity(doc, source.id, replace(original.entities[0], radius=8))
    assert w.tool_sessions.editing
    QTest.qWait(200)
    assert w.tool_sessions.editing and body_result(doc, body.id).status is ResultStatus.READY
    assert volume(doc.objects[body.id].shape) == pytest.approx(120000+640*pi)
    assert calls == [doc.objects[body.id].features[-1]]  # Tool-published base reused on first edit.
    doc.history.undo()
    QTest.qWait(200)
    assert w.tool_sessions.editing and doc.objects[source.id].sketch == original
    assert volume(doc.objects[body.id].shape) == pytest.approx(120000+250*pi)
    w.tool_sessions.end_edit()
    first = doc.objects[body.features[0]]
    doc.replace_feature(replace(first, parameters=replace(first.parameters, depth=30)))
    QTest.qWait(200)
    assert body_result(doc, body.id).status is ResultStatus.READY
    assert volume(doc.objects[body.id].shape) == pytest.approx(180000+250*pi)
    assert 'Erreur' not in w.tree_items[body.id].toolTip(0)
    name = doc.objects[body.id].name
    doc.set_visible(body.id, False)
    assert doc.objects[body.id].name == name
    doc.history.undo()
    doc.history.undo()
    QTest.qWait(200)
    assert body_result(doc, body.id).status is ResultStatus.READY
    assert 'Erreur' not in w.tree_items[body.id].toolTip(0)
    before = doc.objects[doc.objects[body.id].features[-1]].source.supports
    tool = start(w, doc.objects[source.id])
    tool.depth.setValue(20)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    newest = doc.objects[doc.objects[body.id].features[-1]]
    assert newest.source.supports == before
    assert w.services.service('cadastrophe.sketch.profiles').analyze(doc, source.id).valid
    commands.replace_entity(doc, source.id, replace(original.entities[0], radius=6))
    QTest.qWait(200)
    assert body_result(doc, body.id).status is ResultStatus.READY
    assert volume(doc.objects[body.id].shape) == pytest.approx(120000+720*pi)


def test_split_coplanar_support_is_refused_instead_of_reusing_its_index(harness):
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.gp import gp_Pnt
    data = base(harness)
    doc, _, _, _, _, body = data
    source = face_source(harness, data, body)
    slot = BRepPrimAPI_MakeBox(gp_Pnt(40,-10,10), 20.,80.,20.).Shape()
    split = BRepAlgoAPI_Cut(body.shape, slot).Shape()
    assert BRepCheck_Analyzer(split).IsValid()
    profiles = harness.services.service('cadastrophe.sketch.profiles')
    with pytest.raises(ValueError, match='ambigu|perdue'):
        profiles.to_faces(doc, source.id, support_shapes={body.id:split})
