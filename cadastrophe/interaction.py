"""Input state machine: a dragged left click never becomes a selection."""
from dataclasses import dataclass
from enum import Enum, auto
from math import hypot


class Gesture(Enum):
    IDLE = auto()
    CLICK = auto()
    DRAG = auto()
    ROTATE = auto()
    ROLL = auto()
    PAN = auto()
    WINDOW = auto()


class WorkMode(Enum):
    MODEL = auto()
    PLANE = auto()


@dataclass
class InteractionState:
    gesture: Gesture = Gesture.IDLE
    start: tuple[float, float] = (0, 0)
    last: tuple[float, float] = (0, 0)
    button: str = ""

    @staticmethod
    def navigation_gesture(*, ctrl=False, alt=False, plane=False) -> Gesture:
        # Deterministic precedence for combined modifiers; Shift is neutral.
        if ctrl:
            return Gesture.PAN
        if alt:
            return Gesture.ROLL
        return Gesture.PAN if plane else Gesture.ROTATE

    def update_navigation(self, *, ctrl=False, alt=False, plane=False) -> bool:
        if self.button != "middle":
            return False
        gesture = self.navigation_gesture(ctrl=ctrl, alt=alt, plane=plane)
        changed = gesture != self.gesture
        self.gesture = gesture
        return changed

    def press(self, button: str, point: tuple[float, float], *, ctrl=False, alt=False,
              plane=False, window=False):
        self.start = self.last = point
        self.button = button
        if button == "middle":
            self.gesture = self.navigation_gesture(ctrl=ctrl, alt=alt, plane=plane)
        elif button == "left":
            self.gesture = Gesture.WINDOW if window else Gesture.CLICK
        else:
            self.gesture = Gesture.IDLE

    def move(self, point: tuple[float, float], threshold: float = 4):
        if self.gesture == Gesture.CLICK and hypot(
                point[0] - self.start[0], point[1] - self.start[1]) >= threshold:
            self.gesture = Gesture.DRAG
        delta = point[0] - self.last[0], point[1] - self.last[1]
        self.last = point
        return delta

    def release(self, button: str) -> Gesture:
        if button != self.button:
            return Gesture.IDLE
        result = self.gesture
        self.cancel()
        return result

    def cancel(self):
        self.gesture = Gesture.IDLE
        self.button = ""
