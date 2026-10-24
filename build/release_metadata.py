"""Retain the exact package inventory and available third-party notices."""
import json
from pathlib import Path
import shutil
import sys

runtime, output = map(Path, sys.argv[1:3])
packages = [json.loads(path.read_text(encoding='utf8')) for path in sorted((runtime/'conda-meta').glob('*.json'))]
notices = output/'third-party'
notices.mkdir()
for package in packages:
    cached = Path(package.get('link', {}).get('source', ''))/'info/licenses'
    if cached.is_dir():
        shutil.copytree(cached, notices/(package['name']+'-'+package['version']))
data = dict(version=sys.argv[3], python=sys.version, packages=[
    {key: package.get(key) for key in ('name', 'version', 'build', 'channel', 'license', 'url', 'sha256')}
    for package in packages])
(output/'release.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')

# Inno Setup 6 uses MAX_PATH APIs. Reserve two characters beyond the separator
# so a long destination is rejected before extraction instead of rolling back.
longest = max(len(str(path.relative_to(output))) for path in output.rglob('*') if path.is_file())
(output.parent/'installer-limits.iss').write_text(
    f'#define MaxInstallDirLength {257-longest}\n', encoding='utf8')
