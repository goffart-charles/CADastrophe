"""Shards cover every selected test once and isolate clipboard mutations."""
import json
from types import SimpleNamespace

import pytest
from cadastrophe.test_runner import partition, serial_batches, ShardSelection, parse_workers


def test_partition_balances_whole_modules_and_serializes_shared_state(tmp_path):
    modules = {}
    for name, text, count in (("a", "native geometry", 4), ("b", "Qt viewer", 3),
                              ("c", "clipboard.setText", 2), ("d", "edit.paste", 1)):
        path = tmp_path / (name+".py")
        path.write_text(text)
        modules[str(path)] = [f"{name}::{i}" for i in range(count)]
    costs = {path: len(ids) for path, ids in modules.items()}
    groups, serial = partition(modules, costs)
    assert sorted(map(len, groups)) == [3, 4]
    assert set(serial) == {"c::0", "c::1", "d::0"}
    flat = [item for group in (*groups, serial) for item in group]
    assert len(flat) == len(set(flat)) == 10


def test_shard_selection_preserves_order_and_reports_deselected(tmp_path):
    path = tmp_path / "shard.json"
    path.write_text(json.dumps(["module::é", "module::a"]), encoding="utf8")
    values = [SimpleNamespace(nodeid=key) for key in ("module::a", "module::b", "module::é")]
    removed = []
    config = SimpleNamespace(hook=SimpleNamespace(pytest_deselected=lambda items: removed.extend(items)))
    ShardSelection(path).pytest_collection_modifyitems(config, values)
    assert [item.nodeid for item in values] == ["module::a", "module::é"]
    assert [item.nodeid for item in removed] == ["module::b"]


def test_shared_state_batches_keep_whole_modules_and_cover_every_case_once():
    # A slow whole-part module must not consume the time allowance of every
    # other shared-state scenario; an oversized module stays alone.
    selected = ["parts.py::a", "parts.py::b", "copy.py::a", "paste.py::a", "paste.py::b", "history.py::a"]
    costs = {"parts.py": 400., "copy.py": 80., "paste.py": 90., "history.py": 60.}
    batches = serial_batches(selected, costs)
    assert batches == [selected[:2], selected[2:5], selected[5:]]
    assert [node for batch in batches for node in batch] == selected
    assert serial_batches([], {}) == []


def test_worker_imports_only_its_assigned_files(tmp_path):
    path = tmp_path / "shard.json"
    path.write_text(json.dumps(["tests/shape.py::test_a", "tests/shape.py::test_b[x]", "tests/ui.py::test_c"]))
    assert ShardSelection(path).paths(tmp_path) == [str(tmp_path / "tests/shape.py"), str(tmp_path / "tests/ui.py")]


def test_worker_count_is_configurable_and_bounded():
    assert parse_workers(["--test-parallel", "--test-workers", "3"]) == 3
    for count in ("0", "9", "wrong"):
        with pytest.raises(SystemExit):
            parse_workers(["--test-workers", count])
