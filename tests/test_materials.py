"""Native physical properties, persistent materials, history and v6 migration."""
from dataclasses import replace
import json
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox

from cadastrophe.document import SceneObject
from cadastrophe.scene import new_document
from cadastrophe.core.clipboard import encode_objects, decode_objects
from cadastrophe.core.materials import Material
from cadastrophe.core.persistence import save_document, load_document
from cadastrophe.core.units import DisplayUnits
from plugins.measurements.geometry import measure, MeasurementError


def body(document, name='Corps'):
    obj = SceneObject(name, BRepPrimAPI_MakeBox(10., 20., 30.).Shape(), (.5, .5, .5))
    document.add(obj)
    return obj


def test_exact_volume_and_mass_units_and_multi_body_density():
    document = new_document()
    first, second = body(document), body(document, 'Second')
    result = measure('volume', [first.shape])
    assert result.value == pytest.approx(6000.)
    assert result.formatted(DisplayUnits('cm', 3)) == '6.000 cm³'
    result = measure('mass', [first.shape, second.shape], densities=[1250., 2500.])
    assert result.value == pytest.approx(22.5)
    assert result.formatted(DisplayUnits('mm', 3)) == '22.500 g'
    with pytest.raises(MeasurementError, match='matériau'):
        measure('mass', [first.shape], densities=[None])
    with pytest.raises(MeasurementError, match='solides'):
        plane = next(iter(document.objects.values()))
        measure('volume', [plane.shape])


def test_material_is_atomic_undoable_and_survives_file_and_clipboard(tmp_path):
    document = new_document()
    obj = body(document)
    original_revision = obj.revision
    document.history.clear()
    material = Material('Matière d’essai', 1250.)
    document.set_materials({obj.id: material})
    assert document.history.count == 1
    assert document.objects[obj.id].shape.IsSame(obj.shape)
    assert document.objects[obj.id].revision == original_revision
    assert material.mass_grams(measure('volume', [obj.shape]).value) == pytest.approx(7.5)
    document.history.undo()
    assert document.objects[obj.id].material is None
    document.history.redo()
    assert document.objects[obj.id].material == material
    path = tmp_path/'matériau.cada'
    save_document(document, path)
    restored = load_document(path)
    assert restored.id == document.id and restored.objects[obj.id].material == material
    copied, = decode_objects(encode_objects(document, [obj.id]))
    assert copied.id != obj.id and copied.material == material
    assert copied.shape.IsSame(obj.shape) is False
    document.set_materials({obj.id: None})
    assert document.objects[obj.id].material is None
    document.history.undo()
    assert document.objects[obj.id].material == material


def test_mass_resolves_recipe_and_profile_to_body_and_material_survives_rebuild(tmp_path):
    from PySide6.QtWidgets import QApplication
    from cadastrophe.core.documents import DocumentManager
    from cadastrophe.plugin_api import ApplicationServices
    from plugins.measurements.panel import MeasurementsPanel
    from test_parametric_operations import system, profile, append
    from examples.parametric_example import registry
    app = QApplication.instance() or QApplication([])
    doc, service = system()
    doc.equation_evaluator = service.evaluator
    sketch = profile(doc, points=((0, 0), (10, 0), (10, 20), (0, 20)))
    owner, feature, _ = append(doc, service, sketch)
    manager = DocumentManager()
    manager.add(doc)
    services = ApplicationServices(manager)
    panel = MeasurementsPanel(services, None)
    try:
        for selected in (sketch.id, feature.id):
            doc.select_objects((selected,))
            panel.set_mode('mass')
            assert doc.selected_ids == (owner.id,)
            assert panel.material_targets() == (owner.id,)
        panel.material_name.setText('Alliage')
        panel.density.setValue(2700.)
        panel.assign_material()
        assert doc.objects[owner.id].material == Material('Alliage', 2700.)
        assert panel.last_result.value == pytest.approx(5.4)
        doc.update_construction((replace(feature, parameters=replace(feature.parameters, depth=20.)),))
        from cadastrophe.core.body_history import evaluate_body
        from cadastrophe.core.feature_model import ResultStatus
        result = evaluate_body(doc, owner.id, service.evaluator(doc), reuse=True)
        assert result.status is ResultStatus.READY, result.message
        doc.select_objects((owner.id,))
        panel.refresh()
        assert panel.last_result is not None, panel.detail.text()
        assert panel.last_result.value == pytest.approx(10.8)
        assert doc.objects[owner.id].material == Material('Alliage', 2700.)
        path = tmp_path/'materiau-parametrique.cada'
        save_document(doc, path, registry=registry(service))
        loaded = load_document(path, registry=registry(service))
        assert loaded.objects[owner.id].material == Material('Alliage', 2700.)
    finally:
        panel.timer.stop()
        panel.deleteLater()
        manager.deleteLater()
        app.processEvents()


def test_schema6_migration_preserves_breps_and_ids_without_inventing_material(tmp_path):
    document = new_document()
    obj = body(document)
    path = tmp_path/'v7.cada'
    save_document(document, path)
    legacy = tmp_path/'v6.cada'
    with ZipFile(path) as archive, ZipFile(legacy, 'w', compression=ZIP_DEFLATED) as target:
        for name in archive.namelist():
            payload = archive.read(name)
            if name == 'manifest.json':
                data = json.loads(payload)
                assert data['schema_version'] == 9
                data['schema_version'] = 6
                del data['document']['variable_source']
                del data['document']['configuration_state']
                for record in data['objects']:
                    del record['material']
                payload = json.dumps(data).encode('utf8')
            target.writestr(name, payload)
    restored = load_document(legacy)
    assert restored.id == document.id and tuple(restored.objects) == tuple(document.objects)
    assert all(item.material is None for item in restored.objects.values())
    assert measure('volume', [restored.objects[obj.id].shape]).value == pytest.approx(6000.)


@pytest.mark.parametrize('density', [0., -1., float('nan'), float('inf'), True])
def test_invalid_material_is_rejected_before_mutation(density):
    with pytest.raises(ValueError):
        Material('Essai', density)
