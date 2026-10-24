"""Exported geometry is parsed independently: dimensions, winding, holes and units."""
import base64
import json
import struct
from collections import Counter
from math import pi, sqrt
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.gp import gp_Ax2, gp_Pnt, gp_Dir, gp_Trsf, gp_Vec
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.TopoDS import topods
from OCC.Core.TopLoc import TopLoc_Location

from cadastrophe.brep_io import encode_shape
from cadastrophe.document import ObjectType
from plugins.import_export.exporters import default_registry, export_file, ExportFormat, ExportRegistry
from plugins.import_export.mesh import MeshOptions, triangulate, parts


@pytest.fixture
def model():
    block = BRepPrimAPI_MakeBox(40.,30.,12.).Shape()
    boss = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(20,15,12),gp_Dir(0,0,1)),8.,6.).Shape()
    fused = BRepAlgoAPI_Fuse(block,boss).Shape()
    hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(20,15,-1),gp_Dir(0,0,1)),3.,20.).Shape()
    return BRepAlgoAPI_Cut(fused,hole).Shape()


def bounds(vertices):
    return tuple(min(p[i] for p in vertices) for i in range(3))+tuple(max(p[i] for p in vertices) for i in range(3))


def signed_volume(triangles):
    total = 0.
    for a,b,c in triangles:
        cross = (b[1]*c[2]-b[2]*c[1],b[2]*c[0]-b[0]*c[2],b[0]*c[1]-b[1]*c[0])
        total += sum(x*y for x,y in zip(a,cross))/6
    return total


def read_gltf(path):
    if path.suffix == ".glb":
        content = path.read_bytes()
        magic,version,length = struct.unpack_from("<4sII",content)
        assert (magic,version,length) == (b"glTF",2,len(content))
        size,kind = struct.unpack_from("<II",content,12)
        assert kind == 0x4e4f534a and size%4 == 0
        doc = json.loads(content[20:20+size])
        n,kind = struct.unpack_from("<II",content,20+size)
        assert kind == 0x004e4942 and n%4 == 0
        data = content[28+size:]
        assert len(data) == n
    else:
        doc = json.loads(path.read_text(encoding="utf-8"))
        data = base64.b64decode(doc["buffers"][0]["uri"].split(",",1)[1])
    assert doc["buffers"][0]["byteLength"] <= len(data) <= doc["buffers"][0]["byteLength"]+3
    vertices,triangles = [],[]
    for node in doc["nodes"]:
        primitive, = doc["meshes"][node["mesh"]]["primitives"]
        arrays = {}
        for name,index in primitive["attributes"].items():
            entry = doc["accessors"][index]
            view = doc["bufferViews"][entry["bufferView"]]
            assert view["byteOffset"]%4 == 0
            values = [struct.unpack_from("<3f",data,view["byteOffset"]+i*12) for i in range(entry["count"])]
            assert entry["componentType"] == 5126 and entry["type"] == "VEC3"
            if name == "POSITION":
                assert entry["min"] == [min(p[i] for p in values) for i in range(3)]
                assert entry["max"] == [max(p[i] for p in values) for i in range(3)]
            arrays[name] = values
        assert len(arrays["POSITION"]) == len(arrays["NORMAL"])
        for normal in arrays["NORMAL"]:
            assert sum(v*v for v in normal) == pytest.approx(1.,abs=1e-6)
        # Convert the standard glTF metre/Y-up axes back to CAD millimetre/Z-up.
        points = [(x*1000,-z*1000,y*1000) for x,y,z in arrays["POSITION"]]
        triangles.extend(tuple(points[i:i+3]) for i in range(0,len(points),3))
        vertices.extend(points)
    return doc,vertices,triangles


@pytest.mark.parametrize("format_key",["stl","stl_ascii","obj","3mf","glb","gltf","step"])
def test_export_curved_boolean_model_keeps_dimensions_volume_and_hole(model,tmp_path,format_key):
    (tmp_path/"0.brep").write_bytes(encode_shape(model,ObjectType.BODY))
    key = "stl" if format_key == "stl_ascii" else format_key
    output = tmp_path/("échange pièce."+key)
    metadata = [{"id":"stable-id","name":"Bride & support <1>","color":(.2,.5,.8),"transparency":.25}]
    progress = []
    result = export_file(default_registry(),tmp_path,output,metadata,key,
                         {"quality":"high","binary":format_key != "stl_ascii"},lambda v,s:progress.append(v))
    assert result["count"] == 1 and progress == sorted(progress)
    expected = 40*30*12+64*pi*6-9*pi*18
    vertices,triangles = [],[]
    if key == "step":
        from OCC.Core.STEPControl import STEPControl_Reader
        from OCC.Core.IFSelect import IFSelect_RetDone
        from OCC.Core.BRepGProp import brepgprop
        from OCC.Core.GProp import GProp_GProps
        reader = STEPControl_Reader()
        assert reader.ReadFile(str(output)) == IFSelect_RetDone
        reader.SetSystemLengthUnit(1.)
        reader.TransferRoots()
        props = GProp_GProps()
        brepgprop.VolumeProperties(reader.OneShape(),props)
        assert props.Mass() == pytest.approx(expected,rel=1e-9)
        assert "MANIFOLD_SOLID_BREP" in output.read_text(encoding="ascii")
        return
    if key == "stl":
        content = output.read_bytes()
        if format_key == "stl_ascii":
            vertices = [tuple(map(float,line.split()[1:])) for line in content.decode("ascii").splitlines() if line.strip().startswith("vertex ")]
            triangles = [tuple(vertices[i:i+3]) for i in range(0,len(vertices),3)]
            assert content.startswith(b"solid ")
        else:
            count, = struct.unpack_from("<I",content,80)
            assert len(content) == 84+50*count
            for i in range(count):
                facet = struct.unpack_from("<12fH",content,84+50*i)
                assert sum(v*v for v in facet[:3]) == pytest.approx(1,abs=1e-6)
                triangles.append(tuple(tuple(facet[j:j+3]) for j in (3,6,9)))
            vertices = [p for triangle in triangles for p in triangle]
        from OCC.Core.StlAPI import StlAPI_Reader
        from OCC.Core.TopoDS import TopoDS_Shape
        imported = TopoDS_Shape()
        assert StlAPI_Reader().Read(imported,str(output))
        assert not imported.IsNull()
    elif key == "obj":
        normals = []
        for line in output.read_text(encoding="utf-8").splitlines():
            if line.startswith("v "):
                vertices.append(tuple(map(float,line.split()[1:])))
            elif line.startswith("vn "):
                normals.append(tuple(map(float,line.split()[1:])))
            elif line.startswith("f "):
                indices = [tuple(map(int,token.split("//"))) for token in line.split()[1:]]
                assert all(0 < n <= len(normals) for _,n in indices)
                triangles.append(tuple(vertices[v-1] for v,_ in indices))
        assert "o Bride & support <1>" in output.read_text(encoding="utf-8")
        assert all(sum(v*v for v in n) == pytest.approx(1) for n in normals)
    elif key == "3mf":
        with ZipFile(output) as archive:
            assert archive.testzip() is None
            rels = ET.fromstring(archive.read("_rels/.rels"))
            assert rels[0].attrib["Target"] == "/3D/3dmodel.model"
            types = ET.fromstring(archive.read("[Content_Types].xml"))
            assert any(e.attrib["ContentType"] == "application/vnd.ms-package.3dmanufacturing-3dmodel+xml" for e in types)
            root = ET.fromstring(archive.read("3D/3dmodel.model"))
        ns = {"m":"http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
        assert root.attrib["unit"] == "millimeter"
        obj, = root.findall("m:resources/m:object",ns)
        assert obj.attrib["name"] == metadata[0]["name"] and obj.attrib["partnumber"] == "stable-id"
        assert root.find("m:build/m:item",ns).attrib["objectid"] == obj.attrib["id"]
        vertices = [tuple(float(v.attrib[k]) for k in ("x","y","z")) for v in obj.findall("m:mesh/m:vertices/m:vertex",ns)]
        indices = [tuple(int(t.attrib[k]) for k in ("v1","v2","v3")) for t in obj.findall("m:mesh/m:triangles/m:triangle",ns)]
        edges = Counter((a,b) for t in indices for a,b in zip(t,(*t[1:],t[0])))
        assert all(n == 1 and edges[b,a] == 1 for (a,b),n in edges.items())
        triangles = [tuple(vertices[i] for i in t) for t in indices]
    else:
        doc,vertices,triangles = read_gltf(output)
        assert doc["nodes"][0]["name"] == metadata[0]["name"]
        assert doc["nodes"][0]["extras"]["cadastropheObjectId"] == "stable-id"
        assert doc["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] == [.2,.5,.8,.75]
        assert doc["materials"][0]["alphaMode"] == "BLEND"
    assert bounds(vertices) == pytest.approx((0,0,0,40,30,18),abs=2e-5)
    assert signed_volume(triangles) == pytest.approx(expected,rel=.002)
    # The bore remains empty: no triangle crosses the Z axis of the hole.
    assert all(sqrt((x-20)**2+(y-15)**2) >= 2.99 for x,y,z in vertices)
    for triangle in triangles:
        a,b,c = triangle
        determinant = (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        if abs(determinant) > 1e-10:
            u = ((20-a[0])*(c[1]-a[1])-(15-a[1])*(c[0]-a[0]))/determinant
            v = ((b[0]-a[0])*(15-a[1])-(b[1]-a[1])*(20-a[0]))/determinant
            assert not (u >= 0 and v >= 0 and u+v <= 1), "Le trou a été bouché par un triangle."


def test_quality_increases_resolution_and_preserves_source_mesh(model):
    before = [BRep_Tool.Triangulation(topods.Face(face),TopLoc_Location()) for face in parts(model,TopAbs_FACE)]
    low,standard,high = [triangulate(model,MeshOptions(quality=q)) for q in ("low","standard","high")]
    assert len(low.triangles) < len(standard.triangles) < len(high.triangles)
    after = [BRep_Tool.Triangulation(topods.Face(face),TopLoc_Location()) for face in parts(model,TopAbs_FACE)]
    assert all(a == b for a,b in zip(before,after))
    custom = triangulate(model,MeshOptions("custom",.03,7.))
    assert len(low.triangles) < len(custom.triangles)


@pytest.mark.parametrize("format_key",["stl","obj","3mf","glb","gltf"])
def test_multiple_translated_objects_keep_global_placement(tmp_path,format_key):
    shapes = [BRepPrimAPI_MakeBox(10.,20.,30.).Shape()]
    transform = gp_Trsf()
    transform.SetTranslation(gp_Vec(100,200,-5))
    shapes.append(BRepBuilderAPI_Transform(shapes[0],transform,True).Shape())
    objects = [{"id":str(i),"name":f"Objet {i}"} for i in range(2)]
    for i,shape in enumerate(shapes):
        (tmp_path/f"{i}.brep").write_bytes(encode_shape(shape,ObjectType.BODY))
    path = tmp_path/("all."+format_key)
    result = export_file(default_registry(),tmp_path,path,objects,format_key,{},lambda *args:None)
    assert result["count"] == 2 and result["triangles"] == 24
    if format_key in {"glb","gltf"}:
        doc,vertices,triangles = read_gltf(path)
        assert len(doc["nodes"]) == 2
        assert bounds(vertices) == pytest.approx((0,0,-5,110,220,30),abs=2e-5)
        assert signed_volume(triangles) == pytest.approx(12000,rel=1e-6)


@pytest.mark.parametrize("options",[MeshOptions("unknown"),MeshOptions("custom",0,5),MeshOptions("custom",.1,float("nan"))])
def test_invalid_quality_is_rejected(options,model):
    with pytest.raises(ValueError,match="Qualité"):
        triangulate(model,options)


def test_registry_extension_and_unsupported_formats_do_not_touch_output(tmp_path):
    registry = ExportRegistry()
    def writer(directory,output,objects,options,progress):
        output.write_text("custom")
        return {"count":len(objects)}
    registry.register(ExportFormat("custom","Custom",(".custom",),writer,False))
    output = tmp_path/"out.custom"
    export_file(registry,tmp_path,output,[{}],"custom",{},lambda *args:None)
    assert output.read_text() == "custom"
    with pytest.raises(ValueError,match="déjà"):
        registry.register(registry.get("custom"))
    with pytest.raises(ValueError,match="non pris en charge"):
        export_file(registry,tmp_path,output,[{}],"nope",{},lambda *args:None)
    assert output.read_text() == "custom"


@pytest.mark.parametrize("kind",["sphere","cone","torus","collection","rotated"])
def test_curved_solids_and_collections_have_closed_oriented_meshes(tmp_path,kind):
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeSphere, BRepPrimAPI_MakeCone, BRepPrimAPI_MakeTorus
    from OCC.Core.gp import gp_Ax1
    from cadastrophe.core.operation_effects import compound
    shape = {"sphere":lambda:BRepPrimAPI_MakeSphere(7.).Shape(),
             "cone":lambda:BRepPrimAPI_MakeCone(8.,0.,15.).Shape(),
             "torus":lambda:BRepPrimAPI_MakeTorus(15.,3.).Shape(),
             "collection":lambda:compound((BRepPrimAPI_MakeBox(10.,10.,10.).Shape(),
                 BRepPrimAPI_MakeBox(gp_Pnt(30,0,0),5.,5.,5.).Shape())),
             "rotated":lambda:BRepPrimAPI_MakeBox(10.,20.,30.).Shape()}[kind]()
    if kind == "rotated":
        transform = gp_Trsf()
        transform.SetRotation(gp_Ax1(gp_Pnt(5,6,7),gp_Dir(1,2,3)),.7)
        shape = BRepBuilderAPI_Transform(shape,transform,False).Shape()
    (tmp_path/"0.brep").write_bytes(encode_shape(shape,ObjectType.BODY))
    result = export_file(default_registry(),tmp_path,tmp_path/"curved.3mf",[{"id":"0","name":kind}],"3mf",{},lambda *args:None)
    assert result["triangles"] > 0


@pytest.mark.parametrize("format_key",["obj","gltf","glb"])
def test_export_reopens_in_native_independent_mesh_reader(tmp_path,model,format_key):
    from OCC.Core.RWObj import RWObj_CafReader
    from OCC.Core.RWGltf import RWGltf_CafReader
    from OCC.Core.RWMesh import RWMesh_CoordinateSystem_Zup
    from OCC.Core.Message import Message_ProgressRange
    (tmp_path/"0.brep").write_bytes(encode_shape(model,ObjectType.BODY))
    path = tmp_path/("native-reader."+format_key)
    export_file(default_registry(),tmp_path,path,[{"id":"0","name":"Support"}],format_key,{},lambda *args:None)
    reader = RWObj_CafReader() if format_key == "obj" else RWGltf_CafReader()
    reader.SetSystemLengthUnit(.001)
    reader.SetSystemCoordinateSystem(RWMesh_CoordinateSystem_Zup)
    if format_key == "obj":
        reader.SetFileLengthUnit(.001)
        reader.SetFileCoordinateSystem(RWMesh_CoordinateSystem_Zup)
    assert reader.Perform(str(path),Message_ProgressRange())
    imported = reader.SingleShape()
    assert not imported.IsNull()
    points = []
    for face in parts(imported,TopAbs_FACE):
        location = TopLoc_Location()
        mesh = BRep_Tool.Triangulation(topods.Face(face),location)
        assert mesh is not None
        points.extend(mesh.Node(i).Transformed(location.Transformation()).Coord() for i in range(1,mesh.NbNodes()+1))
    assert bounds(points) == pytest.approx((0,0,0,40,30,18),abs=2e-5)


def test_open_faces_and_reversed_solids_are_rejected():
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCC.Core.gp import gp_Pln
    face = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(0,0,0),gp_Dir(0,0,1)),0.,10.,0.,10.).Face()
    with pytest.raises(ValueError):
        triangulate(face)
    box = BRepPrimAPI_MakeBox(10.,20.,30.).Shape()
    with pytest.raises(ValueError,match="inversé"):
        triangulate(box.Reversed())
