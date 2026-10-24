"""Shared native file pickers, French Qt translations and remembered locations."""
import logging
import os
from pathlib import Path
import re

from PySide6.QtCore import QObject, Signal, QLibraryInfo, QLocale, QStandardPaths, QTranslator
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

log = logging.getLogger(__name__)
PURPOSES = {"project", "import", "export", "directory"}


def documents_directory():
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation))


def install_french_translations(app):
    """Qt's own catalog covers its fallback explorer and standard messages.

    Native OS-owned controls retain their system language where Qt exposes no
    localization override. Keep the translator alive for the app's lifetime.
    """
    if getattr(app, "_cadastrophe_qt_fr_translator", None) is not None:
        return True
    translator = QTranslator(app)
    directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if translator.load(QLocale("fr_FR"), "qtbase", "_", directory):
        app.installTranslator(translator)
        app._cadastrophe_qt_fr_translator = translator
        return True
    translator.deleteLater()
    log.warning("Catalogue Qt français introuvable dans %s ; les libellés applicatifs restent en français.", directory)
    return False


def accessible_directory(path, *, writing=False):
    try:
        return path.is_dir() and os.access(path, os.R_OK | os.X_OK | (os.W_OK if writing else 0))
    except OSError:
        return False


def file_error_text(error, path=None):
    cause = error
    while cause.__cause__ is not None:
        cause = cause.__cause__
    if isinstance(cause,OSError):
        error = cause
        path = path if path is not None else getattr(error,"filename",None)
    location = f"\nFichier : {path}" if path is not None else ""
    if isinstance(error, PermissionError):
        return "Accès refusé. Vérifiez les droits du dossier et que le fichier n’est pas verrouillé par une autre application."+location
    if isinstance(error, FileNotFoundError):
        return "Le fichier ou le dossier n’existe plus. Choisissez un emplacement existant."+location
    if isinstance(error, OSError):
        return "Impossible d’accéder au fichier ou de l’écrire. Vérifiez les droits, l’espace disponible et la connexion au dossier."+location+f"\nDétail : {error}"
    return str(error)


def suggested_filename(name, extension):
    # Keep Unicode and accents; only strip characters forbidden in filenames
    # on supported desktop platforms, never interpret a project name as a path.
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip().rstrip(". ") or "Pièce"
    return name if name.lower().endswith(extension.lower()) else name+extension


class FileDialogs(QObject):
    changed = Signal()
    warning = Signal(str)

    def __init__(self, directories=None, parent=None):
        super().__init__(parent)
        install_french_translations(QApplication.instance())
        self.directories = dict(directories or {})
        self._approved_save = None
        self.startup_warning = ""
        self.default_directory = documents_directory()/"CADastrophe"
        try:
            self.default_directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.startup_warning = "Le dossier de projets CADastrophe n’a pas pu être créé. "+file_error_text(error,self.default_directory)
            log.warning(self.startup_warning)

    def initial_directory(self, purpose, *, writing=False):
        if purpose not in PURPOSES:
            raise ValueError("Usage du dialogue de fichiers inconnu.")
        remembered = self.directories.get(purpose)
        if remembered and accessible_directory(Path(remembered),writing=writing):
            return Path(remembered)
        try:
            self.default_directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if accessible_directory(self.default_directory,writing=writing):
            return self.default_directory
        self.warning.emit("Le dossier CADastrophe est inaccessible. Choisissez un autre emplacement.\n"+str(self.default_directory))
        for fallback in (documents_directory(),Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.HomeLocation))):
            if accessible_directory(fallback,writing=writing):
                return fallback
        raise OSError("Aucun dossier accessible n’a été trouvé. Vérifiez les droits de votre session.")

    def remember(self, purpose, directory):
        if purpose not in PURPOSES:
            raise ValueError("Usage du dialogue de fichiers inconnu.")
        try:
            directory = Path(directory).resolve()
        except OSError as error:
            log.warning("Dernier dossier inaccessible : %s",error)
            return
        if accessible_directory(directory) and self.directories.get(purpose) != str(directory):
            self.directories[purpose] = str(directory)
            self.changed.emit()

    def resolve_path(self, text, purpose, *, writing=False):
        path = Path(text).expanduser()
        return (path if path.is_absolute() else self.initial_directory(purpose,writing=writing)/path).resolve()

    def create_dialog(self, parent, *, purpose, title, name_filter="", save=False, directory=False,
                      suggested_name="", initial_path=None, suffix=""):
        folder = self.initial_directory(purpose,writing=save)
        if initial_path:
            candidate = self.resolve_path(initial_path,purpose,writing=save)
            if accessible_directory(candidate if directory else candidate.parent,writing=save):
                folder = candidate if directory else candidate.parent
            if not directory:
                suggested_name = candidate.name
        dialog = QFileDialog(parent)
        # Set options before all other properties. Qt chooses its native helper
        # unless unsupported by the platform (or disabled by the test harness).
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, False)
        dialog.setOption(QFileDialog.Option.DontConfirmOverwrite, False)
        dialog.setWindowTitle(title)
        dialog.setLocale(QLocale("fr_FR"))
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave if save else QFileDialog.AcceptMode.AcceptOpen)
        dialog.setFileMode(QFileDialog.FileMode.Directory if directory else
                           QFileDialog.FileMode.AnyFile if save else QFileDialog.FileMode.ExistingFile)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly,directory)
        if name_filter:
            dialog.setNameFilter(name_filter)
        if suffix:
            dialog.setDefaultSuffix(suffix.lstrip("."))
        dialog.setDirectory(str(folder))
        if suggested_name and not directory:
            dialog.selectFile(suggested_name)
        for label,text in ((QFileDialog.DialogLabel.LookIn,"Dossier :"),
                           (QFileDialog.DialogLabel.FileName,"Nom du dossier :" if directory else "Nom du fichier :"),
                           (QFileDialog.DialogLabel.FileType,"Type de fichier :"),
                           (QFileDialog.DialogLabel.Accept,"Sélectionner" if directory else "Enregistrer" if save else "Ouvrir"),
                           (QFileDialog.DialogLabel.Reject,"Annuler")):
            dialog.setLabelText(label,text)
        return dialog

    def choose(self, parent, **options):
        self._approved_save = None
        dialog = self.create_dialog(parent,**options)
        try:
            if dialog.exec() != QFileDialog.DialogCode.Accepted or not dialog.selectedFiles():
                return None
            path = Path(dialog.selectedFiles()[0]).resolve()
            if options.get("save"):
                self._approved_save = (path,self._stamp(path))
            self.remember(options["purpose"],path if options.get("directory") else path.parent)
            return path
        finally:
            dialog.deleteLater()

    def choose_directory(self, parent=None, *, title="Sélectionner un dossier"):
        return self.choose(parent,purpose="directory",title=title,directory=True)

    @staticmethod
    def _stamp(path):
        try:
            stat = path.stat()
            return stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns
        except FileNotFoundError:
            return None

    def confirm_overwrite(self, parent, path):
        path = Path(path).resolve()
        stamp = self._stamp(path)
        approved,self._approved_save = self._approved_save,None
        if stamp is None or approved == (path,stamp):
            return True
        return QMessageBox.question(parent,"Remplacer le fichier existant ?",
                                   f"Le fichier « {path.name} » existe déjà.\nVoulez-vous le remplacer ?",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                   QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
