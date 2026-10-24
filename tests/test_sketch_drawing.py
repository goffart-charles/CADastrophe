"""Pure E3 drafting math/state. No GUI or OCCT needed by the controller."""
from math import pi
import subprocess
import sys

import pytest

from plugins.sketch.drawing import DrawingController
from plugins.sketch.model import Arc, Circle, Segment, Vec2


def test_drawing_import_is_independent_of_qt_and_occt():
    subprocess.run([sys.executable, "-c", "import sys; import plugins.sketch.drawing; "
                    "assert not any(n.startswith(('PySide6', 'OCC')) for n in sys.modules)"], check=True)


def test_line_chain_advances_only_after_commit_and_escape_preserves_no_pending_shape():
    draft = DrawingController()
    draft.select("line")
    first, second, third = Vec2(1, 2), Vec2(11, 4), Vec2(14, 8)
    assert draft.click(first) == () and draft.state == "drawing"
    proposed = draft.click(second)
    assert proposed == draft.candidate(second)
    assert proposed[0].start == first and proposed[0].end == second
    # An exception in the document commit does not advance the drafting anchor.
    assert draft.anchors == (first,)
    draft.committed(second)
    assert draft.click(third)[0].start == second
    assert draft.click(third)[0].id != proposed[0].id
    draft.select(None)
    assert not draft.anchors and draft.current is None and draft.state == "editing"


@pytest.mark.parametrize("opposite", [Vec2(12, 15), Vec2(-12, 15), Vec2(-12, -15), Vec2(12, -15)])
def test_rectangle_is_four_closed_local_segments_with_distinct_ids(opposite):
    draft = DrawingController()
    draft.select("rectangle")
    draft.click(Vec2(0, 0))
    sides = draft.click(opposite)
    assert len(sides) == len({edge.id for edge in sides}) == 4
    assert all(type(edge) is Segment and edge.end == sides[(i+1) % 4].start for i, edge in enumerate(sides))
    assert all(edge.start.u == edge.end.u or edge.start.v == edge.end.v for edge in sides)
    draft.committed(opposite)
    assert not draft.anchors and draft.tool == "rectangle"


def test_circle_center_radius_and_stable_preview_identity():
    draft = DrawingController()
    draft.select("circle")
    draft.click(Vec2(10, 10))
    first = draft.candidate(Vec2(13, 14))[0]
    second = draft.candidate(Vec2(16, 18))[0]
    assert type(first) is Circle and first.radius == 5 and second.radius == 10
    assert first.id == second.id and first.center == second.center == Vec2(10, 10)


@pytest.mark.parametrize("start,end,expected", [
    (Vec2(10, 0), Vec2(0, 100), pi/2),
    (Vec2(0, 10), Vec2(100, 0), 3*pi/2),
    (Vec2(0, -10), Vec2(100, 0), pi/2),
])
def test_arc_center_start_and_end_direction_ccw_fixed_radius(start, end, expected):
    draft = DrawingController()
    draft.select("arc")
    assert draft.click(Vec2(0, 0)) == ()
    assert type(draft.candidate(start)[0]) is Segment  # radius guide, not a committed segment
    assert draft.click(start) == () and len(draft.anchors) == 2
    arc = draft.click(end)[0]
    assert type(arc) is Arc and arc.radius == 10
    assert arc.sweep == pytest.approx(expected)
    assert (arc.point_at(0).u, arc.point_at(0).v) == pytest.approx((start.u, start.v))


@pytest.mark.parametrize("tool,second", [("line", Vec2(0, 0)), ("circle", Vec2(0, 0)),
                                        ("rectangle", Vec2(0, 10)), ("rectangle", Vec2(10, 0)),
                                        ("arc", Vec2(0, 0))])
def test_degenerate_click_never_discards_the_anchor(tool, second):
    draft = DrawingController()
    draft.select(tool)
    draft.click(Vec2(0, 0))
    with pytest.raises(ValueError):
        draft.click(second)
    assert draft.anchors == (Vec2(0, 0),)


def test_zero_full_turn_arc_is_rejected_and_tool_switch_discards_draft():
    draft = DrawingController()
    draft.select("arc")
    draft.click(Vec2(0, 0))
    draft.click(Vec2(10, 0))
    with pytest.raises(ValueError):
        draft.click(Vec2(100, 0))
    assert len(draft.anchors) == 2
    draft.select("circle")
    assert not draft.anchors and draft.tool == "circle"
    with pytest.raises(ValueError):
        draft.select("unsupported")
