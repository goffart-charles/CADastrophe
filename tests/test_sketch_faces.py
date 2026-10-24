"""Real planar B-Rep supports, conservative references and v1 compatibility."""
from dataclasses import replace
from math import pi
from uuid import uuid4

import pytest
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCC.Core.TopoDS import topods
from OCC.Core.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

from cadastrophe.core.object_types import ObjectTypeRegistry
from cadastrophe.core.persistence import load_document, save_document
from cadastrophe.document import Document, SceneObject, SelectionItem
from cadastrophe.geometry import Vec3
from cadastrophe.scene import new_document
from plugins.sketch.codec import CODEC
from plugins.sketch.commands import create_sketch, add_entity
from plugins.sketch.model import Point, Vec2
from plugins.sketch.objects import support_status
from plugins.sketch.supports import face_map, face_frame, from_selection


def body_document(angle=0):
    transform = gp_Trsf()
    transform.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(1, 2, 3)), angle)
    transform.SetTranslationPart(gp_Vec(120, -50, 80))
    shape = BRepBuilderAPI_Transform(BRepPrimAPI_MakeBox(30, 40, 50).Shape(), transform, True).Shape()
    doc = Document()
    body = SceneObject("Boîte inclinée", shape, (.3, .4, .6))
    doc.add(body)
    return doc, body


def selection(body, face):
    return SelectionItem(body.id, "Face", face, body.revision)


def registry():
    result = ObjectTypeRegistry()
    result.register("cadastrophe.sketch", CODEC)
    return result


@pytest.mark.parametrize("angle", [0, pi/3, -pi/4])
@pytest.mark.parametrize("index", range(1, 7))
def test_each_transformed_box_face_uses_located_oriented_native_frame_and_roundtrips(tmp_path, angle, index):
    doc, body = body_document(angle)
    face = face_map(body.shape).FindKey(index)
    support = from_selection(doc, (selection(body, face),))
    assert support.plane_id == body.id and support.face_index == index
    frame = support.frame
    assert frame.normal.norm() == pytest.approx(1)
    surface = BRepAdaptor_Surface(topods.Face(face), True)
    point = Vec3(*surface.Value((surface.FirstUParameter()+surface.LastUParameter())/2,
                              (surface.FirstVParameter()+surface.LastVParameter())/2).Coord())
    assert frame.to_local(point)[2] == pytest.approx(0, abs=1e-7)
    assert face_frame(face.Reversed(), "Inverse").normal.dot(frame.normal) == pytest.approx(-1)
    obj = create_sketch(doc, support)
    add_entity(doc, obj.id, Point(Vec2(7, 9)))
    obj = doc.objects[obj.id]
    doc.history.undo()
    assert not doc.objects[obj.id].sketch.entities
    doc.history.redo()
    path = tmp_path / "face.cada"
    save_document(doc, path, registry=registry())
    loaded = load_document(path, registry=registry())
    assert loaded.objects[obj.id].sketch == obj.sketch
    assert not support_status(loaded.objects[obj.id], loaded.objects)
    local = loaded.objects[obj.id].sketch.to_local(frame.to_world(7, 9))
    assert (local.u, local.v) == pytest.approx((7, 9), abs=1e-7)


def test_face_support_rejects_curved_foreign_stale_and_whole_object_selections():
    doc, body = body_document()
    faces = face_map(body.shape)
    face = faces.FindKey(1)
    other = BRepPrimAPI_MakeBox(12, 13, 14).Shape()
    for items in ((), (selection(body, face), selection(body, face)),
                  (replace(selection(body, face), topology="Objet", shape=body.shape),),
                  (replace(selection(body, face), revision=body.revision+1),),
                  (selection(body, face_map(other).FindKey(1)),)):
        with pytest.raises(ValueError):
            from_selection(doc, items)
    curved_body = SceneObject("Cylindre", BRepPrimAPI_MakeCylinder(10, 20).Shape(), (.4, .5, .6))
    # Use the cylinder's own face; a foreign shape must not mask the curvature check.
    doc.add(curved_body)
    with pytest.raises(ValueError, match="face plane"):
        from_selection(doc, (selection(curved_body, face_map(curved_body.shape).FindKey(1)),))


def test_changed_deleted_and_invalid_face_support_is_unresolved_without_reattaching(tmp_path):
    doc, body = body_document()
    support = from_selection(doc, (selection(body, face_map(body.shape).FindKey(2)),))
    obj = create_sketch(doc, support)
    doc.rename(body.id, "Renommé")
    assert not support_status(obj, doc.objects)
    bad = replace(obj, sketch=replace(obj.sketch, support=replace(support, face_index=999)))
    assert "face absente" in support_status(bad, doc.objects)
    doc.replace_shape(body.id, BRepPrimAPI_MakeCylinder(5., 8.).Shape())
    assert "corps modifié" in support_status(obj, doc.objects)
    with pytest.raises(ValueError, match="non résolu|perdue"):
        add_entity(doc, obj.id, Point(Vec2(1, 2)))
    path = tmp_path / "unresolved.cada"
    save_document(doc, path, registry=registry())
    loaded = load_document(path, registry=registry())
    assert "corps modifié" in support_status(loaded.objects[obj.id], loaded.objects)
    doc.remove(body.id)
    assert "corps absent" in support_status(obj, doc.objects)


def test_codec_refuses_old_and_future_versions(tmp_path):
    doc = new_document()
    obj = create_sketch(doc, next(iter(doc.objects)))
    types = registry()
    extension, _ = types.encode(obj)
    for version in (1, CODEC.version-1, CODEC.version+1):
        extension["version"] = version
        with pytest.raises(ValueError, match="Version du type"):
            types.decode(obj, extension)


@pytest.mark.parametrize("index", [True, 0, -1, 1.5, "1"])
def test_face_index_is_strictly_positive_integer(index):
    from plugins.sketch.model import PlaneSupport
    from cadastrophe.geometry import REFERENCE_PLANES
    with pytest.raises(ValueError):
        PlaneSupport(str(uuid4()), REFERENCE_PLANES[0], face_index=index)
