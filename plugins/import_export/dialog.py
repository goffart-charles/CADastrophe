"""Nonmodal exchange UI; only completed imports enter the common undo history."""
from cadastrophe.plugin_api import DecimalSpinBox, set_feedback
import json
import logging
import os
from pathlib import Path
import sys
from sys import exc_info
import tempfile

from PySide6.QtCore import QEvent, QProcess, QTimer, Qt, QSignalBlocker
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, QLabel,
                              QLineEdit, QPushButton, QComboBox, QProgressBar,
                              QListWidget, QListWidgetItem, QDoubleSpinBox, QWidget)

import cadastrophe
from cadastrophe.brep_io import encode_shape, decode_shape
from cadastrophe.plugin_api import SceneObject, ObjectType, suggested_filename, file_error_text
from .exporters import default_registry

MAX_SHAPE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_OBJECTS = 10_000
log = logging.getLogger("cadastrophe.plugins.import_export")


class ExchangeDialog(QDialog):
    def __init__(self, context, mode, parent=None, *, format_key="step"):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.context, self.mode = context, mode
        self.reservation = context.reserve_document()
        self.document = self.reservation.document
        self.exports = default_registry()
        self.setWindowTitle(f"{'Importer STEP' if mode == 'import' else 'Exporter'} — {self.document.display_name}")
        self.resize(570, 280)
        self._running = self._cancelled = False
        self._temporary = self._output = None
        self._objects, self._imported = (), []
        self._index = self._total = 0
        self.error_message = ""
        self._stderr = b""
        layout = QVBoxLayout(self)
        self.description = QLabel("Les unités du fichier sont converties automatiquement en mm.\n"
                                  "Les racines STEP sont ajoutées au document actif, sans arbre d’assemblage."
                                  if mode == "import" else
                                  "Export des corps entiers, y compris les corps masqués.\n"
                                  "Les plans de référence sont exclus. Le document natif reste inchangé.")
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        form = QFormLayout()
        file_row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setAccessibleName("Fichier de sortie" if mode == "export" else "Fichier STEP")
        self.browse = QPushButton("Parcourir…")
        self.browse.clicked.connect(self.choose_path)
        file_row.addWidget(self.path, 1)
        file_row.addWidget(self.browse)
        form.addRow("Fichier :", file_row)
        self.scope, self.unit = QComboBox(), QComboBox()
        if mode == "export":
            self.format = QComboBox()
            for descriptor in self.exports.formats.values():
                self.format.addItem(descriptor.label,descriptor.key)
            self.format.setCurrentIndex(self.format.findData(format_key))
            form.addRow("Format :",self.format)
            self.scope.addItem("Tous les corps du document", "all")
            self.scope.addItem("Corps sélectionnés uniquement", "selection")
            self.scope.addItem("Corps cochés ci-dessous", "custom")
            if self._selected_bodies():
                self.scope.setCurrentIndex(1)
            for unit, label in (("mm", "Millimètres (mm)"), ("cm", "Centimètres (cm)"),
                                ("m", "Mètres (m)"), ("in", "Pouces (in)")):
                self.unit.addItem(label, unit)
            self.unit.setCurrentIndex(self.unit.findData(context.services.display_units.unit))
            form.addRow("Contenu :", self.scope)
            self.object_list = QListWidget()
            self.object_list.setAccessibleName("Corps à exporter")
            self.object_list.setMaximumHeight(130)
            for obj in self.document.objects.values():
                if obj.kind is ObjectType.BODY:
                    item = QListWidgetItem(obj.name+(" · masqué" if not obj.visible else ""),self.object_list)
                    item.setData(Qt.ItemDataRole.UserRole,obj.id)
                    item.setCheckState(Qt.CheckState.Unchecked)
            form.addRow("Objets :",self.object_list)
            self._sync_objects()
            self.scope.currentIndexChanged.connect(self._sync_objects)
            self.object_list.itemChanged.connect(lambda _: self.scope.setCurrentIndex(2))
            self.unit_label = QLabel("Unité du STEP :")
            form.addRow(self.unit_label, self.unit)
            self.mesh_options = QWidget()
            mesh_form = QFormLayout(self.mesh_options)
            mesh_form.setContentsMargins(0,0,0,0)
            self.quality = QComboBox()
            for key,label in (("low","Faible"),("standard","Standard"),("high","Élevée"),("custom","Personnalisée")):
                self.quality.addItem(label,key)
            self.quality.setCurrentIndex(1)
            mesh_form.addRow("Qualité du maillage :",self.quality)
            self.custom_options = QWidget()
            custom_form = QFormLayout(self.custom_options)
            custom_form.setContentsMargins(0,0,0,0)
            self.deflection,self.angular = DecimalSpinBox(),DecimalSpinBox()
            self.deflection.setDecimals(6)
            self.deflection.setRange(.000001,10000)
            self.deflection.setValue(.1)
            self.deflection.setSuffix(" mm")
            self.angular.setRange(.1,90)
            self.angular.setValue(15)
            self.angular.setSuffix("°")
            custom_form.addRow("Déviation maximale :",self.deflection)
            custom_form.addRow("Déviation angulaire :",self.angular)
            mesh_form.addRow(self.custom_options)
            self.custom_options.hide()
            self.quality.currentIndexChanged.connect(lambda _: self.custom_options.setVisible(self.quality.currentData() == "custom"))
            form.addRow(self.mesh_options)
            self.stl_encoding = QComboBox()
            self.stl_encoding.addItem("Binaire (recommandé)",True)
            self.stl_encoding.addItem("ASCII",False)
            self.encoding_label = QLabel("STL :")
            form.addRow(self.encoding_label,self.stl_encoding)
            self.format.currentIndexChanged.connect(self._format_changed)
            self._format_changed()
        layout.addLayout(form)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.status = QLabel("Choisissez un fichier STEP (.step ou .stp).")
        if mode == "export":
            self.status.setText("Choisissez le format, les corps et le fichier de destination.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.start_button = QPushButton("Importer" if mode == "import" else "Exporter")
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self.start)
        self.cancel_button = QPushButton("Fermer")
        self.cancel_button.clicked.connect(self.close)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._read_progress)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._worker_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._stage_next)

    def _sync_objects(self,*_):
        if self.scope.currentData() == "custom":
            return
        selected = set(self.document.selected_ids)
        blocker = QSignalBlocker(self.object_list)
        for i in range(self.object_list.count()):
            item = self.object_list.item(i)
            checked = self.scope.currentData() == "all" or item.data(Qt.ItemDataRole.UserRole) in selected
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        del blocker

    def _format_changed(self,*_):
        descriptor = self.exports.get(self.format.currentData())
        self.description.setText(descriptor.description+"\nLe document natif reste inchangé. Les plans et esquisses sont exclus.")
        self.unit.setVisible(not descriptor.meshed)
        self.unit_label.setVisible(not descriptor.meshed)
        self.mesh_options.setVisible(descriptor.meshed)
        self.stl_encoding.setVisible(descriptor.key == "stl")
        self.encoding_label.setVisible(descriptor.key == "stl")
        path = Path(self.path.text())
        known = {ext for f in self.exports.formats.values() for ext in f.extensions}
        if self.path.text() and path.suffix.lower() in known and path.suffix.lower() not in descriptor.extensions:
            self.path.setText(str(path.with_suffix(descriptor.extensions[0])))

    def _export_options(self):
        if not self.exports.get(self.format.currentData()).meshed:
            return {"unit":self.unit.currentData()}
        if self.quality.currentData() == "custom":
            for field in (self.deflection,self.angular):
                if not field.hasAcceptableInput():
                    raise ValueError("Saisissez une tolérance de maillage valide.")
                field.interpretText()
        return {"unit":self.unit.currentData(),"binary":self.stl_encoding.currentData(),
                "quality":self.quality.currentData(),"deflection":self.deflection.value(),"angle":self.angular.value()}

    def _selected_bodies(self):
        return tuple(obj for obj in self.document.objects.values()
                     if obj.id in self.document.selected_ids and obj.kind is ObjectType.BODY)

    def choose_path(self):
        descriptor = self.exports.get(self.format.currentData()) if self.mode == "export" else self.exports.get("step")
        extensions = " ".join("*"+ext for ext in descriptor.extensions)
        try:
            name = self.document.file_path.stem if self.document.file_path is not None else self.document.name
            path = self.context.services.file_dialogs.choose(self,purpose=self.mode,
                title="Exporter un fichier" if self.mode == "export" else "Importer un fichier STEP",
                name_filter=f"{descriptor.label} ({extensions})",save=self.mode == "export",
                suggested_name=suggested_filename(name,descriptor.extensions[0]) if self.mode == "export" else "",
                initial_path=self.path.text() or None,suffix=descriptor.extensions[0] if self.mode == "export" else "")
            if path is not None:
                self.path.setText(str(path))
        except (OSError,ValueError) as error:
            self._error(file_error_text(error))

    def start(self):
        if self._running:
            return
        try:
            if not self.path.text().strip():
                raise ValueError("Choisissez un fichier de destination." if self.mode == "export" else "Choisissez un fichier STEP.")
            self.target = self.context.services.file_dialogs.resolve_path(self.path.text().strip(),self.mode,writing=self.mode == "export")
            descriptor = self.exports.get(self.format.currentData()) if self.mode == "export" else self.exports.get("step")
            if not self.target.suffix and self.mode == "export":
                self.target = self.target.with_suffix(descriptor.extensions[0])
                self.path.setText(str(self.target))
            if self.target.suffix.lower() not in descriptor.extensions:
                raise ValueError("Le fichier doit porter l’extension "+" ou ".join(descriptor.extensions)+".")
            if self.mode == "export":
                self._objects = (self._selected_bodies() if self.scope.currentData() == "selection" else
                                 tuple(obj for obj in self.document.objects.values() if obj.kind is ObjectType.BODY))
                if self.scope.currentData() == "custom":
                    checked = {self.object_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.object_list.count())
                               if self.object_list.item(i).checkState() == Qt.CheckState.Checked}
                    self._objects = tuple(obj for obj in self._objects if obj.id in checked)
                self._options = self._export_options()
                if not self._objects:
                    raise ValueError("Aucun corps à exporter dans le contenu choisi.")
                if len(self._objects) > MAX_OBJECTS:
                    raise ValueError("La limite est de 10 000 corps.")
                if not self.context.services.file_dialogs.confirm_overwrite(self,self.target):
                    return
                fd, output = tempfile.mkstemp(prefix=".cadastrophe-export-", suffix=self.target.suffix, dir=self.target.parent)
                os.close(fd)
                self._output = Path(output)
            elif not self.target.is_file():
                raise ValueError("Le fichier STEP est absent ou n’est pas un fichier.")
            self._temporary = tempfile.TemporaryDirectory(prefix="cadastrophe-step-")
            self._directory = Path(self._temporary.name)
            self._index = self._total = 0
            self._imported = []
            self._cancelled = False
            self._running = True
            self.error_message = ""
            self._stderr = b""
            log.info("STEP %s démarré ; document=%s ; fichier=%s", self.mode, self.document.id, self.target)
            self._set_busy(True)
            self.progress.setValue(0)
            set_feedback(self.status, "Préparation de l’échange…", "info")
            if self.mode == "export":
                self.timer.start(0)
            else:
                self._launch_worker()
        except Exception as exc:
            self._error(file_error_text(exc,getattr(self,"target",None)))

    def _set_busy(self, busy):
        for widget in (self.path, self.browse, self.scope, self.unit, self.start_button):
            widget.setEnabled(not busy)
        if self.mode == "export":
            for widget in (self.format,self.object_list,self.mesh_options,self.stl_encoding):
                widget.setEnabled(not busy)
        self.cancel_button.setText("Annuler" if busy else "Fermer")

    def _launch_worker(self):
        request = {"mode": self.mode, "source": str(self.target), "output": str(self._output),
                   "count": len(self._objects), "unit": self.unit.currentData()}
        if self.mode == "export":
            request.update(format=self.format.currentData(),options=self._options,
                           objects=[{"id":o.id,"name":o.name,"color":o.color,"transparency":o.transparency} for o in self._objects])
        (self._directory / "request.json").write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        self.process.start(sys.executable, ["-u", str(Path(__file__).with_name("worker.py")),
                                          str(Path(cadastrophe.__file__).resolve().parent.parent), str(self._directory)])

    def _read_progress(self):
        while self.process.canReadLine():
            line = bytes(self.process.readLine()).decode("utf-8", errors="replace").strip()
            if line.startswith("CADASTROPHE_STEP "):
                try:
                    data = json.loads(line[len("CADASTROPHE_STEP "):])
                    self.progress.setValue(max(self.progress.value(), min(90, int(data["value"]))))
                    self.status.setText(data["label"])
                except (ValueError, KeyError, TypeError):
                    pass

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart and self._running:
            self._error("Démarrage du processus d’échange impossible : " + self.process.errorString())

    def _read_stderr(self):
        self._stderr = (self._stderr + bytes(self.process.readAllStandardError()))[-8192:]

    def _worker_finished(self, exit_code, exit_status):
        self._read_stderr()
        if not self._running:
            return
        if self._cancelled:
            self._running = False
            self.close()
            return
        try:
            result_path = self._directory / "result.json"
            if not result_path.is_file() or result_path.stat().st_size > 65536:
                raise ValueError("Le processus d’échange s’est arrêté sans résultat exploitable.")
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if exit_code != 0 or exit_status != QProcess.ExitStatus.NormalExit or "error" in result:
                raise ValueError(result.get("error", "Échec du processus d’échange."))
            if self.mode == "export":
                # The worker writes a private sibling; cancellation never overwrites the destination.
                with self._output.open("r+b") as stream:
                    os.fsync(stream.fileno())
                os.replace(self._output, self.target)
                self._output = None
                label = self.format.currentData().upper()
                self._success(f"{label} exporté : {len(self._objects)} corps, unité {result.get('unit',self.unit.currentData())}.")
            else:
                count = result.get("count")
                if type(count) is not int or not 0 < count <= MAX_OBJECTS:
                    raise ValueError("Nombre de formes STEP invalide.")
                self._count = count
                self._source_units = ", ".join(result.get("source_units", [])) or "unité STEP"
                self.timer.start(0)
        except Exception as exc:
            self._error(str(exc))

    def _stage_next(self):
        if not self._running:
            return
        try:
            if self.mode == "export":
                obj = self._objects[self._index]
                data = encode_shape(obj.shape, ObjectType.BODY)
            else:
                path = self._directory / f"{self._index}.brep"
                if path.stat().st_size > MAX_SHAPE:
                    raise ValueError("Une forme dépasse la limite de 64 Mio.")
                data = path.read_bytes()
            self._total += len(data)
            if len(data) > MAX_SHAPE or self._total > MAX_TOTAL:
                raise ValueError("Limite B-Rep dépassée (64 Mio par forme, 256 Mio au total).")
            if self.mode == "export":
                (self._directory / f"{self._index}.brep").write_bytes(data)
                count = len(self._objects)
                self.progress.setValue(int(20 * (self._index + 1) / count))
                self.status.setText(f"Préparation du corps {self._index + 1}/{count}…")
            else:
                count = self._count
                shape = decode_shape(data, ObjectType.BODY)
                name = self.target.stem if count == 1 else f"{self.target.stem} · {self._index + 1}"
                used = {obj.name for obj in self.document.objects.values()} | {obj.name for obj in self._imported}
                base, suffix = name, 2
                while name in used:
                    name = f"{base} ({suffix})"
                    suffix += 1
                self._imported.append(SceneObject(name, shape, (.64, .69, .75)))
                self.progress.setValue(90 + int(9 * (self._index + 1) / count))
                self.status.setText(f"Préparation de l’objet {self._index + 1}/{count}…")
            self._index += 1
            if self._index < count:
                self.timer.start(0)
            elif self.mode == "export":
                self._launch_worker()
            else:
                with self.reservation.transaction("Importer STEP") as document:
                    document.insert_objects(self._imported, title="Importer STEP")
                self._success(f"STEP importé : {count} objet(s), {self._source_units} → mm. F : ajuster la vue.")
        except Exception as exc:
            self._error(str(exc))

    def _success(self, message):
        log.info("STEP %s terminé ; document=%s ; fichier=%s", self.mode, self.document.id, self.target)
        self.progress.setValue(100)
        self.context.services.file_dialogs.remember(self.mode,self.target.parent)
        self.context.services.message.emit(message)
        self._running = False
        self.close()

    def _error(self, message):
        log.warning("STEP %s échoué ; document=%s ; fichier=%s ; %s\n%s", self.mode, self.document.id,
                    self.path.text(), message, self._stderr.decode("utf-8", errors="replace"),
                    exc_info=exc_info()[0] is not None)
        self._running = False
        self.timer.stop()
        self.error_message = message
        set_feedback(self.status, "Échec : " + message)
        self._set_busy(False)
        self._cleanup_files()

    def _cleanup_files(self):
        self._imported = []
        self._objects = ()
        if self._output is not None:
            output = self._output
            self._output = None
            try:
                output.unlink(missing_ok=True)
            except OSError:
                log.exception("Nettoyage du fichier STEP temporaire impossible : %s", output)
        if self._temporary is not None:
            temporary = self._temporary
            self._temporary = None
            try:
                temporary.cleanup()
            except OSError:
                log.exception("Nettoyage du dossier STEP temporaire impossible : %s", temporary.name)

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Escape:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def reject(self):
        if self._running:
            self.close()
            return
        self._cleanup_files()
        self.reservation.close()
        super().reject()

    def closeEvent(self, event):
        if self._running:
            self._cancelled = True
            log.info("Annulation STEP %s ; document=%s", self.mode, self.document.id)
            self.timer.stop()
            if self.process.state() != QProcess.ProcessState.NotRunning:
                self.process.kill()
                event.ignore()
                return
            self._running = False
        self._cleanup_files()
        self.reservation.close()
        super().closeEvent(event)
