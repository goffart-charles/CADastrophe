"""Durable personal catalog and safe failures."""
import json

import pytest

from cadastrophe.core.material_library import MaterialLibrary
from cadastrophe.core.materials import Material


def test_material_library_persists_unicode_and_rejects_name_collisions(tmp_path):
    path = tmp_path/'config'/'materials.json'
    library = MaterialLibrary(path)
    builtin = library.materials
    material = Material('  Résine très légère  ', 1050.25)
    library.add(material)
    reopened = MaterialLibrary(path)
    assert reopened.materials == builtin + (material,)
    assert reopened.custom_materials == (material,) and not reopened.error
    saved = path.read_bytes()
    for name in ('résine très légère', ' ACIER '):
        with pytest.raises(ValueError, match='déjà ce nom'):
            reopened.add(Material(name, 2000))
    assert path.read_bytes() == saved and reopened.materials == library.materials


@pytest.mark.parametrize('payload', ['{', json.dumps({'schema_version': 2, 'materials': []}),
    json.dumps({'schema_version': 1, 'materials': [None]}),
    json.dumps({'schema_version': 1, 'materials': [{'name': 'Bad', 'density_kg_m3': -1}]})])
def test_material_library_preserves_invalid_file_and_keeps_presets(tmp_path, payload):
    path = tmp_path/'materials.json'
    path.write_text(payload, encoding='utf-8')
    library = MaterialLibrary(path)
    assert library.error and library.materials == MaterialLibrary().materials
    with pytest.raises(ValueError, match='conservée'):
        library.add(Material('Nouveau', 1500))
    assert path.read_text(encoding='utf-8') == payload


def test_material_library_failed_write_is_atomic(tmp_path, monkeypatch):
    path = tmp_path/'materials.json'
    library = MaterialLibrary(path)
    library.add(Material('Premier', 1500))
    original = path.read_bytes()

    def fail_replace(*_):
        raise PermissionError('Fichier verrouillé')

    monkeypatch.setattr('cadastrophe.core.material_library.os.replace', fail_replace)
    with pytest.raises(PermissionError):
        library.add(Material('Second', 1600))
    assert path.read_bytes() == original
    assert library.custom_materials == (Material('Premier', 1500),)
    assert not list(tmp_path.glob('.materials-*.tmp'))
