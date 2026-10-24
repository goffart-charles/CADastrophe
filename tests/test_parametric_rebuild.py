"""Native A–E reconstruction scenarios through the existing document and UI."""
from dataclasses import replace
from importlib import import_module
from math import pi

import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from PySide6.QtTest import QTest

from cadastrophe.core.body_history import body_result, result_at
from cadastrophe.core.feature_model import BodyStateRef, ResultStatus
from cadastrophe.core.persistence import save_document
from test_extrusion_tool import extrusion_system, start
from test_boss_tool import base_body, face_sketch
from test_cut_tool import start_cut
from test_feature_edit import edit
from test_primitives import volume
from test_advanced_cad_tools import collect_native_tool_sessions


def module(w, name):
    return import_module(w.plugins._record("cadastrophe.sketch").namespace+"."+name)


def resolved(w, key):
    return module(w, "construction_context").resolve_object(w.viewport.document.objects[key], w.viewport.document)


def bounds(shape):
    box = Bnd_Box()
    box.SetGap(0.)
    brepbndlib.AddOptimal(shape, box, False, False)
    return box.Get()


def two_extrusions(system, *, merge=True):
    w, _, _ = system
    body = base_body(system)
    source = face_sketch(system, body)
    tool = start(w, source)
    tool.merge.setChecked(merge)
    tool.depth.setValue(10.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    return body.id, source.id


def resize_base(system, body_id, width, height=60.):
    w, commands, m = system
    doc = w.viewport.document
    base = doc.objects[doc.objects[body_id].features[0]]
    sketch = doc.objects[base.sketch_id]
    points = tuple(m.Vec2(*p) for p in ((0, 0), (width, 0), (width, height), (0, height)))
    commands.replace_entities(doc, sketch.id, tuple(replace(e, start=p, end=points[(i+1)%4])
                                                  for i, (e, p) in enumerate(zip(sketch.sketch.entities, points))))
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()


def edge_operation(w, body_id, operation, x, y, size=1., z=None):
    from cadastrophe.core.native_naming import subshapes
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.TopoDS import topods
    shape = w.viewport.document.objects[body_id].shape
    selected = []
    for e in subshapes(shape, "Edge"):
        c = BRepAdaptor_Curve(topods.Edge(e))
        p = c.Value((c.FirstParameter()+c.LastParameter())/2)
        if abs(p.X()-x) < 1e-6 and abs(p.Y()-y) < 1e-6 and (z is None or abs(p.Z()-z) < 1e-6):
            selected.append(e)
    assert len(selected) == 1
    assert w.command_registry.execute("cadastrophe.modeling."+operation)
    tool = w.tool_sessions.tool
    tool.add_edge(body_id, selected[0])
    tool.size.setValue(size)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    return w.viewport.document.objects[body_id].features[-1]


@pytest.mark.parametrize("merge", [True, False])
def test_a_face_sketch_follows_height_and_roundtrip(extrusion_system, tmp_path, merge):
    w, _, _ = extrusion_system
    body_id, sketch_id = two_extrusions(extrusion_system, merge=merge)
    doc = w.viewport.document
    ids, recipes = set(doc.objects), {k: obj for k, obj in doc.objects.items()}
    first = doc.objects[body_id].features[0]
    before = result_at(doc, BodyStateRef(body_id, first))
    assert before.topology is not None
    assert doc.objects[sketch_id].sketch.support.reference.labels
    doc.set_visible(sketch_id, True)
    tool = edit(w, first, double_click=True)
    tool.depth.setValue(40.)
    w.tool_sessions.update_preview()
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(240000.)
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert sketch_id in w.tool_host.overlay.hidden_features
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert set(doc.objects) == ids
    assert resolved(w, sketch_id).sketch.support.frame.origin.z == pytest.approx(40.)
    assert doc.objects[sketch_id].sketch.entities == recipes[sketch_id].sketch.entities
    assert doc.objects[sketch_id].sketch.constraints == recipes[sketch_id].sketch.constraints
    if merge:
        assert volume(doc.objects[body_id].shape) == pytest.approx(240000.+250*pi)
        assert bounds(doc.objects[body_id].shape)[5] == pytest.approx(50., abs=1e-6)
    else:
        other = next(o for o in doc.objects.values() if getattr(o, "features", ()) and o.id != body_id)
        assert bounds(other.shape)[2] == pytest.approx(40., abs=1e-6)
        assert bounds(other.shape)[5] == pytest.approx(50., abs=1e-6)
    path = tmp_path / "regenerated.cada"
    save_document(doc, path, registry=w.object_types)
    assert w.close_document()
    reopened = w.open_document(path)
    QTest.qWait(100)
    assert reopened is not None and set(reopened.objects) == ids
    assert resolved(w, sketch_id).sketch.support.frame.origin.z == pytest.approx(40.)
    tool = edit(w, first)
    tool.depth.setValue(50.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert resolved(w, sketch_id).sketch.support.frame.origin.z == pytest.approx(50.)
    reopened.history.undo()
    assert resolved(w, sketch_id).sketch.support.frame.origin.z == pytest.approx(40.)
    reopened.history.redo()
    assert resolved(w, sketch_id).sketch.support.frame.origin.z == pytest.approx(50.)


def test_b_initial_sketch_resize_preserves_ids_and_updates_visible_sketch(extrusion_system):
    w, _, _ = extrusion_system
    body_id, sketch_id = two_extrusions(extrusion_system)
    doc = w.viewport.document
    ids, order = set(doc.objects), doc.objects[body_id].features
    doc.set_visible(sketch_id, True)
    resize_base(extrusion_system, body_id, 120., 80.)
    assert body_result(doc, body_id).status is ResultStatus.READY
    assert set(doc.objects) == ids and doc.objects[body_id].features == order
    assert volume(doc.objects[body_id].shape) == pytest.approx(192000.+250*pi)
    assert not doc.objects[sketch_id].reference_error(doc)
    renderer = w.viewport.document_adapter.features[sketch_id][1]
    assert renderer._frame == resolved(w, sketch_id).sketch.support.frame
    assert renderer._frame.origin.z == pytest.approx(20.)


def test_named_external_vertex_reprojects_and_drives_the_next_extrusion(extrusion_system, tmp_path):
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.TopoDS import topods
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.GeomAbs import GeomAbs_Cylinder
    from cadastrophe.core.native_naming import subshapes
    from cadastrophe.document import SelectionItem
    w, commands, _ = extrusion_system
    body = base_body(extrusion_system)
    obj = face_sketch(extrusion_system, body, center=(80, 40))
    doc = w.viewport.document
    vertex = next(v for v in subshapes(body.shape, "Vertex")
                  if max(abs(a-b) for a, b in zip(BRep_Tool.Pnt(topods.Vertex(v)).Coord(), (100., 60., 20.))) < 1e-6)
    ext = module(w, "external").capture(doc, obj.sketch, SelectionItem(body.id, "Vertex", vertex, body.revision))
    c = module(w, "constraints")
    center, source = c.Reference(obj.sketch.entities[0].id, "center"), c.Reference(ext.geometry.id, "position")
    constraints = (c.Constraint("horizontal_distance", (center, source), 20.),
                   c.Constraint("vertical_distance", (center, source), 20.),
                   c.Constraint("radius", (c.Reference(obj.sketch.entities[0].id),), 5.))
    commands.set_constraints(doc, obj.id, constraints, external=(ext,))
    tool = start(w, doc.objects[obj.id])
    tool.depth.setValue(10.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    ids = set(doc.objects)
    resize_base(extrusion_system, body.id, 120.)
    assert body_result(doc, body.id).status is ResultStatus.READY
    sketch = resolved(w, obj.id).sketch
    assert sketch.entities[0].center.u == pytest.approx(100.)
    assert sketch.entities[0].center.v == pytest.approx(40.)
    surfaces = [BRepAdaptor_Surface(topods.Face(f)) for f in subshapes(doc.objects[body.id].shape, "Face")]
    axis = next(s.Cylinder().Axis().Location() for s in surfaces if s.GetType() == GeomAbs_Cylinder)
    assert (axis.X(), axis.Y()) == pytest.approx((100., 40.))
    path = tmp_path / "external-rebuilt.cada"
    save_document(doc, path, registry=w.object_types)
    assert w.close_document() and w.open_document(path) is not None
    QTest.qWait(100)
    assert set(w.viewport.document.objects) == ids
    assert resolved(w, obj.id).sketch.entities[0].center.u == pytest.approx(100.)
    assert body_result(w.viewport.document, body.id).status is ResultStatus.READY


@pytest.mark.parametrize("edit_initial", [True, False])
def test_sketch_edit_displays_its_history_prefix_until_exit(extrusion_system, edit_initial):
    w, commands, m = extrusion_system
    body = base_body(extrusion_system)
    doc, vp = w.viewport.document, w.viewport
    first = doc.objects[body.features[0]]
    cut_sketch = face_sketch(extrusion_system, body)
    start_cut(w, cut_sketch).depth.setValue(5.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    key = first.sketch_id if edit_initial else cut_sketch.id
    doc.set_visible(key, True)
    original, ids = doc.objects[body.id].shape, set(doc.objects)
    count = doc.history.count
    w.tree_selection_changed([key])
    assert w.command_registry.execute("cadastrophe.sketch.edit")
    assert doc.objects[body.id].shape is original and set(doc.objects) == ids and doc.history.count == count
    if edit_initial:
        assert body.id not in vp.presentations
        assert not vp.selection._occluders()
        assert cut_sketch.id in vp.document_adapter._history_hidden
        resize_base(extrusion_system, body.id, 120.)
        assert body.id not in vp.presentations  # Rebuild notifications preserve rollback display.
    else:
        assert volume(vp.presentations[body.id].Shape()) == pytest.approx(120000.)
        assert volume(vp.selection._occluders()[0].shape) == pytest.approx(120000.)
        # A historical edge remains selectable against its actual producer.
        state = result_at(doc, BodyStateRef(body.id, first.id))
        from cadastrophe.core.native_naming import subshapes
        from cadastrophe.document import SelectionItem
        from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
        from OCC.Core.TopoDS import topods
        def top_edge(edge):
            curve = BRepAdaptor_Curve(topods.Edge(edge))
            return abs(curve.Value((curve.FirstParameter()+curve.LastParameter())/2).Z()-20.) < 1e-6
        edge = next(e for e in subshapes(state.shape, "Edge") if top_edge(e))
        external = module(w, "external").capture(doc, doc.objects[key].sketch,
                    SelectionItem(body.id, "Edge", edge, doc.objects[body.id].revision))
        assert external.producer_id == first.id and external.reference.labels
        circle = doc.objects[key].sketch.entities[0]
        commands.replace_entity(doc, key, replace(circle, radius=6.))
        w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
        assert volume(vp.presentations[body.id].Shape()) == pytest.approx(120000.)
    assert w.command_registry.execute("cadastrophe.sketch.finish")
    assert not vp.document_adapter._history_states and not vp.document_adapter._history_hidden
    assert vp.presentations[body.id].Shape().IsSame(doc.objects[body.id].shape)
    assert volume(doc.objects[body.id].shape) == pytest.approx(144000.-125*pi if edit_initial else 120000.-180*pi)
    assert set(doc.objects) == ids


def test_c_fillet_uses_named_edge_after_initial_dimensions_change(extrusion_system):
    w, _, _ = extrusion_system
    body = base_body(extrusion_system)
    feature_id = edge_operation(w, body.id, "fillet", 100., 0., 2.)
    feature = w.viewport.document.objects[feature_id]
    assert feature.parameters.references[0].labels
    resize_base(extrusion_system, body.id, 120.)
    doc = w.viewport.document
    assert result_at(doc, BodyStateRef(body.id, feature_id)).status is ResultStatus.READY
    assert doc.objects[feature_id] is feature
    assert volume(doc.objects[body.id].shape) == pytest.approx(144000.-20*(4-pi))


def test_d_entire_chain_replays_boss_cut_fillet_chamfer(extrusion_system):
    w, _, _ = extrusion_system
    body_id, _ = two_extrusions(extrusion_system)
    doc = w.viewport.document
    source = face_sketch(extrusion_system, doc.objects[body_id], center=(80, 30))
    start_cut(w, source).depth.setValue(5.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    fillet = edge_operation(w, body_id, "fillet", 0., 0., 2.)
    chamfer = edge_operation(w, body_id, "chamfer", 100., 60., 1.)
    first, order, ids = doc.objects[body_id].features[0], doc.objects[body_id].features, set(doc.objects)
    old_volume = volume(doc.objects[body_id].shape)
    tool = edit(w, first)
    tool.depth.setValue(40.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert body_result(doc, body_id).status is ResultStatus.READY
    assert set(doc.objects) == ids and doc.objects[body_id].features == order
    assert result_at(doc, BodyStateRef(body_id, fillet)).status is ResultStatus.READY
    assert result_at(doc, BodyStateRef(body_id, chamfer)).status is ResultStatus.READY
    assert volume(doc.objects[body_id].shape)-old_volume == pytest.approx(20*(6000.-4+pi-.5))
    assert BRepCheck_Analyzer(doc.objects[body_id].shape).IsValid()


def test_e_failure_marks_responsible_and_blocks_followers_then_recovers(extrusion_system):
    w, _, _ = extrusion_system
    body_id, _ = two_extrusions(extrusion_system)
    doc = w.viewport.document
    fillet = edge_operation(w, body_id, "fillet", 0., 0., 2.)
    first, boss, _ = doc.objects[body_id].features
    ids = set(doc.objects)
    resize_base(extrusion_system, body_id, 40.)  # Circle at x=50 no longer touches the base.
    assert result_at(doc, BodyStateRef(body_id, first)).status is ResultStatus.READY
    assert result_at(doc, BodyStateRef(body_id, boss)).status is ResultStatus.ERROR
    assert result_at(doc, BodyStateRef(body_id, fillet)).status is ResultStatus.BLOCKED
    assert doc.objects[body_id].shape is None and body_result(doc, body_id).shape is None
    assert "Erreur" in w.tree_items[boss].toolTip(0)
    assert "bloquée" in w.tree_items[fillet].toolTip(0)
    assert set(doc.objects) == ids
    resize_base(extrusion_system, body_id, 100.)
    assert body_result(doc, body_id).status is ResultStatus.READY
    assert set(doc.objects) == ids
    assert all(result_at(doc, BodyStateRef(body_id, key)).status is ResultStatus.READY for key in doc.objects[body_id].features)


def test_edit_failure_is_published_and_repair_undo_redo_survive_reopen(extrusion_system, tmp_path):
    w, _, _ = extrusion_system
    body = base_body(extrusion_system)
    fillet = edge_operation(w, body.id, "fillet", 50., 0., 6., z=20.)
    doc = w.viewport.document
    first = doc.objects[body.id].features[0]
    ids = set(doc.objects)
    tool = edit(w, first)
    tool.depth.setValue(1.)
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert tool.rebuild_warning
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert doc.objects[first].parameters.depth == 1.
    assert doc.objects[body.id].shape is None
    assert result_at(doc, BodyStateRef(body.id, fillet)).status is ResultStatus.ERROR
    path = tmp_path / "failed-history.cada"
    save_document(doc, path, registry=w.object_types)
    assert w.close_document()
    assert w.open_document(path) is not None
    QTest.qWait(100)
    doc = w.viewport.document
    assert set(doc.objects) == ids and doc.objects[body.id].shape is None
    tool = edit(w, fillet)
    assert w.tool_sessions.preview_object is not None and not w.tool_sessions.can_commit
    tool.size.setValue(.2)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert body_result(doc, body.id).status is ResultStatus.READY
    doc.history.undo()
    assert body_result(doc, body.id).status is ResultStatus.ERROR and doc.objects[body.id].shape is None
    doc.history.redo()
    assert body_result(doc, body.id).status is ResultStatus.READY


def test_lost_named_edge_can_be_reselected_in_the_view(extrusion_system):
    from cadastrophe.core.topology_refs import TopologyReference
    from cadastrophe.presentation import PresentationSurface
    from cadastrophe.geometry import Vec3, REFERENCE_PLANES
    from PySide6.QtCore import QPoint, Qt
    from test_extrusion_interaction import orient
    w, _, _ = extrusion_system
    body = base_body(extrusion_system)
    feature_id = edge_operation(w, body.id, "fillet", 0., 0., 2.)
    doc = w.viewport.document
    feature = doc.objects[feature_id]
    old = feature.parameters.references[0]
    broken = TopologyReference("Edge", ("deleted-producer-face",), old.geometry)
    doc.replace_feature(replace(feature, parameters=replace(feature.parameters, references=(broken,))))
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert body_result(doc, body.id).status is ResultStatus.ERROR
    tool = edit(w, feature_id)
    assert "perdue" in w.tool_sessions.message and not w.tool_sessions.can_commit
    QTest.mouseClick(tool.clear_button, Qt.MouseButton.LeftButton)
    w.tool_sessions.update_preview()
    orient(w, REFERENCE_PLANES[0])
    x, y = PresentationSurface(w.viewport).project(Vec3(50., 0., 20.))
    QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=QPoint(round(x), round(y)))
    assert len(tool.references) == 1, w.tool_sessions.message
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert body_result(doc, body.id).status is ResultStatus.READY
    assert doc.objects[body.id].features[-1] == feature_id


def test_edit_reuses_unchanged_prefix_and_ignores_saved_face_index(extrusion_system, monkeypatch):
    w, _, _ = extrusion_system
    body_id, sketch_id = two_extrusions(extrusion_system)
    doc = w.viewport.document
    first, second = doc.objects[body_id].features
    original = result_at(doc, BodyStateRef(body_id, first)).shape
    sketch = doc.objects[sketch_id]
    doc.replace_feature(replace(sketch, sketch=replace(sketch.sketch, support=replace(sketch.sketch.support, face_index=999))))
    w.plugins._record("cadastrophe.modeling").instance.rebuilder.flush()
    assert body_result(doc, body_id).status is ResultStatus.READY
    assert resolved(w, sketch_id).sketch.support.face_index != 999
    service = w.services.service("cadastrophe.modeling.extrusions")
    cls = type(service.evaluator(doc))
    native, calls = cls.evaluate, []
    def traced(self, feature, *args):
        calls.append(feature.id)
        return native(self, feature, *args)
    monkeypatch.setattr(cls, "evaluate", traced)
    tool = edit(w, second)
    tool.depth.setValue(12.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert calls and set(calls) == {second}
    assert result_at(doc, BodyStateRef(body_id, first)).shape is original
    assert volume(doc.objects[body_id].shape) == pytest.approx(120000.+300*pi)


def test_missing_or_split_name_is_never_resolved_by_nearest_geometry():
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from cadastrophe.core.native_naming import NamedTopology, capture, resolve, subshapes
    from cadastrophe.core.topology_refs import TopologyReference
    shape = BRepPrimAPI_MakeBox(10., 20., 30.).Shape()
    faces = subshapes(shape, "Face")
    topology = NamedTopology((("original-face", faces[0]),))
    reference = capture(shape, faces[0], "Face", topology)
    assert resolve(shape, reference, NamedTopology(tuple(reversed(topology.faces)))).IsSame(faces[0])
    with pytest.raises(ValueError, match="perdue"):
        resolve(shape, TopologyReference("Face", ("missing-face",), reference.geometry), topology)
    with pytest.raises(ValueError, match="ambiguë"):
        resolve(shape, reference, NamedTopology((("original-face", faces[0]), ("original-face", faces[1]))))


@pytest.mark.parametrize("legacy_edge", [False, True])
def test_old_reference_codecs_are_rejected_without_rewriting(extrusion_system, tmp_path, legacy_edge):
    import json
    from zipfile import ZipFile, ZIP_DEFLATED
    w, _, _ = extrusion_system
    if legacy_edge:
        body_id, sketch_id = base_body(extrusion_system).id, None
        fillet_id = edge_operation(w, body_id, "fillet", 0., 0., 2.)
    else:
        body_id, sketch_id = two_extrusions(extrusion_system)
    doc = w.viewport.document
    first = doc.objects[body_id].features[0]
    path = tmp_path / "v4-face-support.cada"
    save_document(doc, path, registry=w.object_types)
    with ZipFile(path) as archive:
        data = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(data["manifest.json"])
    for record in manifest["objects"]:
        extension = record.get("extension")
        if extension and extension["type_id"] == "cadastrophe.sketch.sketch":
            extension["version"] = 4
            del extension["data"]["patterns"]
            del extension["data"]["support"]["producer_id"]
            del extension["data"]["support"]["reference"]
            del extension["data"]["support"]["attachment"]
        if extension and extension["type_id"] == "cadastrophe.modeling.edges":
            extension["version"] = 1
            del extension["data"]["enabled"]
            del extension["data"]["parameters"]["references"]
    data["manifest.json"] = json.dumps(manifest).encode("utf8")
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, payload in data.items():
            archive.writestr(name, payload)
    assert w.close_document()
    from cadastrophe.core.persistence import DocumentFileError, load_document
    frozen = path.read_bytes()
    with pytest.raises(DocumentFileError, match="Version du type"):
        load_document(path, registry=w.object_types)
    assert path.read_bytes() == frozen
