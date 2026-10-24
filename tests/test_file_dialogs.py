"""Shared file picker policy, real Qt dialogs, persistence and native Windows UI."""
from pathlib import Path
import sys

import pytest
from PySide6.QtCore import QTimer, QCoreApplication
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QWidget

from cadastrophe.app import MainWindow
from cadastrophe.core.preferences import PreferenceStore, Preferences, validate_layout
from cadastrophe.core.persistence import FILE_FILTER, load_document
from cadastrophe.ui.file_dialogs import FileDialogs, file_error_text, install_french_translations
from test_import_export_ui import window, plugin, wait_until


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def fallback(service,monkeypatch):
    create = service.create_dialog
    def factory(*args,**kwargs):
        dialog = create(*args,**kwargs)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog)
        return dialog
    monkeypatch.setattr(service,"create_dialog",factory)


def test_first_run_creates_system_documents_folder_and_missing_last_folder_falls_back(app,tmp_path,monkeypatch):
    location = tmp_path/"Documents redirigés é"
    monkeypatch.setattr("cadastrophe.ui.file_dialogs.documents_directory",lambda:location)
    assert not (location/"CADastrophe").exists()
    service = FileDialogs({"project":str(tmp_path/"supprimé")})
    assert service.default_directory == location/"CADastrophe"
    assert service.default_directory.is_dir()
    assert service.initial_directory("project") == service.default_directory
    assert service.initial_directory("export",writing=True) == service.default_directory
    service.default_directory.rmdir()
    assert service.initial_directory("project").is_dir()


def test_inaccessible_folder_and_creation_error_have_an_accessible_fallback(app,tmp_path,monkeypatch):
    from cadastrophe.ui import file_dialogs
    remembered = tmp_path/"inaccessible"
    remembered.mkdir()
    service = FileDialogs({"export":str(remembered)})
    check = file_dialogs.accessible_directory
    monkeypatch.setattr(file_dialogs,"accessible_directory",lambda p,**kw:False if p == remembered else check(p,**kw))
    assert service.initial_directory("export",writing=True) == service.default_directory
    blocked = tmp_path/"Documents"
    blocked.write_text("Un fichier empêche la création du dossier.")
    monkeypatch.setattr(file_dialogs,"documents_directory",lambda:blocked)
    service = FileDialogs()
    assert service.startup_warning and "créé" in service.startup_warning
    messages = []
    service.warning.connect(messages.append)
    assert service.initial_directory("project").is_dir() and messages


@pytest.mark.parametrize("mode,button",[("open","Ouvrir"),("save","Enregistrer"),("directory","Sélectionner")])
def test_native_preference_french_labels_and_compatible_filters(app,mode,button):
    service = FileDialogs()
    dialog = service.create_dialog(None,purpose="project",title="Choisir un projet CADastrophe",name_filter=FILE_FILTER,
                                   save=mode == "save",directory=mode == "directory",suffix=".cada")
    try:
        assert not dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
        assert not dialog.testOption(QFileDialog.Option.DontConfirmOverwrite)
        assert dialog.labelText(QFileDialog.DialogLabel.Accept) == button
        assert dialog.labelText(QFileDialog.DialogLabel.Reject) == "Annuler"
        assert dialog.defaultSuffix() == "cada" and dialog.nameFilters() == [FILE_FILTER]
        assert dialog.directory().absolutePath() == str(service.default_directory).replace("\\","/")
        assert install_french_translations(app)
        assert QCoreApplication.translate("QFileDialog","Look in:") != "Look in:"
    finally:
        dialog.deleteLater()


def test_save_as_keeps_name_and_cancel_has_no_document_or_preference_effect(window,tmp_path,monkeypatch):
    doc = window.services.active_document
    target = tmp_path/"projet original é.cada"
    assert window.save_document(path=target)
    doc.rename(next(o.id for o in doc.objects.values() if o.plane is None),"Modifié")
    before = dict(doc.objects),doc.file_path,doc.history.count,doc.is_modified
    locations = dict(window.file_dialogs.directories)
    fallback(window.file_dialogs,monkeypatch)
    seen = []
    def reject():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog,QFileDialog)
        seen.append(Path(dialog.selectedFiles()[0]).name)
        assert dialog.defaultSuffix() == "cada"
        Path("artifacts").mkdir(exist_ok=True)
        assert dialog.grab().save("artifacts/file-dialog-fr.png")
        dialog.reject()
    QTimer.singleShot(0,reject)
    assert not window.save_document(save_as=True)
    assert seen == [target.name]
    assert (dict(doc.objects),doc.file_path,doc.history.count,doc.is_modified) == before
    assert window.file_dialogs.directories == locations


def test_current_cada_filter_and_save_as_suggestion(window, tmp_path, monkeypatch):
    from cadastrophe.core.persistence import FILE_FILTER, SAVE_FILE_FILTER, EXTENSION
    assert EXTENSION == ".cada"
    assert "*.cada" in FILE_FILTER and "*.caop" not in FILE_FILTER
    assert "*.caop" not in SAVE_FILE_FILTER
    legacy = tmp_path/"ancien projet.cada"
    assert window.save_document(path=legacy)
    snapshot = legacy.read_bytes()
    assert window.close_document()
    doc = window.open_document(legacy)
    assert doc is not None and doc.file_path == legacy
    seen = []
    def choose(parent, **options):
        seen.append(options)
        return None
    monkeypatch.setattr(window.file_dialogs, "choose", choose)
    assert window.choose_save_path(doc) is None
    assert seen[0]["suggested_name"] == "ancien projet.cada"
    assert seen[0]["suffix"] == ".cada" and seen[0]["name_filter"] == SAVE_FILE_FILTER
    current = tmp_path/"copie.cada"
    assert window.save_document(path=current, save_as=True)
    assert legacy.read_bytes() == snapshot
    assert window.close_document()
    loaded = window.open_document(current)
    assert loaded.id == doc.id and loaded.file_path == current


def test_project_and_export_locations_persist_across_restart_without_changing_project_path(app,tmp_path):
    settings = tmp_path/"préférences.json"
    root = tmp_path/"plugins"
    root.mkdir()
    first = MainWindow(plugin_directory=root,preferences_path=settings)
    first.show()
    app.processEvents()
    project = tmp_path/"projets é"/"dessin.cada"
    exports = tmp_path/"exports pièces"
    project.parent.mkdir()
    exports.mkdir()
    doc = first.services.active_document
    assert first.save_document(path=project)
    first.file_dialogs.remember("export",exports)
    assert doc.file_path == project
    # Preferences are persisted immediately, before closing the application.
    assert PreferenceStore(settings).load()[1]["file_directories"] == {"project":str(project.parent),"export":str(exports)}
    first.confirm_discard = lambda document:True
    assert first.close()
    first.deleteLater()
    second = MainWindow(plugin_directory=root,preferences_path=settings)
    try:
        assert second.file_dialogs.initial_directory("project") == project.parent
        assert second.file_dialogs.initial_directory("export") == exports
        assert second.open_document(project).file_path == project
        exports.rmdir()
        assert second.file_dialogs.initial_directory("export") == second.file_dialogs.default_directory
    finally:
        second.confirm_discard = lambda document:True
        second.close()
        second.deleteLater()


def test_save_as_overwrite_decline_then_accept_and_normal_save_does_not_ask(window,tmp_path,monkeypatch):
    doc = window.services.active_document
    path,existing = tmp_path/"current.cada",tmp_path/"existing.cada"
    assert window.save_document(path=path)
    existing.write_bytes(b"previous file")
    asked = []
    def decline(*args):
        asked.append(args[1:3])
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox,"question",decline)
    assert not window.save_document(path=existing,save_as=True)
    assert existing.read_bytes() == b"previous file" and doc.file_path == path
    assert asked and "Remplacer" in asked[0][0]
    assert window.save_document() and len(asked) == 1
    monkeypatch.setattr(QMessageBox,"question",lambda *args:QMessageBox.StandardButton.Yes)
    assert window.save_document(path=existing,save_as=True)
    assert load_document(existing).id == doc.id and doc.file_path == existing


def test_save_picker_confirms_overwrite_once_and_remembers_approval(window,tmp_path,monkeypatch):
    existing = tmp_path/"déjà présent.cada"
    existing.write_bytes(b"old")
    service = window.file_dialogs
    fallback(service,monkeypatch)
    confirmations = []
    def accept_overwrite():
        box = QApplication.activeModalWidget()
        assert isinstance(box,QMessageBox)
        confirmations.append(box.text())
        box.button(QMessageBox.StandardButton.Yes).click()
    def accept_save():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog,QFileDialog)
        dialog.setDirectory(str(tmp_path))
        dialog.selectFile(existing.name)
        QTimer.singleShot(0,accept_overwrite)
        dialog.accept()
    # QFileDialog's C++ confirmation remains real; a second Python-level prompt
    # from the caller would be a regression and block the single-action workflow.
    monkeypatch.setattr(QMessageBox,"question",lambda *args:pytest.fail("Double confirmation d’écrasement"))
    QTimer.singleShot(0,accept_save)
    assert window.save_document(save_as=True)
    assert len(confirmations) == 1 and load_document(existing).id == window.services.active_document.id


@pytest.mark.parametrize("failure",["permission","missing"])
def test_project_write_errors_are_french_and_preserve_document_and_existing_file(window,tmp_path,monkeypatch,failure):
    import cadastrophe.app as application
    doc = window.services.active_document
    original = tmp_path/"original.cada"
    assert window.save_document(path=original)
    doc.rename(next(o.id for o in doc.objects.values() if o.plane is None),"Modifications")
    before = dict(doc.objects),doc.file_path,doc.history.count,doc.is_modified,original.read_bytes()
    errors = []
    monkeypatch.setattr(QMessageBox,"warning",lambda parent,title,text:errors.append((title,text)))
    if failure == "permission":
        def deny(*args,**kwargs):
            raise PermissionError(13,"Permission denied",str(tmp_path/"refusé.cada"))
        monkeypatch.setattr(application,"save_document",deny)
        target = tmp_path/"refusé.cada"
    else:
        target = tmp_path/"supprimé"/"fichier.cada"
    assert not window.save_document(path=target,save_as=True)
    assert errors and ("Accès refusé" if failure == "permission" else "n’existe plus") in errors[-1][1]
    assert (dict(doc.objects),doc.file_path,doc.history.count,doc.is_modified,original.read_bytes()) == before


def test_directory_picker_is_french_and_remembers_only_accepted_selection(app,tmp_path,monkeypatch):
    service = FileDialogs()
    fallback(service,monkeypatch)
    chosen = tmp_path/"dossier é & pièces"
    chosen.mkdir()
    def accept():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog,QFileDialog)
        assert dialog.labelText(QFileDialog.DialogLabel.Accept) == "Sélectionner"
        dialog.setDirectory(str(chosen))
        dialog.accept()
    QTimer.singleShot(0,accept)
    assert service.choose_directory() == chosen
    assert service.directories["directory"] == str(chosen)
    before = dict(service.directories)
    QTimer.singleShot(0,lambda:QApplication.activeModalWidget().reject())
    assert service.choose_directory() is None and service.directories == before


@pytest.mark.parametrize("key",["stl","step","obj","3mf","glb","gltf"])
def test_export_browser_uses_common_native_service_and_correct_filter(window,tmp_path,monkeypatch,key):
    doc = window.services.active_document
    project = tmp_path/"projet é.cada"
    assert window.save_document(path=project)
    assert window.command_registry.execute("file.export")
    exchange = plugin(window).dialog
    exchange.format.setCurrentIndex(exchange.format.findData(key))
    service = window.file_dialogs
    assert exchange.context.services.file_dialogs is service
    fallback(service,monkeypatch)
    seen = []
    def accept():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog,QFileDialog)
        assert dialog.windowTitle() == "Exporter un fichier"
        assert Path(dialog.selectedFiles()[0]).name == project.stem+"."+key
        assert dialog.defaultSuffix() == key
        assert dialog.nameFilters() == [exchange.exports.get(key).label+" ("+" ".join("*"+ext for ext in exchange.exports.get(key).extensions)+")"]
        seen.append(True)
        dialog.selectFile("export épreuve")
        dialog.accept()
    QTimer.singleShot(0,accept)
    exchange.choose_path()
    target = service.default_directory/("export épreuve."+key)
    assert Path(exchange.path.text()) == target and seen
    assert service.initial_directory("export") == target.parent
    assert doc.file_path == project
    exchange.close()
    wait_until(lambda:plugin(window).dialog is None)


def test_errors_are_actionable_and_old_preferences_remain_compatible(tmp_path):
    assert "droits" in file_error_text(PermissionError("denied"))
    assert "n’existe plus" in file_error_text(FileNotFoundError("missing"))
    assert "espace disponible" in file_error_text(OSError("disk full"))
    store = PreferenceStore(tmp_path/"old.json")
    store.save(Preferences(),{})
    assert store.load() == (Preferences(),{})
    with pytest.raises(ValueError,match="Dossiers"):
        validate_layout({"file_directories":{"project":"relative"}})


@pytest.mark.skipif(sys.platform != "win32",reason="Contrôle du dialogue Windows natif")
def test_actual_native_windows_file_picker_can_be_cancelled(app):
    import ctypes
    from ctypes import wintypes
    import threading
    import time
    user = ctypes.WinDLL("user32",use_last_error=True)
    kernel = ctypes.WinDLL("kernel32",use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    user.EnumThreadWindows.argtypes = [wintypes.DWORD,callback_type,wintypes.LPARAM]
    user.EnumChildWindows.argtypes = [wintypes.HWND,callback_type,wintypes.LPARAM]
    user.GetWindowTextW.argtypes = [wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    user.GetClassNameW.argtypes = [wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    user.PostMessageW.argtypes = [wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
    thread_id = kernel.GetCurrentThreadId()
    title = "Ouvrir un projet CADastrophe · contrôle natif"
    service = FileDialogs()
    observed = []
    stop = threading.Event()
    def text(handle,*,class_name=False):
        buffer = ctypes.create_unicode_buffer(1024)
        (user.GetClassNameW if class_name else user.GetWindowTextW)(handle,buffer,1024)
        return buffer.value
    def dismiss():
        started = time.monotonic()
        while not stop.wait(.05):
            @callback_type
            def inspect(handle,unused):
                if text(handle) == title:
                    native = text(handle,class_name=True) == "#32770"
                    if native:
                        labels = []
                        @callback_type
                        def child(child_handle,unused):
                            # Shell/DirectUI descendants may expose apartment-bound
                            # COM objects. Inspect only ordinary Win32 buttons from
                            # this observer thread, including the native Open button.
                            if text(child_handle,class_name=True) == "Button":
                                labels.append(text(child_handle))
                            return True
                        user.EnumChildWindows(handle,child,0)
                        observed.append(labels)
                    if native or time.monotonic()-started > 6:
                        user.PostMessageW(handle,0x0010,0,0)  # WM_CLOSE, own thread/dialog only.
                return True
            user.EnumThreadWindows(thread_id,inspect,0)
    worker = threading.Thread(target=dismiss,daemon=True)
    worker.start()
    try:
        assert service.choose(None,purpose="project",title=title,name_filter=FILE_FILTER) is None
    finally:
        stop.set()
        worker.join(timeout=1)
    assert observed,"Le sélecteur natif Windows n’a pas été observé."
    assert any("Ouvrir" in label.replace("&","") for labels in observed for label in labels)
    assert service.directories == {}
