"""Copy application code and assets, excluding development material."""
from pathlib import Path
import shutil
import sys

from package_content import application_file_allowed


repository, output = map(Path, sys.argv[1:3])
for folder in ('cadastrophe', 'plugins'):
    for source in (repository/folder).rglob('*'):
        relative = source.relative_to(repository)
        if source.is_file() and application_file_allowed(relative):
            destination = output/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
shutil.copy2(repository/'build/entrypoint.py', output/'main.py')
shutil.copy2(repository/'build/bootstrap.py', output/'bootstrap.py')

# Remove only the previously shipped application development files on upgrade.
# The runtime is replaced separately; custom plugins and unknown files remain.
obsolete = {'README.md', 'environment.yml', 'installer-limits.iss'}
for folder in ('cadastrophe', 'plugins', 'examples'):
    for source in (repository/folder).rglob('*'):
        relative = source.relative_to(repository)
        if (source.is_file() and '__pycache__' not in relative.parts
                and (folder == 'examples' or not application_file_allowed(relative))
                and relative.as_posix() != 'plugins/settings.json'):
            obsolete.add(str(relative))
obsolete.add('cadastrophe/sample_scene.py')
(output.parent/'obsolete-files.iss').write_text(''.join(
    f'Type: files; Name: "{{app}}\\{name}"; Check: ExistingInstallation\n'
    for name in sorted(obsolete)), encoding='utf-8-sig')
