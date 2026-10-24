"""Count actual native work on small and large shared construction graphs."""
from dataclasses import replace
from math import pi
from pathlib import Path
from time import perf_counter
import json
import os

import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from cadastrophe.ui.computation import Computation

from cadastrophe.document import ObjectType
from cadastrophe.core.body_history import evaluate_body
from cadastrophe.core.configurations import create, set_override, activate
from cadastrophe.core.feature_model import ExtrusionParameters, Operation, SketchInput, ResultStatus
from cadastrophe.core.feature_objects import ExtrusionFeature
from examples.parametric_example import ParametricExample, polygon, mass
from test_parametric_operations import bounds


NATIVE_TEST_COST = 300.


@pytest.mark.parametrize("bosses", [8, 128])
def test_complete_parts_small_and_large_graphs_measure_real_native_work(bosses, monkeypatch):
    app = QApplication.instance() or QApplication([])
    model = ParametricExample("Mesure du graphe commun")
    doc, xy = model.document, model.plane("XY")
    source = model.sketch("base", "Semelle", xy, polygon(((0, 0), (100, 0), (100, 70), (0, 70))))
    body = model.body
    base = ExtrusionFeature("Semelle", None, body.color, kind=ObjectType.FEATURE, body_id=body.id,
        operation=Operation.NEW_BODY, source=SketchInput(source.id), parameters=ExtrusionParameters(depth=10.))
    features = [base]
    for index in range(bosses):
        sketch = model.circle(str(index), f"Profil {index}", xy, (5+6*(index%16), 5+8*(index//16)), 2.)
        features.append(ExtrusionFeature(f"Plot {index}", None, body.color, kind=ObjectType.FEATURE,
            body_id=body.id, operation=Operation.BOSS, source=SketchInput(sketch.id), parameters=ExtrusionParameters(depth=12.)))
    doc.update_construction((replace(body, features=tuple(f.id for f in features)), *features))
    native = type(model.service.evaluator(doc)).evaluate
    calls = []
    def traced(self, feature, *args):
        calls.append(feature.id)
        return native(self, feature, *args)
    monkeypatch.setattr(type(model.service.evaluator(doc)), "evaluate", traced)
    started = perf_counter()
    full = evaluate_body(doc, body.id, model.service.evaluator(doc), reuse=False)
    full_seconds = perf_counter()-started
    assert full.status is ResultStatus.READY, full.message
    assert calls == [f.id for f in features]
    assert mass(full.shape) == pytest.approx(70000+bosses*8*pi, abs=1e-5)
    calls.clear()
    started = perf_counter()
    for _ in range(3):
        cached = evaluate_body(doc, body.id, model.service.evaluator(doc), reuse=True)
        assert cached.shape is full.shape
    cached_seconds = perf_counter()-started
    assert calls == []
    doc.equation_evaluator = model.service.evaluator
    variant = create(doc, "Dernier plot plus haut")
    set_override(doc, variant, features[-1].id+":depth", 14.)
    assert calls == []
    started = perf_counter()
    beats = []
    timer = QTimer()
    timer.timeout.connect(lambda: beats.append(perf_counter()))
    timer.start(1)
    task = Computation(None, activate(doc, variant, incremental=True), 'Configuration du grand graphe')
    deadline = perf_counter()+300
    while task.outcome == 'running' and perf_counter() < deadline:
        QTest.qWait(10)
    timer.stop()
    assert task.outcome == 'completed' and beats
    incremental_seconds = perf_counter()-started
    maximum_gap = max(b-a for a, b in zip([started, *beats], [*beats, perf_counter()]))
    assert calls == [features[-1].id]
    expected = 70000+(bosses*8+8)*pi
    edited = doc.objects[body.id].shape
    assert mass(edited) == pytest.approx(expected, abs=1e-5)
    from cadastrophe.core.body_history import body_result
    labels = {label for label, _ in body_result(doc, body.id).topology.faces}
    calls.clear()
    started = perf_counter()
    rebuilt = evaluate_body(doc, body.id, model.service.evaluator(doc), reuse=False)
    repeated_full_seconds = perf_counter()-started
    assert calls == [f.id for f in features]
    assert mass(rebuilt.shape) == pytest.approx(expected, abs=1e-5)
    assert bounds(rebuilt.shape) == pytest.approx(bounds(edited))
    assert {label for label, _ in rebuilt.topology.faces} == labels
    assert BRepCheck_Analyzer(rebuilt.shape, True).IsValid()
    destination = os.environ.get('CADASTROPHE_RESPONSIVENESS_EXAMPLES')
    if destination and bosses == 128:
        from cadastrophe.core.persistence import save_document
        from examples.parametric_example import registry
        output = Path(destination)/'03-graphe-129.cada'
        output.parent.mkdir(parents=True, exist_ok=True)
        save_document(doc, output, registry=registry(model.service))
    Path("artifacts").mkdir(exist_ok=True)
    Path(f"artifacts/complete-parts-{bosses+1}.json").write_text(json.dumps(dict(
        features=bosses+1, full_native_calls=bosses+1, cached_rebuilds=3, cached_native_calls=0,
        incremental_native_calls=1, repeated_full_native_calls=len(calls), full_seconds=full_seconds,
        cached_seconds=cached_seconds, incremental_seconds=incremental_seconds,
        incremental_qt_beats=len(beats), incremental_maximum_qt_gap_seconds=maximum_gap,
        repeated_full_seconds=repeated_full_seconds, volume_mm3=mass(rebuilt.shape)), indent=2), encoding="utf-8")
