"""Relocate the shipped Conda runtime once, then run the ordinary application."""
import os
from pathlib import Path
import runpy
import subprocess
import sys

root = Path(__file__).resolve().parent
runtime = root/'runtime'
marker = runtime/'.cadastrophe-prefix'
try:
    if marker.exists():
        if marker.read_text(encoding='utf8') != str(runtime):
            raise RuntimeError('Le runtime a été déplacé. Réinstaller CADastrophe dans ce dossier.')
    else:
        subprocess.run([str(runtime/'python.exe'), '-B', str(runtime/'Scripts/conda-unpack-script.py')],
                       check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        marker.write_text(str(runtime), encoding='utf8')
    if '--initialize-runtime' not in sys.argv:
        sys.path.insert(0, str(root))
        sys.argv[0] = str(root/'main.py')
        runpy.run_path(sys.argv[0], run_name='__main__')
except Exception as error:
    import traceback
    log_path = Path(os.environ.get('LOCALAPPDATA', str(root)))/'CADastrophe/logs'
    log_path.mkdir(parents=True, exist_ok=True)
    detail = traceback.format_exc()
    if isinstance(error, subprocess.CalledProcessError):
        detail += '\n'+(error.stdout or b'').decode('utf8', errors='replace')
        detail += '\n'+(error.stderr or b'').decode('utf8', errors='replace')
    (log_path/'launcher.log').write_text(detail, encoding='utf8')
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, 'Démarrage impossible. Détails : '+str(log_path/'launcher.log'), 'CADastrophe', 16)
    raise SystemExit(1)
