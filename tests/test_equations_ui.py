"""Real Qt tools, bound numeric recipes, native B-Reps and round trips."""
from dataclasses import replace
import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from cadastrophe.core.expressions import Expression
from cadastrophe.core.feature_objects import BodyObject, ExtrusionFeature
from cadastrophe.core.persistence import load_document, save_document
from cadastrophe.ui.equations import EquationsDialog
from test_equations import variable
from test_extrusion_tool import extrusion_system, rectangle, start
from test_feature_edit import edit
from test_primitives import volume
from test_sketch_mode import system


def enter(widget, text):
    widget.setFocus()
    widget.lineEdit().selectAll()
    QTest.keyClicks(widget.lineEdit(), text)
    widget.interpretText()


def test_equations_native_extrusion_creation_edit_propagation_and_reopen(extrusion_system, tmp_path):
    w, _, _ = extrusion_system
    sketch = rectangle(extrusion_system)
    doc = w.viewport.document
    doc.set_variables((variable("epaisseur", "10"), variable("largeur", "100")))
    tool = start(w, sketch)
    enter(tool.depth, "=epaisseur*2")
    assert tool.depth.internal_value == 20
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert w.tool_sessions.commit(), w.tool_sessions.message
    body, = (o for o in doc.objects.values() if isinstance(o, BodyObject))
    feature, = (o for o in doc.objects.values() if isinstance(o, ExtrusionFeature))
    assert feature.expressions[0].text == "=epaisseur*2"
    assert volume(doc.objects[body.id].shape) == pytest.approx(120000)
    old_shape = doc.objects[body.id].shape
    doc.set_variables(tuple(replace(v, expression=Expression("value", "=123")) if v.name == "largeur" else v for v in doc.variables))
    assert doc.objects[body.id].shape is old_shape  # Unrelated variable: no geometry rebuild.
    doc.history.clear()
    doc.set_variables(tuple(replace(v, expression=Expression("value", "=15")) if v.name == "epaisseur" else v for v in doc.variables))
    assert volume(doc.objects[body.id].shape) == pytest.approx(180000)
    assert BRepCheck_Analyzer(doc.objects[body.id].shape).IsValid()
    assert doc.history.count == 1
    doc.history.undo()
    assert volume(doc.objects[body.id].shape) == pytest.approx(120000)
    doc.history.redo()
    before = dict(doc.objects)
    with pytest.raises(ValueError):
        doc.set_variables(tuple(replace(v, expression=Expression("value", "=-1")) if v.name == "epaisseur" else v for v in doc.variables))
    assert dict(doc.objects) == before
    tool = edit(w, feature.id)
    tool.depth.setFocus()
    QApplication.processEvents()
    assert "=epaisseur*2" in tool.depth.lineEdit().text()
    enter(tool.depth, "=epaisseur*3")
    w.tool_sessions.update_preview()
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert doc.objects[feature.id].parameters.depth == 45
    assert doc.objects[feature.id].expressions[0].text == "=epaisseur*3"
    path = tmp_path / "equations-3d.cada"
    save_document(doc, path, registry=w.object_types)
    loaded = load_document(path, registry=w.object_types)
    loaded.equation_evaluator = doc.equation_evaluator
    assert volume(loaded.objects[body.id].shape) == pytest.approx(270000)
    loaded.set_variables(tuple(replace(v, expression=Expression("value", "=5")) if v.name == "epaisseur" else v for v in loaded.variables))
    assert volume(loaded.objects[body.id].shape) == pytest.approx(90000)
    stable_objects, stable_count = dict(doc.objects), doc.history.count
    QTest.qWait(250)
    assert dict(doc.objects) == stable_objects and doc.history.count == stable_count


def test_equations_manager_edits_and_errors_leave_document_coherent(extrusion_system):
    w, _, _ = extrusion_system
    doc = w.viewport.document
    doc.set_variables((variable("largeur", "100"), variable("hauteur", "=largeur/2")))
    dialog = EquationsDialog(doc, w)
    dialog.show()
    assert dialog.table.columnCount() == 4
    dialog.table.item(0, 1).setText("=120")
    assert dialog.table.item(1, 2).text() == "60"
    snapshot = doc.variables
    dialog.table.item(0, 1).setText("=hauteur")
    assert "circulaire" in dialog.message.text()
    assert doc.variables == snapshot
    dialog.table.item(0, 1).setText("=140")
    assert not dialog.message.text()
    dialog.table.setCurrentCell(0, 0)
    dialog._remove()
    assert "absente" in dialog.message.text()
    assert len(doc.variables) == 2
    dialog.accept()


def test_equations_native_revolution_angle_and_unlink(extrusion_system):
    from cadastrophe.core.feature_objects import RevolutionFeature
    from cadastrophe.core.operation_model import GeometryReference as Ref
    from test_parametric_operations_ui import choose
    from math import pi
    w, commands, m = extrusion_system
    doc = w.viewport.document
    xy = next(o for o in doc.objects.values() if o.plane is not None and o.plane.name == "XY")
    source = commands.create_sketch(doc, xy.id)
    points = tuple(m.Vec2(*p) for p in ((5, -5), (10, -5), (10, 5), (5, 5)))
    axis = m.Segment(m.Vec2(0, -20), m.Vec2(0, 20), construction=True)
    commands.add_entities(doc, source.id, (*tuple(m.Segment(p, points[(i+1)%4]) for i, p in enumerate(points)), axis), title="Profil")
    doc.set_variables((replace(variable("angle", "90"), unit="angle"),))
    w.tree_selection_changed([source.id])
    tool = choose(w, "modeling.revolve")
    tool.axis.set_reference(Ref("sketch_line", source.id, entity_id=axis.id))
    enter(tool.angle, "=angle")
    assert w.tool_sessions.commit(), w.tool_sessions.message
    feature, = (o for o in doc.objects.values() if isinstance(o, RevolutionFeature))
    assert volume(doc.objects[feature.body_id].shape) == pytest.approx(750*pi/4)
    doc.set_variables((replace(doc.variables[0], expression=Expression("value", "=180")),))
    assert volume(doc.objects[feature.body_id].shape) == pytest.approx(750*pi/2)
    tool = edit(w, feature.id)
    enter(tool.angle, "45")
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert doc.objects[feature.id].expressions == ()
    assert volume(doc.objects[feature.body_id].shape) == pytest.approx(750*pi/8)


def test_equations_native_sketch_popup_and_variable_suggestions(system):
    from test_sketch_drawing_ui import begin
    from test_sketch_constraints_ui import api
    from PySide6.QtWidgets import QDialogButtonBox
    w, commands, m, _ = system
    doc = w.viewport.document
    doc.set_variables((variable("largeur", "100"),))
    session = begin(system)
    circle = m.Circle(m.Vec2(0, 0), 5.)
    commands.add_entities(doc, session.object_id, (circle,), title="Cercle")
    constraints = api(system)
    c = constraints.Constraint("radius", (constraints.Reference(circle.id),), 5.)
    tools = session.view_tools
    tools.open_editor(c, 100, 100, new=True)
    field = tools.value_editor
    assert "largeur — 100 mm" in field._completer.model().stringList()
    enter(field, "=largeur/4")
    tools.dialog.findChild(QDialogButtonBox).accepted.emit()
    QApplication.processEvents()
    obj = doc.objects[session.object_id]
    assert obj.sketch.entities[0].radius == pytest.approx(25.)
    assert obj.expressions[0].text == "=largeur/4"
    updated = obj.sketch.constraints[0]
    tools.open_editor(updated, 100, 100, new=False)
    assert tools.value_editor.equation_expression().text == "=largeur/4"
    tools.dialog.reject()


def pick_from_manager(field, label):
    """Use the real modal table opened by a dimension/feature editor."""
    errors = []
    def choose():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, EquationsDialog)
            assert dialog.table.columnCount() == 5
            row = next(row for row in range(dialog.table.rowCount()) if dialog.table.item(row, 0).text() == label)
            dialog.table.scrollToItem(dialog.table.item(row, 0))
            QTest.mouseClick(dialog.table.cellWidget(row, 4), Qt.MouseButton.LeftButton)
            assert dialog.result() == EquationsDialog.DialogCode.Accepted, dialog.message.text()
        except BaseException as exc:
            errors.append(exc)
            if dialog is not None:
                dialog.reject()
    QTimer.singleShot(50, choose)
    field._open_manager()
    assert not errors, errors


def test_equations_picker_uses_variable_and_dimension_in_native_function(extrusion_system, tmp_path):
    from importlib import import_module
    w, commands, _ = extrusion_system
    sketch = rectangle(extrusion_system)
    doc = w.viewport.document
    constraints = import_module(commands.__package__+".constraints")
    cote = constraints.Constraint("length", (constraints.Reference(sketch.sketch.entities[0].id),), 100.)
    edges = sketch.sketch.entities
    relations = (cote, *(constraints.Constraint("coincident", (constraints.Reference(e.id, "end"),
                           constraints.Reference(edges[(i+1)%4].id, "start"))) for i, e in enumerate(edges)),
                 *(constraints.Constraint("horizontal" if i%2 == 0 else "vertical", (constraints.Reference(e.id),))
                   for i, e in enumerate(edges)),
                 constraints.Constraint("length", (constraints.Reference(edges[1].id),), 60.),
                 constraints.Constraint("fixed", (constraints.Reference(edges[0].id, "start"),), edges[0].start))
    commands.set_constraints(doc, sketch.id, relations)
    doc.set_variables((variable("epaisseur", "5"),))
    snapshot, count = dict(doc.objects), doc.history.count
    tool = start(w, doc.objects[sketch.id])
    pick_from_manager(tool.depth, "epaisseur")
    assert tool.depth.internal_value == 5.
    assert tool.depth.equation_expression().text == "=epaisseur"
    pick_from_manager(tool.depth, f"D1@{sketch.name}")
    assert tool.depth.internal_value == 100.
    assert dict(doc.objects) == snapshot and doc.history.count == count
    assert w.tool_sessions.commit(), w.tool_sessions.message
    feature, = (o for o in doc.objects.values() if isinstance(o, ExtrusionFeature))
    assert feature.expressions[0].bindings == ((f"D1@{sketch.name}", sketch.id+":"+cote.id),)
    assert volume(doc.objects[feature.body_id].shape) == pytest.approx(600000)
    doc.rename(sketch.id, "Profil renommé")
    changed = tuple(replace(c, value=80.) if c.id == cote.id else c for c in doc.objects[sketch.id].sketch.constraints)
    commands.set_constraints(doc, sketch.id, changed)
    assert doc.objects[feature.id].parameters.depth == pytest.approx(80.)
    assert volume(doc.objects[feature.body_id].shape) == pytest.approx(384000)
    path = tmp_path/"reference-choisie.cada"
    save_document(doc, path, registry=w.object_types)
    loaded = load_document(path, registry=w.object_types)
    assert loaded.objects[feature.id].expressions == doc.objects[feature.id].expressions


def test_equations_picker_in_sketch_cotation_keeps_uncommitted_choice(system):
    from test_sketch_drawing_ui import begin
    from test_sketch_constraints_ui import api
    from PySide6.QtWidgets import QDialogButtonBox
    w, commands, m, _ = system
    doc = w.viewport.document
    doc.set_variables((variable("rayon", "12"),))
    session = begin(system)
    circle = m.Circle(m.Vec2(0, 0), 5.)
    commands.add_entities(doc, session.object_id, (circle,), title="Cercle")
    constraints = api(system)
    cote = constraints.Constraint("radius", (constraints.Reference(circle.id),), 5.)
    tools = session.view_tools
    snapshot, count = dict(doc.objects), doc.history.count
    tools.open_editor(cote, 100, 100, new=True)
    pick_from_manager(tools.value_editor, "rayon")
    assert tools.value_editor.internal_value == 12.
    assert dict(doc.objects) == snapshot and doc.history.count == count
    tools.dialog.findChild(QDialogButtonBox).accepted.emit()
    QApplication.processEvents()
    assert doc.objects[session.object_id].sketch.entities[0].radius == pytest.approx(12.)
    assert doc.objects[session.object_id].expressions[0].text == "=rayon"


def test_equations_picker_rejects_self_reference_and_incompatible_units(extrusion_system):
    w, commands, _ = extrusion_system
    sketch = rectangle(extrusion_system)
    tool = start(w, sketch)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    doc = w.viewport.document
    feature, = (o for o in doc.objects.values() if isinstance(o, ExtrusionFeature))
    doc.set_variables((replace(variable("angle", "30"), unit="angle"),))
    tool = edit(w, feature.id)
    dialog = EquationsDialog(doc, w, choose=tool.depth._choose_reference)
    dialog.setReadOnly(True)
    dialog.show()
    before = tool.depth.internal_value, tool.depth.equation_expression(), dict(doc.objects)
    row = next(i for i in range(dialog.table.rowCount()) if dialog.table.item(i, 0).text() == f"D1@{feature.name}")
    QTest.mouseClick(dialog.table.cellWidget(row, 4), Qt.MouseButton.LeftButton)
    assert "circulaire" in dialog.message.text() and dialog.isVisible()
    QTest.mouseClick(dialog.table.cellWidget(0, 4), Qt.MouseButton.LeftButton)
    assert "Unité" in dialog.message.text() and dialog.isVisible()
    assert (tool.depth.internal_value, tool.depth.equation_expression(), dict(doc.objects)) == before
    dialog.reject()


def test_equations_picker_refresh_has_no_orphan_buttons_and_double_click_edits(extrusion_system):
    from PySide6.QtWidgets import QPushButton, QLineEdit
    w, _, _ = extrusion_system
    doc = w.viewport.document
    doc.set_variables((variable("largeur", "12"),))
    variable_id = doc.variables[0].id
    chosen, errors = [], []
    dialog = EquationsDialog(doc, w, choose=chosen.append)
    dialog.setReadOnly(False)  # Reproduce refresh before entering the modal event loop.
    def inspect():
        try:
            def check_buttons():
                QApplication.processEvents()
                buttons = [dialog.table.cellWidget(row, 4) for row in range(dialog.table.rowCount())]
                visible = [b for b in dialog.table.findChildren(QPushButton) if b.isVisible()]
                assert len(visible) == len(buttons) and set(visible) == set(buttons)
                for row, button in enumerate(buttons):
                    rect = dialog.table.visualRect(dialog.table.model().index(row, 4))
                    assert rect.contains(button.geometry().center())
            check_buttons()
            QTest.mouseClick(dialog.add_button, Qt.MouseButton.LeftButton)
            assert len(doc.variables) == 2
            check_buttons()
            item = dialog.table.item(0, 0)
            at = dialog.table.visualItemRect(item).center()
            QTest.mouseClick(dialog.table.viewport(), Qt.MouseButton.LeftButton, pos=at)
            QTest.mouseDClick(dialog.table.viewport(), Qt.MouseButton.LeftButton, pos=at)
            assert dialog.isVisible() and not chosen
            editor = QApplication.focusWidget()
            assert isinstance(editor, QLineEdit) and dialog.table.isAncestorOf(editor)
            editor.selectAll()
            QTest.keyClicks(editor, "largeur_modifiee")
            QTest.keyClick(editor, Qt.Key.Key_Return)
            QApplication.processEvents()  # The delegate queues commitData on Return.
            assert doc.variables[0].name == "largeur_modifiee", dialog.message.text()
            check_buttons()
            QTest.mouseClick(dialog.table.cellWidget(0, 4), Qt.MouseButton.LeftButton)
            assert [node.id for node in chosen] == [variable_id]
        except BaseException as exc:
            errors.append(exc)
        finally:
            if dialog.isVisible():
                dialog.reject()
    QTimer.singleShot(50, inspect)
    dialog.exec()
    assert not errors, errors
