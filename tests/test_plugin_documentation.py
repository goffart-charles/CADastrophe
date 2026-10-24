"""Execute the public quickstart exactly as copied by a plugin author."""
from pathlib import Path
import re

from test_plugins import harness, plugin_root


def test_plugin_documentation_quickstart_activates_executes_and_unloads(harness):
    text = (Path(__file__).parent.parent/'plugins/docs/README.md').read_text(encoding='utf8')
    manifest = re.search(r'```json\n(.*?)\n```', text, re.S).group(1)
    source = re.search(r'```python\n(.*?)\n```', text, re.S).group(1)
    directory = harness.root/'informations'
    directory.mkdir()
    (directory/'plugin.json').write_text(manifest, encoding='utf8')
    (directory/'plugin.py').write_text(source, encoding='utf8')
    messages = []
    harness.services.message.connect(messages.append)
    harness.manager.startup()
    record, = harness.manager.records
    assert record.state == 'active', record.error
    assert harness.registry.execute('exemple.informations.compter')
    assert messages[-1] == f'{harness.doc.display_name} : 1 objets'
    assert harness.doc.history.count == 0
    assert harness.manager.deactivate('exemple.informations')
    assert 'exemple.informations.compter' not in {command.id for command in harness.registry}
