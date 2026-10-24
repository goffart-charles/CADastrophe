"""E4 pure parameter edits and strict clipboard records, plus shared transport."""
from dataclasses import asdict, replace
from math import pi
import json
import subprocess
import sys
from uuid import uuid4

import pytest

from plugins.sketch.model import Point, Segment, Circle, Arc, Vec2
from plugins.sketch.editing import translated, control_points, moved_handle
from plugins.sketch.clipboard import encode_entities, decode_entities


def samples():
    return (Point(Vec2(1, 2)), Segment(Vec2(0, 0), Vec2(10, 5)), Circle(Vec2(4, 5), 6),
            Arc(Vec2(-4, 3), 8, 5.7, -1.5))


def test_edit_math_and_entity_clipboard_import_without_qt_or_occt():
    subprocess.run([sys.executable, "-c", "import sys; import plugins.sketch.editing, plugins.sketch.clipboard; "
                    "assert not any(n.startswith(('PySide6', 'OCC')) for n in sys.modules)"], check=True)


@pytest.mark.parametrize("index", range(4))
def test_translate_preserves_parameters_ids_types_and_roundtrip(index):
    entity = samples()[index]
    original = asdict(entity)
    moved = translated(entity, Vec2(10, -20))
    assert moved.id == entity.id and type(moved) is type(entity)
    assert translated(moved, Vec2(-10, 20)) == entity
    assert asdict(entity) == original


def test_control_points_and_point_segment_circle_edits():
    point, segment, circle, arc = samples()
    assert moved_handle(point, "position", Vec2(5, 6)).position == Vec2(5, 6)
    assert moved_handle(segment, "end", Vec2(5, 6)).start == segment.start
    assert moved_handle(segment, "start", Vec2(-5, -6)).end == segment.end
    assert moved_handle(circle, "center", Vec2(5, 6)).radius == circle.radius
    assert moved_handle(circle, "radius", Vec2(7, 9)).radius == 5
    assert {part for part, _ in control_points(arc)} == {"center", "start", "end", "radius"}
    with pytest.raises(ValueError):
        moved_handle(segment, "start", segment.end)
    with pytest.raises(ValueError):
        moved_handle(circle, "radius", circle.center)


@pytest.mark.parametrize("sweep", [pi/2, -pi/2, 3*pi/2, -3*pi/2])
@pytest.mark.parametrize("part", ["start", "end", "radius", "center"])
def test_arc_handles_preserve_sign_and_untouched_parameters(sweep, part):
    arc = Arc(Vec2(0, 0), 10, 0, sweep)
    point = Vec2(-8, 6)
    moved = moved_handle(arc, part, point)
    assert moved.id == arc.id and type(moved) is Arc and moved.sweep * arc.sweep > 0
    if part != "radius":
        assert moved.radius == 10
    if part == "center":
        assert moved.center == point and moved.start_angle == arc.start_angle and moved.sweep == sweep
    elif part == "start":
        expected, actual = arc.point_at(1), moved.point_at(1)
        assert (actual.u, actual.v) == pytest.approx((expected.u, expected.v))
    elif part == "end":
        assert moved.start_angle == arc.start_angle


def test_clipboard_new_ids_offset_all_types_and_no_support_references():
    original = samples()
    payload = encode_entities(str(uuid4()), str(uuid4()), original)
    result = decode_entities(payload, Vec2(10, 20))
    other = decode_entities(payload, Vec2(10, 20))
    assert not {entity.id for entity in result} & {entity.id for entity in (*original, *other)}
    for source, pasted in zip(original, result):
        assert replace(pasted, id=source.id) == translated(source, Vec2(10, 20))
    assert b"plane_id" not in payload and b"brep" not in payload


@pytest.mark.parametrize("corruption", ["version", "unit", "extra", "uuid", "duplicate", "nan", "unknown", "reference"])
def test_clipboard_rejects_corruption_and_future_unknown_references(corruption):
    data = json.loads(encode_entities(str(uuid4()), str(uuid4()), samples()))
    if corruption == "version": data["schema_version"] = True
    elif corruption == "unit": data["length_unit"] = "in"
    elif corruption == "extra": data["constraints"] = []
    elif corruption == "uuid": data["source_sketch"] = "dangling"
    elif corruption == "duplicate": data["entities"].append(data["entities"][0])
    elif corruption == "nan": data["entities"][2]["radius"] = float("nan")
    elif corruption == "unknown": data["entities"][0]["type"] = "spline"
    else: data["entities"][0]["support_id"] = str(uuid4())
    with pytest.raises(ValueError):
        decode_entities(json.dumps(data).encode(), Vec2(0, 0))


def test_clipboard_duplicate_keys_empty_and_truncated_are_rejected():
    for payload in (b"", b"{", b'{"format":1,"format":2}', b"\xff\xff"):
        with pytest.raises(ValueError):
            decode_entities(payload, Vec2(0, 0))
