"""Current product protocols; previous development identities are refused."""
from dataclasses import replace
import json
from math import pi
from zipfile import ZipFile

import pytest


def test_current_native_project_rebuild_edit_undo_redo_and_reopen(tmp_path):
    from PySide6.QtWidgets import QApplication
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    from cadastrophe.core import persistence
    from cadastrophe.core.body_history import evaluate_body
    from cadastrophe.core.feature_model import ExtrusionParameters, Operation
    from examples.parametric_example import ParametricExample, mass, registry
    from plugins.sketch import commands
    app = QApplication.instance() or QApplication([])
    model = ParametricExample("caoproto.nom utilisateur conservé")
    sketch = model.circle("circle", "Cercle", model.plane("XY"), (0, 0), 3.)
    model.append("extrusion", "Extrusion", Operation.NEW_BODY, ExtrusionParameters(depth=10.), sketch)
    types = registry(model.service)
    path = tmp_path/"current.cada"
    persistence.save_document(model.document, path, registry=types)
    loaded = persistence.load_document(path, registry=types)
    assert loaded.name == model.document.name
    assert loaded.id == model.document.id and tuple(loaded.objects) == tuple(model.document.objects)
    constraints = loaded.objects[sketch.id].sketch.constraints
    commands.set_constraints(loaded, sketch.id, tuple(replace(c, value=10.) if c.kind == "diameter" else c for c in constraints))
    state = evaluate_body(loaded, model.body.id, model.service.evaluator(loaded), reuse=True)
    assert BRepCheck_Analyzer(state.shape, True).IsValid()
    assert mass(state.shape) == pytest.approx(pi*25*10)
    loaded.history.undo()
    state = evaluate_body(loaded, model.body.id, model.service.evaluator(loaded), reuse=True)
    assert mass(state.shape) == pytest.approx(pi*9*10)
    loaded.history.redo()
    evaluate_body(loaded, model.body.id, model.service.evaluator(loaded), reuse=True)
    persistence.save_document(loaded, path, registry=types)
    reopened = persistence.load_document(path, registry=types)
    assert mass(reopened.objects[model.body.id].shape) == pytest.approx(pi*25*10)
    assert app is not None


@pytest.mark.parametrize("fault", ["identity", *range(1, 6)])
def test_previous_document_protocols_are_rejected_without_rewriting(tmp_path, fault):
    from cadastrophe.core import persistence
    from cadastrophe.sample_scene import demo_document
    path = tmp_path/"previous.cada"
    persistence.save_document(demo_document(), path)
    with ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    data = json.loads(entries["manifest.json"])
    if fault == "identity":
        data["format"] = "caoproto.document"
    else:
        data["schema_version"] = fault
    entries["manifest.json"] = json.dumps(data).encode()
    with ZipFile(path, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    frozen = path.read_bytes()
    with pytest.raises(persistence.DocumentFileError):
        persistence.load_document(path)
    assert path.read_bytes() == frozen


def test_previous_clipboard_protocols_are_not_accepted():
    from PySide6.QtCore import QMimeData
    from cadastrophe.core.clipboard import MimeClipboard, ClipboardError, decode_objects
    class Clipboard:
        def mimeData(self):
            return self.mime
    backend = Clipboard()
    backend.mime = QMimeData()
    backend.mime.setData("application/x-caoproto-objects+json", b"previous payload")
    transport = MimeClipboard(backend)
    assert not transport.has("application/x-cadastrophe-objects+json")
    with pytest.raises(ClipboardError):
        transport.read("application/x-cadastrophe-objects+json")
    with pytest.raises(ClipboardError):
        decode_objects(json.dumps(dict(format="caoproto.clipboard", schema_version=1)).encode())


def test_previous_preference_directory_is_not_read(tmp_path):
    from cadastrophe.core.preferences import Preferences, PreferenceStore
    old = tmp_path/"CAOProto/preferences.json"
    PreferenceStore(old).save(Preferences(theme="dark"), {})
    frozen = old.read_bytes()
    current = PreferenceStore(tmp_path/"CADastrophe/preferences.json")
    assert current.load() == (Preferences(), {})
    assert not current.error and old.read_bytes() == frozen
