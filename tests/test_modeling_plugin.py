"""X1 ownership through actual isolated plugin loading, without visible widgets."""
from importlib import import_module
from pathlib import Path
import shutil

import pytest

from test_plugins import harness, plugin_root
from test_modeling_history import construction
from cadastrophe.scene import new_document


def test_plugin_dependency_service_types_and_undo_retention_use_existing_framework(harness):
    h = harness
    from cadastrophe.core.presentations import PresentationRegistry
    h.manager.host.presentations = PresentationRegistry()
    root = Path(__file__).resolve().parents[1] / "plugins"
    for name in ("sketch", "modeling"):
        shutil.copytree(root / name, h.root / name)
    h.manager.startup()
    records = {r.manifest.id: r for r in h.manager.records}
    assert records["cadastrophe.modeling"].state == "active"
    service = h.services.service("cadastrophe.modeling.history")
    assert len(h.types.owned_classes("cadastrophe.modeling")) == 11
    assert [command.id for command in h.registry if command.id.startswith("cadastrophe.modeling.")] == [
        "cadastrophe.modeling.extrude", "cadastrophe.modeling.cut_extrude",
        "cadastrophe.modeling.history_before", "cadastrophe.modeling.history_after", "cadastrophe.modeling.history_end",
        "cadastrophe.modeling.disable_features", "cadastrophe.modeling.enable_features",
        "cadastrophe.modeling.repair_references", "cadastrophe.modeling.select_parents",
        "cadastrophe.modeling.select_consumers", "cadastrophe.modeling.select_error_sources", "cadastrophe.modeling.edit_feature",
        "cadastrophe.modeling.hole_wizard",
        "cadastrophe.modeling.shell", "cadastrophe.modeling.draft", "cadastrophe.modeling.edit_plane", "cadastrophe.modeling.reference_plane", "cadastrophe.modeling.revolve",
        "cadastrophe.modeling.cut_revolve", "cadastrophe.modeling.sweep", "cadastrophe.modeling.cut_sweep", "cadastrophe.modeling.loft", "cadastrophe.modeling.cut_loft", "cadastrophe.modeling.mirror", "cadastrophe.modeling.linear_pattern",
        "cadastrophe.modeling.circular_pattern",
        "cadastrophe.modeling.fillet", "cadastrophe.modeling.chamfer", "cadastrophe.modeling.rebuild"]
    assert not h.registry.action("cadastrophe.modeling.edit_feature").isEnabled()
    assert not h.registry.action("cadastrophe.modeling.extrude").isEnabled()
    assert not h.manager.deactivate("cadastrophe.sketch")
    doc = h.documents.add(new_document())
    commands = import_module(records["cadastrophe.sketch"].namespace + ".commands")
    sketch = commands.create_sketch(doc, next(iter(doc.objects)))
    doc.history.clear()
    body, _ = construction(doc, sketch)
    assert not h.manager.deactivate("cadastrophe.modeling")
    doc.history.undo()
    assert body.id not in doc.objects
    assert not h.manager.deactivate("cadastrophe.modeling")  # redo retains recipes
    doc.history.clear()
    assert h.manager.deactivate("cadastrophe.modeling")
    with pytest.raises(LookupError):
        h.services.service("cadastrophe.modeling.history")
    with pytest.raises(RuntimeError, match="fermé"):
        service.result(doc, body.id)
