"""Pack the native runtime once per Windows path, preserving relocation metadata."""
import os
from hashlib import file_digest
from pathlib import Path
import sys
from zipfile import ZipFile

from conda_pack import CondaEnv
from package_content import runtime_file_allowed

runtime, output = map(Path, sys.argv[1:3])
environment = CondaEnv.from_prefix(str(runtime.resolve()))
groups = {}
duplicates = 0
for record in environment.files:
    if not runtime_file_allowed(record.target):
        continue
    key = record.target.replace('\\', '/').casefold()
    groups.setdefault(key, []).append(record)

def digest(path):
    with open(path, 'rb') as stream:
        return file_digest(stream, 'sha256').digest()

unique = {}
for key, records in groups.items():
    if len(records) == 1:
        unique[key] = records[0]
        continue
    # Multiple Conda packages can own the same Windows path. Retain the cache
    # record whose payload matches the actually installed file, without dropping
    # the prefix relocation metadata or choosing an arbitrary package version.
    installed = runtime/records[0].target
    installed_digest = digest(installed)
    matches = [record for record in records if os.path.samefile(record.source, installed)
               or digest(record.source) == installed_digest]
    metadata = {(record.is_conda, record.file_mode, record.prefix_placeholder) for record in matches}
    if not matches or len(metadata) != 1:
        raise ValueError(f'Fichiers concurrents non résolus dans le runtime : {records[0].target}')
    unique[key] = matches[0]
    duplicates += len(records)-1
environment.files = list(unique.values())
print(f'Runtime : {len(unique)} fichiers, {duplicates} déclarations de chemins partagés résolues.', flush=True)
environment.pack(output=str(output), format='zip', verbose=True)
with ZipFile(output) as archive:
    targets = [name.casefold() for name in archive.namelist()]
    if len(set(targets)) != len(targets):
        raise ValueError('Le runtime contient des chemins Windows dupliqués.')
print('Archive d’exécution sans fichiers de développement et chemins uniques.', flush=True)
