"""Reject development files and missing entrypoints in the staged product."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'build'))
from package_content import application_file_allowed, runtime_file_allowed


output = Path(sys.argv[1])
generated = {'scripts/conda-unpack-script.py', 'scripts/conda-unpack.exe',
             'scripts/conda_unpack_progress.py',
             'scripts/activate', 'scripts/activate.bat', 'scripts/deactivate.bat'}
unexpected = []
for path in output.rglob('*'):
    if not path.is_file():
        continue
    relative = path.relative_to(output).as_posix()
    if relative.startswith('runtime/'):
        runtime_path = relative.removeprefix('runtime/')
        if runtime_path.lower() not in generated and not runtime_file_allowed(runtime_path):
            unexpected.append(relative)
    elif relative.startswith(('cadastrophe/', 'plugins/')):
        if not application_file_allowed(relative):
            unexpected.append(relative)
    elif relative.startswith(('examples/', 'tests/', 'build/', 'doc-dev/')):
        unexpected.append(relative)
for required in ('CADastrophe.exe', 'main.py', 'bootstrap.py', 'runtime/python.exe',
                 'runtime/pythonw.exe', 'runtime/Scripts/conda-unpack-script.py',
                 'cadastrophe/app.py', 'plugins/modeling/plugin.json'):
    if not (output/required).is_file():
        raise SystemExit('Fichier requis absent : '+required)
if unexpected:
    raise SystemExit('Fichiers de développement dans le paquet :\n'+'\n'.join(unexpected[:30]))
print('Audit du paquet : aucun fichier de développement, test ou exemple.', flush=True)
