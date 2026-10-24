"""Native geometry, operation replay and reference-driven reconstruction."""
from dataclasses import replace
from math import pi

import pytest

from cadastrophe.scene import new_document
from cadastrophe.document import ObjectType
from cadastrophe.geometry import Vec3, REFERENCE_PLANES
from cadastrophe.core.feature_model import Operation, SketchInput, ExtrusionParameters, ResultStatus, BodyStateRef
from cadastrophe.core.feature_objects import BodyObject, ExtrusionFeature, RevolutionFeature, ReplayFeature
from cadastrophe.core.operation_model import GeometryReference as Ref, PlaneParameters, RevolutionParameters, ReplayParameters
from cadastrophe.core.reference_objects import DatumPlaneObject
from cadastrophe.core.reference_geometry import resolve_plane, plane_shape, capture_reference
from cadastrophe.core.body_history import evaluate_body, result_at, validate_graph
from plugins.sketch import commands
from plugins.sketch.model import Segment, Circle, Vec2
from plugins.sketch.profile_service import SketchProfiles
from plugins.modeling.extrusion_service import Extrusions
from test_primitives import volume


def system():
    return new_document(), Extrusions(SketchProfiles())


def plane(document, name="XY"):
    return next(obj for obj in document.objects.values() if obj.plane is not None and obj.plane.name == name)


def profile(document, points=None, *, support=None, circle=None):
    obj = commands.create_sketch(document, (support or plane(document)).id)
    points = points or ((0, 0), (100, 0), (100, 100), (0, 100))
    vertices = tuple(Vec2(*p) for p in points)
    curves = (Circle(Vec2(*circle[:2]), circle[2]),) if circle else tuple(Segment(p, vertices[(i+1)%len(vertices)]) for i, p in enumerate(vertices))
    commands.add_entities(document, obj.id, curves, title="Profil")
    return document.objects[obj.id]


def datum(document, parameters):
    obj = DatumPlaneObject("Plan personnalisé", plane_shape(REFERENCE_PLANES[0]), (.6,.7,.8),
                           plane=REFERENCE_PLANES[0], parameters=parameters)
    frame = resolve_plane(document, obj)
    obj = replace(obj, plane=frame, shape=plane_shape(frame))
    document.add(obj)
    return obj


def append(document, service, sketch=None, *, body=None, operation=Operation.NEW_BODY, parameters=None, replay=None):
    if body is None:
        body = BodyObject("Corps", None, (.7,.7,.8), features=())
    else:
        body = document.objects[body.id]
    common = dict(body_id=body.id, operation=operation, kind=ObjectType.FEATURE)
    if replay:
        feature = ReplayFeature("Motif", None, body.color, parameters=replay, **common)
    elif isinstance(parameters, RevolutionParameters):
        feature = RevolutionFeature("Révolution", None, body.color, source=SketchInput(sketch.id), parameters=parameters, **common)
    else:
        feature = ExtrusionFeature("Extrusion", None, body.color, source=SketchInput(sketch.id),
                                   parameters=parameters or ExtrusionParameters(depth=10), **common)
    document.update_construction((replace(body, features=(*body.features, feature.id)), feature))
    result = evaluate_body(document, body.id, service.evaluator(document), reuse=True)
    return document.objects[body.id], feature, result


def bounds(shape):
    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.BRepBndLib import brepbndlib
    box = Bnd_Box()
    brepbndlib.AddOptimal(shape, box, False, False)
    return box.Get()


@pytest.mark.parametrize("reverse", [False, True])
def test_offset_plane_and_sketch_follow_parameter_change(reverse):
    doc, service = system()
    base = plane(doc)
    d = datum(doc, PlaneParameters("offset", (Ref("plane", base.id),), distance=5, reversed=reverse))
    sketch = profile(doc, support=d)
    body, feature, result = append(doc, service, sketch)
    assert result.status is ResultStatus.READY, result.message
    assert bounds(result.shape)[2] == pytest.approx(-5 if reverse else 5)
    doc.replace_feature(replace(d, parameters=replace(d.parameters, distance=20)))
    result = evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert result.status is ResultStatus.READY, result.message
    assert bounds(result.shape)[2] == pytest.approx(-20 if reverse else 20)
    assert doc.objects[body.id].features == (feature.id,)
    assert doc.objects[sketch.id].sketch.entities == sketch.sketch.entities


def test_inclined_and_three_point_planes_and_invalid_references():
    doc, _ = system()
    d = datum(doc, PlaneParameters("inclined", (Ref("plane", plane(doc).id), Ref("axis", values=(0,0,0,1,0,0))), angle=90))
    assert resolve_plane(doc, d).normal.tuple() == pytest.approx((0,-1,0))
    points = tuple(Ref("point", values=v) for v in ((0,0,5), (10,0,5), (0,10,5)))
    d = datum(doc, PlaneParameters("three_points", points))
    assert resolve_plane(doc, d).normal.tuple() == pytest.approx((0,0,1))
    with pytest.raises(ValueError, match="alignés"):
        resolve_plane(doc, replace(d, parameters=PlaneParameters("three_points", (points[0], points[1], Ref("point", values=(20,0,5))))))
    bad = replace(d, parameters=PlaneParameters("offset", (Ref("plane", d.id),)))
    with pytest.raises(ValueError, match="circulaire"):
        validate_graph({**doc.objects, d.id: bad})


@pytest.mark.parametrize("angle,reverse", [(360,False), (360,True), (90,False), (180,True)])
def test_revolution_complete_partial_construction_axis_and_parameter_rebuild(angle, reverse):
    doc, service = system()
    sketch = profile(doc, ((5,-5), (10,-5), (10,5), (5,5)))
    axis = Segment(Vec2(0,-20), Vec2(0,20), construction=True)
    commands.add_entity(doc, sketch.id, axis)
    params = RevolutionParameters(Ref("sketch_line", sketch.id, entity_id=axis.id), angle, reverse)
    body, feature, result = append(doc, service, sketch, parameters=params)
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(750*pi*angle/360)
    changed = replace(feature, parameters=replace(params, angle=45))
    doc.replace_feature(changed)
    result = evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(750*pi/8)
    current = doc.objects[sketch.id].sketch
    commands.replace_entities(doc,sketch.id,tuple(replace(e,start=Vec2(e.start.u*1.2,e.start.v),end=Vec2(e.end.u*1.2,e.end.v))
                                                for e in current.entities if not e.construction))
    result = evaluate_body(doc,body.id,service.evaluator(doc),reuse=True)
    assert result.status is ResultStatus.READY,result.message
    assert volume(result.shape) == pytest.approx(1080*pi/8)
    commands.replace_entities(doc,sketch.id,(replace(axis,start=Vec2(-2,-20),end=Vec2(-2,20)),))
    result = evaluate_body(doc,body.id,service.evaluator(doc),reuse=True)
    assert result.status is ResultStatus.READY,result.message
    assert volume(result.shape) == pytest.approx(1320*pi/8)
    doc.history.undo()
    assert evaluate_body(doc, body.id, service.evaluator(doc), reuse=True).status is ResultStatus.READY


@pytest.mark.parametrize("crosses", [False, True])
def test_revolution_profile_touches_axis_or_fails_cleanly(crosses):
    doc, service = system()
    sketch = profile(doc, ((-5 if crosses else 0,-5), (10,-5), (10,5), (-5 if crosses else 0,5)))
    params = RevolutionParameters(Ref("axis", values=(0,0,0,0,1,0)))
    body, feature, result = append(doc, service, sketch, parameters=params)
    if crosses:
        assert result.status is ResultStatus.ERROR and result.shape is None
        assert "traverse" in result.message and doc.objects[body.id].shape is None
    else:
        assert result.status is ResultStatus.READY, result.message
        assert volume(result.shape) == pytest.approx(1000*pi)


def test_revolution_subtracts_only_its_generating_volume():
    doc, service = system()
    block = profile(doc, ((-20,-20), (20,-20), (20,20), (-20,20)))
    body, _, _ = append(doc, service, block, parameters=ExtrusionParameters(depth=40))
    # A rectangle in XZ, revolved around Z, cuts an annular groove.
    sketch = profile(doc, ((5,0), (10,0), (10,10), (5,10)), support=plane(doc,"XZ"))
    body, _, result = append(doc, service, sketch, body=body, operation=Operation.CUT,
        parameters=RevolutionParameters(Ref("axis", values=(0,0,0,0,0,1))))
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(64000-750*pi)


def patterned_source(*, cut=False, center=(20,0)):
    doc,service = system()
    base_plane = datum(doc,PlaneParameters("offset",(Ref("plane",plane(doc).id),),distance=-5))
    block = profile(doc,((-50,-50),(50,-50),(50,50),(-50,50)),support=base_plane)
    body,base,result = append(doc,service,block)
    assert result.status is ResultStatus.READY,result.message
    support = datum(doc,PlaneParameters("offset",(Ref("plane",plane(doc).id),),distance=3 if cut else 5))
    sketch = profile(doc,support=support,circle=(*center,2))
    body,source,result = append(doc,service,sketch,body=body,operation=Operation.CUT if cut else Operation.BOSS,
                                parameters=ExtrusionParameters(depth=2 if cut else 5))
    assert result.status is ResultStatus.READY,result.message
    return doc,service,body,base,source,sketch


@pytest.mark.parametrize("cut",[False,True])
@pytest.mark.parametrize("operation,count",[(Operation.LINEAR_PATTERN,4),(Operation.CIRCULAR_PATTERN,8),(Operation.MIRROR,2)])
def test_replay_transforms_only_effects_and_rebuilds_all_occurrences(cut,operation,count):
    center = (-30,0) if operation is Operation.LINEAR_PATTERN else (20,0)
    doc,service,body,base,source,sketch = patterned_source(cut=cut,center=center)
    reference = Ref("plane",plane(doc,"YZ").id) if operation is Operation.MIRROR else Ref("axis",values=(0,0,0,1,0,0) if operation is Operation.LINEAR_PATTERN else (0,0,0,0,0,1))
    p = ReplayParameters((source.id,),reference,count=count,spacing=10)
    body,motif,result = append(doc,service,body=body,operation=operation,replay=p)
    assert result.status is ResultStatus.READY,result.message
    sign,depth = (-1,2) if cut else (1,5)
    assert volume(result.shape) == pytest.approx(100000+sign*count*4*pi*depth)
    first = result_at(doc,BodyStateRef(body.id,base.id)).shape
    circle, = sketch.sketch.entities
    commands.replace_entities(doc,sketch.id,(replace(circle,radius=3),))
    result = evaluate_body(doc,body.id,service.evaluator(doc),reuse=True)
    assert result.status is ResultStatus.READY,result.message
    assert volume(result.shape) == pytest.approx(100000+sign*count*9*pi*depth)
    assert result_at(doc,BodyStateRef(body.id,base.id)).shape is first
    assert doc.objects[body.id].features == (base.id,source.id,motif.id)
    if operation is not Operation.MIRROR:
        p = replace(p,count=3,spacing=12,angle=90 if operation is Operation.CIRCULAR_PATTERN else 360)
        doc.replace_feature(replace(motif,parameters=p))
        result = evaluate_body(doc,body.id,service.evaluator(doc),reuse=True)
        assert result.status is ResultStatus.READY,result.message
        assert volume(result.shape) == pytest.approx(100000+sign*3*9*pi*depth)


@pytest.mark.parametrize("cut", [False, True])
def test_circular_pattern_of_linear_pattern_rebuilds_complete_motif_once(cut):
    doc, service, body, base, source, sketch = patterned_source(cut=cut)
    body, linear, result = append(doc, service, body=body, operation=Operation.LINEAR_PATTERN,
        replay=ReplayParameters((source.id,), Ref("axis", values=(0,0,0,1,0,0)), count=2, spacing=10))
    assert result.status is ResultStatus.READY, result.message
    # Selecting both the seed and its motif must not reproduce the seed twice.
    body, circular, result = append(doc, service, body=body, operation=Operation.CIRCULAR_PATTERN,
        replay=ReplayParameters((linear.id, source.id), Ref("axis", values=(0,0,0,0,0,1)), count=4))
    sign, depth = (-1, 2) if cut else (1, 5)
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(100000+sign*8*4*pi*depth)
    from cadastrophe.core.native_naming import capture, resolve, subshapes
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.GeomAbs import GeomAbs_Cylinder
    from OCC.Core.TopoDS import topods
    copied_face = next(f for f in subshapes(result.shape, "Face")
        if (s := BRepAdaptor_Surface(topods.Face(f))).GetType() == GeomAbs_Cylinder
        and abs(s.Cylinder().Location().X()) < 1e-6 and abs(s.Cylinder().Location().Y()-30) < 1e-6)
    copied_ref = capture(result.shape, copied_face, "Face", result.topology)
    circle, = sketch.sketch.entities
    commands.replace_entities(doc, sketch.id, (replace(circle, radius=3),))
    doc.replace_feature(replace(linear, parameters=replace(linear.parameters, count=3)))
    result = evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(100000+sign*12*9*pi*depth)
    corresponding = resolve(result.shape, copied_ref, result.topology)
    assert BRepAdaptor_Surface(topods.Face(corresponding)).Cylinder().Radius() == pytest.approx(3)
    assert doc.objects[body.id].features == (base.id, source.id, linear.id, circular.id)
    doc.remove(linear.id)
    assert linear.id not in doc.objects and circular.id not in doc.objects
    doc.history.undo()
    assert evaluate_body(doc, body.id, service.evaluator(doc), reuse=True).status is ResultStatus.READY


def test_nested_through_all_pattern_preserves_target_dependent_extent():
    doc, service, body, base, source, _ = patterned_source(cut=True)
    source = replace(source, parameters=replace(source.parameters, depth=None, end_condition="through_all"))
    doc.replace_feature(source)
    body, linear, result = append(doc, service, body=body, operation=Operation.LINEAR_PATTERN,
        replay=ReplayParameters((source.id,), Ref("axis", values=(0,0,0,1,0,0)), count=2, spacing=10))
    assert result.status is ResultStatus.READY, result.message
    body, _, result = append(doc, service, body=body, operation=Operation.MIRROR,
        replay=ReplayParameters((linear.id,), Ref("plane", plane(doc).id)))
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(100000-32*pi)
    doc.replace_feature(replace(base, parameters=replace(base.parameters, depth=20)))
    result = evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(200000-112*pi)


def test_disconnected_pattern_option_and_following_cut_rebuild():
    from cadastrophe.core.operation_effects import checked_solids
    doc, service = system()
    sketch = profile(doc, ((0,0),(10,0),(10,10),(0,10)))
    body, base, _ = append(doc, service, sketch)
    params = ReplayParameters((base.id,), Ref("axis", values=(0,0,0,1,0,0)), count=3, spacing=20)
    body, motif, result = append(doc, service, body=body, operation=Operation.LINEAR_PATTERN, replay=params)
    assert result.status is ResultStatus.ERROR and "Fusionner le résultat" in result.message
    doc.replace_feature(replace(motif, parameters=replace(params, merge_result=False)))
    result = evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert result.status is ResultStatus.READY, result.message
    assert len(checked_solids(result.shape)) == 3
    assert volume(result.shape) == pytest.approx(3000)
    from cadastrophe.core.feature_model import EdgeParameters
    from plugins.modeling.edge_geometry import apply_edges, edges_of, edge_reference
    # Edge treatments keep the other solids rather than extracting only one.
    edge = edges_of(result.shape)[0]
    rounded = apply_edges(result.shape, Operation.FILLET,
                          EdgeParameters(edges=(edge_reference(result.shape, edge),), size=.5))
    assert len(checked_solids(rounded)) == 3
    assert 2900 < volume(rounded) < 3000
    cut_sketch = profile(doc, circle=(5,5,2))
    body, cut, result = append(doc, service, cut_sketch, body=body, operation=Operation.CUT)
    assert result.status is ResultStatus.READY, result.message
    assert len(checked_solids(result.shape)) == 3
    assert volume(result.shape) == pytest.approx(3000-40*pi)
    doc.replace_feature(replace(base, parameters=replace(base.parameters, depth=15)))
    result = evaluate_body(doc, body.id, service.evaluator(doc), reuse=True)
    assert result.status is ResultStatus.READY, result.message
    assert volume(result.shape) == pytest.approx(4500-40*pi)


def test_replay_codec_requires_explicit_merge_and_rejects_invalid_option():
    from plugins.modeling.operation_codec import decode_replay, encode_replay
    doc, service, body, _, source, _ = patterned_source()
    _, motif, _ = append(doc, service, body=body, operation=Operation.LINEAR_PATTERN,
        replay=ReplayParameters((source.id,), Ref("axis", values=(0,0,0,1,0,0)), spacing=10))
    data = encode_replay(motif)
    del data["parameters"]["merge_result"]
    with pytest.raises(ValueError):
        decode_replay(motif, data)
    with pytest.raises(ValueError):
        replace(motif.parameters, merge_result="false")


@pytest.mark.parametrize("plane_name",["XY","XZ","YZ"])
@pytest.mark.parametrize("cut",[False,True])
def test_mirror_main_planes_and_custom_offset_rebuild(plane_name,cut):
    doc,service,body,_,source,_ = patterned_source(cut=cut,center=(20,20))
    mirror = datum(doc,PlaneParameters("offset",(Ref("plane",plane(doc,plane_name).id),),distance=0))
    body,motif,result = append(doc,service,body=body,operation=Operation.MIRROR,
        replay=ReplayParameters((source.id,),Ref("plane",mirror.id)))
    assert result.status is ResultStatus.READY,result.message
    assert volume(result.shape) == pytest.approx(100000+(-1 if cut else 1)*2*4*pi*(2 if cut else 5))
    if plane_name != "XY":
        doc.replace_feature(replace(mirror,parameters=replace(mirror.parameters,distance=2)))
        result = evaluate_body(doc,body.id,service.evaluator(doc),reuse=True)
        assert result.status is ResultStatus.READY,result.message
        assert doc.objects[motif.id].parameters.reference.object_id == mirror.id


def test_multiple_sources_replay_in_history_order_without_replaying_the_base():
    doc,service,body,base,boss,sketch = patterned_source(center=(-30,0))
    # Drill the boss, then repeat both functions. The cut depends on the boss.
    hole = profile(doc,support=doc.objects[sketch.sketch.support.plane_id],circle=(-30,0,1))
    body,cut,result = append(doc,service,hole,body=body,operation=Operation.CUT,parameters=ExtrusionParameters(depth=5))
    assert result.status is ResultStatus.READY,result.message
    body,_,result = append(doc,service,body=body,operation=Operation.LINEAR_PATTERN,
        replay=ReplayParameters((cut.id,boss.id),Ref("axis",values=(0,0,0,1,0,0)),count=3,spacing=20))
    assert result.status is ResultStatus.READY,result.message
    assert volume(result.shape) == pytest.approx(100000+3*(4-1)*pi*5)


def test_non_intersecting_subtractive_occurrence_is_a_controlled_error():
    doc,service,body,_,source,_ = patterned_source(cut=True)
    body,feature,result = append(doc,service,body=body,operation=Operation.LINEAR_PATTERN,
        replay=ReplayParameters((source.id,),Ref("axis",values=(0,0,0,1,0,0)),count=3,spacing=1000))
    assert result.status is ResultStatus.ERROR and result.shape is None
    assert "Occurrence 2" in result.message and doc.objects[body.id].shape is None
    assert result_at(doc,BodyStateRef(body.id,source.id)).status is ResultStatus.READY
    doc.replace_feature(replace(feature,parameters=replace(feature.parameters,spacing=10)))
    assert evaluate_body(doc,body.id,service.evaluator(doc),reuse=True).status is ResultStatus.READY


def test_circular_replay_of_revolution_keeps_its_local_axis_and_source_volume():
    doc,service,body,base,_,_ = patterned_source(center=(-30,-30))
    # A small cylindrical boss at x=20, z=5..10, generated by revolution.
    xz = plane(doc,"XZ")
    sketch = profile(doc,((20,5),(23,5),(23,10),(20,10)),support=xz)
    body,source,result = append(doc,service,sketch,body=body,operation=Operation.BOSS,
        parameters=RevolutionParameters(Ref("axis",values=(20,0,0,0,0,1))))
    assert result.status is ResultStatus.READY,result.message
    before = volume(result.shape)
    body,_,result = append(doc,service,body=body,operation=Operation.CIRCULAR_PATTERN,
        replay=ReplayParameters((source.id,),Ref("axis",values=(0,0,0,0,0,1)),count=4))
    assert result.status is ResultStatus.READY,result.message
    assert volume(result.shape) == pytest.approx(before+3*45*pi)


def test_face_based_plane_resolves_its_producer_before_the_dependent_boss():
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.GeomAbs import GeomAbs_Plane
    from OCC.Core.TopoDS import topods
    from cadastrophe.core.native_naming import subshapes
    from cadastrophe.document import SelectionItem
    doc,service = system()
    sketch = profile(doc)
    body,base,result = append(doc,service,sketch)
    top = next(f for f in subshapes(result.shape,"Face") if (s:=BRepAdaptor_Surface(topods.Face(f))).GetType() == GeomAbs_Plane and abs(s.Plane().Location().Z()-10)<1e-6)
    ref = capture_reference(doc,SelectionItem(body.id,"Face",top,body.revision))
    d = datum(doc,PlaneParameters("offset",(ref,),distance=0))
    circle = profile(doc,support=d,circle=(20,20,3))
    body,boss,result = append(doc,service,circle,body=body,operation=Operation.BOSS,parameters=ExtrusionParameters(depth=3))
    assert result.status is ResultStatus.READY,result.message
    doc.replace_feature(replace(base,parameters=replace(base.parameters,depth=20)))
    result = evaluate_body(doc,body.id,service.evaluator(doc),reuse=True)
    assert result.status is ResultStatus.READY,result.message
    assert volume(result.shape) == pytest.approx(200000+27*pi)
    assert resolve_plane(doc,doc.objects[d.id]).origin.z == pytest.approx(20)


def test_through_all_is_replayed_with_its_condition_and_originals_are_not_repeated():
    doc,service,body,base,source,_ = patterned_source(cut=True)
    source = replace(source,parameters=replace(source.parameters,depth=None,end_condition="through_all"))
    doc.replace_feature(source)
    assert evaluate_body(doc,body.id,service.evaluator(doc),reuse=True).status is ResultStatus.READY
    body,motif,result = append(doc,service,body=body,operation=Operation.MIRROR,
        replay=ReplayParameters((source.id,),Ref("plane",plane(doc).id)))
    assert result.status is ResultStatus.READY,result.message
    assert volume(result.shape) == pytest.approx(100000-16*pi)
    doc.replace_feature(replace(base,parameters=replace(base.parameters,depth=20)))
    result = evaluate_body(doc,body.id,service.evaluator(doc),reuse=True)
    assert result.status is ResultStatus.READY,result.message
    assert volume(result.shape) == pytest.approx(200000-56*pi)
    assert doc.objects[source.id].parameters.depth is None


def test_future_sources_missing_axis_and_deletion_dependencies_are_explicit():
    doc,service,body,_,source,sketch = patterned_source()
    axis = Segment(Vec2(-10,0),Vec2(10,0),construction=True)
    commands.add_entity(doc,sketch.id,axis)
    body,motif,result = append(doc,service,body=body,operation=Operation.LINEAR_PATTERN,
        replay=ReplayParameters((source.id,),Ref("sketch_line",sketch.id,entity_id=axis.id),spacing=10))
    assert result.status is ResultStatus.READY,result.message
    commands.remove_entities(doc,sketch.id,(axis.id,))
    result = evaluate_body(doc,body.id,service.evaluator(doc),reuse=True)
    assert result.status is ResultStatus.ERROR and "ligne ou point supprimé" in result.message
    doc.history.undo()
    assert evaluate_body(doc,body.id,service.evaluator(doc),reuse=True).status is ResultStatus.READY
    bad = replace(motif,parameters=replace(motif.parameters,sources=(motif.id,)))
    with pytest.raises(ValueError):
        validate_graph({**doc.objects,motif.id:bad})
    doc.remove(source.id)
    assert source.id not in doc.objects and motif.id not in doc.objects
    doc.history.undo()
    assert source.id in doc.objects and motif.id in doc.objects


def test_datum_deletion_removes_dependent_features_and_is_undoable():
    doc,service = system()
    d = datum(doc,PlaneParameters("offset",(Ref("plane",plane(doc).id),),distance=5))
    sketch = profile(doc,support=d)
    body,feature,result = append(doc,service,sketch)
    assert result.status is ResultStatus.READY,result.message
    ids = set(doc.objects)
    doc.remove(d.id)
    assert {d.id,body.id,feature.id}.isdisjoint(doc.objects)
    assert sketch.id in doc.objects and doc.objects[sketch.id].reference_error(doc)
    doc.history.undo()
    assert set(doc.objects) == ids
    assert evaluate_body(doc,body.id,service.evaluator(doc),reuse=True).status is ResultStatus.READY


@pytest.mark.parametrize("method",["inclined","three_points"])
def test_rotated_datum_repositions_the_same_local_sketch_and_rebuilds(method):
    doc,service = system()
    p = PlaneParameters("inclined",(Ref("plane",plane(doc).id),Ref("axis",values=(0,0,0,1,0,0))),angle=0) if method == "inclined" else PlaneParameters(
        "three_points",tuple(Ref("point",values=v) for v in ((0,0,0),(10,0,0),(0,10,0))))
    d = datum(doc,p)
    sketch = profile(doc,((0,0),(10,0),(10,10),(0,10)),support=d)
    body,feature,result = append(doc,service,sketch,parameters=ExtrusionParameters(depth=5))
    assert result.status is ResultStatus.READY,result.message
    p = replace(p,angle=90) if method == "inclined" else replace(p,references=(*p.references[:2],Ref("point",values=(0,0,10))))
    doc.replace_feature(replace(d,parameters=p))
    result = evaluate_body(doc,body.id,service.evaluator(doc),reuse=True)
    assert result.status is ResultStatus.READY,result.message
    assert bounds(result.shape) == pytest.approx((0,-5,0,10,0,10),abs=1e-6)
    assert doc.objects[sketch.id].sketch.entities == sketch.sketch.entities
    assert doc.objects[body.id].features == (feature.id,)


def test_open_revolution_profile_fails_and_blocks_the_following_feature():
    doc,service = system()
    sketch = profile(doc,((5,-5),(10,-5),(10,5),(5,5)))
    commands.remove_entities(doc,sketch.id,(sketch.sketch.entities[0].id,))
    params = RevolutionParameters(Ref("axis",values=(0,0,0,0,1,0)))
    body,feature,result = append(doc,service,sketch,parameters=params)
    assert result.status is ResultStatus.ERROR and result.message and result.shape is None
    body,following,result = append(doc,service,sketch,body=body,operation=Operation.BOSS,parameters=params)
    assert result.status is ResultStatus.BLOCKED and result.shape is None
    assert result_at(doc,BodyStateRef(body.id,feature.id)).status is ResultStatus.ERROR


def test_linear_direction_can_reference_a_native_edge_after_upstream_resize():
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.GeomAbs import GeomAbs_Line
    from OCC.Core.TopoDS import topods
    from cadastrophe.core.native_naming import subshapes
    from cadastrophe.document import SelectionItem
    doc,service,body,base,source,_ = patterned_source(center=(-30,0))
    edge = next(e for e in subshapes(body.shape,"Edge") if (c:=BRepAdaptor_Curve(topods.Edge(e))).GetType() == GeomAbs_Line
                and c.Line().Direction().X() > .9)
    ref = capture_reference(doc,SelectionItem(body.id,"Edge",edge,body.revision))
    assert ref.topology.labels
    body,_,result = append(doc,service,body=body,operation=Operation.LINEAR_PATTERN,
        replay=ReplayParameters((source.id,),ref,count=4,spacing=10))
    assert result.status is ResultStatus.READY,result.message
    base_sketch = doc.objects[base.sketch_id].sketch
    def point(p):
        return Vec2(60 if p.u == 50 else p.u,p.v)
    commands.replace_entities(doc,base.sketch_id,tuple(replace(e,start=point(e.start),end=point(e.end)) for e in base_sketch.entities))
    result = evaluate_body(doc,body.id,service.evaluator(doc),reuse=True)
    assert result.status is ResultStatus.READY,result.message
    assert volume(result.shape) == pytest.approx(110000+80*pi)


def test_legacy_reference_plane_revision_cannot_reuse_a_stale_prefix():
    doc,service = system()
    support = plane(doc)
    sketch = profile(doc)
    body,_,result = append(doc,service,sketch)
    assert result.status is ResultStatus.READY
    doc.replace_shape(support.id,plane_shape(support.plane,extent=120))
    result = evaluate_body(doc,body.id,service.evaluator(doc),reuse=True)
    assert result.status is ResultStatus.ERROR and "plan modifié" in result.message
    assert doc.objects[body.id].shape is None
