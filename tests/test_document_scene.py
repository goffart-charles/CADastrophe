import pytest
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop
from cadastrophe.document import Document, SceneObject
from cadastrophe.sample_scene import demo_document


def test_demo_breps_are_valid_and_independent():
    doc = demo_document()
    assert len(doc.objects) == 7
    assert len({obj.id for obj in doc.objects.values()}) == 7
    bodies = [obj for obj in doc.objects.values() if not obj.plane]
    assert len(bodies) == 4
    volumes = []
    for obj in doc.objects.values():
        assert not obj.shape.IsNull()
        assert BRepCheck_Analyzer(obj.shape).IsValid()
        if not obj.plane:
            props = GProp_GProps()
            brepgprop.VolumeProperties(obj.shape, props)
            volumes.append(props.Mass())
    assert volumes[0] == pytest.approx(40 ** 3)
    import math
    assert volumes[2] == pytest.approx(55 * 42 * 26 - math.pi * 10**2 * 26)
    assert volumes[3] < 64 * 40 * 24


def test_document_identity_and_delete():
    doc = Document()
    obj = SceneObject("test", None, (1, 0, 0))
    doc.add(obj)
    with pytest.raises(ValueError):
        doc.add(obj)
    doc.remove(obj.id)
    assert not doc.objects
