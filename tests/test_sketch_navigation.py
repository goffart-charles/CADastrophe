"""Native orbit pivots and annotation reuse, with optional profiling."""
from importlib import import_module
import json
import os
from pathlib import Path
from time import perf_counter

import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from OCC.Core.gp import gp_Pnt

from test_sketch_mode import system
from test_sketch_drawing_ui import begin, screen
from test_sketch_face_navigation_ui import navigation_move


def test_empty_sketch_orbit_uses_the_clicked_workplane_point(system):
    w = system[0]
    w.create_document()
    QApplication.processEvents()
    session = begin(system)
    vp, document = w.viewport, w.viewport.document
    start = screen(system, (25., 18.))
    ray = vp.camera.screen_ray(*vp.pixel(start))
    clicked = session.workplane.intersect(ray)
    before, count = dict(document.objects), document.history.count
    QTest.mousePress(vp, Qt.MouseButton.MiddleButton, pos=start)
    assert vp._middle_pivot.tuple() == pytest.approx(clicked.tuple(), abs=1e-7)
    projected = vp.view.Camera().Project(gp_Pnt(*clicked.tuple())).Coord()
    navigation_move(vp, QPoint(start.x()+65, start.y()+35))
    assert vp.view.Camera().Project(gp_Pnt(*clicked.tuple())).Coord() == pytest.approx(projected, abs=1e-9)
    QTest.mouseRelease(vp, Qt.MouseButton.MiddleButton, pos=start)
    assert dict(document.objects) == before and document.history.count == count


def test_orbit_reuses_native_annotations_without_hover_or_model_changes(system, monkeypatch):
    w, commands, model, editor = system
    w.create_document()
    QApplication.processEvents()
    session = begin(system)
    relations = import_module(editor.__package__+'.constraints')
    entities = tuple(model.Segment(model.Vec2(10.+i%10*12., 10.+i//10*8.),
                                  model.Vec2(18.+i%10*12., 10.+i//10*8.)) for i in range(60))
    constraints = tuple(relations.Constraint('horizontal', (relations.Reference(e.id),)) for e in entities)
    commands.add_entities(session.document, session.object_id, entities, title='Profil de navigation', constraints=constraints)
    vp = w.viewport
    vp.frame_objects([session.object_id])
    QApplication.processEvents()
    presentation = session.surface.presentation
    handles = {key: item[1] for key, item in presentation.annotations.items.items()}
    before, count = dict(session.document.objects), session.document.history.count
    start = screen(system, (35., 25.))
    QTest.mouseMove(vp, start)
    profiler = None
    if os.environ.get('CADASTROPHE_NAVIGATION_PROFILE'):
        import cProfile
        profiler = cProfile.Profile()
        profiler.enable()
    started = perf_counter()
    QTest.mousePress(vp, Qt.MouseButton.MiddleButton, pos=start)
    detections = []
    detect = session.surface.detect
    def tracked_detect(*args):
        detections.append(args)
        return detect(*args)
    monkeypatch.setattr(session.surface, 'detect', tracked_detect)
    for i in range(1, 13):
        navigation_move(vp, QPoint(start.x()+i*3, start.y()+i*2))
        QApplication.processEvents()
    assert not detections
    QTest.mouseRelease(vp, Qt.MouseButton.MiddleButton, pos=start)
    QApplication.processEvents()
    elapsed = perf_counter()-started
    if profiler:
        profiler.disable()
        target = Path(os.environ['CADASTROPHE_NAVIGATION_PROFILE'])
        profiler.dump_stats(str(target.with_suffix('.prof')))
        target.with_suffix('.json').write_text(json.dumps(dict(frames=12, seconds=elapsed,
            annotations=len(handles), retained=sum(presentation.annotations.items[key][1] == handle for key, handle in handles.items()))), encoding='utf8')
    assert all(presentation.annotations.items[key][1] == handle for key, handle in handles.items())
    assert dict(session.document.objects) == before and session.document.history.count == count
