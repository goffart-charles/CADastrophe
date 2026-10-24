"""Actual File/Export dialog, isolated native workers and output protection."""
from pathlib import Path
from zipfile import ZipFile

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from cadastrophe.document import SceneObject
from test_import_export_ui import window, plugin, wait_until, pose, PLUGIN_ID
from test_mesh_exports import model, read_gltf


@pytest.mark.parametrize("format_key",["stl","stl_ascii","step","obj","3mf","glb","gltf"])
def test_file_export_dialog_real_formats_custom_objects_and_unchanged_document(window,model,tmp_path,format_key):
    doc = window.services.active_document
    obj = SceneObject("Support percé",model,(.2,.6,.8))
    doc.add(obj)
    doc.set_visible(obj.id,False)
    doc.select_objects(())
    snapshot = dict(doc.objects)
    state = doc.history.count,doc.history.index,doc.is_modified,doc.selected_ids,pose(window.viewport)
    assert window.command_registry.execute("file.export")
    dialog = plugin(window).dialog
    assert dialog is not None and dialog.format.currentData() == "stl"
    assert dialog.stl_encoding.currentData() is True
    key = "stl" if format_key == "stl_ascii" else format_key
    dialog.format.setCurrentIndex(dialog.format.findData(key))
    dialog.scope.setCurrentIndex(2)
    for i in range(dialog.object_list.count()):
        item = dialog.object_list.item(i)
        item.setCheckState(Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole) == obj.id else Qt.CheckState.Unchecked)
    assert dialog.scope.currentData() == "custom"
    assert dialog.mesh_options.isVisible() == (key != "step")
    if key == "stl":
        dialog.stl_encoding.setCurrentIndex(1 if format_key == "stl_ascii" else 0)
        dialog.quality.setCurrentIndex(dialog.quality.findData("custom"))
        assert dialog.custom_options.isVisible()
        dialog.deflection.setValue(.05)
        dialog.angular.setValue(10)
    target = tmp_path/("support épreuve."+key)
    dialog.path.setText(str(target))
    if key == "stl" and format_key != "stl_ascii":
        Path("artifacts").mkdir(exist_ok=True)
        assert dialog.grab().save("artifacts/export-multiformats.png")
    dialog.start()
    assert not dialog.format.isEnabled() and not dialog.object_list.isEnabled()
    wait_until(lambda: plugin(window).dialog is None or bool(dialog.error_message))
    assert plugin(window).dialog is None,dialog.error_message
    assert target.is_file() and target.stat().st_size > 100
    if key == "step":
        text = target.read_text(encoding="ascii")
        assert text.startswith("ISO-10303-21;") and text.count("MANIFOLD_SOLID_BREP") == 1
    elif key == "stl":
        assert target.read_bytes().startswith(b"solid ") == (format_key == "stl_ascii")
    elif key == "obj":
        text = target.read_text(encoding="utf-8")
        assert "o Support percé" in text and "\nvn " in text
        assert text.count("\no ") == 1
    elif key == "3mf":
        with ZipFile(target) as archive:
            assert b"Support perc" in archive.read("3D/3dmodel.model")
    else:
        data,_,_ = read_gltf(target)
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["extras"]["cadastropheObjectId"] == obj.id
    assert dict(doc.objects) == snapshot
    assert (doc.history.count,doc.history.index,doc.is_modified,doc.selected_ids,pose(window.viewport)) == state
    assert not window.documents.blocked


def test_format_changes_extensions_empty_checks_and_invalid_destination(window,tmp_path):
    assert window.command_registry.execute("file.export")
    dialog = plugin(window).dialog
    dialog.path.setText(str(tmp_path/"part.stl"))
    dialog.quality.setCurrentIndex(dialog.quality.findData("custom"))
    dialog.deflection.lineEdit().setText("invalid")
    dialog.format.setCurrentIndex(dialog.format.findData("step"))
    assert dialog.path.text().endswith(".step") and dialog.unit.isVisible()
    dialog.path.setText(str(tmp_path/"part.stp"))
    dialog.start()
    wait_until(lambda: plugin(window).dialog is None or bool(dialog.error_message))
    assert plugin(window).dialog is None,dialog.error_message
    assert (tmp_path/"part.stp").is_file()
    assert window.command_registry.execute("file.export")
    dialog = plugin(window).dialog
    dialog.scope.setCurrentIndex(2)
    for i in range(dialog.object_list.count()):
        dialog.object_list.item(i).setCheckState(Qt.CheckState.Unchecked)
    dialog.path.setText(str(tmp_path/"empty.stl"))
    dialog.start()
    assert "Aucun corps" in dialog.error_message and not (tmp_path/"empty.stl").exists()
    dialog.scope.setCurrentIndex(0)
    dialog.path.setText(str(tmp_path/"wrong.xyz"))
    dialog.start()
    assert "extension" in dialog.error_message
    dialog.path.setText(str(tmp_path/"without_suffix"))
    dialog.start()
    wait_until(lambda: plugin(window).dialog is None or bool(dialog.error_message))
    assert plugin(window).dialog is None,dialog.error_message
    assert (tmp_path/"without_suffix.stl").is_file()


def test_mesh_export_failure_preserves_existing_destination_and_can_retry(window,tmp_path,monkeypatch):
    assert window.command_registry.execute("file.export")
    dialog = plugin(window).dialog
    target = tmp_path/"keep.stl"
    target.write_bytes(b"existing-file")
    dialog.path.setText(str(target))
    monkeypatch.setattr(QMessageBox,"question",lambda *args:QMessageBox.StandardButton.Yes)
    # Real worker validation failure, not a mocked writer or fake exported file.
    original = dialog._export_options
    dialog._export_options = lambda: {**original(),"quality":"invalid"}
    dialog.start()
    wait_until(lambda: bool(dialog.error_message))
    assert "Qualité" in dialog.error_message
    assert target.read_bytes() == b"existing-file" and dialog._output is None
    assert not list(tmp_path.glob(".cadastrophe-export-*"))
    assert dialog.start_button.isEnabled() and window.documents.blocked
    dialog._export_options = original
    dialog.start()
    wait_until(lambda: plugin(window).dialog is None or bool(dialog.error_message))
    assert plugin(window).dialog is None,dialog.error_message
    assert target.read_bytes().startswith(b"CADastrophe STL")


def test_cancel_mesh_worker_keeps_destination_and_document(window,tmp_path,monkeypatch):
    doc = window.services.active_document
    snapshot = dict(doc.objects),doc.history.count,doc.selected_ids,doc.is_modified
    assert window.command_registry.execute("file.export")
    dialog = plugin(window).dialog
    target = tmp_path/"cancel.glb"
    target.write_bytes(b"previous")
    dialog.format.setCurrentIndex(dialog.format.findData("glb"))
    dialog.path.setText(str(target))
    monkeypatch.setattr(QMessageBox,"question",lambda *args:QMessageBox.StandardButton.Yes)
    worker = next(r.path for r in window.plugins.records if r.manifest.id == PLUGIN_ID)/"worker.py"
    worker.write_text("import time\ntime.sleep(10)\n",encoding="utf-8")
    dialog.start()
    wait_until(lambda: dialog.process.processId() != 0)
    dialog.close()
    wait_until(lambda: plugin(window).dialog is None)
    assert target.read_bytes() == b"previous" and not window.documents.blocked
    assert (dict(doc.objects),doc.history.count,doc.selected_ids,doc.is_modified) == snapshot
    assert not list(tmp_path.glob(".cadastrophe-export-*"))
