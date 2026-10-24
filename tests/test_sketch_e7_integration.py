"""E7 acceptance through Qt, documents, plugin API and actual OCCT topology."""
from dataclasses import replace
from importlib import import_module
from pathlib import Path
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from test_sketch_mode import system,enter
from test_sketch_drawing_ui import choose,click,move
from test_sketch_constraints_ui import draw_rectangle
from test_sketch_interaction_polish import clean_scene,current
from test_sketch_view_tools import accept_value


SERVICE = "cadastrophe.sketch.profiles"


@pytest.mark.parametrize("plane",["XY","XZ","YZ"])
def test_e7_mouse_rectangle_dimension_save_reload_analyze_and_export_valid_wire(system,tmp_path,plane):
    clean_scene(system)
    s = draw_rectangle(system,plane)
    window = system[0]
    doc,key = s.document,s.object_id
    before = current(s)
    height = before.entities[1].end.v-before.entities[1].start.v
    choose(system,"dimension")
    for e in (before.entities[1],before.entities[3]):
        click(system,((e.start.u+e.end.u)/2,(e.start.v+e.end.v)/2))
        assert s.view_tools.dialog is None
    click(system,(0,30))
    accept_value(s,60)
    snapshot = current(s)
    count = doc.history.count
    assert window.command_registry.execute("cadastrophe.sketch.repair")
    report = s.profile_panel.report
    assert report.valid and report.contours[0].signed_area == pytest.approx(60*height)
    assert doc.history.count == count and current(s) == snapshot
    path = tmp_path/f"e7-{plane}.cada"
    assert window.save_document(path=path)
    assert window.close_document()
    loaded = window.open_document(path)
    QApplication.processEvents()
    service = window.services.service(SERVICE)
    report = service.analyze(loaded,key)
    assert report.valid and loaded.objects[key].sketch == snapshot
    history = loaded.history.count
    output = service.to_native(loaded,key,analysis=report)
    assert len(output.contours) == 1
    assert BRep_Tool.IsClosed(output.contours[0].wire)
    assert BRepCheck_Analyzer(output.contours[0].wire,True).IsValid()
    assert loaded.history.count == history and not loaded.is_modified
    enter(system,loaded.objects[key])
    window.command_registry.execute("cadastrophe.sketch.repair")
    assert window.tool_sessions.editor.profile_panel.report.valid


def test_analysis_builds_no_native_wires_and_stale_results_cannot_be_exported(system,monkeypatch):
    from OCC.Core import BRepBuilderAPI
    clean_scene(system)
    s = draw_rectangle(system)
    window,commands,model,_ = system
    service = window.services.service(SERVICE)
    actual = BRepBuilderAPI.BRepBuilderAPI_MakeWire
    def forbidden(*args,**kwargs):
        pytest.fail("L’analyse ne doit pas construire de fil natif.")
    monkeypatch.setattr(BRepBuilderAPI,"BRepBuilderAPI_MakeWire",forbidden)
    report = service.analyze(s.document,s.object_id)
    assert report.valid
    s.check_profiles()
    monkeypatch.setattr(BRepBuilderAPI,"BRepBuilderAPI_MakeWire",actual)
    assert service.to_native(s.document,s.object_id,analysis=report).contours
    commands.add_entity(s.document,s.object_id,model.Point(model.Vec2(50,50)))
    assert s.profile_panel.report is None
    with pytest.raises(ValueError,match="périmée"):
        service.to_native(s.document,s.object_id,analysis=report)


def test_open_contour_diagnostics_highlight_source_and_undo_redo_refresh_report(system):
    clean_scene(system)
    s = draw_rectangle(system)
    window,commands,_,_ = system
    before = current(s)
    s.check_profiles()
    assert s.profile_panel.report.valid
    commands.remove_entity(s.document,s.object_id,before.entities[-1].id)
    report = s.check_profiles()
    assert not report.valid and s.profile_panel.results.count() == 2
    count = s.document.history.count
    item = s.profile_panel.results.item(0)
    s.profile_panel.highlight(item)
    assert s.interaction.selected and s.document.history.count == count
    assert s.profile_panel.isVisible() and s.context_title.text() == "Vérifier l’esquisse"
    window.command_registry.execute("edit.undo")
    assert s.check_profiles().valid and current(s) == before
    window.command_registry.execute("edit.redo")
    assert not s.check_profiles().valid


def test_multiple_sketches_documents_pending_tools_and_solver_error_recover_cleanly(system):
    clean_scene(system)
    s = draw_rectangle(system)
    window,commands,model,editor = system
    first,key,snapshot = s.document,s.object_id,current(s)
    api = import_module(editor.__package__+".constraints")
    # A rejected dimension cannot corrupt a later profile check.
    a = snapshot.entities[0]
    fixed = api.Constraint("fixed",(api.Reference(a.id,"start"),),a.start)
    fixed_end = api.Constraint("fixed",(api.Reference(a.id,"end"),),a.end)
    commands.set_constraints(first,key,(*snapshot.constraints,fixed,fixed_end))
    valid = current(s)
    with pytest.raises(ValueError):
        commands.set_constraints(first,key,(*valid.constraints,api.Constraint("length",(api.Reference(a.id),),1000)))
    assert current(s) == valid and s.check_profiles().valid
    notice = s.notice
    choose(system,"line")
    click(system,(0,25))
    move(system,(20,25))
    surface,preview = s.surface,s.renderer
    window.create_document()
    QApplication.processEvents()
    window.viewport.camera_animation_duration = 0  # Same settled-camera fixture as the first document.
    window.viewport.view.Camera().SetScale(100)  # Empty-document fit differs from the demonstration scene.
    second = window.viewport.document
    assert not window.tool_sessions.active and surface.closed and not preview.entities
    assert not notice.isVisible() and s.notice is None
    assert first.objects[key].sketch == valid
    other = draw_rectangle(system,"YZ")
    second_key = other.object_id
    assert other.check_profiles().valid
    choose(system,"finish")
    window.documents.activate(first.id)
    QApplication.processEvents()
    assert window.services.active_document is first and not window.viewport.selection.suspended
    enter(system,first.objects[key])
    assert window.tool_sessions.editor.check_profiles().valid
    service = window.services.service(SERVICE)
    assert service.to_native(first,key).contours
    assert service.to_native(second,second_key).contours
    assert first.objects[key].sketch == valid


def test_profile_service_rejects_unresolved_support_and_is_released_on_plugin_unload(system):
    clean_scene(system)
    s = draw_rectangle(system)
    window = system[0]
    doc,key = s.document,s.object_id
    service = window.services.service(SERVICE)
    choose(system,"finish")
    obj = doc.objects[key]
    support = obj.sketch.support
    doc.replace_feature(replace(obj,sketch=replace(obj.sketch,support=replace(support,revision=support.revision+1))))
    assert not service.analyze(doc,key).valid
    with pytest.raises(ValueError,match="non résolu"):
        service.to_native(doc,key)
    window.confirm_discard = lambda document: True
    assert window.close_document()
    assert window.plugins.deactivate("cadastrophe.sketch")
    with pytest.raises(LookupError):
        window.services.service(SERVICE)
    with pytest.raises(RuntimeError,match="fermé"):
        service.analyze(doc,key)


def test_propertymanager_and_profile_results_capture_for_review(system):
    clean_scene(system)
    s = draw_rectangle(system)
    s.check_profiles()
    QApplication.processEvents()
    root = Path(__file__).resolve().parents[1]/"artifacts"
    assert system[0].tool_host.panel.grab().save(str(root/"e7-propertymanager.png"))
    s.report_error("Déplacement bloqué : la position est entièrement contrainte.")
    QApplication.processEvents()
    assert system[0].viewport.view.Dump(str(root/"e7-native-viewport.png"))
    assert s.notice.grab().save(str(root/"e7-viewport-notice.png"))
    assert system[0].grab().save(str(root/"e7-shell.png"))


def test_two_sketches_in_one_document_keep_separate_profiles_and_histories(system):
    clean_scene(system)
    first = draw_rectangle(system)
    doc,key,snapshot = first.document,first.object_id,current(first)
    choose(system,"finish")
    other = draw_rectangle(system,"XZ")
    other_key = other.object_id
    assert other_key != key and other.check_profiles().valid
    choose(system,"finish")
    service = system[0].services.service(SERVICE)
    assert service.to_native(doc,key).contours and service.to_native(doc,other_key).contours
    enter(system,doc.objects[key])
    active = system[0].tool_sessions.editor
    choose(system,"circle")
    click(system,(0,0))
    QTest.keyClick(system[0].viewport,Qt.Key.Key_Escape)
    assert not active.draft.anchors and current(active) == snapshot
    assert active.check_profiles().valid
    QTest.keyClick(system[0].viewport,Qt.Key.Key_Escape)
    assert not active.checking_profiles and system[0].tool_sessions.editing
    active.check_profiles()
    choose(system,"dimension")
    assert not active.checking_profiles and active.constraint_panel.isVisible()
    assert active.context_title.text() == "Cotes au clic"
    active.check_profiles()
    active.view_tools.start(True)  # Floating relation toolbar uses this entry point directly.
    assert not active.checking_profiles and active.constraint_panel.isVisible()
    assert active.context_title.text() == "Cotes au clic"
