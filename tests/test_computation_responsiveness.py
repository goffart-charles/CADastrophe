"""Real native measurements and cooperative candidate lifetime checks."""
from dataclasses import replace
import json
import os
from pathlib import Path
from time import perf_counter

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from cadastrophe.core.body_history import preview_construction, affected_bodies
from cadastrophe.core.configurations import create, set_override, activate
from cadastrophe.core.operation_model import GeometryReference, SweepParameters
from cadastrophe.core.feature_model import ResultStatus
from test_curves import curve
from test_parametric_operations import system, profile
from test_sweep import swept
from test_extrusion_tool import extrusion_system
from test_interactive_computation import project, wait_for
from test_feature_edit import edit
from test_primitives import volume


def export_example(document, service, name):
    destination = os.environ.get('CADASTROPHE_RESPONSIVENESS_EXAMPLES')
    if destination:
        from cadastrophe.core.persistence import save_document
        from examples.parametric_example import registry
        from plugins.curves.codec import CODEC
        types = registry(service)
        types.register('cadastrophe.curves', CODEC)
        path = Path(destination)/name
        path.parent.mkdir(parents=True, exist_ok=True)
        save_document(document, path, registry=types)


def configured_model(tmp_path):
    model, path = project(tmp_path)
    doc = model.document
    doc.equation_evaluator = model.service.evaluator
    variant = create(doc, 'Hauteur 40')
    set_override(doc, variant, model.body.features[0] + ':depth', 40.)
    export_example(doc, model.service, '01-configurations.cada')
    return model, variant


@pytest.mark.parametrize('before_start', [False, True])
def test_responsiveness_configuration_cancel_is_atomic_and_releases_history(tmp_path, before_start):
    from cadastrophe.core.computation import complete
    from cadastrophe.core.history import HistoryBusyError
    from examples.reference_parts import PARTS
    model, variant = configured_model(tmp_path)
    doc = model.document
    snapshot = dict(doc.objects), doc.configuration_state, doc.history.count, doc.history.index
    caches = dict(doc._body_evaluation_cache)
    steps = activate(doc, variant, incremental=True)
    assert doc.history.blocked
    with pytest.raises(HistoryBusyError):
        doc.history.undo()
    if not before_start:
        next(steps)
    assert (dict(doc.objects), doc.configuration_state, doc.history.count, doc.history.index) == snapshot
    steps.close()
    assert not doc.history.blocked and doc._body_evaluation_cache == caches
    assert (dict(doc.objects), doc.configuration_state, doc.history.count, doc.history.index) == snapshot
    complete(activate(doc, variant, incremental=True))
    assert doc.active_configuration.id == variant
    assert volume(doc.objects[model.body.id].shape) == pytest.approx(PARTS[0].expected_volume(40))
    doc.history.undo()
    assert doc.configuration_state == snapshot[1]
    assert doc.objects[model.body.id].shape is snapshot[0][model.body.id].shape
    doc.history.redo()
    assert doc.active_configuration.id == variant


def test_responsiveness_detached_preview_rejects_changed_scope_without_cache_publication(tmp_path):
    from cadastrophe.core.body_history import preview_construction_steps
    from cadastrophe.core.computation import complete
    model, _ = configured_model(tmp_path)
    doc = model.document
    feature = doc.objects[model.body.features[0]]
    candidate = {**doc.objects, feature.id: replace(feature, parameters=replace(feature.parameters, depth=41.))}
    snapshot, cache = dict(doc.objects), {}
    steps = preview_construction_steps(doc, candidate, affected_bodies(candidate, (feature.id,)),
                                       model.service.evaluator, cache=cache)
    next(steps)
    doc.set_parameter_scope('common')
    with pytest.raises(ValueError, match='changé'):
        complete(steps)
    assert dict(doc.objects) == snapshot and cache == {}


def test_responsiveness_preview_replaces_inputs_and_commits_only_latest_candidate(extrusion_system, tmp_path, monkeypatch):
    from importlib import import_module
    from examples.reference_parts import PARTS
    model, path = project(tmp_path)
    w, _, _ = extrusion_system
    doc = w.open_document(path)
    wait_for(lambda: w.services.can_start_tool)
    snapshot, count = dict(doc.objects), doc.history.count
    FeatureEditSession = import_module(w.plugins._record('cadastrophe.modeling').namespace + '.feature_edit').FeatureEditSession
    monkeypatch.setattr(FeatureEditSession, 'cooperative_preview', True)
    tool = edit(w, model.body.features[0])
    original = w.computations.active
    tool.depth.setValue(40.)
    tool.depth.setValue(41.)
    tool.depth.setValue(42.)
    assert original.outcome == 'cancelled'
    w.tool_sessions.update_preview(interactive=True)
    outdated = w.computations.active
    def replace_input(*_):
        tool.depth.setValue(43.)
        tool.depth.setValue(44.)
    outdated.progressed.connect(replace_input)
    beats = []
    timer = QTimer(w)
    timer.timeout.connect(lambda: beats.append(perf_counter()))
    timer.start(1)
    wait_for(lambda: w.tool_sessions.can_commit and w.tool_sessions._preview_task is None, timeout=30)
    timer.stop()
    assert outdated.outcome == 'cancelled' and beats
    assert tool._candidate.parameters.depth == 44.
    assert dict(doc.objects) == snapshot and doc.history.count == count
    assert volume(tool._final_results[model.body.id].shape) == pytest.approx(PARTS[0].expected_volume(44))
    assert w.tool_sessions.commit_interactive()
    assert doc.history.count == count+1 and not w.tool_sessions.active
    assert volume(doc.objects[model.body.id].shape) == pytest.approx(PARTS[0].expected_volume(44))
    doc.history.undo()
    doc.history.redo()
    filename = tmp_path/'reactivite.cada'
    assert w.save_document(path=filename) and w.close_document()
    restored = w.open_document(filename)
    assert volume(restored.objects[model.body.id].shape) == pytest.approx(PARTS[0].expected_volume(44))


def test_responsiveness_window_can_close_document_during_preview_and_start_another_tool(extrusion_system, tmp_path, monkeypatch):
    from importlib import import_module
    model, path = project(tmp_path)
    w, _, _ = extrusion_system
    doc = w.open_document(path)
    wait_for(lambda: w.services.can_start_tool)
    FeatureEditSession = import_module(w.plugins._record('cadastrophe.modeling').namespace + '.feature_edit').FeatureEditSession
    monkeypatch.setattr(FeatureEditSession, 'cooperative_preview', True)
    edit(w, model.body.features[0])
    task = w.computations.active
    assert task is not None
    assert w.command_registry.action('file.close').isEnabled()
    assert w.close_document()
    assert task.outcome == 'cancelled' and not doc.history.blocked
    restored = w.open_document(path)
    wait_for(lambda: w.services.can_start_tool)
    edit(w, model.body.features[0])
    assert w.tool_sessions.document is restored
    assert w.tool_sessions.cancel()
    from PySide6.QtTest import QTest
    QTest.qWait(100)
    assert not w.computations.busy and not w.tool_sessions.active


def test_responsiveness_native_configuration_actions_progress_cancel_validate_and_close(extrusion_system, tmp_path, monkeypatch):
    from cadastrophe.core import computation
    from cadastrophe.core.persistence import save_document
    from examples.parametric_example import registry
    from examples.reference_parts import PARTS
    from test_configurations_ui import select
    model, variant = configured_model(tmp_path)
    path = tmp_path/'configurations.cada'
    save_document(model.document, path, registry=registry(model.service))
    w, _, _ = extrusion_system
    doc = w.open_document(path)
    wait_for(lambda: w.services.can_start_tool)
    panel = w.manager_pane.configurations
    monkeypatch.setattr(computation, 'cooperative_required', lambda objects: True)
    select(panel, variant)
    snapshot = dict(doc.objects), doc.configuration_state, doc.history.count
    assert w.command_registry.execute('configuration.activate')
    task = w.computations.active
    task.progressed.connect(lambda *_: task.cancel())
    wait_for(lambda: task.outcome != 'running')
    assert task.outcome == 'cancelled'
    assert (dict(doc.objects), doc.configuration_state, doc.history.count) == snapshot
    select(panel, variant)
    assert w.command_registry.execute('configuration.activate')
    task = w.computations.active
    beats = []
    timer = QTimer(w)
    timer.timeout.connect(lambda: beats.append(perf_counter()))
    timer.start(1)
    wait_for(lambda: task.outcome != 'running')
    timer.stop()
    assert task.outcome == 'completed' and beats
    assert doc.active_configuration.id == variant
    assert volume(doc.objects[model.body.id].shape) == pytest.approx(PARTS[0].expected_volume(40))
    panel.validate()
    task = w.computations.active
    wait_for(lambda: task.outcome != 'running')
    assert task.outcome == 'completed' and panel.message.text() == 'Toutes les configurations sont valides.'
    panel.validate()
    task = w.computations.active
    w.confirm_discard = lambda document: True
    assert w.close_document()
    assert task.outcome == 'cancelled' and not doc.history.blocked


def test_responsiveness_measure_helical_native_steps(monkeypatch):
    app = QApplication.instance() or QApplication([])
    doc, service = system()
    path = curve(doc, radius=10., pitch=8., turns=2.)
    section = profile(doc, circle=(10., 0., 1.))
    body, feature, result = swept(doc, service, section, None,
        parameters=SweepParameters((GeometryReference('curve', path.id),)))
    assert result.status is ResultStatus.READY, result.message
    doc.equation_evaluator = service.evaluator
    variant = create(doc, 'Profil tourné')
    set_override(doc, variant, feature.id + ':roll', 15.)
    native = type(service.evaluator(doc)).evaluate_steps
    calls, beats = [], []
    def measured(self, feature, *args):
        steps, elapsed = native(self, feature, *args), 0.
        try:
            while True:
                started = perf_counter()
                try:
                    step = next(steps)
                except StopIteration as result:
                    calls.append(elapsed + perf_counter()-started)
                    return result.value
                elapsed += perf_counter()-started
                yield step
        finally:
            steps.close()
    monkeypatch.setattr(type(service.evaluator(doc)), 'evaluate_steps', measured)
    timer = QTimer()
    timer.timeout.connect(lambda: beats.append(perf_counter()))
    timer.start(1)
    started = perf_counter()
    candidate = {**doc.objects, feature.id: replace(feature, parameters=replace(feature.parameters, roll=30.))}
    preview_construction(doc, candidate, affected_bodies(candidate, (feature.id,)), service.evaluator)
    preview_seconds, preview_beats = perf_counter()-started, len(beats)
    started = perf_counter()
    activate(doc, variant)
    activation_seconds = perf_counter()-started
    timer.stop()
    assert len(calls) == 2 and preview_beats == 0
    output = Path('artifacts/p09-helical-synchronous.json')
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(dict(preview_seconds=preview_seconds,
        activation_seconds=activation_seconds, native_calls=len(calls),
        native_seconds=calls, preview_qt_beats=preview_beats), indent=2), encoding='utf-8')


def test_responsiveness_helical_phases_yield_cancel_and_match_full_native_result():
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    from cadastrophe.core.body_history import preview_construction_steps
    from cadastrophe.ui.computation import Computation
    from test_parametric_operations import bounds
    from PySide6.QtTest import QTest
    app = QApplication.instance() or QApplication([])
    doc, service = system()
    path = curve(doc, radius=10., pitch=8., turns=2.)
    section = profile(doc, circle=(10., 0., 1.))
    body, feature, result = swept(doc, service, section, None,
        parameters=SweepParameters((GeometryReference('curve', path.id),)))
    export_example(doc, service, '02-balayage-helicoidal.cada')
    candidate = {**doc.objects, path.id: replace(path, parameters=replace(path.parameters, turns=3.))}
    affected = affected_bodies(candidate, (path.id,))
    snapshot, cache = dict(doc.objects), {}
    task = Computation(None, preview_construction_steps(doc, candidate, affected, service.evaluator, cache=cache),
                       'Balayage annulé')
    task.progressed.connect(lambda *_: task.cancel())
    wait_for(lambda: task.outcome != 'running')
    assert task.outcome == 'cancelled' and not cache and dict(doc.objects) == snapshot
    beats, results, durations, labels = [], [], [], []
    def measured(steps):
        try:
            while True:
                started = perf_counter()
                try:
                    step = next(steps)
                except StopIteration as result:
                    durations.append(perf_counter()-started)
                    return result.value
                durations.append(perf_counter()-started)
                labels.append(step[2])
                yield step
        finally:
            steps.close()
    timer = QTimer()
    timer.timeout.connect(lambda: beats.append(perf_counter()))
    timer.start(1)
    started = perf_counter()
    task = Computation(None, measured(preview_construction_steps(doc, candidate, affected, service.evaluator)),
                       'Balayage coopératif', completed=results.append)
    wait_for(lambda: task.outcome != 'running')
    timer.stop()
    elapsed = perf_counter()-started
    assert task.outcome == 'completed' and len(beats) >= 2
    assert 'Construction du balayage' in labels and 'Vérification du volume balayé' in labels
    shape = results[0][0][body.id].shape
    assert BRepCheck_Analyzer(shape, True).IsValid()
    full, _ = preview_construction(doc, candidate, affected, service.evaluator)
    assert volume(shape) == pytest.approx(volume(full[body.id].shape), rel=1e-9)
    assert bounds(shape) == pytest.approx(bounds(full[body.id].shape))
    assert {label for label, _ in results[0][0][body.id].topology.faces} == {
        label for label, _ in full[body.id].topology.faces}
    assert dict(doc.objects) == snapshot
    Path('artifacts/p09-helical-cooperative.json').write_text(json.dumps(dict(
        elapsed_seconds=elapsed, qt_beats=len(beats), native_step_seconds=durations,
        maximum_step_seconds=max(durations), phases=labels), indent=2), encoding='utf-8')


@pytest.mark.parametrize('when', ['presentation', 'commit'])
def test_responsiveness_async_display_changes_and_pending_commit_use_current_inputs(extrusion_system, tmp_path, monkeypatch, when):
    from importlib import import_module
    from examples.reference_parts import PARTS
    model, path = project(tmp_path)
    w, _, _ = extrusion_system
    doc = w.open_document(path)
    wait_for(lambda: w.services.can_start_tool)
    cls = import_module(w.plugins._record('cadastrophe.modeling').namespace+'.feature_edit').FeatureEditSession
    monkeypatch.setattr(cls, 'cooperative_preview', True)
    tool = edit(w, model.body.features[0])
    tool.depth.setValue(40.)
    if when == 'presentation':
        host = w.tool_sessions.host
        show, seen = host.preview, []
        def presenting(obj):
            show(obj)
            if not seen:
                seen.append(obj)
                tool.depth.setValue(41.)
        monkeypatch.setattr(host, 'preview', presenting)
        wait_for(lambda: w.tool_sessions.can_commit and tool._candidate.parameters.depth == 41.)
        assert doc.objects[model.body.features[0]].parameters.depth == 30.
        assert w.tool_sessions.commit_interactive()
        expected = 41.
    else:
        assert w.tool_sessions.commit_interactive()
        assert w.tool_sessions.active and doc.history.count == 0
        wait_for(lambda: not w.tool_sessions.active)
        expected = 40.
    assert doc.history.count == 1
    assert volume(doc.objects[model.body.id].shape) == pytest.approx(PARTS[0].expected_volume(expected))
