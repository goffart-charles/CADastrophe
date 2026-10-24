"""Native historical consultation and insertion through the ordinary commands."""
from dataclasses import replace
from math import pi
from pathlib import Path
import os

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox

from cadastrophe.core.body_history import body_result, result_at, preview_construction, validate_graph
from cadastrophe.core.feature_model import BodyStateRef, Operation, ResultStatus, SketchInput
from cadastrophe.core.feature_objects import ExtrusionFeature, ReplayFeature, ShellFeature, DraftFeature
from cadastrophe.core.history_position import HistoryPosition, insertion_candidate
from cadastrophe.core.persistence import write_document, load_document
from cadastrophe.document import SceneObject, ObjectType
from cadastrophe.geometry import Vec3
from test_extrusion_tool import extrusion_system, rectangle, start
from test_boss_tool import base_body, face_sketch
from test_parametric_rebuild import edge_operation, two_extrusions
from test_feature_activation import flush, new_body, toggle, assert_solid
from test_primitives import volume


def position(w, feature_id=None, *, after=True):
    QTest.qWait(150)
    if feature_id is not None:
        w.tree_selection_changed([feature_id])
    action = "cadastrophe.modeling.history_" + ("end" if feature_id is None else "after" if after else "before")
    assert w.command_registry.action(action).isEnabled()
    assert w.command_registry.execute(action)


def displayed(w, body_id):
    return w.viewport.document_adapter.display_object(w.viewport.document.objects[body_id])


def test_history_position_consultation_no_mutation_save_full_and_missing_cache(extrusion_system, tmp_path, monkeypatch):
    import sys
    unhandled = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, error, tb: unhandled.append((kind.__name__, str(error))))
    w, _, _ = extrusion_system
    body_id, _ = two_extrusions(extrusion_system)
    doc = w.viewport.document
    body = doc.objects[body_id]
    final = body_result(doc, body_id)
    snapshots = dict(doc.objects)
    index, count, dirty = doc.history.index, doc.history.count, doc.is_modified
    base, boss = body.features
    model = w.plugins._record("cadastrophe.modeling").instance
    evaluated = []
    adapter_cls = type(model.extrusions.evaluator(doc))
    native = adapter_cls.evaluate
    def evaluate(self, feature, *args):
        evaluated.append(feature.id)
        return native(self, feature, *args)
    monkeypatch.setattr(adapter_cls, "evaluate", evaluate)
    position(w, base, after=False)
    assert displayed(w, body_id).shape is None
    assert w.tree_adapter.items[boss].font(0).italic()
    assert "Repère" in w.tree_adapter.items[base].parent().child(0).text(0)
    position(w, base)
    assert volume(displayed(w, body_id).shape) == pytest.approx(120000.)
    assert evaluated == []
    # A missing cache is reconstructed on a detached document, never published as final.
    ref = BodyStateRef(body_id, base)
    doc._body_prefixes[body_id].pop(ref)
    doc._body_evaluation_cache.clear()
    position(w, boss, after=False)
    assert volume(displayed(w, body_id).shape) == pytest.approx(120000.)
    assert evaluated == [base]
    assert body_result(doc, body_id) is final and doc.objects[body_id].shape is body.shape
    assert (doc.history.index, doc.history.count, doc.is_modified) == (index, count, dirty)
    assert all(doc.objects[k] is obj for k, obj in snapshots.items())
    path = tmp_path / "complete.cada"
    write_document(doc, path, registry=w.object_types)
    restored = load_document(path, registry=w.object_types)
    try:
        assert restored.objects[body_id].features == body.features
        assert volume(restored.objects[body_id].shape) == pytest.approx(volume(body.shape))
        assert restored.history_context.position is None
    finally:
        restored.deleteLater()
    position(w)
    assert displayed(w, body_id).shape is body.shape
    assert not w.tree_adapter.items[boss].font(0).italic()
    for _ in range(3):
        position(w,base)
        doc.clear()
        assert doc.history_context.position is None
        doc.history.undo(); flush(w)
        assert doc.history_context.position is None
        assert_solid(doc,body_id,volume(body.shape))
    assert not unhandled, unhandled


@pytest.mark.parametrize("operation", ["fillet", "chamfer"])
def test_history_position_insert_boss_before_finishing_cancel_undo_reload(extrusion_system, tmp_path, operation):
    w, _, _ = extrusion_system
    doc = w.viewport.document
    body = base_body(extrusion_system)
    source = face_sketch(extrusion_system, body)
    fid = edge_operation(w, body.id, operation, 0., 0., 2.)
    original = doc.objects[fid]
    before = volume(doc.objects[body.id].shape)
    doc.history.clear()
    position(w, fid, after=False)
    cursor = doc.history_context.position
    snapshot = dict(doc.objects)
    tool = start(w, source)
    tool.depth.setValue(10.)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(120000.+250*pi)
    assert doc.history.count == 0 and all(doc.objects[k] is v for k,v in snapshot.items())
    assert w.tool_sessions.cancel()
    assert doc.history_context.position == cursor
    assert all(doc.objects[k] is v for k,v in snapshot.items())
    output = os.environ.get("CADASTROPHE_HISTORY_EXAMPLE")
    if output and operation == "fillet":
        Path(output).mkdir(parents=True, exist_ok=True)
        write_document(doc, Path(output)/"01-avant.cada", registry=w.object_types)
    tool = start(w, source)
    tool.depth.setValue(10.)
    inserted = tool.feature_id
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert doc.history.count == 1
    assert doc.objects[body.id].features == (body.features[0], inserted, fid)
    assert doc.objects[fid] is original
    assert_solid(doc, body.id, before+250*pi)
    assert doc.history_context.position == HistoryPosition(inserted)
    doc.history.undo(); flush(w)
    assert doc.objects[body.id].features == (body.features[0], fid)
    assert doc.history_context.position is None
    assert_solid(doc, body.id, before)
    doc.history.redo(); flush(w)
    assert_solid(doc, body.id, before+250*pi)
    assert doc.objects[fid].parameters == original.parameters
    if output and operation == "fillet":
        write_document(doc, Path(output)/"02-apres.cada", registry=w.object_types)
    path = tmp_path / "inserted.cada"
    assert w.save_document(path=path) and w.close_document() and w.open_document(path) is not None
    doc = w.viewport.document
    assert doc.objects[body.id].features == (body.features[0], inserted, fid)
    assert_solid(doc, body.id, before+250*pi)


def test_history_position_insert_native_edge_before_boss_and_interbody(extrusion_system):
    w, _, _ = extrusion_system
    body_id, _ = two_extrusions(extrusion_system)
    doc = w.viewport.document
    body = doc.objects[body_id]
    dependent_source = face_sketch(extrusion_system, body, height=30.)
    other = new_body(w, dependent_source, 5.)
    independent = SceneObject("Corps indépendant", BRepPrimAPI_MakeBox(3.,4.,5.).Shape(), (.4,.4,.4))
    doc.add(independent)
    doc.history.clear()
    position(w, body.features[-1], after=False)
    assert displayed(w, other.id).shape is None
    assert not w.viewport.document_adapter.display_object(dependent_source).visible
    assert displayed(w, independent.id).shape is independent.shape
    assert w.command_registry.execute("cadastrophe.modeling.chamfer")
    tool = w.tool_sessions.tool
    w.command_registry.execute("view.fit")
    x,y = tool.surface.project(Vec3(0.,0.,10.))
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(round(x),round(y)))
    assert tool.body_id == body_id and len(tool.references) == 1
    tool.size.setValue(2.)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    new_id = tool.feature_id
    assert w.tool_sessions.commit()
    assert doc.objects[body_id].features == (body.features[0], new_id, body.features[-1])
    assert_solid(doc, body_id, 120000.+250*pi-40.)
    assert_solid(doc, other.id, 125*pi)
    assert doc.objects[independent.id] is independent
    assert doc.objects[dependent_source.id].producer_refs == dependent_source.producer_refs
    position(w)
    assert displayed(w, other.id).shape is doc.objects[other.id].shape


def test_history_position_insertion_reports_downstream_failure_before_commit(extrusion_system):
    w, commands, m = extrusion_system
    body = base_body(extrusion_system)
    doc = w.viewport.document
    fid = edge_operation(w, body.id, "fillet", 0.,0.,2.)
    original_shape = doc.objects[body.id].shape
    plane = next(o for o in doc.objects.values() if o.plane and o.plane.name == "XY")
    sketch = commands.create_sketch(doc, plane.id)
    points = tuple(m.Vec2(*p) for p in ((-5,-5),(10,-5),(10,65),(-5,65)))
    commands.add_entities(doc,sketch.id,tuple(m.Segment(p,points[(i+1)%4]) for i,p in enumerate(points)),title="Profil de coupe")
    doc.history.clear()
    position(w, fid, after=False)
    w.tree_selection_changed([sketch.id])
    assert w.command_registry.execute("cadastrophe.modeling.cut_extrude")
    tool = w.tool_sessions.tool
    tool.depth.setValue(40.)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert tool.rebuild_warning and fid in tool._insertion_prepared[1][body.id].diagnostics[0].root_ids
    assert doc.objects[body.id].shape is original_shape
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(108000.)
    assert w.tool_sessions.commit()
    assert body_result(doc, body.id).status is ResultStatus.ERROR and doc.objects[body.id].shape is None
    assert doc.history.count == 1 and doc.objects[fid].parameters.size == 2.
    position(w)
    assert displayed(w, body.id).shape is None
    doc.history.undo(); flush(w)
    assert_solid(doc, body.id, volume(original_shape))
    doc.history.redo(); flush(w)
    assert body_result(doc,body.id).status is ResultStatus.ERROR
    # A valid historical prefix remains editable while the final body is failed.
    position(w,fid,after=False)
    assert w.command_registry.execute("cadastrophe.modeling.chamfer")
    tool = w.tool_sessions.tool
    from plugins.modeling.edge_geometry import edges_of
    tool.add_edge(body.id,edges_of(displayed(w,body.id).shape)[0])
    tool.size.setValue(1.)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit,w.tool_sessions.message
    assert tool.rebuild_warning
    assert w.tool_sessions.commit(),w.tool_sessions.message
    assert body_result(doc,body.id).status is ResultStatus.ERROR and doc.objects[body.id].shape is None


def test_history_position_insert_before_pattern_keeps_source_effect(extrusion_system, discard_document_changes):
    w, _, _ = extrusion_system
    assert w.open_document(Path(__file__).parent/"fonctions/repetition-lineaire/02-apres.cada") is not None
    QTest.qWait(150)
    doc = w.viewport.document
    pattern = next(o for o in doc.objects.values() if isinstance(o,ReplayFeature))
    body = doc.objects[pattern.body_id]
    position(w, pattern.id, after=False)
    # An inactive inserted finishing step leaves the replay's generating volumes unchanged.
    from cadastrophe.core.feature_objects import EdgeFeature
    from cadastrophe.core.feature_model import EdgeParameters
    from plugins.modeling.edge_geometry import edges_of, edge_reference
    base_shape = displayed(w, body.id).shape
    feature = EdgeFeature("Finition insérée",None,body.color,kind=ObjectType.FEATURE,body_id=body.id,
        operation=Operation.FILLET,enabled=False,parameters=EdgeParameters((edge_reference(base_shape,edges_of(base_shape)[0]),),1.))
    objects = (replace(body,features=(*body.features,feature.id)),feature)
    model = w.plugins._record("cadastrophe.modeling").instance
    graph = {**doc.objects, **{o.id:o for o in objects}}
    results,prefixes = preview_construction(doc,graph,(body.id,),model.extrusions.evaluator,strict=False)
    expected = volume(body.shape)
    doc.history.clear()
    doc.update_construction(objects,results=results,prefixes=prefixes)
    assert doc.objects[body.id].features[-2:] == (feature.id,pattern.id)
    assert doc.objects[pattern.id].parameters == pattern.parameters
    assert_solid(doc,body.id,expected)
    doc.history.undo(); flush(w)
    assert_solid(doc,body.id,expected)
    doc.history.redo(); flush(w)
    assert_solid(doc,body.id,expected)


@pytest.mark.parametrize("case, cls", [("coque", ShellFeature), ("depouille", DraftFeature)])
def test_history_position_insertion_rebinds_shell_structure_preserves_face_identity(extrusion_system, discard_document_changes, tmp_path, case, cls):
    w, _, _ = extrusion_system
    assert w.open_document(Path(__file__).parent/"fonctions"/case/"02-apres.cada") is not None
    QTest.qWait(150)
    doc = w.viewport.document
    shell = next(o for o in doc.objects.values() if isinstance(o,cls))
    body = doc.objects[shell.body_id]
    expected = volume(body.shape)
    position(w,shell.id,after=False)
    from cadastrophe.core.feature_objects import EdgeFeature
    from cadastrophe.core.feature_model import EdgeParameters
    from plugins.modeling.edge_geometry import edges_of, edge_reference
    base_shape = displayed(w, body.id).shape
    feature = EdgeFeature("Finition inactive",None,body.color,kind=ObjectType.FEATURE,body_id=body.id,
        operation=Operation.CHAMFER,enabled=False,parameters=EdgeParameters((edge_reference(base_shape,edges_of(base_shape)[0]),),1.))
    objects = (replace(body,features=(*body.features,feature.id)),feature)
    candidate = insertion_candidate(doc,{**doc.objects,**{o.id:o for o in objects}})
    assert candidate[shell.id].input_state == BodyStateRef(body.id,feature.id)
    assert candidate[shell.id].parameters.faces == shell.parameters.faces
    validate_graph(candidate)
    doc.update_construction(objects); flush(w)
    assert_solid(doc,body.id,expected)
    assert doc.objects[shell.id].parameters == shell.parameters
    doc.history.undo(); flush(w)
    assert doc.objects[shell.id].input_state == shell.input_state
    assert_solid(doc,body.id,expected)
    doc.history.redo(); flush(w)
    position(w)
    path = tmp_path/(case+"-insertion.cada")
    assert w.save_document(path=path) and w.close_document() and w.open_document(path) is not None
    QTest.qWait(150)
    doc = w.viewport.document
    assert doc.objects[shell.id].input_state == BodyStateRef(body.id,feature.id)
    assert doc.objects[shell.id].parameters.faces == shell.parameters.faces
    assert_solid(doc,body.id,expected)
    w.tree_selection_changed([shell.id])
    assert w.command_registry.execute("cadastrophe.modeling.edit_feature")
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit,w.tool_sessions.message
    assert w.tool_sessions.commit(),w.tool_sessions.message
    assert_solid(doc,body.id,expected)


def test_history_position_future_reference_and_baseless_insertion_refused(extrusion_system):
    w, _, _ = extrusion_system
    body_id,_ = two_extrusions(extrusion_system)
    doc = w.viewport.document
    body = doc.objects[body_id]
    future = face_sketch(extrusion_system,body,height=30.)
    position(w,body.features[-1],after=False)
    new = ExtrusionFeature("Référence future",None,body.color,kind=ObjectType.FEATURE,body_id=body_id,
        operation=Operation.BOSS,source=SketchInput(future.id,future.producer_refs))
    count = doc.history.count
    with pytest.raises(ValueError,match="future"):
        doc.update_construction((replace(body,features=(*body.features,new.id)),new))
    assert doc.history.count == count and new.id not in doc.objects
    position(w,body.features[0],after=False)
    with pytest.raises(ValueError,match="base antérieure"):
        doc.update_construction((replace(body,features=(*body.features,new.id)),new))
    assert doc.history.count == count


def test_history_position_disabled_step_and_successive_insertions(extrusion_system):
    w, _, _ = extrusion_system
    body = base_body(extrusion_system)
    doc = w.viewport.document
    source = face_sketch(extrusion_system,body)
    fid = edge_operation(w,body.id,"fillet",0.,0.,2.)
    toggle(w,(fid,),False)
    position(w,fid,after=False)
    doc.history.clear()
    tool = start(w,source)
    tool.depth.setValue(10.)
    first = tool.feature_id
    assert w.tool_sessions.commit(),w.tool_sessions.message
    assert not doc.objects[fid].enabled
    assert_solid(doc,body.id,120000.+250*pi)
    # A second insertion uses the advanced marker, rather than reversing the order.
    other_source = face_sketch(extrusion_system,doc.objects[body.id],center=(70,30),height=20.)
    anchor = w.tree_adapter.items[first]
    assert "Repère" in anchor.parent().child(anchor.parent().indexOfChild(anchor)+1).text(0)
    tool = start(w,other_source)
    tool.depth.setValue(5.)
    second = tool.feature_id
    assert w.tool_sessions.commit(),w.tool_sessions.message
    assert doc.objects[body.id].features == (body.features[0],first,second,fid)
    assert_solid(doc,body.id,120000.+375*pi)


def test_history_position_active_insertion_before_pattern_equations_and_repeated_rebuild(extrusion_system, tmp_path):
    w, _, _ = extrusion_system
    body_id,_ = two_extrusions(extrusion_system)
    doc = w.viewport.document
    body = doc.objects[body_id]
    source = face_sketch(extrusion_system,body,center=(70,50),height=20.)
    w.tree_selection_changed([body.features[-1]])
    assert w.command_registry.execute("cadastrophe.modeling.linear_pattern")
    tool = w.tool_sessions.tool
    from cadastrophe.core.operation_model import GeometryReference
    tool.reference.set_reference(GeometryReference("axis",values=(0,0,0,1,0,0)))
    tool.count.setValue(3)
    tool.spacing.set_internal_value(15.)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    pattern_id = doc.objects[body_id].features[-1]
    pattern = doc.objects[pattern_id]
    assert_solid(doc,body_id,120000.+750*pi)
    from test_equations import variable
    from cadastrophe.core.expressions import Expression
    doc.set_variables((variable("hauteur","12"),))
    position(w,pattern_id,after=False)
    doc.history.clear()
    tool = start(w,source)
    tool.depth.lineEdit().setText("=hauteur/2")
    tool.depth.lineEdit().textEdited.emit("=hauteur/2")
    inserted = tool.feature_id
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit,w.tool_sessions.message
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(120000.+400*pi)
    assert w.tool_sessions.commit(),w.tool_sessions.message
    assert doc.history.count == 1 and doc.objects[body_id].features[-2:] == (inserted,pattern_id)
    assert doc.objects[pattern_id].parameters == pattern.parameters
    assert_solid(doc,body_id,120000.+900*pi)
    position(w)
    for value in (16.,8.,12.):
        doc.set_variables((replace(doc.variables[0],expression=Expression("value","="+str(value))),))
        flush(w)
        assert_solid(doc,body_id,120000.+750*pi+25*pi*value/2)
        assert doc.objects[pattern_id].parameters.sources == pattern.parameters.sources
    path = tmp_path/"motif-insertion.cada"
    assert w.save_document(path=path) and w.close_document() and w.open_document(path) is not None
    doc = w.viewport.document
    assert doc.objects[inserted].expressions[0].text == "=hauteur/2"
    assert_solid(doc,body_id,120000.+900*pi)
    doc.set_variables((replace(doc.variables[0],expression=Expression("value","=20")),))
    flush(w)
    assert_solid(doc,body_id,120000.+1000*pi)


def test_history_position_export_native_step_uses_complete_model(extrusion_system, tmp_path):
    w, _, _ = extrusion_system
    body_id,_ = two_extrusions(extrusion_system)
    doc = w.viewport.document
    position(w,doc.objects[body_id].features[0])
    assert volume(displayed(w,body_id).shape) == pytest.approx(120000.)
    from plugins.import_export.dialog import ExchangeDialog
    from shiboken6 import isValid
    context = w.plugins._record("cadastrophe.modeling").context
    output = tmp_path/"complete.step"
    cursor = doc.history_context.position
    index,count = doc.history.index,doc.history.count
    def exchange(mode):
        dialog = ExchangeDialog(context,mode,w)
        dialog.show()
        QTest.qWait(50)
        dialog.path.setText(str(output))
        if mode == "export":
            dialog.scope.setCurrentIndex(dialog.scope.findData("all"))
        try:
            dialog.start()
            for _ in range(2000):
                if not dialog._running:
                    break
                QTest.qWait(10)
            assert not dialog._running and not dialog.error_message,dialog.error_message
        finally:
            if isValid(dialog):
                dialog.close()
        assert not w.documents.blocked
    exchange("export")
    assert output.exists()
    assert (doc.history.index,doc.history.count) == (index,count)
    assert doc.history_context.position == cursor
    imported = w.create_document()
    QTest.qWait(150)
    assert w.viewport.camera is not None and not w.viewport._failed
    exchange("import")  # STEP's process-global state stays in the ordinary exchange worker.
    solids = [o for o in imported.objects.values() if o.kind is ObjectType.BODY]
    assert len(solids) == 1
    shape = solids[0].shape
    assert BRepCheck_Analyzer(shape).IsValid()
    assert volume(shape) == pytest.approx(120000.+250*pi)


@pytest.mark.parametrize("case, cls", [("coque",ShellFeature),("depouille",DraftFeature)])
def test_history_position_face_codec_rejects_old_versions(extrusion_system, discard_document_changes, case, cls):
    w, _, _ = extrusion_system
    assert w.open_document(Path(__file__).parent/"fonctions"/case/"02-apres.cada") is not None
    feature = next(o for o in w.viewport.document.objects.values() if isinstance(o, cls))
    extension, _ = w.object_types.encode(feature)
    assert extension["version"] == 3
    for version in (1, 2):
        extension["version"] = version
        with pytest.raises(ValueError, match="Version du type"):
            w.object_types.decode(feature, extension)


def test_history_position_static_base_before_first_function(extrusion_system):
    w, _, _ = extrusion_system
    body = base_body(extrusion_system,static=True)
    source = face_sketch(extrusion_system,body)
    doc = w.viewport.document
    fid = edge_operation(w,body.id,"chamfer",0.,0.,2.)
    position(w,fid,after=False)
    assert displayed(w,body.id).shape is doc.objects[body.id].source_shape
    tool = start(w,source)
    tool.depth.setValue(5.)
    inserted = tool.feature_id
    assert w.tool_sessions.commit(),w.tool_sessions.message
    assert doc.objects[body.id].features == (inserted,fid)
    assert doc.objects[body.id].source_shape is body.shape
    assert_solid(doc,body.id,120000.-40.+125*pi)


def test_history_position_hides_transitive_datums_sketch_and_body_and_restores(extrusion_system):
    w, commands, m = extrusion_system
    body_id,_ = two_extrusions(extrusion_system)
    doc = w.viewport.document
    body = doc.objects[body_id]
    source = face_sketch(extrusion_system,body,height=30.)
    from cadastrophe.core.reference_objects import DatumPlaneObject
    from cadastrophe.core.operation_model import GeometryReference as Ref,PlaneParameters
    from cadastrophe.core.reference_geometry import resolve_plane,plane_shape
    frame = source.sketch.support.frame
    ref = Ref("face",body_id,body.features[-1],source.sketch.support.reference)
    datum = DatumPlaneObject("Plan porté",plane_shape(frame),body.color,plane=frame,
                            parameters=PlaneParameters("offset",(ref,),distance=5.))
    frame = resolve_plane(doc,datum)
    datum = replace(datum,plane=frame,shape=plane_shape(frame))
    doc.update_construction((datum,))
    sketch = commands.create_sketch(doc,datum.id)
    commands.add_entity(doc,sketch.id,m.Circle(m.Vec2(0.,0.),5.))
    consumer = new_body(w,doc.objects[sketch.id],5.)
    doc.set_visible(sketch.id,True)
    original = dict(doc.objects)
    position(w,body.features[-1],after=False)
    unavailable = doc.history_context.display()[1]
    assert {datum.id,sketch.id,consumer.id} <= unavailable
    count = doc.history.count
    with pytest.raises(ValueError,match="future"):
        commands.create_sketch(doc,datum.id)
    assert doc.history.count == count
    for key in (datum.id,sketch.id,consumer.id):
        obj = w.viewport.document_adapter.display_object(doc.objects[key])
        assert not obj.visible
        presentation = w.viewport.presentations.get(key)
        assert presentation is None or not w.viewport.context.IsDisplayed(presentation)
    assert all(doc.objects[key] is value for key,value in original.items())
    position(w)
    for key in (datum.id,sketch.id,consumer.id):
        assert w.viewport.document_adapter.display_object(doc.objects[key]).visible
    assert_solid(doc,consumer.id,125*pi)
