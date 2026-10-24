"""X2 read-only service on real face supports and isolated Sketch/X1 plugins."""
from dataclasses import replace
from importlib import import_module
from math import pi
from pathlib import Path
import shutil

import pytest

from cadastrophe.core.body_history import body_result
from cadastrophe.core.feature_model import Operation, ResultStatus
from cadastrophe.core.persistence import load_document, save_document
from cadastrophe.core.presentations import PresentationRegistry
from cadastrophe.scene import new_document
from plugins.sketch.commands import create_sketch, add_entities
from plugins.sketch.profile_service import SketchProfiles
from plugins.sketch.supports import face_map, from_selection
from test_sketch_faces import body_document, selection
from test_sketch_profiles import rectangle
from plugins.sketch.model import Circle, Vec2
from test_plugins import harness, plugin_root
from test_modeling_history import construction


@pytest.mark.parametrize("angle", [0, pi/3])
@pytest.mark.parametrize("index", range(1, 7))
def test_faces_service_on_each_located_oriented_box_support_is_read_only(angle, index):
    doc, body = body_document(angle)
    support = from_selection(doc, (selection(body, face_map(body.shape).FindKey(index)),))
    obj = create_sketch(doc, support)
    add_entities(doc, obj.id, (*rectangle(), Circle(Vec2(50, 30), 10)), title="Profil percé")
    service = SketchProfiles()
    before = dict(doc.objects), doc.history.count, doc.history.index, body.shape
    report = service.analyze(doc, obj.id)
    faces = service.to_faces(doc, obj.id, analysis=report)
    assert faces.area == pytest.approx(6000-100*pi) and len(faces.regions[0].hole_ids) == 1
    assert (dict(doc.objects), doc.history.count, doc.history.index, doc.objects[body.id].shape) == before
    doc.replace_shape(body.id, body.shape)
    # A revision change with identical geometry does not lose a persistent face.
    assert service.to_faces(doc, obj.id, analysis=report).area == pytest.approx(faces.area)


def test_public_service_with_x1_recipe_roundtrip_history_staleness_and_plugin_lifetime(harness, tmp_path):
    h = harness
    h.manager.host.presentations = PresentationRegistry()
    root = Path(__file__).resolve().parents[1] / "plugins"
    for name in ("sketch", "modeling"):
        shutil.copytree(root / name, h.root / name, ignore=shutil.ignore_patterns("__pycache__"))
    h.manager.startup()
    records = {record.manifest.id: record for record in h.manager.records}
    assert all(record.state == "active" for record in records.values())
    package = records["cadastrophe.sketch"].namespace
    commands, model = (import_module(package + "." + name) for name in ("commands", "model"))
    doc = h.documents.add(new_document())
    obj = commands.create_sketch(doc, next(iter(doc.objects)))
    points = tuple(model.Vec2(*point) for point in ((0, 0), (100, 0), (100, 60), (0, 60)))
    curves = tuple(model.Segment(point, points[(i+1)%4]) for i, point in enumerate(points))
    commands.add_entities(doc, obj.id, (*curves, model.Circle(model.Vec2(50, 30), 10)), title="Profil percé")
    body, _ = construction(doc, doc.objects[obj.id], (Operation.NEW_BODY,))
    service = h.services.service("cadastrophe.sketch.profiles")
    report = service.analyze(doc, obj.id)
    before = dict(doc.objects), doc.history.count, doc.history.index
    faces = service.to_faces(doc, obj.id, analysis=report, contour_ids=(min(e.id for e in curves),))
    assert faces.area == pytest.approx(6000-100*pi)
    assert before == (dict(doc.objects), doc.history.count, doc.history.index)
    assert body_result(doc, body.id).status is ResultStatus.DIRTY and doc.objects[body.id].shape is None
    commands.add_entity(doc, obj.id, model.Point(model.Vec2(5, 5)))
    with pytest.raises(ValueError, match="périmée"):
        service.to_faces(doc, obj.id, analysis=report)
    doc.history.undo()
    assert service.to_faces(doc, obj.id, analysis=report).area == pytest.approx(faces.area)
    doc.history.redo()
    path = tmp_path / "x2.cada"
    save_document(doc, path, registry=h.types)
    h.documents.remove(doc.id)
    loaded = h.documents.add(load_document(path, registry=h.types))
    assert service.to_faces(loaded, obj.id).area == pytest.approx(faces.area)
    assert not loaded.is_modified and loaded.history.count == 0
    other = h.documents.add(new_document())
    with pytest.raises(ValueError, match="ce document"):
        service.to_faces(other, obj.id)
    for document in (loaded, other):
        h.documents.remove(document.id)
    assert h.manager.deactivate("cadastrophe.modeling")
    assert h.manager.deactivate("cadastrophe.sketch")
    with pytest.raises(LookupError):
        h.services.service("cadastrophe.sketch.profiles")
    with pytest.raises(RuntimeError, match="fermé"):
        service.to_faces(doc, obj.id)
