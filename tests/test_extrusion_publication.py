"""X4 publication receipts and load hooks, without a widget or native viewer."""
from dataclasses import replace

import pytest

from cadastrophe.core.body_history import body_result, result_signature
from cadastrophe.core.feature_model import EvaluationResult, ExtrusionParameters, ResultStatus
from cadastrophe.core.feature_model import BodyStateRef
from cadastrophe.core.history import HistoryBusyError
from cadastrophe.core.object_types import ObjectTypeRegistry
from cadastrophe.document import Document
from test_modeling_history import sketch_document, feature, key
from cadastrophe.core.feature_objects import BodyObject
from plugins.sketch.commands import add_entity
from plugins.sketch.model import Point, Vec2


def test_validated_result_is_published_before_graph_notifications_and_reused_on_redo():
    doc, sketch = sketch_document()
    body = BodyObject("Corps", None, (.5, .6, .7), id=key())
    extrusion = feature(body.id, sketch)
    body = replace(body, features=(extrusion.id,))
    shape = object()  # Common Document treats native payloads as opaque, like X1 adapters.
    observations = []
    doc.object_added.connect(lambda key: observations.append(
        (key, doc.objects[body.id].shape, body_result(doc, body.id).status)))
    doc.update_construction((body, extrusion), results={body.id: EvaluationResult(ResultStatus.READY, shape)})
    assert len(observations) == 2 and all(value[1:] == (shape, ResultStatus.READY) for value in observations)
    assert doc.history.count == 1
    doc.history.undo()
    assert body.id not in doc.objects and extrusion.id not in doc.objects
    doc.history.redo()
    assert doc.objects[body.id].shape is shape
    assert doc.history.count == 1
    before = result_signature(doc.objects, body.id)
    doc.rename(sketch.id, "Nouveau nom")
    doc.set_visible(sketch.id, False)
    assert result_signature(doc.objects, body.id) == before
    add_entity(doc, sketch.id, Point(Vec2(7, 9)))
    assert result_signature(doc.objects, body.id) != before
    assert doc.objects[body.id].shape is None


def test_result_receipt_never_revives_geometry_for_changed_inputs():
    doc, sketch = sketch_document()
    body = BodyObject("Corps", None, (.5, .6, .7), id=key())
    extrusion = feature(body.id, sketch)
    body = replace(body, features=(extrusion.id,))
    doc.update_construction((body, extrusion), results={body.id: EvaluationResult(ResultStatus.READY, object())})
    # Receipt replay also checks inputs outside changed_ids; stale external
    # snapshots can never turn a dirty result back into a claimed valid solid.
    from cadastrophe.core.body_history import result_receipts
    receipt = result_receipts(doc.objects, {body.id: body_result(doc, body.id)})
    add_entity(doc, sketch.id, Point(Vec2(3, 4)))
    doc._apply_construction(tuple(doc.objects.values()), {extrusion.id}, receipt)
    assert doc.objects[body.id].shape is None
    assert body_result(doc, body.id).status is ResultStatus.DIRTY


def test_edit_with_validated_result_undo_restores_previous_receipt():
    doc, sketch = sketch_document()
    body = BodyObject("Corps", None, (.5, .6, .7), id=key())
    extrusion = feature(body.id, sketch)
    body = replace(body, features=(extrusion.id,))
    old, new = object(), object()
    doc.update_construction((body, extrusion), results={body.id: EvaluationResult(ResultStatus.READY, old)})
    doc.update_construction((replace(extrusion, parameters=ExtrusionParameters(depth=20)),),
                            results={body.id: EvaluationResult(ResultStatus.READY, new)})
    assert doc.objects[body.id].shape is new
    doc.history.undo()
    assert doc.objects[body.id].shape is old
    doc.history.redo()
    assert doc.objects[body.id].shape is new


@pytest.mark.parametrize("value", [None, object(), EvaluationResult(ResultStatus.ERROR, message="Erreur")])
def test_unvalidated_receipts_are_rejected_before_history_mutation(value):
    doc, sketch = sketch_document()
    body = BodyObject("Corps", None, (.5, .6, .7), id=key())
    extrusion = feature(body.id, sketch)
    body = replace(body, features=(extrusion.id,))
    before = dict(doc.objects)
    with pytest.raises(ValueError):
        doc.update_construction((body, extrusion), results={body.id: value})
    assert dict(doc.objects) == before and doc.history.count == 0


@pytest.mark.parametrize("case", ["other_body", "other_producer", "error", "different_final", "no_final"])
def test_invalid_intermediate_results_are_rejected_before_publication(case):
    doc, sketch = sketch_document()
    body = BodyObject("Corps", None, (.5, .6, .7), id=key())
    extrusion = feature(body.id, sketch)
    body = replace(body, features=(extrusion.id,))
    ready = EvaluationResult(ResultStatus.READY, object())
    ref = BodyStateRef(body.id, extrusion.id)
    value = ready
    if case == "other_body":
        ref = BodyStateRef(key(), extrusion.id)
    elif case == "other_producer":
        ref = BodyStateRef(body.id, key())
    elif case == "error":
        value = EvaluationResult(ResultStatus.ERROR, message="Erreur")
    elif case == "different_final":
        value = EvaluationResult(ResultStatus.READY, object())
    before = dict(doc.objects)
    with pytest.raises(ValueError):
        doc.update_construction((body, extrusion), results={} if case == "no_final" else {body.id: ready},
                                prefixes={body.id: {ref: value}})
    assert dict(doc.objects) == before and doc.history.count == 0


def test_document_restorer_is_reserved_and_always_releases_after_exception():
    registry, doc = ObjectTypeRegistry(), Document()
    observations = []
    def restore(document):
        observations.append(document)
        with pytest.raises(HistoryBusyError):
            document.history.clear()
        raise RuntimeError("Échec de calcul simulé")
    registry.register_document_restorer("test.owner.initial", restore)
    with pytest.raises(RuntimeError, match="simulé"):
        registry.restore_document(doc)
    assert observations == [doc] and not doc.history.blocked and doc.history.count == 0
    registry.unregister_document_restorer("test.owner.initial")
    registry.restore_document(doc)
    assert observations == [doc]
