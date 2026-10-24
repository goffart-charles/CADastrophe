"""Fixed native body projections, strict records, solver and version migrations."""
from dataclasses import replace
from math import pi

import pytest
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
from OCC.Core.gp import gp_Ax2, gp_Circ, gp_Pnt, gp_Dir

from cadastrophe.document import SceneObject, SelectionItem
from cadastrophe.core.persistence import save_document, load_document
from plugins.sketch.constraints import Constraint, Reference
from plugins.sketch.model import Point, Segment, Vec2, ExternalGeometry
from plugins.sketch.commands import add_entity, set_constraints, remove_constraints
from plugins.sketch.external import capture, shape_map, project, status
from plugins.sketch.solver import solve
from test_sketch_document import registry, setup_document


def prepare():
    doc, obj = setup_document(native=True)
    body = SceneObject("Référence", BRepPrimAPI_MakeBox(20, 30, 10).Shape(), (.5, .5, .5))
    doc.add(body)
    vertex = shape_map(body.shape, "Vertex").FindKey(1)
    external = capture(doc, obj.sketch, SelectionItem(body.id, "Vertex", vertex, body.revision))
    source = Point(Vec2(-10, -10))
    add_entity(doc, obj.id, source)
    ref = Constraint("coincident", (Reference(source.id, "position"), Reference(external.geometry.id, "position")), label_position=Vec2(12, 14))
    return doc, obj, body, external, ref


def test_fixed_projected_vertex_drives_sketch_without_moving_body_and_roundtrips(registry, tmp_path):
    doc, obj, body, external, ref = prepare()
    doc.history.clear()
    set_constraints(doc, obj.id, (ref,), external=(external,))
    sketch = doc.objects[obj.id].sketch
    assert sketch.entities[0].position.u == pytest.approx(external.geometry.position.u, abs=1e-8)
    assert sketch.entities[0].position.v == pytest.approx(external.geometry.position.v, abs=1e-8)
    assert doc.objects[body.id] is body
    assert solve(sketch).dof == 0 and doc.history.count == 1
    path = tmp_path / "external-label-position.cada"
    save_document(doc, path, registry=registry)
    loaded = load_document(path, registry=registry)
    assert loaded.objects[obj.id].sketch == sketch
    assert status(loaded.objects[obj.id].sketch, loaded.objects) == ""
    doc.history.undo()
    assert not doc.objects[obj.id].sketch.external and not doc.objects[obj.id].sketch.constraints
    doc.history.redo()
    assert doc.objects[obj.id].sketch == sketch


def test_changed_or_missing_body_is_explicit_and_removing_relation_repairs_reference():
    doc, obj, body, external, ref = prepare()
    set_constraints(doc, obj.id, (ref,), external=(external,))
    doc.replace_shape(body.id, BRepPrimAPI_MakeCylinder(5., 8.).Shape())
    before = doc.objects[obj.id]
    count = doc.history.count
    assert "modifié" in status(before.sketch, doc.objects)
    with pytest.raises(ValueError, match="non résolue|perdue"):
        set_constraints(doc, obj.id, (ref,))
    assert doc.objects[obj.id] is before and doc.history.count == count
    doc.remove(body.id)
    assert "absent" in status(before.sketch, doc.objects)
    remove_constraints(doc, obj.id, (ref.id,))
    assert not doc.objects[obj.id].sketch.external and not doc.objects[obj.id].sketch.constraints
    doc.history.undo()
    assert doc.objects[obj.id].sketch == before.sketch


def test_native_edge_projection_and_capture_are_reused_at_same_revision():
    doc, obj, body, external, ref = prepare()
    edges = shape_map(body.shape, "Edge")
    chosen = None
    for index in range(1, edges.Size()+1):
        try:
            value = capture(doc, obj.sketch, SelectionItem(body.id, "Edge", edges.FindKey(index), body.revision))
        except ValueError:
            continue
        if type(value.geometry) is Segment:
            chosen = value
            break
    assert chosen is not None
    sketch = replace(obj.sketch, external=(chosen,))
    same = capture(doc, sketch, SelectionItem(body.id, "Edge", edges.FindKey(chosen.index), body.revision))
    assert same is chosen


def test_circular_edge_parallel_to_support_is_exact_and_oblique_projection_is_refused():
    doc, obj = setup_document(native=True)
    frame = obj.sketch.support.frame
    circle = gp_Circ(gp_Ax2(gp_Pnt(5, 7, 20), gp_Dir(0, 0, 1)), 12)
    result = project(BRepBuilderAPI_MakeEdge(circle).Edge(), "Edge", frame)
    assert result.center == Vec2(5, 7) and result.radius == 12
    arc = project(BRepBuilderAPI_MakeEdge(circle, 0, pi/2).Edge(), "Edge", frame)
    assert arc.radius == 12 and arc.sweep == pytest.approx(pi/2)
    circle = gp_Circ(gp_Ax2(gp_Pnt(5, 7, 20), gp_Dir(1, 0, 1)), 12)
    with pytest.raises(ValueError, match="ellipse"):
        project(BRepBuilderAPI_MakeEdge(circle).Edge(), "Edge", frame)


@pytest.mark.parametrize("kind,value", [("horizontal_distance", 15), ("vertical_distance", 25), ("distance", 30)])
def test_dimension_between_projected_fixed_point_and_internal_point(kind, value):
    doc, obj, body, external, ref = prepare()
    original = doc.objects[obj.id].sketch
    constraint = Constraint(kind, ref.refs, value)
    result = solve(replace(original, external=(external,), constraints=(constraint,)))
    assert result.success and result.dof == 1
    assert result.sketch.external == (external,)


def test_legacy_v3_codec_is_rejected(registry):
    doc, obj, body, external, ref = prepare()
    # A real v3 record has constraints but no placement or external records.
    circle_ref = Constraint("fixed", (Reference(doc.objects[obj.id].sketch.entities[0].id, "position"),), Vec2(3, 4))
    set_constraints(doc, obj.id, (circle_ref,))
    original = doc.objects[obj.id]
    extension, _ = registry.encode(original)
    extension["version"] = 3
    del extension["data"]["patterns"]
    del extension["data"]["support"]["producer_id"]
    del extension["data"]["support"]["reference"]
    del extension["data"]["support"]["attachment"]
    del extension["data"]["external"]
    for c in extension["data"]["constraints"]:
        del c["label_position"]
        del c["driven"]
    base = SceneObject(original.name, None, original.color, id=original.id, kind=original.kind, revision=original.revision)
    with pytest.raises(ValueError, match="Version du type"):
        registry.decode(base, extension)


@pytest.mark.parametrize("damage", ["body_uuid", "revision", "topology", "index", "geometry", "duplicate"])
def test_external_model_rejects_invalid_records(damage):
    doc, obj, body, ext, ref = prepare()
    with pytest.raises(ValueError):
        if damage == "body_uuid": replace(ext, body_id="invalid")
        elif damage == "revision": replace(ext, revision=True)
        elif damage == "topology": replace(ext, topology="Face")
        elif damage == "index": replace(ext, index=0)
        elif damage == "geometry": replace(ext, geometry=Segment(Vec2(0, 0), Vec2(1, 0)))
        else: replace(obj.sketch, entities=(ext.geometry,), external=(ext,))
