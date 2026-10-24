from cadastrophe.interaction import Gesture, InteractionState
import pytest


def test_drag_never_selects():
    state = InteractionState()
    state.press("left", (10, 10))
    state.move((30, 10))
    state.move((10, 10))
    assert state.release("left") == Gesture.DRAG
    assert state.gesture == Gesture.IDLE


def test_small_motion_is_click():
    state = InteractionState()
    state.press("left", (10, 10))
    state.move((11, 12))
    assert state.release("left") == Gesture.CLICK


def test_navigation_and_cancel():
    state = InteractionState()
    for ctrl, plane, expected in [(False, False, Gesture.ROTATE),
                                  (True, False, Gesture.PAN), (False, True, Gesture.PAN)]:
        state.press("middle", (0, 0), ctrl=ctrl, plane=plane)
        assert state.gesture == expected
        assert state.move((5, -7)) == (5, -7)
        assert state.release("left") == Gesture.IDLE
        assert state.gesture == expected
        assert state.release("middle") == expected
    state.press("left", (0, 0), window=True)
    assert state.gesture == Gesture.WINDOW
    state.cancel()
    assert state.release("left") == Gesture.IDLE


@pytest.mark.parametrize("ctrl,alt,plane,expected", [
    (False, False, False, Gesture.ROTATE),
    (False, True, False, Gesture.ROLL),
    (True, False, False, Gesture.PAN),
    (True, True, False, Gesture.PAN),
    (False, False, True, Gesture.PAN),
    (False, True, True, Gesture.ROLL),
    (True, True, True, Gesture.PAN),
])
def test_modifier_precedence(ctrl, alt, plane, expected):
    state = InteractionState()
    state.press("middle", (10, 20), ctrl=ctrl, alt=alt, plane=plane)
    assert state.gesture == expected


def test_live_modifier_changes_and_left_click_isolation():
    state = InteractionState()
    state.press("middle", (10, 20))
    assert state.update_navigation(alt=True)
    assert state.gesture == Gesture.ROLL
    assert not state.update_navigation(alt=True)
    assert state.update_navigation(ctrl=True, alt=True)
    assert state.gesture == Gesture.PAN
    assert state.update_navigation()
    assert state.gesture == Gesture.ROTATE
    state.release("middle")
    state.press("left", (10, 20), alt=True)
    assert not state.update_navigation(ctrl=True)
    assert state.release("left") == Gesture.CLICK
