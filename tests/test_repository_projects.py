"""Every shipped project must open with the current strict native readers."""
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from PySide6.QtWidgets import QApplication
from OCC.Core.BRepCheck import BRepCheck_Analyzer

from cadastrophe.core.persistence import SCHEMA_VERSION, load_document
from examples.parametric_example import registry
from plugins.modeling.extrusion_service import Extrusions
from plugins.sketch.profile_service import SketchProfiles

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = sorted({*ROOT.joinpath("examples").rglob("*.cada"), *ROOT.joinpath("tests").rglob("*.cada"), *ROOT.glob("*.cada")})


@pytest.mark.parametrize("path", PROJECTS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_shipped_project_uses_current_codecs_and_rebuilds(path):
    app = QApplication.instance() or QApplication([])
    types = registry(Extrusions(SketchProfiles()))
    from plugins.curves.codec import CODEC
    types.register('cadastrophe.curves', CODEC)
    with ZipFile(path) as archive:
        data = json.loads(archive.read("manifest.json"))
    assert data["format"] == "cadastrophe.document" and data["schema_version"] in (8, SCHEMA_VERSION)
    for record in data["objects"]:
        if "extension" in record:
            extension = record["extension"]
            assert extension["version"] == types.binding(extension["type_id"]).codec.version
    document = load_document(path, registry=types)
    try:
        assert document.id == data["document"]["id"]
        if data["schema_version"] == 8:
            assert len(document.configurations) == 1 and not document.active_configuration.overrides
        assert tuple(document.objects) == tuple(record["id"] for record in data["objects"])
        for obj in document.objects.values():
            if obj.shape is not None:
                assert BRepCheck_Analyzer(obj.shape, True).IsValid(), obj.name
    finally:
        document.deleteLater()
    assert app is not None
