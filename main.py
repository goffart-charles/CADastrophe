"""Launch from PyCharm or a shell using the isolated native runtime when present."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    root = Path(__file__).resolve().parent
    runtime = root / ".tools/mamba/envs/.occt-env"
    executable = runtime / "python.exe"
    # PyCharm may expose this runtime's packages to a different Python version.
    # Finding their Python modules does not mean its native extensions can load.
    if executable.exists() and Path(sys.executable).resolve() != executable.resolve():
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join((str(runtime), str(runtime / "Library/bin"),
                                       str(runtime / "Scripts"), env.get("PATH", "")))
        return subprocess.call([str(executable), str(Path(__file__).resolve()), *sys.argv[1:]], env=env)
    if "--test-parallel" in sys.argv:
        from cadastrophe.test_runner import run_parallel, parse_workers
        return run_parallel(Path(__file__).resolve(), workers=parse_workers(sys.argv[1:]))
    if sys.platform == "win32" and any(flag in sys.argv for flag in ("--test", "--smoke")):
        from cadastrophe.test_desktop import WORKER_ENV, run_on_test_desktop
        if "--visible-tests" not in sys.argv and not os.environ.get(WORKER_ENV):
            return run_on_test_desktop(Path(__file__).resolve(), sys.argv[1:], timeout=600 if "--test" in sys.argv else 300)
    if importlib.util.find_spec("OCC") is None or importlib.util.find_spec("PySide6") is None:
        print("Dépendances absentes. Créer l'environnement décrit dans environment.yml.", file=sys.stderr)
        return 1
    # Keep DLL search handles alive for the duration of the process (Windows).
    dll_handles = []
    if sys.platform == "win32":
        library = Path(sys.prefix) / "Library/bin"
        if library.exists():
            dll_handles.append(os.add_dll_directory(str(library)))
    if "--test" in sys.argv:
        import pytest
        from cadastrophe.test_runner import SHARD_ENV, ShardSelection
        selection = ShardSelection(os.environ[SHARD_ENV]) if os.environ.get(SHARD_ENV) else None
        paths = selection.paths(root) if selection is not None else [str(root / "tests")]
        return pytest.main([*paths, "--rootdir", str(root), "-q", "-p", "no:cacheprovider"],
                           plugins=[selection] if selection is not None else [])
    if "--probe" in sys.argv:
        from cadastrophe.diagnostics import probe
        probe()
        return 0
    if "--smoke" in sys.argv:
        from cadastrophe.diagnostics import run_smoke
        return run_smoke()
    from cadastrophe.app import run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
