"""Plugin startup policy, dependency planning and reversible contributions."""
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import sys

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMainWindow, QTreeWidget, QMenu

from cadastrophe.core.commands import CommandRegistry
from cadastrophe.core.documents import DocumentManager
from cadastrophe.core.object_types import ObjectTypeRegistry
from cadastrophe.core.plugins import PluginManager
from cadastrophe.document import Document, SceneObject
from cadastrophe.plugin_api import ApplicationServices
from cadastrophe.ui.tool_ribbon import ToolRibbon
from cadastrophe.ui.workspace import ManagerPane
from cadastrophe.ui.plugin_host import PluginUiHost

SAMPLE = Path(__file__).parent / "fixtures/plugins/sample"
EMPTY_PLUGIN = """
class Plugin:
    def activate(self, context):
        pass
    def deactivate(self):
        pass
def create_plugin():
    return Plugin()
"""


def test_owned_async_document_reservation_transaction_rollback_and_cleanup(harness):
    h = harness
    write_plugin(h.root, "async")
    h.manager.startup()
    context = h.manager.records[0].context
    h.doc.rename(h.obj.id, "temp")
    h.doc.history.undo()
    reservation = context.reserve_document()
    assert reservation.document is h.doc and h.documents.blocked
    assert not h.services.editable and not h.doc.is_modified
    with pytest.raises(RuntimeError):
        context.reserve_document()
    with pytest.raises(ValueError, match="rollback"):
        with reservation.transaction("Import failed") as document:
            document.rename(h.obj.id, "partial")
            raise ValueError("rollback")
    assert h.doc.objects[h.obj.id].name == "Original" and h.doc.history.index == 0
    assert h.documents.blocked
    reservation.close()
    reservation.close()
    assert h.doc.history.can_redo and not h.documents.blocked and not h.doc.is_modified
    with pytest.raises(ValueError, match="fermée"):
        with reservation.transaction("Closed"):
            pass
    reservation = context.reserve_document()
    with reservation.transaction("Async rename") as document:
        document.rename(h.obj.id, "imported")
    assert h.doc.history.count == 1 and h.doc.history.undo_text == "Async rename"
    context.close()
    assert not h.documents.blocked
    h.doc.history.undo()
    assert h.doc.objects[h.obj.id].name == "Original"


@pytest.fixture
def plugin_root(tmp_path):
    root = tmp_path / "plugins"
    root.mkdir()
    return root


def write_plugin(root, folder, *, plugin_id=None, code=EMPTY_PLUGIN, **overrides):
    directory = root / folder
    directory.mkdir()
    manifest = dict(id=plugin_id or "fixture." + folder, name=folder, version="1.0.0", api_version=1,
                    entry_point="plugin:create_plugin", enabled_by_default=True, dependencies=[])
    manifest.update(overrides)
    (directory / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "plugin.py").write_text(code, encoding="utf-8")
    return directory


@pytest.fixture
def harness(plugin_root):
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    registry = CommandRegistry(window)
    ribbon = ToolRibbon(window)
    pane = ManagerPane(QTreeWidget(), window)
    menu = QMenu(window)
    documents = DocumentManager()
    doc = documents.add(Document())
    obj = SceneObject("Original", None, (0.5, 0.6, 0.7))
    doc.add(obj)
    doc.history.clear()
    services = ApplicationServices(documents)
    types = ObjectTypeRegistry()
    host = PluginUiHost(registry, ribbon, pane, menu)
    manager = PluginManager(plugin_root, services, host, types, documents)
    services.state_changed.connect(manager.refresh)
    result = SimpleNamespace(app=app, window=window, registry=registry, ribbon=ribbon, pane=pane,
                             menu=menu, documents=documents, doc=doc, obj=obj, services=services,
                             types=types, manager=manager, root=plugin_root)
    try:
        yield result
    finally:
        for document in documents.documents:
            documents.remove(document.id)
        manager.shutdown()
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_sample_loads_unloads_and_releases_actions_shortcuts_panels_groups_and_modules(harness):
    h = harness
    shutil.copytree(SAMPLE, h.root / "sample")
    h.manager.startup()
    record = h.manager.records[0]
    assert record.state == "active" and not record.error
    namespace = record.namespace
    assert namespace + ".helper" in sys.modules
    assert h.pane.count() == 5 and not h.menu.actions()
    action = h.registry.action("fixture.sample.rename")
    assert not action.isEnabled()
    h.doc.select_objects([h.obj.id])
    assert action.isEnabled()
    assert h.registry.execute("fixture.sample.rename")
    assert h.doc.objects[h.obj.id].name == "Nom depuis le plugin" and h.doc.history.count == 1
    h.doc.history.undo()
    assert h.doc.objects[h.obj.id].name == "Original"
    assert h.manager.deactivate("fixture.sample")
    assert h.pane.count() == 4 and not h.menu.actions()
    assert all(command.id != "fixture.sample.rename" for command in h.registry)
    assert not any(name == namespace or name.startswith(namespace + ".") for name in sys.modules)
    h.registry.register("reuse", "Réutiliser", "Test", lambda: None, shortcuts=("Ctrl+Alt+P",))
    assert not action.isEnabled() and h.types.owned_classes("fixture.sample") == ()
    h.doc.select_objects([])  # no callbacks into removed panel


@pytest.mark.parametrize("issue", ["api", "manifest", "duplicate", "entry", "import", "factory"])
def test_invalid_plugin_is_reported_without_importing_incompatible_code_or_crashing(harness, issue):
    h = harness
    marker = h.root / "imported.txt"
    code = f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n" + EMPTY_PLUGIN
    folder = write_plugin(h.root, "broken", code=code)
    if issue == "api":
        data = json.loads((folder / "plugin.json").read_text())
        data["api_version"] = 99
        (folder / "plugin.json").write_text(json.dumps(data))
    elif issue == "manifest":
        (folder / "plugin.json").write_text("{")
    elif issue == "duplicate":
        write_plugin(h.root, "duplicate", plugin_id="fixture.broken", code=code)
    elif issue == "entry":
        data = json.loads((folder / "plugin.json").read_text())
        data["entry_point"] = "../outside:factory"
        (folder / "plugin.json").write_text(json.dumps(data))
    elif issue == "import":
        (folder / "plugin.py").write_text("raise SystemExit('bad import')")
    else:
        (folder / "plugin.py").write_text("def create_plugin(): return object()")
    h.manager.startup()
    assert all(record.state in ("error", "incompatible") and record.error for record in h.manager.records)
    assert not marker.exists() and not h.manager._active
    assert not h.menu.actions() and h.pane.count() == 4


def test_dependency_order_is_deterministic_and_unload_requires_dependents_first(harness):
    h = harness
    order = h.root / "order.txt"
    for folder, plugin_id, deps in (("a", "fixture.a", [{"id": "fixture.z", "min_version": "1.0.0"}]),
                                    ("z", "fixture.z", [])):
        code = EMPTY_PLUGIN.replace("        pass", f"        open({str(order)!r}, 'a').write('{plugin_id}\\n')", 1)
        write_plugin(h.root, folder, plugin_id=plugin_id, code=code, dependencies=deps)
    h.manager.startup()
    assert [record.manifest.id for record in h.manager.records] == ["fixture.a", "fixture.z"]
    assert order.read_text().splitlines() == ["fixture.z", "fixture.a"]
    assert not h.manager.deactivate("fixture.z")
    assert h.manager.deactivate("fixture.a") and h.manager.deactivate("fixture.z")


@pytest.mark.parametrize("problem", ["missing", "old", "cycle", "disabled"])
def test_dependency_failure_prevents_import_of_the_dependent(harness, problem):
    h = harness
    marker = h.root / "imported.txt"
    code = f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n" + EMPTY_PLUGIN
    write_plugin(h.root, "a", code=code, dependencies=[{"id": "fixture.b", "min_version": "2.0.0"}])
    if problem != "missing":
        write_plugin(h.root, "b", version="1.0.0" if problem == "old" else "2.0.0",
                     enabled_by_default=False,
                     dependencies=[{"id": "fixture.a", "min_version": "1.0.0"}] if problem == "cycle" else [])
    if problem == "disabled":
        h.manager.write_settings({"fixture.b": False})
    h.manager.startup()
    assert h.manager._record("fixture.a").state == "blocked"
    assert h.manager._record("fixture.a").error and not marker.exists()


@pytest.mark.parametrize("stage", ["activate", "shortcut", "panel", "deactivate"])
def test_partial_activation_and_stop_errors_still_remove_all_owned_contributions(harness, stage):
    h = harness
    code = '''
class Plugin:
    def activate(self, ctx):
        ctx.register_action("one", "Un", lambda: None, requires_document=False, shortcuts=("Ctrl+Alt+L",))
        ctx.register_ribbon("ribbon", "Affichage", "Test", ["one"])
        STOP
    def deactivate(self):
        END
def create_plugin(): return Plugin()
'''
    stop = {"activate": "raise RuntimeError('activation failed')",
            "shortcut": 'ctx.register_action("two", "Deux", lambda: None, shortcuts=("Ctrl+Alt+L",))',
            "panel": 'ctx.register_panel("bad", "Panneau", lambda services, parent: None)',
            "deactivate": "pass"}[stage]
    code = code.replace("STOP", stop).replace("END", "raise RuntimeError('stop failed')" if stage == "deactivate" else "pass")
    write_plugin(h.root, "a", code=code)
    h.manager.startup()
    if stage == "deactivate":
        assert h.manager.deactivate("fixture.a")
    assert not h.menu.actions() and h.pane.count() == 4
    assert not h.types.owned_classes("fixture.a")
    h.registry.register("reuse", "Autre", "Test", lambda: None, shortcuts=("Ctrl+Alt+L",))
    assert h.manager._record("fixture.a").error


def test_failed_mutating_action_rolls_back_and_disables_it_without_raising_into_qt(harness, caplog):
    h = harness
    code = '''
class Plugin:
    def activate(self, ctx):
        self.ctx = ctx
        ctx.register_action("fail", "Échec", self.run, mutates=True)
    def run(self):
        doc = self.ctx.services.active_document
        doc.rename(next(iter(doc.objects)), "Temporaire")
        raise RuntimeError("action failed")
    def deactivate(self): pass
def create_plugin(): return Plugin()
'''
    write_plugin(h.root, "a", code=code)
    h.manager.startup()
    assert h.registry.execute("fixture.a.fail")
    assert h.doc.objects[h.obj.id].name == "Original" and h.doc.history.count == 0
    assert not h.registry.action("fixture.a.fail").isEnabled()
    h.manager.refresh()
    assert not h.registry.execute("fixture.a.fail") and "action failed" in h.manager.records[0].error
    assert any(record.exc_info and "fixture.a.fail" in record.getMessage() for record in caplog.records)


def test_reservation_cleanup_error_does_not_skip_other_plugin_contributions(harness, monkeypatch):
    h = harness
    write_plugin(h.root, "cleanup")
    h.manager.startup()
    context = h.manager.records[0].context
    context.register_action("owned", "Owned", lambda: None)
    reservation = context.reserve_document()
    original_close = reservation.close
    def fail_after_release():
        original_close()
        raise RuntimeError("reservation cleanup failure")
    monkeypatch.setattr(reservation, "close", fail_after_release)
    context.close()
    assert not h.documents.blocked
    assert not any(command.id == "fixture.cleanup.owned" for command in h.registry)
    assert "reservation cleanup failure" in h.manager.records[0].error


def test_settings_are_applied_on_next_start_and_bad_settings_import_nothing(harness):
    h = harness
    write_plugin(h.root, "a")
    h.manager.write_settings({"fixture.a": False})
    h.manager.startup()
    assert h.manager.records[0].state == "disabled"
    h.manager.write_settings({"fixture.a": True})
    assert h.manager.records[0].state == "disabled"  # no live reload
    other = PluginManager(h.root, h.services, h.manager.host, h.types, h.documents)
    other.startup()
    assert other.records[0].state == "active"
    other.deactivate("fixture.a")
    (h.root / "settings.json").write_text("{")
    third = PluginManager(h.root, h.services, h.manager.host, h.types, h.documents)
    third.startup()
    assert third.settings_error and not third._active


def test_types_pin_plugin_through_undo_redo_until_document_is_closed(harness):
    h = harness
    shutil.copytree(SAMPLE, h.root / "sample")
    h.manager.startup()
    cls = h.types.binding("fixture.sample.tagged").codec.object_class
    obj = cls("Type du plugin", None, h.obj.color, tag="persistant")
    with h.doc.history.transaction("Création groupée"):
        h.doc.add(obj)
    h.doc.history.undo()
    assert obj.id not in h.doc.objects and not h.manager.deactivate("fixture.sample")
    h.doc.history.redo()
    h.doc.history.clear()
    h.doc.remove(obj.id)
    assert not h.manager.deactivate("fixture.sample")
    h.documents.remove(h.doc.id)
    assert h.manager.deactivate("fixture.sample")


def test_similar_identifiers_have_isolated_import_namespaces(harness):
    h = harness
    for folder, plugin_id, marker in (("one", "fixture.a-b", "first"), ("two", "fixture.a_b", "second")):
        code = EMPTY_PLUGIN.replace("        pass", f"        self.marker = {marker!r}", 1)
        write_plugin(h.root, folder, plugin_id=plugin_id, code=code)
    h.manager.startup()
    first, second = h.manager.records
    assert first.instance.marker == "first" and second.instance.marker == "second"
    assert first.namespace != second.namespace
    assert h.manager.deactivate(first.manifest.id)
    assert second.namespace + ".plugin" in sys.modules


def test_failing_availability_predicate_is_isolated_and_keeps_other_actions_available(harness):
    h = harness
    code = '''
class Plugin:
    def activate(self, ctx):
        ctx.register_action("bad", "Mauvais", lambda: None, enabled_when=self.enabled)
        ctx.register_action("good", "Bon", lambda: None)
    def enabled(self, services):
        raise SystemExit("bad predicate")
    def deactivate(self): pass
def create_plugin(): return Plugin()
'''
    write_plugin(h.root, "a", code=code)
    h.manager.startup()
    assert not h.registry.action("fixture.a.bad").isEnabled()
    assert h.registry.action("fixture.a.good").isEnabled()
    assert "bad predicate" in h.manager.records[0].error
