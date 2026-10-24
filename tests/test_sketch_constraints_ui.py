"""Native E6 mouse drawing, PropertyManager, persistence, rollback and drag."""
from importlib import import_module
from dataclasses import replace
from pathlib import Path
from math import hypot

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget

from test_sketch_mode import system, camera_state
from test_sketch_drawing_ui import begin, choose, click, screen
from test_sketch_editing_ui import mouse_move

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("conflict", [False, True])
def test_legacy_relations_are_located_and_can_be_removed_with_undo(system, conflict):
    session = draw_rectangle(system)
    document, key, panel = session.document, session.object_id, session.constraint_panel
    api_constraints = api(system)
    source = document.objects[key]
    edge = source.sketch.entities[0]
    if conflict:
        relations = (api_constraints.Constraint("fixed", (api_constraints.Reference(edge.id, "start"),), edge.start),
                     api_constraints.Constraint("fixed", (api_constraints.Reference(edge.id, "start"),),
                                                replace(edge.start, u=edge.start.u+5)))
    else:
        existing = next(c for c in source.sketch.constraints if c.kind in {"horizontal", "vertical"})
        relations = (api_constraints.Constraint(existing.kind, existing.refs),)
    legacy = replace(source, sketch=replace(source.sketch, constraints=(*source.sketch.constraints, *relations)))
    document.replace_feature(legacy)
    QTest.qWait(30)
    panel.refresh()
    marked = [panel.list.item(i) for i in range(panel.list.count())
              if ("contradiction" if conflict else "redondance locale") in panel.list.item(i).text()]
    assert len(marked) == (2 if conflict else 1)
    assert all(item.toolTip() for item in marked)
    target = next(item for item in marked if item.data(Qt.ItemDataRole.UserRole) == relations[-1].id)
    panel.list.setCurrentItem(target)
    count = document.history.count
    QTest.mouseClick(panel.remove, Qt.MouseButton.LeftButton)
    assert document.history.count == count+1 and relations[-1].id not in {c.id for c in document.objects[key].sketch.constraints}
    assert not panel.message.text(), panel.message.text()
    assert system[0].command_registry.execute("edit.undo")
    assert document.objects[key].sketch.constraints == legacy.sketch.constraints


def api(system):
    return import_module(system[3].__package__ + ".constraints")


def select_ref(combo, ref):
    for index in range(combo.count()):
        if combo.itemData(index) == ref:
            combo.setCurrentIndex(index)
            return
    pytest.fail(f"Reference missing from combo: {ref}")


def add(panel, kind, refs, value=None):
    panel.kind.setCurrentIndex(panel.kind.findData(kind))
    select_ref(panel.first, refs[0])
    if len(refs) == 2:
        select_ref(panel.second, refs[1])
    if value is not None:
        panel.value.set_internal_value(value)
    QTest.mouseClick(panel.add, Qt.MouseButton.LeftButton)
    assert not panel.message.text(), panel.message.text()


def draw_rectangle(system, plane="XY"):
    session = begin(system, plane)
    vp = system[0].viewport
    choose(system, "rectangle")
    start, end = screen(system, (-20, -10)), screen(system, (20, 10))
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=start)
    mouse_move(system, end)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=end)
    choose(system, "select")
    assert len(session.document.objects[session.object_id].sketch.entities) == 4
    return session


@pytest.mark.parametrize("plane", ["XY", "XZ", "YZ"])
def test_rectangle_driving_dimensions_property_manager_undo_save_reopen(system, tmp_path, plane):
    session = draw_rectangle(system, plane)
    window, vp = system[0], system[0].viewport
    doc, p, key = session.document, session.constraint_panel, session.object_id
    Ref = api(system).Reference
    original = doc.objects[key].sketch
    pose = camera_state(vp)
    QTest.mouseClick(p.link, Qt.MouseButton.LeftButton)
    assert len(doc.objects[key].sketch.constraints) == 8
    assert window.command_registry.execute("cadastrophe.sketch.relations")
    edges = original.entities
    add(p, "length", (Ref(edges[0].id),), 70)
    add(p, "length", (Ref(edges[1].id),), 35)
    add(p, "fixed", (Ref(edges[0].id, "start"),))
    assert "≈ 0" in p.diagnostic.text()
    assert not session.error
    before = doc.objects[key].sketch
    count = doc.history.count
    p.list.setCurrentRow(8)
    p.value.set_internal_value(90)
    p.value.setFocus()
    QApplication.processEvents()
    QTest.keyClick(p.value, Qt.Key.Key_Return)
    after = doc.objects[key].sketch
    assert doc.history.count == count+1
    assert hypot(after.entities[0].end.u-after.entities[0].start.u,
                 after.entities[0].end.v-after.entities[0].start.v) == pytest.approx(90, abs=1e-7)
    assert camera_state(vp) == pose
    assert window.command_registry.execute("edit.undo")
    assert doc.objects[key].sketch == before and p.value.internal_value == 70
    assert window.command_registry.execute("edit.redo")
    assert doc.objects[key].sketch == after and p.value.internal_value == 90
    assert not session.renderer.entities and not session.interaction.pending
    assert window.save_document(path=tmp_path / "e6-native.cada")
    if plane == "XY":
        scroll = window.tool_host.panel.findChild(QScrollArea)
        QApplication.processEvents()
        QTest.qWait(20)
        scroll.ensureWidgetVisible(p.value)
        QApplication.processEvents()
        assert p.group.width() <= scroll.viewport().width(), [(type(w).__name__, w.minimumSizeHint().width(), w.accessibleName())
            for w in p.findChildren(QWidget) if w.minimumSizeHint().width() > 180]
        assert p.first.height() >= p.first.minimumSizeHint().height()
        assert window.grab().save(str(ROOT / "artifacts" / "e6-constraints.png"))
        assert p.grab().save(str(ROOT / "artifacts" / "e6-constraints-panel.png"))
        assert vp.view.Dump(str(ROOT / "artifacts" / "e6-rectangle.png"))
    assert window.command_registry.execute("cadastrophe.sketch.finish")
    assert not window.command_registry.action("cadastrophe.sketch.dimension").isEnabled()
    assert window.close_document()
    loaded = window.open_document(tmp_path / "e6-native.cada")
    QApplication.processEvents()
    assert loaded.objects[key].sketch == after
    window.tree_selection_changed([key])
    QApplication.processEvents()
    assert window.command_registry.execute("cadastrophe.sketch.edit")
    new_panel = window.tool_sessions.editor.constraint_panel
    assert "≈ 0" in new_panel.diagnostic.text()
    new_panel.list.setCurrentRow(9)
    new_panel.value.set_internal_value(50)
    QTest.mouseClick(new_panel.modify, Qt.MouseButton.LeftButton)
    assert not new_panel.message.text()
    changed = loaded.objects[key].sketch.entities[1]
    assert hypot(changed.end.u-changed.start.u, changed.end.v-changed.start.v) == pytest.approx(50, abs=1e-7)


def test_circle_ribbon_dimension_in_display_units_and_relation_removal(system):
    system[0].apply_preferences(replace(system[0].preferences, length_unit="in", decimals=4))
    session = begin(system)
    choose(system, "circle")
    click(system, (10, 10))
    click(system, (20, 10))
    choose(system, "select")
    window = system[0]
    circle = session.document.objects[session.object_id].sketch.entities[0]
    session.interaction.set_selected((circle.id,))
    assert window.command_registry.execute("cadastrophe.sketch.dimension")
    p = session.constraint_panel
    assert p.kind.currentData() == "radius"
    assert p.value.suffix().strip() == "in"
    p.value.setValue(.5)
    QTest.mouseClick(p.add, Qt.MouseButton.LeftButton)
    assert not p.message.text()
    changed = session.document.objects[session.object_id].sketch
    assert changed.entities[0].radius == pytest.approx(12.7)
    assert "0.5000 in" in p.list.item(0).text()
    assert changed.entities[0].center == circle.center and "≈ 2" in p.diagnostic.text()
    p.list.setCurrentRow(0)
    p.kind.setCurrentIndex(p.kind.findData("diameter"))
    p.value.set_internal_value(50)
    QTest.mouseClick(p.modify, Qt.MouseButton.LeftButton)
    assert session.document.objects[session.object_id].sketch.entities[0].radius == pytest.approx(25)
    QTest.mouseClick(p.remove, Qt.MouseButton.LeftButton)
    assert not session.document.objects[session.object_id].sketch.constraints
    assert window.command_registry.execute("edit.undo")
    assert session.document.objects[session.object_id].sketch.constraints[0].kind == "diameter"


def test_conflicting_ui_constraint_reports_failure_without_geometry_or_history_change(system):
    session = draw_rectangle(system)
    p, doc = session.constraint_panel, session.document
    Ref = api(system).Reference
    edge = doc.objects[session.object_id].sketch.entities[0]
    add(p, "length", (Ref(edge.id),), 50)
    before, count = doc.objects[session.object_id], doc.history.count
    p.value.set_internal_value(80)
    QTest.mouseClick(p.add, Qt.MouseButton.LeftButton)
    assert "Échec" in p.message.text() and "Échec" in session.notice.label.text()
    assert doc.objects[session.object_id] is before and doc.history.count == count
    assert not session.interaction.pending and not session.surface.presentation.hidden_ids
    p.list.setCurrentRow(8)
    p.value.set_internal_value(80)
    QTest.mouseClick(p.modify, Qt.MouseButton.LeftButton)
    assert not p.message.text() and doc.history.count == count+1


def test_constrained_drag_defers_solver_until_release_and_escape_keeps_snapshot(system, monkeypatch):
    session = begin(system)
    _, commands, model, editor = system
    circle = model.Circle(model.Vec2(0, 0), 15)
    commands.add_entity(session.document, session.object_id, circle)
    c = api(system)
    commands.set_constraints(session.document, session.object_id, (c.Constraint("radius", (c.Reference(circle.id),), 15),))
    session.document.history.clear()
    session.interaction.set_selected((circle.id,))
    solver = import_module(editor.__package__ + ".solver")
    calls = []
    actual = solver.solve
    def record(*args, **kwargs):
        calls.append(True)
        return actual(*args, **kwargs)
    monkeypatch.setattr(solver, "solve", record)
    vp = system[0].viewport
    start, end = screen(system, (0, 0)), screen(system, (15, 20))
    before = session.document.objects[session.object_id].sketch
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=start)
    mouse_move(system, end)
    assert not calls and not session.constraint_panel.group.isEnabled()
    assert session.document.objects[session.object_id].sketch == before
    QTest.keyClick(vp, Qt.Key.Key_Escape)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=end)
    assert not calls and session.document.history.count == 0
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=start)
    mouse_move(system, end)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=end)
    assert len(calls) == 1 and session.document.history.count == 1
    changed = session.document.objects[session.object_id].sketch.entities[0]
    assert changed.center != circle.center and changed.radius == 15
