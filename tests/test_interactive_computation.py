"""Real OCCT prefixes, cancellation, Qt heartbeats and atomic document adoption."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from math import pi
import os
from pathlib import Path
from time import monotonic

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest

from cadastrophe.core.body_history import body_result, evaluate_body_steps, prefix_results, result_signature
from cadastrophe.core.computation import complete
from cadastrophe.core.feature_model import ResultStatus
from cadastrophe.core.history import HistoryBusyError
from cadastrophe.core.persistence import save_document
from examples.reference_parts import PARTS, build_reference
from examples.parametric_example import registry
from test_extrusion_tool import extrusion_system
from test_primitives import volume


def wait_for(predicate, timeout=10):
    deadline = monotonic()+timeout
    while not predicate() and monotonic() < deadline:
        QTest.qWait(10)
    assert predicate(), "Le calcul interactif ne se termine pas."


def project(tmp_path):
    model = build_reference(PARTS[0])
    path = tmp_path/"boitier.cada"
    save_document(model.document, path, registry=registry(model.service))
    return model, path


def test_native_prefix_steps_do_not_publish_before_completion_and_cancel_is_atomic():
    model = build_reference(PARTS[0])
    doc, body_id = model.document, model.body.id
    feature = doc.objects[model.body.features[0]]
    doc.replace_feature(replace(feature, parameters=replace(feature.parameters, depth=40)))
    before, count = dict(doc.objects), doc.history.count
    prefixes = dict(prefix_results(doc, body_id))
    steps = evaluate_body_steps(doc, body_id, model.service.evaluator(doc), reuse=True)
    assert doc.history.blocked
    with pytest.raises(HistoryBusyError):
        doc.rename(body_id, "Interdit pendant le calcul")
    current, total, _ = next(steps)
    assert current == 1 and total == len(model.body.features)
    assert all(doc.objects[key] is obj for key, obj in before.items())
    assert dict(prefix_results(doc, body_id)) == prefixes and body_result(doc, body_id).status is ResultStatus.DIRTY
    steps.close()
    assert not doc.history.blocked and doc.history.count == count
    assert all(doc.objects[key] is obj for key, obj in before.items())
    result = complete(evaluate_body_steps(doc, body_id, model.service.evaluator(doc), reuse=True))
    assert result.status is ResultStatus.READY and not doc.history.blocked
    assert volume(result.shape) == pytest.approx(PARTS[0].expected_volume(40))


def test_cancel_before_first_step_also_releases_reservation():
    model = build_reference(PARTS[0])
    steps = evaluate_body_steps(model.document, model.body.id, model.service.evaluator(model.document))
    assert model.document.history.blocked
    steps.close()
    assert not model.document.history.blocked
    steps.close()


def test_native_interactive_open_cancel_then_complete_keeps_tabs_atomic(extrusion_system, tmp_path):
    model, path = project(tmp_path)
    window, _, _ = extrusion_system
    previous = window.documents.documents
    old = window.documents.active_document
    before = dict(old.objects), old.history.count, old.is_modified
    task = window.open_document_interactive(path)
    assert task is not None and window.computations.busy and old.history.blocked
    task.progressed.connect(lambda *_: task.cancel())
    wait_for(lambda: task.outcome != "running")
    assert task.outcome == "cancelled" and window.documents.documents == previous
    assert (dict(old.objects), old.history.count, old.is_modified) == before
    assert not window.services.busy
    beats, progress = [], []
    timer = QTimer(window)
    timer.timeout.connect(lambda: beats.append(monotonic()))
    timer.start(1)
    task = window.open_document_interactive(path)
    def observed(current, total, label):
        assert window.documents.documents == previous
        progress.append((current, total, label))
    task.progressed.connect(observed)
    wait_for(lambda: task.outcome != "running")
    timer.stop()
    assert task.outcome == "completed" and len(progress) >= 3 and len(beats) >= 2
    restored = window.documents.active_document
    assert restored is not old and restored.id == model.document.id
    assert volume(restored.objects[model.body.id].shape) == pytest.approx(PARTS[0].expected_volume(30))
    assert not restored.is_modified and restored.history.count == 0 and not window.services.busy


@pytest.mark.parametrize("on_close", [False, True])
def test_interactive_open_failed_restorer_never_adopts_partial_document(extrusion_system, tmp_path, monkeypatch, on_close):
    _, path = project(tmp_path)
    window, _, _ = extrusion_system
    previous = window.documents.documents
    errors = []
    monkeypatch.setattr(window, "report_file_error", lambda title, error: errors.append(str(error)))
    def fail(document):
        if on_close:
            try:
                yield 1, 2, "Restaurateur de test"
            finally:
                raise ValueError("Extension défaillante")
        yield 1, 2, "Restaurateur de test"
        raise ValueError("Extension défaillante")
    window.object_types.register_document_restorer("fixture.failure", fail)
    task = window.open_document_interactive(path)
    if on_close:
        task.progressed.connect(lambda current, total, label: task.cancel() if label == "Restaurateur de test" else None)
    wait_for(lambda: task.outcome != "running")
    assert task.outcome == "failed" and errors == ["Extension défaillante"]
    assert window.documents.documents == previous and not window.services.busy
    window.object_types.unregister_document_restorer("fixture.failure")


def test_cancelled_rebuild_ignores_cosmetic_edits_and_resumes_after_real_edit_and_undo(extrusion_system, tmp_path):
    model, path = project(tmp_path)
    window, _, _ = extrusion_system
    doc = window.open_document(path)
    rebuilder = window.plugins._record("cadastrophe.modeling").instance.rebuilder
    rebuilder.threshold = 1
    feature = doc.objects[model.body.features[0]]
    doc.replace_feature(replace(feature, parameters=replace(feature.parameters, depth=40)))
    rebuilder.flush()
    task = window.computations.active
    task.progressed.connect(lambda *_: task.cancel())
    wait_for(lambda: task.outcome != "running")
    doc.rename(model.body.id, "Nom changé après annulation")
    QTest.qWait(200)
    assert window.computations.active is None and body_result(doc, model.body.id).status is ResultStatus.DIRTY
    feature = doc.objects[feature.id]
    doc.replace_feature(replace(feature, parameters=replace(feature.parameters, depth=41)))
    wait_for(lambda: body_result(doc, model.body.id).status is ResultStatus.READY)
    assert volume(doc.objects[model.body.id].shape) == pytest.approx(PARTS[0].expected_volume(41))
    doc.history.undo()
    wait_for(lambda: body_result(doc, model.body.id).status is ResultStatus.READY)
    assert volume(doc.objects[model.body.id].shape) == pytest.approx(PARTS[0].expected_volume(40))


def test_live_native_rebuild_cancel_retry_undo_and_reopen(extrusion_system, tmp_path):
    model, path = project(tmp_path)
    window, _, _ = extrusion_system
    doc = window.open_document(path)
    rebuilder = window.plugins._record("cadastrophe.modeling").instance.rebuilder
    rebuilder.threshold = 1
    feature = doc.objects[model.body.features[0]]
    doc.replace_feature(replace(feature, parameters=replace(feature.parameters, depth=40)))
    snapshot, count = dict(doc.objects), doc.history.count
    signature, prefixes = result_signature(doc.objects, model.body.id), dict(prefix_results(doc, model.body.id))
    rebuilder.flush()
    task = window.computations.active
    assert task is not None
    task.progressed.connect(lambda *_: task.cancel())
    wait_for(lambda: task.outcome != "running")
    assert task.outcome == "cancelled" and not doc.history.blocked
    assert set(doc.objects) == set(snapshot) and doc.history.count == count
    assert result_signature(doc.objects, model.body.id) == signature
    assert doc.objects[model.body.id].shape is snapshot[model.body.id].shape
    assert dict(prefix_results(doc, model.body.id)) == prefixes
    QTest.qWait(150)
    assert window.computations.active is None and body_result(doc, model.body.id).status is ResultStatus.DIRTY
    assert window.command_registry.execute("cadastrophe.modeling.rebuild")
    wait_for(lambda: body_result(doc, model.body.id).status is ResultStatus.READY)
    assert doc.history.count == count and volume(doc.objects[model.body.id].shape) == pytest.approx(PARTS[0].expected_volume(40))
    assert window.command_registry.execute("edit.undo")
    wait_for(lambda: body_result(doc, model.body.id).status is ResultStatus.READY)
    assert volume(doc.objects[model.body.id].shape) == pytest.approx(PARTS[0].expected_volume(30))
    assert window.command_registry.execute("edit.redo")
    wait_for(lambda: body_result(doc, model.body.id).status is ResultStatus.READY)
    assert set(doc.objects) == set(snapshot)
    output = tmp_path/"recalcule.cada"
    assert window.save_document(path=output) and window.close_document()
    restored = window.open_document(output)
    assert volume(restored.objects[model.body.id].shape) == pytest.approx(PARTS[0].expected_volume(40))


def test_window_close_cancels_staged_open_and_releases_original_document(extrusion_system, tmp_path):
    _, path = project(tmp_path)
    window, _, _ = extrusion_system
    old = window.documents.active_document
    task = window.open_document_interactive(path)
    assert old.history.blocked
    window.close()
    assert task.outcome == "cancelled" and not old.history.blocked


@pytest.mark.parametrize("bosses", [32, pytest.param(128, marks=pytest.mark.skipif(
    os.environ.get("CADASTROPHE_PROFILE_GROWTH") != "1", reason="Mesure explicite du modèle de 129 fonctions"))])
def test_growing_native_model_open_yields_to_qt_and_has_real_progress(extrusion_system, tmp_path, bosses):
    from examples.parametric_example import ParametricExample, polygon
    from cadastrophe.core.feature_model import Operation, ExtrusionParameters
    model = ParametricExample("Charge interactive")
    xy = model.plane("XY")
    base = model.sketch("base", "Semelle", xy, polygon(((0, 0), (100, 0), (100, 70), (0, 70))))
    model.append("base_feature", "Semelle", Operation.NEW_BODY, ExtrusionParameters(depth=10), base)
    for index in range(bosses):
        circle = model.circle(f"circle_{index}", f"Plot {index}", xy, (5+6*(index%16), 5+8*(index//16)), 2)
        model.append(f"boss_{index}", f"Bossage {index}", Operation.BOSS, ExtrusionParameters(depth=12), circle)
    path = tmp_path/"charge.cada"
    save_document(model.document, path, registry=registry(model.service))
    window, _, _ = extrusion_system
    original = window.documents.documents
    started, beats, progress = monotonic(), [], []
    timer = QTimer(window)
    timer.timeout.connect(lambda: beats.append(monotonic()))
    timer.start(10)
    task = window.open_document_interactive(path)
    def advanced(current, total, _):
        assert window.documents.documents == original
        progress.append((current, total))
    task.progressed.connect(advanced)
    wait_for(lambda: task.outcome != "running", timeout=300)
    elapsed = monotonic()-started
    timer.stop()
    assert task.outcome == "completed" and len(progress) == bosses+1 and len(beats) >= 10
    gaps = [later-earlier for earlier, later in zip([started, *beats], [*beats, monotonic()])]
    # Real event-loop progress must occur throughout, not just before/after a blocking restore.
    assert max(gaps) < elapsed*.5
    doc = window.documents.active_document
    assert volume(doc.objects[model.body.id].shape) == pytest.approx(70000+bosses*8*pi, abs=1e-4)
    report = dict(measured_at_utc=datetime.now(timezone.utc).isoformat(), features=bosses+1,
                  open_seconds=elapsed, maximum_qt_gap_seconds=max(gaps), heartbeat_count=len(beats),
                  progress_steps=len(progress), status="passed")
    output = Path(f"artifacts/interactive-growth-{bosses+1}.json")
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
