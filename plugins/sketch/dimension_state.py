"""Smart Dimension gesture lifecycle, independent of Qt, OCCT and documents."""
from enum import Enum, auto


class Phase(Enum):
    INACTIVE = auto()
    SELECTING = auto()
    PREVIEW = auto()
    PLACING = auto()
    EDITING = auto()


class DimensionState:
    def __init__(self):
        self.phase = Phase.INACTIVE
        self.refs = []

    def start(self, refs=()):
        self.refs = list(refs[:2])
        self.phase = Phase.SELECTING

    def select(self, ref, compatible):
        if self.phase not in {Phase.SELECTING, Phase.PREVIEW}:
            return False
        proposed = [r for r in self.refs if r != ref] if ref in self.refs else [*self.refs, ref]
        if len(proposed) > 2 or (len(proposed) == 2 and not compatible(proposed)):
            raise ValueError("Sélection incompatible : choisir au plus deux références compatibles. Échap retire la dernière.")
        self.refs = proposed
        self.phase = Phase.SELECTING
        return True

    def preview(self, valid):
        if self.phase in {Phase.SELECTING, Phase.PREVIEW}:
            self.phase = Phase.PREVIEW if valid else Phase.SELECTING

    def place(self):
        if self.phase is not Phase.PREVIEW:
            return False
        self.phase = Phase.PLACING
        return True

    def edit(self):
        self.phase = Phase.EDITING

    def cancel_placement(self):
        if self.phase is Phase.PLACING:
            self.phase = Phase.PREVIEW

    def finish(self):
        self.refs = []
        self.phase = Phase.SELECTING

    def escape(self):
        if self.refs:
            self.refs.pop()
            self.phase = Phase.SELECTING
            return True
        self.phase = Phase.INACTIVE
        return False

    def abort(self):
        self.refs = []
        self.phase = Phase.INACTIVE
