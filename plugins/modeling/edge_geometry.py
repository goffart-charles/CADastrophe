"""Native edge treatments with geometric references to an explicit body prefix."""
from math import radians

from cadastrophe.core.feature_model import Operation


def edges_of(shape):
    from OCC.Core.TopAbs import TopAbs_EDGE
    from OCC.Core.TopExp import topexp
    from OCC.Core.TopTools import TopTools_IndexedMapOfShape
    from OCC.Core.TopoDS import topods
    result = TopTools_IndexedMapOfShape()
    topexp.MapShapes(shape, TopAbs_EDGE, result)
    return tuple(topods.Edge(result.FindKey(i)) for i in range(1, result.Size()+1))


def edge_reference(shape, edge):
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.BRepBndLib import brepbndlib
    box = Bnd_Box()
    box.SetGap(0.)
    brepbndlib.Add(shape, box)
    values = box.Get()
    curve = BRepAdaptor_Curve(edge)
    first, last = curve.FirstParameter(), curve.LastParameter()
    def normalized(t):
        p = curve.Value(t)
        return tuple(round((v-values[i])/max(values[i+3]-values[i], 1e-7), 6)
                     for i, v in enumerate((p.X(), p.Y(), p.Z())))
    a, b = sorted((normalized(first), normalized(last)))
    return (int(curve.GetType()), *a, *normalized((first+last)/2), *b)


def resolve_edges(shape, references):
    available = [(edge_reference(shape, e), e) for e in edges_of(shape)]
    result = []
    for ref in references:
        matches = [e for key, e in available if key[0] == ref[0]
                   and max(abs(a-b) for a, b in zip(key[1:], ref[1:])) < 2e-5]
        if len(matches) != 1:
            raise ValueError("Arête source absente ou ambiguë après reconstruction ; choisissez-la à nouveau.")
        result.append(matches[0])
    return tuple(result)


def apply_edges(shape, operation, parameters, *, with_base_faces=False, topology=None, trace=None):
    if shape is None or shape.IsNull():
        raise ValueError("Le corps source ne possède pas de solide disponible.")
    from OCC.Core.BRepFilletAPI import BRepFilletAPI_MakeFillet, BRepFilletAPI_MakeChamfer
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    from OCC.Core.TopExp import topexp, TopExp_Explorer
    from OCC.Core.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
    from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID
    from OCC.Core.TopoDS import topods
    if parameters.references:
        from cadastrophe.core.native_naming import resolve
        selected = tuple(topods.Edge(resolve(shape, ref, topology)) for ref in parameters.references)
    else:
        selected = resolve_edges(shape, parameters.edges)
    maker = BRepFilletAPI_MakeFillet(shape) if operation is Operation.FILLET else BRepFilletAPI_MakeChamfer(shape)
    faces = TopTools_IndexedDataMapOfShapeListOfShape()
    topexp.MapShapesAndAncestors(shape, TopAbs_EDGE, TopAbs_FACE, faces)
    for edge in selected:
        if maker.Contour(edge):
            continue
        if operation is Operation.FILLET:
            if parameters.mode == "variable":
                a, b = (parameters.size2, parameters.size) if parameters.flip else (parameters.size, parameters.size2)
                maker.Add(a, b, edge)
            else:
                maker.Add(parameters.size, edge)
        elif parameters.mode == "symmetric":
            maker.Add(parameters.size, edge)
        else:
            adjacent = tuple(faces.FindFromKey(edge))
            from OCC.Core.BRepGProp import brepgprop
            from OCC.Core.GProp import GProp_GProps
            def side_key(face):
                properties = GProp_GProps()
                brepgprop.SurfaceProperties(face, properties)
                return properties.CentreOfMass().Coord()
            adjacent = sorted(adjacent, key=side_key)
            if not adjacent:
                raise ValueError("L’arête ne borde aucune face du solide.")
            face = topods.Face(adjacent[-1 if parameters.flip else 0])
            if parameters.mode == "distance_angle":
                maker.AddDA(parameters.size, radians(parameters.angle), edge, face)
            else:
                maker.Add(parameters.size, parameters.size2, edge, face)
    if not parameters.tangent:
        for i in range(1, maker.NbContours()+1):
            for j in range(1, maker.NbEdges(i)+1):
                if not any(maker.Edge(i, j).IsSame(e) for e in selected):
                    raise ValueError("Cette chaîne tangente nécessite la propagation ; sélectionnez toutes ses arêtes ou activez-la.")
    try:
        maker.Build()
        if not maker.IsDone():
            raise ValueError("Le traitement est impossible avec ces paramètres ; réduisez la taille ou la sélection.")
        result = maker.Shape()
        explorer, solids = TopExp_Explorer(result, TopAbs_SOLID), []
        while explorer.More():
            solids.append(topods.Solid(explorer.Current()))
            explorer.Next()
        from cadastrophe.core.operation_effects import checked_solids, compound
        previous_count = len(checked_solids(shape))
        if len(solids) != previous_count:
            raise ValueError("Le traitement ne produit pas un solide valide.")
        checked_solids(result)
        result = compound(tuple(solids))
        checked_solids(result)
        if trace is not None:
            trace.operation(maker, (shape,), result)
        if with_base_faces:
            source_faces, result_faces = [], []
            for original, target in ((shape, source_faces), (result, result_faces)):
                explorer = TopExp_Explorer(original, TopAbs_FACE)
                while explorer.More():
                    target.append(topods.Face(explorer.Current()))
                    explorer.Next()
            base_faces = []
            for face in source_faces:
                modified = tuple(maker.Modified(face))
                for survivor in modified or (face,):
                    for result_face in result_faces:
                        if result_face.IsSame(survivor) and not any(result_face.IsSame(old) for old in base_faces):
                            base_faces.append(result_face)
            return result, tuple(base_faces)
        return result
    except RuntimeError as exc:
        raise ValueError(f"Congé/chanfrein impossible : {exc}") from exc
