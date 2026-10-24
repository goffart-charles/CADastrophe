"""Native reconstruction of transported equations, variants and shared links."""
from dataclasses import replace
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer

from cadastrophe.core.clipboard import ObjectClipboard, ClipboardError, encode_objects, decode_objects, MIME_TYPE
from cadastrophe.core.configurations import create, activate, set_override
from cadastrophe.core.expressions import Variable, Expression
from cadastrophe.core.persistence import save_document, load_document
from cadastrophe.core.variable_files import export_variables, bind_library, reload_variables
from cadastrophe.scene import new_document
from examples.parametric_example import registry
from test_clipboard import MemoryClipboard, volume
from test_variable_files import part, edit_file
from test_extrusion_tool import extrusion_system


def snapshot(doc):
    return (dict(doc.objects), doc.variables, doc.variable_source, doc.configuration_state,
            doc.selected_ids, doc.history.count)


def driven():
    doc, service, body, feature, variable = part(10.)
    doc.set_parameter_scope('common')
    doc.set_variables((*doc.variables, Variable('double', Expression('value', '=epaisseur*2')),
                       Variable('inutile', Expression('value', '=123'))))
    doc.set_equation(feature.id, 'depth', '=double')
    set_override(doc, doc.default_configuration.id, feature.id+':depth', None)
    variant = create(doc, 'Épaisse')
    set_override(doc, variant, variable.id, Expression('value', '=15'))
    activate(doc, variant)
    doc.history.clear()
    return doc, service, body, feature, variant


def test_driven_clipboard_transitive_variables_variants_edit_rename_undo_reopen(tmp_path):
    source, service, body, feature, variant = driven()
    codecs = registry(service)
    copier = ObjectClipboard(MemoryClipboard(), codecs)
    before = snapshot(source)
    assert copier.copy(source, (body.id,)) == 1
    assert snapshot(source) == before
    data = json.loads(bytes(copier.clipboard.mimeData().data(MIME_TYPE)))
    assert data['schema_version'] == 3
    assert {v['name'] for v in data['parameters']['variables']} == {'epaisseur', 'double'}
    target = new_document('Cible pilotée')
    target.equation_evaluator = service.evaluator
    initial = snapshot(target)
    pasted, = copier.paste(target)
    assert target.history.count == 1 and target.selected_ids == (pasted,)
    assert target.active_configuration.id == target.default_configuration.id
    assert volume(target.objects[pasted].shape) == pytest.approx(200000.)
    assert not {v.id for v in target.variables} & {v.id for v in source.variables}
    assert not set(target.objects) & set(source.objects) - set(initial[0])
    thick = next(c.id for c in target.configurations if c.name == 'Épaisse')
    activate(target, thick)
    assert volume(target.objects[pasted].shape) == pytest.approx(300000.)
    assert BRepCheck_Analyzer(target.objects[pasted].shape).IsValid()
    target.set_parameter_scope('common')
    target.set_variables(tuple(replace(v, name='epaisseur_renommee') if v.name == 'epaisseur' else v for v in target.variables))
    assert volume(target.objects[pasted].shape) == pytest.approx(300000.)
    activate(target, target.default_configuration.id)
    target.set_variables(tuple(replace(v, expression=Expression('value', '=12')) if v.name == 'epaisseur_renommee' else v for v in target.variables))
    assert volume(target.objects[pasted].shape) == pytest.approx(240000.)
    path = tmp_path/'copie-pilotee.cada'
    save_document(target, path, registry=codecs)
    restored = load_document(path, registry=codecs)
    assert restored.variables == target.variables
    assert restored.configuration_state == target.configuration_state
    assert volume(restored.objects[pasted].shape) == pytest.approx(240000.)
    while target.history.can_undo:
        target.history.undo()
    assert snapshot(target)[:5] == initial[:5]
    target.history.redo()
    assert volume(target.objects[pasted].shape) == pytest.approx(200000.)
    assert snapshot(source) == before
    output = os.environ.get('CADASTROPHE_DRIVEN_CLIPBOARD_EXAMPLE')
    if output:
        directory = Path(output).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        save_document(source, directory/'01-source.cada', registry=codecs)
        save_document(target, directory/'02-copie.cada', registry=codecs)


def test_driven_clipboard_same_document_independent_variables_and_named_variants():
    doc, service, body, feature, variant = driven()
    copier = ObjectClipboard(MemoryClipboard(), registry(service))
    before = snapshot(doc)
    copier.copy(doc, (body.id,))
    pasted, = copier.paste(doc)
    assert len(doc.configurations) == 2
    assert volume(doc.objects[pasted].shape) == pytest.approx(300000.)
    assert {'epaisseur_copie', 'double_copie'} <= {v.name for v in doc.variables}
    assert 'epaisseur → epaisseur_copie' in copier.last_message
    doc.set_parameter_scope('active')
    doc.set_variables(tuple(replace(v, expression=Expression('value', '=18')) if v.name == 'epaisseur_copie' else v for v in doc.variables))
    assert volume(doc.objects[pasted].shape) == pytest.approx(360000.)
    assert volume(doc.objects[body.id].shape) == pytest.approx(300000.)
    doc.history.undo()
    doc.history.undo()
    assert snapshot(doc)[:5] == before[:5]
    doc.history.redo()
    assert volume(doc.objects[pasted].shape) == pytest.approx(300000.)


def test_driven_clipboard_shared_library_reuse_reload_and_conflict_atomic(tmp_path):
    doc, service, body, feature, _ = part(10.)
    library = tmp_path/'parametres.txt'
    export_variables(doc, library)
    copier = ObjectClipboard(MemoryClipboard(), registry(service))
    copier.copy(doc, (body.id,))
    target = new_document('Bibliothèque cible')
    target.equation_evaluator = service.evaluator
    pasted, = copier.paste(target)
    assert len(target.variables) == 1 and target.variable_source.path == str(library)
    first = target.variables[0].id
    copier.paste(target)
    assert len(target.variables) == 1 and target.variables[0].id == first
    edit_file(library, lambda data: data['variables'][0]['expression'].update(text='=25'))
    reload_variables(target)
    assert all(volume(o.shape) == pytest.approx(250000.) for o in target.objects.values() if getattr(o, 'features', ()))
    before = snapshot(target)
    with pytest.raises(ClipboardError, match='incompatibles'):
        copier.paste(target)
    assert snapshot(target) == before
    reload_variables(doc)
    copier.copy(doc, (body.id,))
    copier.paste(target)
    assert len(target.variables) == 1
    alien, _, _, _, _ = part(10.)
    export_variables(alien, tmp_path/'autre.txt')
    before = snapshot(alien)
    with pytest.raises(ClipboardError, match='incompatibles'):
        copier.paste(alien)
    assert snapshot(alien) == before
    path = tmp_path/'liee.cada'
    save_document(target, path, registry=registry(service))
    library.unlink()
    offline = load_document(path, registry=registry(service))
    assert offline.variable_source == target.variable_source
    assert volume(offline.objects[pasted].shape) == pytest.approx(250000.)


@pytest.mark.parametrize('damage', ['variable', 'binding', 'inactive', 'scope', 'source'])
def test_driven_clipboard_corruption_rejected_before_publication(damage):
    doc, service, body, _, _ = driven()
    data = json.loads(encode_objects(doc, (body.id,), registry(service)))
    if damage == 'variable':
        data['parameters']['variables'].pop(0)
    elif damage == 'binding':
        exp = next(e['object']['expressions'][0] for e in data['objects'] if 'expressions' in e['object'])
        exp['bindings'][0][1] = str(uuid4())
    elif damage == 'inactive':
        data['parameters']['configuration_state']['configurations'][0]['overrides'].append(
            dict(target=data['parameters']['variables'][0]['id'], kind='expression', value=dict(target='value', text='=0/0', bindings=[], scale=1.)))
    elif damage == 'scope':
        data['parameters']['configuration_scope'] = 'active'
    else:
        data['parameters']['variable_source'] = dict(path='relative.txt', library_id=str(uuid4()), digest='0'*64, bindings=[])
    before = snapshot(doc)
    with pytest.raises(ClipboardError):
        decode_objects(json.dumps(data).encode(), registry(service))
    assert snapshot(doc) == before


def test_driven_clipboard_native_actions_transport_variants_and_reopen(extrusion_system, tmp_path):
    from PySide6.QtTest import QTest
    doc, service, body, feature, _ = driven()
    path = tmp_path/'source.cada'
    save_document(doc, path, registry=registry(service))
    w, _, _ = extrusion_system
    source = w.open_document(path)
    QTest.qWait(150)
    w.tree_selection_changed((body.id,))
    w.command_registry.execute('edit.copy')
    target = w.create_document()
    QTest.qWait(150)
    w.command_registry.execute('edit.paste')
    assert target.selected_ids, w.hint.text()
    pasted, = target.selected_ids
    assert 'Toutes les configurations' in w.hint.text()
    assert volume(target.objects[pasted].shape) == pytest.approx(200000.)
    thick = next(c.id for c in target.configurations if c.name == 'Épaisse')
    activate(target, thick)
    assert volume(target.objects[pasted].shape) == pytest.approx(300000.)
    assert pasted in w.viewport.presentations
    assert volume(source.objects[body.id].shape) == pytest.approx(300000.)
    output = tmp_path/'native-copie.cada'
    assert w.save_document(path=output) and w.close_document()
    restored = w.open_document(output)
    assert volume(restored.objects[pasted].shape) == pytest.approx(300000.)


def test_driven_clipboard_dimension_dependency_closure_and_units(tmp_path):
    from test_parametric_operations import system, profile, append
    from cadastrophe.core.feature_objects import BodyObject
    doc, service = system()
    doc.equation_evaluator = service.evaluator
    sketch = profile(doc)
    master, feature, _ = append(doc, service, sketch)
    doc.update_construction((replace(doc.objects[feature.id], name='Maître'),))
    child, child_feature, _ = append(doc, service, sketch)
    doc.set_variables((Variable('facteur', Expression('value', '=2'), unit='number'),))
    doc.set_equation(child_feature.id, 'depth', '=[D1@Maître]*facteur')
    copier = ObjectClipboard(MemoryClipboard(), registry(service))
    copier.copy(doc, (child.id,))
    target = new_document()
    target.equation_evaluator = service.evaluator
    pasted, = copier.paste(target)
    assert volume(target.objects[pasted].shape) == pytest.approx(200000.)
    bodies = [o for o in target.objects.values() if isinstance(o, BodyObject)]
    assert len(bodies) == 2
    master_copy = next(o for o in bodies if o.id != pasted)
    copied_feature = target.objects[master_copy.features[0]]
    target.update_construction((replace(copied_feature, parameters=replace(copied_feature.parameters, depth=12.)),))
    assert volume(target.objects[pasted].shape) == pytest.approx(240000.)
    assert volume(doc.objects[child.id].shape) == pytest.approx(200000.)
    assert target.variables[0].unit == 'number'
    # A document variable still consuming a source dimension prevents cut.
    doc.set_variables((*doc.variables, Variable('cote_source', Expression('value', '=[D1@Maître]'))))
    before = snapshot(doc)
    with pytest.raises(ClipboardError):
        copier.copy(doc, (master.id,), cut=True)
    assert snapshot(doc) == before


def test_driven_clipboard_cut_transport_failure_and_success_undo():
    doc, service, body, feature, _ = driven()
    class Rejecting(MemoryClipboard):
        def setMimeData(self, mime):
            pass
    before = snapshot(doc)
    with pytest.raises(ClipboardError):
        ObjectClipboard(Rejecting(), registry(service)).copy(doc, (body.id,), cut=True)
    assert snapshot(doc) == before
    copier = ObjectClipboard(MemoryClipboard(), registry(service))
    copier.copy(doc, (body.id,), cut=True)
    assert body.id not in doc.objects and feature.id not in doc.objects
    assert doc.variables == before[1]
    doc.history.undo()
    assert snapshot(doc)[:5] == before[:5]
    doc.history.redo()
    pasted, = copier.paste(doc)
    assert volume(doc.objects[pasted].shape) == pytest.approx(300000.)


def test_driven_clipboard_configuration_correspondence_and_ambiguous_mapping_refused():
    from cadastrophe.core.configurations import rename
    doc, service, body, feature, _ = driven()
    copier = ObjectClipboard(MemoryClipboard(), registry(service))
    copier.copy(doc, (body.id,))
    target = new_document()
    target.equation_evaluator = service.evaluator
    extra = create(target, 'Autre variante')
    activate(target, extra)
    pasted, = copier.paste(target)
    assert target.active_configuration.id == extra
    assert volume(target.objects[pasted].shape) == pytest.approx(200000.)
    assert {c.name for c in target.configurations} == {'Configuration par défaut', 'Autre variante', 'Épaisse'}
    conflict = new_document()
    rename(conflict, conflict.default_configuration.id, 'Épaisse')
    before = snapshot(conflict)
    with pytest.raises(ClipboardError, match='même configuration'):
        copier.paste(conflict)
    assert snapshot(conflict) == before


def test_driven_clipboard_shared_local_name_and_variant_conflicts(tmp_path):
    doc, service, body, _, _ = part(10.)
    path = tmp_path/'commun.txt'
    export_variables(doc, path)
    copier = ObjectClipboard(MemoryClipboard(), registry(service))
    copier.copy(doc, (body.id,))
    local, _, _, _, _ = part(10.)
    before = snapshot(local)
    with pytest.raises(ClipboardError, match='conflit avec une variable locale'):
        copier.paste(local)
    assert snapshot(local) == before
    bind_library(local, path)
    set_override(local, local.default_configuration.id, local.variables[0].id, Expression('value', '=20'))
    before = snapshot(local)
    with pytest.raises(ClipboardError, match='Substitution incompatible'):
        copier.paste(local)
    assert snapshot(local) == before


def test_driven_clipboard_keeps_expression_scale_and_static_native_source(tmp_path):
    from cadastrophe.core.feature_objects import BodyObject
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    doc, service, body, feature, _ = part(10.)
    doc.set_equation(feature.id, 'depth', '=1', scale=25.4)
    shape = BRepPrimAPI_MakeBox(2., 3., 4.).Shape()
    static = BodyObject('Base statique', shape, (.3, .5, .8), source_shape=shape, source_kind='static')
    doc.update_construction((static,))
    copier = ObjectClipboard(MemoryClipboard(), registry(service))
    copier.copy(doc, (body.id, static.id))
    target = new_document()
    roots = copier.paste(target)
    copied = next(target.objects[key] for key in roots if target.objects[key].features)
    copied_static = next(target.objects[key] for key in roots if not target.objects[key].features)
    assert volume(copied.shape) == pytest.approx(254000.)
    assert volume(copied_static.shape) == pytest.approx(24.)
    assert copied_static.source_kind == 'static'
    expression = target.objects[copied.features[0]].expressions[0]
    assert expression.text == '=1' and expression.scale == 25.4
    path = tmp_path/'unites-et-base.cada'
    save_document(target, path, registry=registry(service))
    restored = load_document(path, registry=registry(service))
    assert volume(restored.objects[copied.id].shape) == pytest.approx(254000.)
    assert volume(restored.objects[copied_static.id].shape) == pytest.approx(24.)


def test_driven_clipboard_invalid_inactive_geometry_never_cuts_source():
    doc, service, body, feature, _ = part(10.)
    bad = create(doc, 'Profondeur nulle')
    set_override(doc, bad, feature.id+':depth', 0.)
    before = snapshot(doc)
    copier = ObjectClipboard(MemoryClipboard(), registry(service))
    with pytest.raises(ClipboardError):
        copier.copy(doc, (body.id,), cut=True)
    assert snapshot(doc) == before
    assert not copier.available
