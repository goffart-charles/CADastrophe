"""Construction metadata, constraints, persistence and profile exclusion."""
from dataclasses import replace
import json
from math import pi
from uuid import uuid4

import pytest

from plugins.sketch.model import Segment, Circle, Ellipse, Vec2
from plugins.sketch.drawing import DrawingController
from plugins.sketch.entity_codec import _encode_entity, _decode_entity
from plugins.sketch.profiles import analyze
from plugins.sketch.profile_regions import analyze_regions
from plugins.sketch.native_regions import arrangement
from plugins.sketch.profile_faces import to_faces
from plugins.sketch.constraints import Constraint, Reference as Ref
from plugins.sketch.solver import solve
from plugins.sketch.clipboard import encode_entities, decode_entities
from test_sketch_profiles import source, rectangle, polygon


def test_construction_line_uses_chained_line_drawing_and_preserves_draft_ids():
    controller = DrawingController()
    controller.select("construction_line")
    assert controller.click(Vec2(0, 0)) == ()
    preview, = controller.candidate(Vec2(10, 0))
    committed, = controller.click(Vec2(10, 0))
    assert preview == committed and committed.construction
    controller.committed(Vec2(10, 0))
    second, = controller.candidate(Vec2(20, 0))
    assert second.start == committed.end and second.id != committed.id and second.construction
    controller.select("line")
    controller.click(Vec2(0, 0))
    assert not controller.candidate(Vec2(10, 0))[0].construction


@pytest.mark.parametrize("all_construction", [False, True])
def test_construction_cannot_close_a_profile(all_construction):
    edges = polygon(((0, 0), (20, 0), (20, 20)))
    edges = tuple(replace(e, construction=all_construction or i == 2) for i, e in enumerate(edges))
    sketch = source(edges)
    assert not analyze(sketch).valid
    assert not analyze_regions(sketch).valid


@pytest.mark.parametrize("shared", [False, True])
def test_construction_crossings_do_not_split_normal_regions_or_generate_faces(shared):
    edges = rectangle(width=20, height=20)
    if shared:
        edges += rectangle((20, 0), width=20, height=20)
    original = source(edges)
    baseline = analyze_regions(original)
    guide = Segment(Vec2(-100, 10), Vec2(100, 10), construction=True)
    sketch = replace(original, entities=(*edges, guide))
    report = analyze_regions(sketch)
    assert report.valid
    assert report.contours == baseline.contours
    assert all(guide.id != use.entity_id for contour in report.contours for use in contour.edges)
    assert to_faces(sketch, report).area == pytest.approx(800 if shared else 400)


def test_native_ellipse_region_ignores_construction_crossing():
    ellipse = Ellipse(Vec2(0, 0), 20, 10)
    axis = Segment(Vec2(-100, 0), Vec2(100, 0), construction=True)
    faces, _ = arrangement(source((ellipse, axis)))
    assert len(faces.regions) == 1
    assert faces.area == pytest.approx(200*pi)


def test_construction_axis_drives_constraints_on_normal_geometry():
    axis = Segment(Vec2(0, 0), Vec2(20, 1), construction=True)
    line = Segment(Vec2(0, 10), Vec2(20, 12))
    constraints = (
        Constraint("fixed", (Ref(axis.id, "start"),), Vec2(0, 0)),
        Constraint("horizontal", (Ref(axis.id),)),
        Constraint("length", (Ref(axis.id),), 40),
        Constraint("fixed", (Ref(line.id, "start"),), Vec2(0, 10)),
        Constraint("parallel", (Ref(axis.id), Ref(line.id))),
        Constraint("length", (Ref(line.id),), 20),
    )
    result = solve(replace(source((axis, line)), constraints=constraints))
    assert result.success, result.message
    assert result.dof == 0
    a, b = result.sketch.entities
    assert a.construction and not b.construction
    assert (a.end.u, a.end.v) == pytest.approx((40, 0), abs=1e-7)
    assert (b.end.u, b.end.v) == pytest.approx((20, 10), abs=1e-7)
    assert (a.id, b.id) == (axis.id, line.id)


@pytest.mark.parametrize("value", [1, "true", None])
def test_construction_flag_is_strictly_boolean(value):
    line = Segment(Vec2(0, 0), Vec2(10, 0))
    with pytest.raises(ValueError):
        replace(line, construction=value)
    with pytest.raises(ValueError):
        _decode_entity(dict(_encode_entity(line), construction=value))


def test_codec_is_generic_but_old_entities_default_to_normal_geometry():
    for entity in (Segment(Vec2(0, 0), Vec2(10, 0)), Circle(Vec2(0, 0), 5)):
        constructed = replace(entity, construction=True)
        assert _decode_entity(_encode_entity(constructed)) == constructed
        legacy = _encode_entity(entity)
        legacy.pop("construction")
        assert _decode_entity(legacy) == entity


def test_entity_clipboard_preserves_construction_and_accepts_legacy_records():
    line = Segment(Vec2(0, 0), Vec2(10, 0), construction=True)
    payload = encode_entities(str(uuid4()), str(uuid4()), (line,))
    pasted, = decode_entities(payload, Vec2(5, 10))
    assert pasted.construction and pasted.id != line.id
    assert pasted.start == Vec2(5, 10) and pasted.end == Vec2(15, 10)
    data = json.loads(payload)
    data["entities"][0].pop("construction")
    assert not decode_entities(json.dumps(data).encode(), Vec2(0, 0))[0].construction


def test_project_save_preserves_identity_and_rejects_incomplete_recipes(tmp_path):
    from cadastrophe.core.persistence import save_document, load_document
    from cadastrophe.core.object_types import ObjectTypeRegistry
    from plugins.sketch.codec import CODEC, encode, decode
    from plugins.sketch.commands import add_entity
    from test_sketch_document import setup_document
    registry = ObjectTypeRegistry()
    registry.register("cadastrophe.sketch", CODEC)
    document, obj = setup_document(native=True)
    line = Segment(Vec2(0, 0), Vec2(10, 0), construction=True)
    add_entity(document, obj.id, line)
    path = tmp_path / "construction.cada"
    save_document(document, path, registry=registry)
    loaded = load_document(path, registry=registry)
    assert loaded.objects[obj.id].sketch.entities == (line,)
    # The identity inventory retains construction entities so downstream recipes
    # can report an invalid profile after conversion, rather than a broken graph.
    assert line.id in loaded.objects[obj.id].profile_source_ids
    data = encode(document.objects[obj.id])
    del data["patterns"]
    data["entities"][0].pop("construction")
    with pytest.raises(ValueError):
        decode(obj, data)
