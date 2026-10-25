"""Count actual evaluator work and exercise cooperative whole-part rebuilding."""
from pathlib import Path
from time import perf_counter
import json

import pytest

from cadastrophe.core.body_history import evaluate_body, evaluate_body_steps, prefix_results
from cadastrophe.core.configurations import activate
from cadastrophe.core.mass_properties import precise_volume
from examples.threaded_parts import PARTS, build_threaded_part
from test_threaded_parts import certify

NATIVE_TEST_COST = 30


@pytest.mark.parametrize("key", PARTS)
def test_threaded_parts_native_work_progress_cancellation_and_cache(key, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from cadastrophe.ui.computation import Computation
    app = QApplication.instance() or QApplication([])
    model = build_threaded_part(key)
    doc = model.document
    certify(model)
    evaluator_type = type(model.service.evaluator(doc))
    native = evaluator_type.evaluate_steps
    calls = []
    def traced(self, feature, *args):
        calls.append(feature.id)
        return (yield from native(self, feature, *args))
    monkeypatch.setattr(evaluator_type, "evaluate_steps", traced)
    snapshot = dict(doc.objects), doc.configuration_state, doc.history.count
    prefixes = dict(prefix_results(doc, model.body_id))
    steps = evaluate_body_steps(doc, model.body_id, model.service.evaluator(doc), reuse=False)
    for _ in range(200):
        next(steps)
        if model.thread_id in calls:
            break
    assert model.thread_id in calls
    steps.close()
    assert not doc.history.blocked
    assert (dict(doc.objects), doc.configuration_state, doc.history.count) == snapshot
    assert dict(prefix_results(doc, model.body_id)) == prefixes
    calls.clear()
    beats = []
    timer = QTimer()
    timer.timeout.connect(lambda: beats.append(perf_counter()))
    timer.start(1)
    started = perf_counter()
    task = Computation(None, evaluate_body_steps(doc, model.body_id, model.service.evaluator(doc), reuse=False), "Recalcul de la pièce taraudée")
    try:
        deadline = started+300
        while task.outcome == "running" and perf_counter() < deadline:
            QTest.qWait(10)
        assert task.outcome == "completed" and len(beats) > 1
    finally:
        timer.stop()
        if task.outcome == "running":
            task.cancel()
    elapsed = perf_counter()-started
    feature_count = len(doc.objects[model.body_id].features)
    assert calls == list(doc.objects[model.body_id].features)
    certify(model)
    calls.clear()
    evaluate_body(doc, model.body_id, model.service.evaluator(doc), reuse=True)
    assert not calls
    started_variant = perf_counter()
    activate(doc, model.configurations[1])
    assert model.hole_id not in calls
    assert calls[0] == model.thread_id
    certify(model)
    Path("artifacts").mkdir(exist_ok=True)
    Path(f"artifacts/threaded-part-{key}.json").write_text(json.dumps(dict(
        features=feature_count, full_native_calls=feature_count, cached_native_calls=0,
        configuration_native_calls=len(calls), configuration_features=calls,
        full_seconds=elapsed, configuration_seconds=perf_counter()-started_variant,
        qt_beats=len(beats), maximum_qt_gap_seconds=max(b-a for a, b in zip([started, *beats], [*beats, started+elapsed])),
        volume_mm3=precise_volume(doc.objects[model.body_id].shape)), indent=2), encoding="utf-8")
    assert app is not None
