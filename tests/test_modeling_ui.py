"""Presentation guards for uncomputed construction bodies on the private desktop."""
from dataclasses import replace

from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from PySide6.QtWidgets import QApplication

from cadastrophe.document import ObjectType
from cadastrophe.core.body_history import BodyHistory
from cadastrophe.core.feature_model import Operation, EvaluationResult, ResultStatus, ExtrusionParameters
from cadastrophe.geometry import Ray, Vec3
from test_sketch_mode import system
from test_modeling_history import construction


def test_uncomputed_body_selection_rendering_invalidation_visibility_and_undo(system):
    window, commands, _, _ = system
    doc, vp = window.viewport.document, window.viewport
    doc.remove_objects([obj.id for obj in doc.objects.values() if obj.kind is ObjectType.BODY])
    sketch = commands.create_sketch(doc, next(iter(doc.objects)))
    body, (base,) = construction(doc, sketch, (Operation.NEW_BODY,))
    service = BodyHistory()
    assert body.id not in vp.presentations
    window.tree_selection_changed([body.id])
    assert vp.selection.items()[0].shape is None
    assert vp.selection.pick_surface(Ray(Vec3(0, 0, 100), Vec3(0, 0, -1))) is None
    assert not vp.selection._occluders()
    vp.update_rotation_pivot()
    doc.set_visible(body.id, False)
    doc.set_transparencies({body.id: .5})
    doc.set_visible(body.id, True)
    assert service.evaluate(doc, body.id).status is ResultStatus.UNAVAILABLE
    shape = BRepPrimAPI_MakeBox(10, 20, 30).Shape()
    class PresentationAdapter:
        def evaluate(self, *args):
            return EvaluationResult(ResultStatus.READY, shape)
    service.evaluate(doc, body.id, PresentationAdapter())
    assert body.id in vp.presentations
    assert vp.selection._occluders()
    doc.replace_feature(replace(base, parameters=ExtrusionParameters(depth=20)))
    assert body.id not in vp.presentations and not vp.selection._occluders()
    doc.history.undo()
    assert doc.objects[base.id].parameters.depth == 10 and doc.objects[body.id].shape is None
    doc.remove(body.id)
    assert base.id not in doc.objects
    doc.history.undo()
    assert body.id in doc.objects and body.id not in vp.presentations
    QApplication.processEvents()
