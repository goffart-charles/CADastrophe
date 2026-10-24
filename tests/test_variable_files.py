"""Shared variables through real native rebuilds, files and common history."""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer

from cadastrophe.core.variable_files import bind_library, export_variables, read_library, reload_variables
from cadastrophe.core.expressions import Variable, Expression
from cadastrophe.core.persistence import save_document, load_document, DocumentFileError
from cadastrophe.core.body_history import body_result
from cadastrophe.core.feature_model import ResultStatus
from examples.parametric_example import registry
from test_parametric_operations import system, profile, append
from test_primitives import volume


def part(value):
    document, service = system()
    sketch = profile(document)
    body, feature, _ = append(document, service, sketch)
    document.equation_evaluator = service.evaluator
    variable = Variable('epaisseur', Expression('value', '='+str(value)))
    document.set_variables((variable,))
    document.set_equation(feature.id, 'depth', '=epaisseur')
    return document, service, body, feature, variable


def edit_file(path, change):
    data = json.loads(path.read_text(encoding='utf8'))
    change(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')


def test_two_parts_share_values_preserve_local_ids_and_reload_atomically(tmp_path):
    first, _, _, _, a = part(20.)
    second, service, body, feature, b = part(10.)
    first.set_variables((*first.variables, Variable('demi', Expression('value', '=epaisseur/2'))))
    path = tmp_path/'paramètres communs.txt'
    export_variables(first, path)
    previous_ids = set(second.objects)
    original = volume(second.objects[body.id].shape)
    bind_library(second, path)
    assert second.variables[0].id == b.id and b.id != a.id
    assert dict(second.variable_source.bindings)[a.id] == b.id
    assert volume(second.objects[body.id].shape) == pytest.approx(original*2)
    second.history.clear()
    snapshot = (dict(second.objects), second.variables, second.variable_source)
    def change(data):
        data['variables'][0]['expression']['text'] = '=35'
        data['variables'][0]['name'] = 'epaisseur_partagee'
    edit_file(path, change)
    assert second.variables == snapshot[1]  # File changes never mutate a project implicitly.
    reload_variables(second)
    assert second.history.count == 1 and set(second.objects) == previous_ids
    assert second.variables[0].id == b.id and second.variables[0].name == 'epaisseur_partagee'
    assert second.objects[feature.id].expressions[0].bindings == (('epaisseur', b.id),)
    assert body_result(second, body.id).status is ResultStatus.READY
    assert volume(second.objects[body.id].shape) == pytest.approx(original*3.5)
    assert BRepCheck_Analyzer(second.objects[body.id].shape, True).IsValid()
    second.history.undo()
    assert second.variables == snapshot[1] and second.variable_source == snapshot[2]
    assert volume(second.objects[body.id].shape) == pytest.approx(original*2)
    second.history.redo()
    project = tmp_path/'pièce.cada'
    save_document(second, project, registry=registry(service))
    with ZipFile(project) as archive:
        assert json.loads(archive.read('manifest.json'))['document']['variable_source']['path'] == path.name
    moved = tmp_path/'copie'
    moved.mkdir()
    shutil.copy2(project, moved/project.name)
    shutil.copy2(path, moved/path.name)
    restored = load_document(moved/project.name, registry=registry(service))
    assert restored.variable_source.path == str(moved/path.name)
    assert restored.objects[feature.id].expressions == second.objects[feature.id].expressions
    (moved/path.name).unlink()
    cached = load_document(moved/project.name, registry=registry(service))
    assert volume(cached.objects[body.id].shape) == pytest.approx(original*3.5)
    before = (dict(cached.objects), cached.variables, cached.variable_source, cached.history.count)
    with pytest.raises(ValueError, match='Lecture'):
        reload_variables(cached)
    assert before == (dict(cached.objects), cached.variables, cached.variable_source, cached.history.count)

    destination = os.environ.get('CADASTROPHE_SHARED_VARIABLE_EXAMPLE')
    if destination:
        directory = Path(destination).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        source, source_service, source_body, _, _ = part(20.)
        target, target_service, target_body, _, _ = part(10.)
        common = directory/'parametres.txt'
        export_variables(source, common)
        bind_library(target, common)
        assert source.id != target.id
        for name, doc, engine, solid in (
            ('01-piece-source', source, source_service, source_body),
            ('02-piece-liee', target, target_service, target_body),
        ):
            project = directory/(name+'.cada')
            save_document(doc, project, registry=registry(engine))
            restored = load_document(project, registry=registry(engine))
            assert restored.id == doc.id and restored.variables == doc.variables
            assert restored.variable_source == doc.variable_source
            assert BRepCheck_Analyzer(restored.objects[solid.id].shape, True).IsValid()
            assert volume(restored.objects[solid.id].shape) == pytest.approx(volume(doc.objects[solid.id].shape))


@pytest.mark.parametrize('fault', ['negative', 'cycle', 'unit', 'identity', 'removed'])
def test_bad_reload_keeps_the_whole_project_and_redo(fault, tmp_path):
    document, _, body, _, _ = part(20.)
    path = tmp_path/'variables.txt'
    export_variables(document, path)
    if fault == 'identity':
        change = lambda data: data.update(id=str(uuid4()))
    elif fault == 'removed':
        change = lambda data: data.update(variables=[])
    elif fault == 'unit':
        change = lambda data: data['variables'][0].update(unit='angle')
    else:
        change = lambda data: data['variables'][0]['expression'].update(text='=-2' if fault == 'negative' else '=epaisseur')
    edit_file(path, change)
    before = (dict(document.objects), document.variables, document.variable_source, document.history.count)
    with pytest.raises(ValueError):
        reload_variables(document)
    assert before == (dict(document.objects), document.variables, document.variable_source, document.history.count)
    assert BRepCheck_Analyzer(document.objects[body.id].shape, True).IsValid()


def test_export_rejects_project_dimensions_and_a_changed_shared_file(tmp_path):
    document, _, _, feature, _ = part(20.)
    path = tmp_path/'variables.txt'
    export_variables(document, path)
    edit_file(path, lambda data: data['variables'][0]['expression'].update(text='=30'))
    payload, source = path.read_bytes(), document.variable_source
    with pytest.raises(ValueError, match='modifié ailleurs'):
        export_variables(document, path)
    assert path.read_bytes() == payload and document.variable_source == source
    measured = Variable('mesure', Expression('value', '=profondeur', (('profondeur', feature.id+':depth'),)))
    document.set_variables((*document.variables, measured))
    with pytest.raises(ValueError, match='cote propre'):
        export_variables(document, tmp_path/'interdit.txt')
    assert not (tmp_path/'interdit.txt').exists()


def test_unlink_clear_and_schema7_migration_keep_values_and_native_geometry(tmp_path):
    document, service, body, _, _ = part(20.)
    path = tmp_path/'variables.txt'
    export_variables(document, path)
    source = document.variable_source
    document.update_construction(variable_source=None)
    assert document.variable_source is None and document.variables
    document.history.undo()
    assert document.variable_source == source
    document.clear()
    assert document.variable_source is None and not document.variables
    document.history.undo()
    assert document.variable_source == source and document.variables
    from cadastrophe.core.body_history import evaluate_body
    assert evaluate_body(document, body.id, service.evaluator(document), reuse=True).status is ResultStatus.READY
    project = tmp_path/'actuel.cada'
    save_document(document, project, registry=registry(service))
    old = tmp_path/'schema7.cada'
    with ZipFile(project) as archive, ZipFile(old, 'w', ZIP_DEFLATED) as target:
        for name in archive.namelist():
            payload = archive.read(name)
            if name == 'manifest.json':
                data = json.loads(payload)
                data['schema_version'] = 7
                del data['document']['variable_source']
                del data['document']['configuration_state']
                payload = json.dumps(data).encode('utf8')
            target.writestr(name, payload)
    restored = load_document(old, registry=registry(service))
    assert restored.variable_source is None and restored.variables == document.variables
    assert volume(restored.objects[body.id].shape) == pytest.approx(volume(document.objects[body.id].shape))
