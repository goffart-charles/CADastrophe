"""Reject development files and missing entrypoints in the staged product."""
from pathlib import Path
import struct
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
for required in ('CADastrophe.exe', 'CADastrophe.ico', 'main.py', 'bootstrap.py', 'runtime/python.exe',
                 'runtime/pythonw.exe', 'runtime/Scripts/conda-unpack-script.py',
                 'cadastrophe/app.py', 'plugins/modeling/plugin.json'):
    if not (output/required).is_file():
        raise SystemExit('Fichier requis absent : '+required)
if unexpected:
    raise SystemExit('Fichiers de développement dans le paquet :\n'+'\n'.join(unexpected[:30]))
icon = (output/'CADastrophe.ico').read_bytes()
if len(icon) < 6 or struct.unpack_from('<HHH', icon) != (0, 1, 7):
    raise SystemExit('Icône Windows multirésolution absente ou invalide.')
sizes = set()
for index in range(7):
    width, height, _, _, planes, depth, length, offset = struct.unpack_from('<BBBBHHII', icon, 6+16*index)
    sizes.add(width or 256)
    if (width != height or planes != 1 or depth != 32 or offset < 118
            or length < 24 or offset+length > len(icon) or icon[offset:offset+8] != b'\x89PNG\r\n\x1a\n'
            or struct.unpack_from('>II', icon, offset+16) != (width or 256, height or 256)):
        raise SystemExit('Image invalide dans l’icône Windows.')
if sizes != {16, 24, 32, 48, 64, 128, 256}:
    raise SystemExit('Tailles manquantes dans l’icône Windows.')
if sys.platform == 'win32':
    import ctypes
    from ctypes import wintypes
    extract = ctypes.WinDLL('shell32', use_last_error=True).ExtractIconExW
    extract.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.HICON),
                        ctypes.POINTER(wintypes.HICON), wintypes.UINT]
    extract.restype = wintypes.UINT
    for name in ('CADastrophe.exe', 'CADastrophe.ico'):
        if extract(str((output/name).resolve()), -1, None, None, 0) != 1:
            raise SystemExit('Ressource d’icône Windows absente ou invalide : '+name)
print('Audit du paquet : aucun fichier de développement, test ou exemple.', flush=True)
print('Icône Windows validée : sept résolutions et ressource intégrée au lanceur.', flush=True)
