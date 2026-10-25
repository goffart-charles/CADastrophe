"""Native test workers, with shared clipboard scenarios in a serial phase.

Modules stay together: their fixtures and exported images belong to one worker.
No pytest extension or application runtime dependency is required.
"""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4
import argparse

SHARD_ENV = "CADASTROPHE_TEST_SHARD"
SHARED_STATE = re.compile(r"clipboard|\b(?:copy|paste)\b|edit\.(?:copy|cut|paste)|Key_[CVX]", re.I)


class ShardSelection:
    def __init__(self, path):
        self.nodeids = set(json.loads(Path(path).read_text(encoding="utf8")))

    def paths(self, root):
        """Import only assigned modules, with the same root-relative node IDs."""
        return [str(root / path) for path in sorted({node.split("::", 1)[0] for node in self.nodeids})]

    def pytest_collection_modifyitems(self, config, items):
        removed = [item for item in items if item.nodeid not in self.nodeids]
        items[:] = [item for item in items if item.nodeid in self.nodeids]
        config.hook.pytest_deselected(items=removed)


class Collection:
    def __init__(self):
        self.modules = defaultdict(list)
        self.costs = defaultdict(float)

    def pytest_collection_finish(self, session):
        for item in session.items:
            path = str(item.path)
            self.modules[path].append(item.nodeid)
            gui = any(name in item.fixturenames for name in ("system", "extrusion_system", "window", "qt_app", "app"))
            # Native graph benchmarks can cost minutes without a Qt fixture.
            # Their measured weight keeps them out of already full GUI lots.
            self.costs[path] += getattr(item.module, "NATIVE_TEST_COST", 1. if gui else .1)


def partition(modules, costs, *, workers=2):
    groups, loads, serial = [[] for _ in range(workers)], [0.] * workers, []
    for path in sorted(modules, key=lambda p: (-costs[p], p)):
        # Clipboard is shared by desktops in the same Windows window station.
        # Keep any module exercising copy/paste outside the concurrent phase.
        if SHARED_STATE.search(Path(path).read_text(encoding="utf8")):
            serial.extend(modules[path])
        else:
            index = min(range(workers), key=loads.__getitem__)
            groups[index].extend(modules[path])
            loads[index] += costs[path]
    return [group for group in groups if group], serial


def parse_workers(arguments):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--test-workers", type=int, choices=range(1, 9), default=min(4, os.cpu_count() or 1))
    options, _ = parser.parse_known_args(arguments)
    return options.test_workers


def serial_batches(nodeids, costs, *, maximum_cost=200.):
    """Bound shared-state lots without splitting modules or running them together."""
    modules = defaultdict(list)
    for nodeid in nodeids:
        modules[nodeid.split("::", 1)[0]].append(nodeid)
    batches, current, load = [], [], 0.
    for path, selected in modules.items():
        cost = costs[path]
        if current and load + cost > maximum_cost:
            batches.append(current)
            current, load = [], 0.
        current.extend(selected)
        load += cost
    if current:
        batches.append(current)
    return batches


def run_parallel(script, *, workers=4):
    import pytest
    started = time.monotonic()
    collection = Collection()
    output = StringIO()
    with redirect_stdout(output):
        code = pytest.main([str(script.parent / "tests"), "--rootdir", str(script.parent),
                            "--collect-only", "-q", "-p", "no:cacheprovider"], plugins=[collection])
    if code:
        print(output.getvalue())
        return int(code)
    groups, serial = partition(collection.modules, collection.costs, workers=workers)
    root = script.parent / "artifacts"
    root.mkdir(exist_ok=True)
    run_id = uuid4().hex[:10]

    def execute(index, nodeids):
        path = root / f"test-shard-{run_id}-{index}.json"
        path.write_text(json.dumps(nodeids), encoding="utf8")
        environment = {SHARD_ENV: str(path)}
        print(f"Lot {index} : {len(nodeids)} tests", flush=True)
        if sys.platform == "win32":
            from .test_desktop import run_on_test_desktop
            return run_on_test_desktop(script, ["--test"], timeout=600, environment=environment)
        return subprocess.call([sys.executable, str(script), "--test"], cwd=script.parent,
                               env=dict(os.environ, **environment))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(execute, index+1, group) for index, group in enumerate(groups)]
        codes = [future.result() for future in futures]
    if serial:
        print("Phase isolee : tests utilisant le presse-papiers.", flush=True)
        module_costs = {nodeids[0].split("::", 1)[0]: collection.costs[path]
                        for path, nodeids in collection.modules.items()}
        for index, batch in enumerate(serial_batches(serial, module_costs), 1):
            codes.append(execute(f"serial-{index}", batch))
    code = next((code for code in codes if code), 0)
    print(f"Tests termines en {time.monotonic()-started:.2f} s ; code {code}.", flush=True)
    return code
