"""Long native graph checks must not be packed into an already full GUI lot."""
from types import SimpleNamespace

from cadastrophe.test_runner import Collection, partition


def test_native_scheduling_uses_measured_cost_alongside_existing_gui_estimate(tmp_path):
    native = tmp_path/"native.py"
    ui = tmp_path/"ui.py"
    scalar = tmp_path/"scalar.py"
    for path in (native, ui, scalar):
        path.write_text("# Independent native test module\n", encoding="utf-8")
    def item(path, fixtures=(), cost=None):
        return SimpleNamespace(path=path, nodeid=path.name+"::test_case", fixturenames=fixtures,
            module=SimpleNamespace(**({"NATIVE_TEST_COST": cost} if cost else {})))
    collection = Collection()
    collection.pytest_collection_finish(SimpleNamespace(items=[
        item(native, cost=300.), item(ui, ("extrusion_system",)), item(scalar)]))
    assert collection.costs[str(native)] == 300.
    assert collection.costs[str(ui)] == 1.
    assert collection.costs[str(scalar)] == .1
    groups, serial = partition(collection.modules, collection.costs, workers=2)
    assert groups == [[native.name+"::test_case"], [ui.name+"::test_case", scalar.name+"::test_case"]]
    assert not serial
