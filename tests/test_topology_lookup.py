"""Exact native topology lookup, including orientation and ambiguous names."""
import cProfile
import io
import os
from pathlib import Path
import pstats
from time import perf_counter

import pytest
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.gp import gp_Trsf, gp_Vec

from cadastrophe.core.native_naming import NamedTopology, subshapes


@pytest.mark.parametrize("kind", ["Face", "Edge", "Vertex"])
def test_topology_labels_match_exact_incident_faces_in_any_orientation(kind):
    shape = BRepPrimAPI_MakeBox(10,20,30).Shape()
    faces = subshapes(shape, "Face")
    named = tuple((f"face-{i}", face) for i, face in enumerate(faces)) + (("shared-label", faces[0]), ("shared-label", faces[1]))
    topology = NamedTopology(named)
    for part in subshapes(shape, kind):
        expected = tuple(sorted({label for label, face in named if face.IsSame(part)
            or kind != "Face" and any(p.IsSame(part) for p in subshapes(face, kind))}))
        assert topology.labels(part, kind) == expected
        assert topology.labels(part.Reversed(), kind) == expected
        assert topology.labels(part, kind) == expected  # Repeat after cache fill.
    transform = gp_Trsf()
    transform.SetTranslation(gp_Vec(100,0,0))
    other = BRepBuilderAPI_Transform(shape, transform, False).Shape()
    for part in subshapes(other, kind):
        assert topology.labels(part, kind) == ()


@pytest.mark.skipif(os.environ.get("CADASTROPHE_PROFILE_DEMO") != "1", reason="Profilage explicite de l’exemple Atlas.")
def test_profile_atlas_native_open():
    from examples.parametric_flange import registry
    from cadastrophe.core.persistence import load_document
    from cadastrophe.core.feature_objects import BodyObject
    from cadastrophe.core.operation_effects import checked_solids
    from plugins.modeling.extrusion_service import Extrusions
    from plugins.sketch.profile_service import SketchProfiles
    path = Path(__file__).parents[1]/"examples/bride_atlas/bride_atlas.cada"
    service = Extrusions(SketchProfiles())
    profiler = cProfile.Profile()
    start = perf_counter()
    profiler.enable()
    document = load_document(path, registry=registry(service))
    profiler.disable()
    elapsed = perf_counter()-start
    body, = (o for o in document.objects.values() if isinstance(o, BodyObject))
    assert len(checked_solids(body.shape)) == 1
    stream = io.StringIO()
    stream.write(f"Atlas native open: {elapsed:.3f} s; {len(body.features)} features\n")
    pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(35)
    output = Path("artifacts")/("atlas-open-"+os.environ.get("CADASTROPHE_PROFILE_LABEL", "profile")+".txt")
    output.parent.mkdir(exist_ok=True)
    output.write_text(stream.getvalue(), encoding="utf-8")
