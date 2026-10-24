"""Small standards-based mesh serializers, with no additional dependency."""
import base64
import json
import struct
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED


def safe_name(name):
    return " ".join(str(name).split()) or "Corps"


def write_stl(objects, output, options):
    binary = options.get("binary",True)
    with open(output,"wb" if binary else "w", **({} if binary else {"encoding":"ascii","newline":"\n"})) as stream:
        if binary:
            count = sum(len(mesh.triangles) for _,mesh in objects)
            stream.write(b"CADastrophe STL - coordinates in millimeters".ljust(80,b" "))
            stream.write(struct.pack("<I",count))
        for obj,mesh in objects:
            if not binary:
                stream.write("solid "+safe_name(obj["name"]).encode("ascii","replace").decode()+"\n")
            for normal,points in mesh.facets():
                if binary:
                    stream.write(struct.pack("<12fH",*normal,*(v for point in points for v in point),0))
                else:
                    stream.write("  facet normal %.17g %.17g %.17g\n    outer loop\n" % normal)
                    for point in points:
                        stream.write("      vertex %.17g %.17g %.17g\n" % point)
                    stream.write("    endloop\n  endfacet\n")
            if not binary:
                stream.write("endsolid\n")


def write_obj(objects, output, options):
    with open(output,"w",encoding="utf-8",newline="\n") as stream:
        stream.write("# CADastrophe OBJ - millimeters, Z up\n")
        vertex_offset, normal_offset = 1,1
        for obj,mesh in objects:
            stream.write("o "+safe_name(obj["name"])+"\ns off\n")
            for point in mesh.vertices:
                stream.write("v %.17g %.17g %.17g\n" % point)
            for normal,_ in mesh.facets():
                stream.write("vn %.17g %.17g %.17g\n" % normal)
            for i,triangle in enumerate(mesh.triangles):
                stream.write("f "+" ".join(f"{j+vertex_offset}//{i+normal_offset}" for j in triangle)+"\n")
            vertex_offset += len(mesh.vertices)
            normal_offset += len(mesh.triangles)


def write_3mf(objects, output, options):
    namespace = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    ET.register_namespace("",namespace)
    def tag(name):
        return "{"+namespace+"}"+name
    model = ET.Element(tag("model"),{"unit":"millimeter","{http://www.w3.org/XML/1998/namespace}lang":"fr-FR"})
    ET.SubElement(model,tag("metadata"),{"name":"Application"}).text = "CADastrophe"
    resources, build = ET.SubElement(model,tag("resources")), ET.SubElement(model,tag("build"))
    for i,(obj,mesh) in enumerate(objects,1):
        resource = ET.SubElement(resources,tag("object"),{"id":str(i),"type":"model","name":safe_name(obj["name"]),"partnumber":obj["id"]})
        native = ET.SubElement(resource,tag("mesh"))
        vertices,triangles = ET.SubElement(native,tag("vertices")),ET.SubElement(native,tag("triangles"))
        for point in mesh.vertices:
            ET.SubElement(vertices,tag("vertex"),dict(zip(("x","y","z"),(format(v,".17g") for v in point))))
        for triangle in mesh.triangles:
            ET.SubElement(triangles,tag("triangle"),dict(zip(("v1","v2","v3"),map(str,triangle))))
        ET.SubElement(build,tag("item"),{"objectid":str(i)})
    with ZipFile(output,"w",compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml",'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
        archive.writestr("_rels/.rels",'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" Target="/3D/3dmodel.model"/></Relationships>')
        archive.writestr("3D/3dmodel.model",ET.tostring(model,encoding="utf-8",xml_declaration=True))


def write_gltf(objects, output, options):
    binary = options.get("glb",True)
    data = bytearray()
    doc = {"asset":{"version":"2.0","generator":"CADastrophe"},"scene":0,"scenes":[{"nodes":[]}],
           "nodes":[],"meshes":[],"materials":[],"buffers":[],"bufferViews":[],"accessors":[]}
    def accessor(values,*,position=False):
        offset = len(data)
        for value in values:
            data.extend(struct.pack("<3f",*value))
        # All attributes are float VEC3; each view is naturally 4-byte aligned.
        index = len(doc["accessors"])
        doc["bufferViews"].append({"buffer":0,"byteOffset":offset,"byteLength":len(data)-offset,"target":34962})
        entry = {"bufferView":index,"componentType":5126,"count":len(values),"type":"VEC3"}
        if position:
            # Bounds must describe the serialized float32 values exactly.
            floats = [struct.unpack_from("<3f",data,offset+i*12) for i in range(len(values))]
            entry.update(min=[min(p[i] for p in floats) for i in range(3)],max=[max(p[i] for p in floats) for i in range(3)])
        doc["accessors"].append(entry)
        return index
    for i,(obj,mesh) in enumerate(objects):
        positions,normals = [],[]
        # CAD: mm/Z up. glTF: metres/Y up; proper rotation, no reflection.
        for normal,points in mesh.facets():
            normals.extend([(normal[0],normal[2],-normal[1])]*3)
            positions.extend((x*.001,z*.001,-y*.001) for x,y,z in points)
        position,normal = accessor(positions,position=True),accessor(normals)
        color = obj.get("color",(.7,.7,.7))
        # The viewport uses Quantity_TOC_RGB (linear), like glTF material factors.
        alpha = 1.-obj.get("transparency",0.)
        material = {"pbrMetallicRoughness":{"baseColorFactor":[*color,alpha],"metallicFactor":0.,"roughnessFactor":.65}}
        if alpha < 1.:
            material["alphaMode"] = "BLEND"
        doc["materials"].append(material)
        doc["meshes"].append({"name":obj["name"],"primitives":[{"attributes":{"POSITION":position,"NORMAL":normal},"material":i,"mode":4}]})
        doc["nodes"].append({"name":obj["name"],"mesh":i,"extras":{"cadastropheObjectId":obj["id"]}})
        doc["scenes"][0]["nodes"].append(i)
    doc["buffers"].append({"byteLength":len(data)})
    if binary:
        encoded = json.dumps(doc,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode("utf-8")
        encoded += b" "*((-len(encoded))%4)
        data.extend(b"\0"*((-len(data))%4))
        with open(output,"wb") as stream:
            stream.write(struct.pack("<4sII",b"glTF",2,12+8+len(encoded)+8+len(data)))
            stream.write(struct.pack("<II",len(encoded),0x4E4F534A)+encoded)
            stream.write(struct.pack("<II",len(data),0x004E4942)+data)
    else:
        # Embedded data keeps export atomic: no sidecar .bin to overwrite halfway.
        doc["buffers"][0]["uri"] = "data:application/octet-stream;base64,"+base64.b64encode(data).decode("ascii")
        with open(output,"w",encoding="utf-8",newline="\n") as stream:
            json.dump(doc,stream,ensure_ascii=False,separators=(",",":"),allow_nan=False)
