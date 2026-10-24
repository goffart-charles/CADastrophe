"""Extensible format registry and worker boundary; the shell knows no formats."""
from dataclasses import dataclass
from pathlib import Path

from cadastrophe.brep_io import decode_shape
from cadastrophe.document import ObjectType

from .mesh import MeshOptions, triangulate, MAX_TRIANGLES
from .mesh_writers import write_stl, write_obj, write_3mf, write_gltf
from .step_io import write_step, MAX_OBJECTS, MAX_SHAPE, MAX_TOTAL


@dataclass(frozen=True)
class ExportFormat:
    key: str
    label: str
    extensions: tuple
    writer: object
    meshed: bool = True
    description: str = ""


class ExportRegistry:
    def __init__(self):
        self.formats = {}

    def register(self, descriptor):
        if descriptor.key in self.formats:
            raise ValueError("Format d’export déjà enregistré.")
        self.formats[descriptor.key] = descriptor

    def get(self, key):
        try:
            return self.formats[key]
        except KeyError:
            raise ValueError(f"Format d’export non pris en charge : {key}.") from None


def _step(directory, output, objects, options, progress):
    return write_step(directory,output,len(objects),options.get("unit","mm"),progress)


def _mesh_writer(writer, **fixed):
    def export(directory, output, objects, options, progress):
        settings = MeshOptions(options.get("quality","standard"),options.get("deflection",.1),options.get("angle",15.))
        settings.tolerances()
        meshes,total,triangles = [],0,0
        for i,obj in enumerate(objects):
            path = Path(directory)/f"{i}.brep"
            total += path.stat().st_size
            if path.stat().st_size > MAX_SHAPE or total > MAX_TOTAL:
                raise ValueError("Limite B-Rep dépassée (64 Mio par corps, 256 Mio au total).")
            progress(20+int(55*i/len(objects)),f"Triangulation : {obj['name']} ({i+1}/{len(objects)})…")
            mesh = triangulate(decode_shape(path.read_bytes(),ObjectType.BODY),settings)
            triangles += len(mesh.triangles)
            if triangles > MAX_TRIANGLES:
                raise ValueError("L’export dépasse 2 millions de triangles ; réduisez la qualité.")
            meshes.append((obj,mesh))
        progress(80,"Écriture du fichier…")
        writer(meshes,output,{**options,**fixed})
        return {"count":len(objects),"triangles":triangles,"unit":"m" if writer is write_gltf else "mm"}
    return export


def default_registry():
    registry = ExportRegistry()
    for descriptor in (
        ExportFormat("stl","STL · impression 3D",(".stl",),_mesh_writer(write_stl),description="Maillage. Coordonnées en mm ; STL ne déclare pas d’unité. Les corps sont réunis dans un fichier."),
        ExportFormat("step","STEP · géométrie CAO exacte",(".step",".stp"),_step,False,"Géométrie B-Rep exacte, sans triangulation. L’unité choisie est déclarée dans le fichier."),
        ExportFormat("obj","OBJ · maillage avec normales",(".obj",),_mesh_writer(write_obj),description="Maillage avec normales et noms d’objets. Coordonnées en mm, Z vertical ; OBJ ne déclare pas d’unité."),
        ExportFormat("3mf","3MF · impression 3D",(".3mf",),_mesh_writer(write_3mf),description="Maillage fermé, unités en mm et objets nommés dans une archive 3MF. Sans paramètres de tranchage."),
        ExportFormat("glb","GLB · scène 3D binaire",(".glb",),_mesh_writer(write_gltf,glb=True),description="Maillage, normales, couleurs et objets nommés. Conversion en mètres, Y vertical selon glTF."),
        ExportFormat("gltf","glTF · scène 3D",(".gltf",),_mesh_writer(write_gltf,glb=False),description="Scène glTF avec données intégrées dans un fichier unique. Mètres et Y vertical."),
    ):
        registry.register(descriptor)
    return registry


def export_file(registry, directory, output, objects, format_key, options, progress):
    descriptor = registry.get(format_key)
    if not objects or len(objects) > MAX_OBJECTS:
        raise ValueError("Aucun corps à exporter ou limite de 10 000 corps dépassée.")
    if Path(output).suffix.lower() not in descriptor.extensions:
        raise ValueError("L’extension ne correspond pas au format choisi.")
    result = descriptor.writer(directory,output,objects,options,progress)
    if not Path(output).is_file() or not Path(output).stat().st_size:
        raise ValueError("Le moteur d’export n’a produit aucun fichier.")
    return result
