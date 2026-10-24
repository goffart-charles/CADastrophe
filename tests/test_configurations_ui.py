"""Native configuration actions, source scopes and transactional activation."""
from math import pi

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QInputDialog

from cadastrophe.core.configurations import create, activate, set_override
from test_extrusion_tool import extrusion_system
from test_boss_tool import base_body
from test_feature_edit import edit
from test_primitives import volume


def select(panel, key):
    panel.list.setCurrentRow(next(i for i in range(panel.list.count())
        if panel.list.item(i).data(Qt.ItemDataRole.UserRole) == key))


def test_configurations_ui_registry_crud_activation_scope_preview_and_reload(extrusion_system, monkeypatch, tmp_path):
    w, _, _ = extrusion_system
    doc = w.viewport.document
    body = base_body(extrusion_system)
    feature = doc.objects[body.features[0]]
    default = doc.active_configuration.id
    panel = w.manager_pane.configurations
    w.manager_pane.setCurrentIndex(2)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Épaisse", True))
    assert w.command_registry.execute("configuration.create")
    variant = doc.configurations[-1].id
    assert panel.selected_id() == variant
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Copie", True))
    assert w.command_registry.execute("configuration.duplicate")
    duplicate = panel.selected_id()
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Copie renommée", True))
    assert w.command_registry.execute("configuration.rename")
    assert doc.configuration_state.configuration(duplicate).name == "Copie renommée"
    assert w.command_registry.execute("configuration.delete")
    assert duplicate not in {c.id for c in doc.configurations}
    select(panel, variant)
    assert w.command_registry.execute("configuration.activate")
    assert doc.active_configuration.id == variant
    panel.apply_value(feature.id+":depth", "30")
    assert not panel.message.text()
    assert volume(doc.objects[body.id].shape) == pytest.approx(180000.)
    # Editing a common value must retain this configuration's local override
    # in the real preview as well as the committed native body.
    tool = edit(w, feature.id)
    tool.depth.setValue(25.)
    doc.set_parameter_scope("common")
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert volume(w.tool_sessions.preview_object.shape) == pytest.approx(180000.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert doc.objects[feature.id].parameters.depth == 30.
    activate(doc, default)
    assert doc.objects[feature.id].parameters.depth == 25.
    assert volume(doc.objects[body.id].shape) == pytest.approx(150000.)
    activate(doc, variant)
    w.manager_pane.setCurrentIndex(2)
    select(panel, variant)
    row = next(i for i in range(panel.overrides.rowCount())
        if panel.overrides.item(i, 0).data(Qt.ItemDataRole.UserRole) == feature.id+":depth")
    panel.overrides.setCurrentCell(row, 0)
    QTest.mouseClick(panel.restore, Qt.MouseButton.LeftButton)
    assert doc.objects[feature.id].parameters.depth == 25.
    doc.history.undo()
    assert doc.objects[feature.id].parameters.depth == 30.
    doc.history.redo()
    panel.validate()
    assert panel.message.text() == "Toutes les configurations sont valides."
    import os
    capture = os.environ.get("CADASTROPHE_CONFIGURATION_CAPTURE")
    if capture:
        QTest.qWait(100)
        assert w.grab().save(capture)
    path = tmp_path / "variantes.cada"
    assert w.save_document(path=path)
    assert w.close_document()
    restored = w.open_document(path)
    assert restored.active_configuration.id == variant
    assert restored.configuration_state == doc.configuration_state


def test_configurations_ui_failed_activation_reports_candidate_and_sketch_guard(extrusion_system):
    w, commands, m = extrusion_system
    doc = w.viewport.document
    body = base_body(extrusion_system)
    feature = doc.objects[body.features[0]]
    variant = create(doc, "Impossible")
    set_override(doc, variant, feature.id+":depth", -5.)
    panel = w.manager_pane.configurations
    select(panel, variant)
    before = dict(doc.objects), doc.configuration_state, doc.history.index
    assert w.command_registry.execute("configuration.activate")
    assert panel.message.text()
    assert (dict(doc.objects), doc.configuration_state, doc.history.index) == before
    xy = next(o for o in doc.objects.values() if o.plane and o.plane.name == "XY")
    sketch = commands.create_sketch(doc, xy.id)
    w.tree_selection_changed([sketch.id])
    assert w.command_registry.execute("cadastrophe.sketch.edit")
    assert doc.configuration_editing
    assert not w.command_registry.action("configuration.activate").isEnabled()
    with pytest.raises(RuntimeError, match="édition"):
        activate(doc, variant)
    assert w.tool_sessions.end_edit()
    assert not doc.configuration_editing


def test_configurations_ui_interbody_consumer_and_busy_tool(extrusion_system, tmp_path):
    from test_boss_tool import face_sketch
    from test_feature_activation import new_body, assert_solid
    from test_parametric_operations import bounds
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    w, _, _ = extrusion_system
    parent = base_body(extrusion_system)
    doc = w.viewport.document
    sketch = face_sketch(extrusion_system, parent)
    other = new_body(w, sketch, depth=5.)
    feature = doc.objects[parent.features[0]]
    producer = doc.objects[sketch.id].sketch.support.reference
    variant = create(doc, "Support haut")
    set_override(doc, variant, feature.id+":depth", 30.)
    before = dict(doc.objects), doc.configuration_state, doc.history.index
    edit(w, feature.id)
    with pytest.raises(RuntimeError):
        activate(doc, variant)
    assert not w.command_registry.action("configuration.activate").isEnabled()
    w.tool_sessions.cancel()
    assert (dict(doc.objects), doc.configuration_state, doc.history.index) == before
    for key, height in ((variant, 30.), (doc.default_configuration.id, 20.), (variant, 30.)):
        activate(doc, key)
        assert_solid(doc, parent.id, 6000.*height)
        assert_solid(doc, other.id, 125*pi)
        assert bounds(doc.objects[other.id].shape)[2] == pytest.approx(height)
        assert bounds(doc.objects[other.id].shape)[5] == pytest.approx(height+5.)
        assert doc.objects[sketch.id].sketch.support.reference == producer
    assert w.save_document(path=tmp_path/"intercorps.cada")
    assert w.close_document()
    restored = w.open_document(tmp_path/"intercorps.cada")
    assert restored.active_configuration.id == variant
    assert_solid(restored, other.id, 125*pi)
    assert BRepCheck_Analyzer(restored.objects[other.id].shape).IsValid()


def test_configurations_ui_step_exports_only_active_variant(extrusion_system, tmp_path):
    from plugins.import_export.dialog import ExchangeDialog
    from cadastrophe.document import ObjectType
    from shiboken6 import isValid
    w, _, _ = extrusion_system
    body = base_body(extrusion_system)
    doc = w.viewport.document
    variant = create(doc, "Export haut")
    set_override(doc, variant, body.features[0]+":depth", 30.)
    context = w.plugins._record("cadastrophe.modeling").context
    def exchange(mode, path):
        dialog = ExchangeDialog(context, mode, w)
        dialog.show()
        QTest.qWait(50)
        dialog.path.setText(str(path))
        if mode == "export":
            dialog.scope.setCurrentIndex(dialog.scope.findData("all"))
        try:
            dialog.start()
            for _ in range(2000):
                if not dialog._running:
                    break
                QTest.qWait(10)
            assert not dialog._running and not dialog.error_message, dialog.error_message
        finally:
            if isValid(dialog):
                dialog.close()
    for key, expected in ((doc.default_configuration.id, 120000.), (variant, 180000.)):
        w.documents.activate(doc.id)
        activate(doc, key)
        before = doc.configuration_state, doc.history.index
        output = tmp_path/(key+".step")
        exchange("export", output)
        assert (doc.configuration_state, doc.history.index) == before
        imported = w.create_document()
        QTest.qWait(150)
        exchange("import", output)
        solids = [o for o in imported.objects.values() if o.kind is ObjectType.BODY]
        assert len(solids) == 1
        assert volume(solids[0].shape) == pytest.approx(expected)


def test_configurations_ui_common_datum_preview_preserves_local_value(extrusion_system):
    from cadastrophe.core.reference_objects import DatumPlaneObject
    from test_parametric_operations_ui import choose
    w, _, _ = extrusion_system
    doc = w.viewport.document
    xy = next(o for o in doc.objects.values() if o.plane and o.plane.name == "XY")
    w.tree_selection_changed([xy.id])
    tool = choose(w, "cadastrophe.modeling.reference_plane")
    tool.distance.set_internal_value(5.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    datum, = (o for o in doc.objects.values() if isinstance(o, DatumPlaneObject))
    variant = create(doc, "Plan local")
    set_override(doc, variant, datum.id+":distance", 12.)
    activate(doc, variant)
    w.tree_selection_changed([datum.id])
    tool = choose(w, "cadastrophe.modeling.edit_plane")
    tool.distance.set_internal_value(8.)
    doc.set_parameter_scope("common")
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert w.tool_sessions.preview_object.plane.origin.z == pytest.approx(12.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert doc.objects[datum.id].parameters.distance == 12.
    assert doc.objects[datum.id].plane.origin.z == pytest.approx(12.)
    activate(doc, doc.default_configuration.id)
    assert doc.objects[datum.id].parameters.distance == 8.
    assert doc.objects[datum.id].plane.origin.z == pytest.approx(8.)
