"""OCCT naming receipts for an exact history prefix, without native indexes.

Faces retain their semantic source labels through Modified/Generated history.
An edge/vertex is named by its incident named faces. Existing split references
remain ambiguous; an explicit face pick may refine its named neighborhood.
Neither path selects the first result or the nearest face.
"""
from dataclasses import dataclass, field
from hashlib import sha256
from .topology_refs import TopologyReference


def subshapes(shape, kind):
    from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX
    from OCC.Core.TopTools import TopTools_IndexedMapOfShape
    from OCC.Core.TopExp import topexp
    mapping = TopTools_IndexedMapOfShape()
    topexp.MapShapes(shape, {"Face": TopAbs_FACE, "Edge": TopAbs_EDGE, "Vertex": TopAbs_VERTEX}[kind], mapping)
    return tuple(mapping.FindKey(i) for i in range(1, mapping.Size()+1))


def descriptor(part, kind):
    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.BRepBndLib import brepbndlib
    def bounds(s):
        box = Bnd_Box()
        box.SetGap(0.)
        brepbndlib.AddOptimal(s, box, False, False)
        return box.Get()
    coordinates = tuple(round(v, 7) for v in bounds(part))
    if kind == "Face":
        from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
        from OCC.Core.TopoDS import topods
        from OCC.Core.GeomAbs import GeomAbs_Plane
        from OCC.Core.TopAbs import TopAbs_REVERSED
        surface = BRepAdaptor_Surface(topods.Face(part))
        normal = (0., 0., 0.)
        if surface.GetType() == GeomAbs_Plane:
            sign = -1 if part.Orientation() == TopAbs_REVERSED else 1
            normal = tuple(round(sign*v, 8) for v in surface.Plane().Axis().Direction().Coord())
        return (int(surface.GetType()), *normal, *coordinates)
    if kind == "Edge":
        from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
        from OCC.Core.TopoDS import topods
        curve = BRepAdaptor_Curve(topods.Edge(part))
        return (int(curve.GetType()), *coordinates)
    return coordinates


def _digest(values):
    return sha256(repr(values).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class NamedTopology:
    # Multiple descendants may deliberately carry the same label.
    faces: tuple = ()
    # Reconstructed old naming convention, for references saved by older builds.
    # This is an alternative complete naming, never extra face ancestry.
    legacy_faces: tuple = ()
    _labels_cache: dict = field(default_factory=dict, init=False, repr=False, compare=False)

    def entries(self, *, legacy=False):
        return self.legacy_faces if legacy and self.legacy_faces else self.faces

    def labels(self, part, kind, *, legacy=False):
        cache_key = kind, legacy
        cached = self._labels_cache.get(cache_key)
        if cached is None:
            from OCC.Core.TopTools import TopTools_IndexedMapOfShape
            mapping, names = TopTools_IndexedMapOfShape(), []
            for label, face in self.entries(legacy=legacy):
                for shape in (face,) if kind == "Face" else subshapes(face, kind):
                    index = mapping.Add(shape)
                    if index > len(names):
                        names.append(set())
                    names[index-1].add(label)
            cached = mapping, tuple(tuple(sorted(labels)) for labels in names)
            self._labels_cache[cache_key] = cached
        mapping, names = cached
        index = mapping.FindIndex(part)
        return names[index-1] if index else ()


def _shape_map(shapes):
    """Transient IsSame lookup; no index is stored in a document reference."""
    from OCC.Core.TopTools import TopTools_IndexedMapOfShape
    mapping = TopTools_IndexedMapOfShape()
    for shape in shapes:
        mapping.Add(shape)
    return mapping


def _boundary_certificate(shape, face, topology):
    """Exact neighboring face names, never positions, distances or edge indexes."""
    import json
    faces = subshapes(shape, "Face")
    boundaries = []
    for edge in subshapes(face, "Edge"):
        names = set()
        for neighbor in faces:
            if neighbor.IsSame(face):
                continue
            if any(edge.IsSame(e) for e in subshapes(neighbor, "Edge")):
                labels = topology.labels(neighbor, "Face")
                if not labels:
                    raise ValueError("Le voisinage de cette face ne possède pas de noms persistants complets.")
                names.update(labels)
        if not names:
            raise ValueError("Le voisinage de cette face ne permet pas une référence persistante unique.")
        boundaries.append(tuple(sorted(names)))
    return "@boundary:" + json.dumps(sorted(boundaries), separators=(",", ":"))


def capture(shape, part, kind, topology=None):
    if not any(part.IsSame(p) for p in subshapes(shape, kind)):
        raise ValueError("La référence n’appartient pas à cet état producteur.")
    labels = topology.labels(part, kind) if topology is not None else ()
    ref = TopologyReference(kind, labels, descriptor(part, kind))
    try:
        resolve(shape, ref, topology)
    except ValueError:
        if kind != "Face" or topology is None or not labels:
            raise
        # An explicit pick can refine a split face by its named boundaries.
        # Existing references are never refined while merely resolving them.
        ref = TopologyReference(kind, (*labels, _boundary_certificate(shape, part, topology)), ref.geometry)
        resolve(shape, ref, topology)  # Refuse symmetric/ambiguous neighborhoods too.
    return ref


def resolve(shape, ref, topology=None):
    parts = subshapes(shape, ref.kind)
    if ref.labels:
        if topology is None:
            raise ValueError("Référence topologique perdue : historique du producteur indisponible.")
        certificates = tuple(label for label in ref.labels if label.startswith("@boundary:"))
        if len(certificates) > 1 or certificates and (ref.kind != "Face" or len(certificates) == len(ref.labels)):
            raise ValueError("Certificat de référence topologique invalide.")
        expected = set(ref.labels) - set(certificates)
        candidates = [(p, tuple(set(topology.labels(p, ref.kind, legacy=legacy))
                                for legacy in (False, True))) for p in parts]
        matches = [p for p, names in candidates if any(expected <= labels for labels in names)]
        if certificates:
            refined = []
            for part in matches:
                if expected != set(topology.labels(part, ref.kind)):
                    continue  # A provenance fusion is not a continuation of the chosen face.
                try:
                    if _boundary_certificate(shape, part, topology) == certificates[0]:
                        refined.append(part)
                except ValueError:
                    continue
            matches = refined
        if len(matches) == 1 and not any(p.IsSame(matches[0]) and expected in names for p, names in candidates):
            raise ValueError("Référence topologique ambiguë après fusion des provenances ; réassociation nécessaire.")
    else:
        matches = [p for p in parts if len(ref.geometry) == len(descriptor(p, ref.kind))
                   and max(abs(a-b) for a, b in zip(ref.geometry, descriptor(p, ref.kind))) < 1e-7]
    if len(matches) != 1:
        what = "perdue" if not matches else "ambiguë"
        raise ValueError(f"Référence topologique {what} : {ref.kind} du producteur ; réassociation nécessaire.")
    return matches[0]


def canonical_reference(shape, ref, topology):
    """Upgrade a saved name only after proving its unique original selection."""
    if not ref.labels or topology is None:
        return ref
    part = resolve(shape, ref, topology)
    current = capture(shape, part, ref.kind, topology)
    return TopologyReference(ref.kind, current.labels, ref.geometry)


def static_topology(shape, namespace="static"):
    return NamedTopology(tuple((namespace+":"+_digest(descriptor(f, "Face")), f)
                               for f in subshapes(shape, "Face")))


def isolated_input(shape, topology, trace=None):
    """Deep native copy with exact naming correspondence for mutating builders."""
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Copy
    copier = BRepBuilderAPI_Copy(shape, True, False)
    if not copier.IsDone() or copier.Shape().IsNull():
        raise ValueError("Impossible d'isoler la B-Rep source du calcul.")
    copied = copier.Shape()
    names = NamedTopology(tuple((label, copier.ModifiedShape(face)) for label, face in topology.faces),
                          tuple((label, copier.ModifiedShape(face)) for label, face in topology.legacy_faces))
    if trace is not None:
        trace.known.append((copied, names))
    return copied, names


class NamingTrace:
    """Short-lived observer of native builders; retains no builder after return."""
    def __init__(self, feature_id, previous=None, sources=()):
        self.feature_id, self.sources = feature_id, tuple(sources)
        self.known = []
        if previous is not None and previous.shape is not None:
            self.known.append((previous.shape, previous.topology or static_topology(previous.shape)))
        self.topology = None

    def _source(self, edge, face):
        from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
        from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
        from OCC.Core.TopoDS import topods
        from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Common
        from OCC.Core.BRepGProp import brepgprop
        from OCC.Core.GProp import GProp_GProps
        from .native_boolean import build_boolean
        curve = BRepAdaptor_Curve(topods.Edge(edge))
        start, end = curve.FirstParameter(), curve.LastParameter()
        points = [BRepBuilderAPI_MakeVertex(curve.Value(start+(end-start)*t)).Vertex() for t in (0., .37, 1.)]
        properties = GProp_GProps()
        brepgprop.LinearProperties(edge, properties)
        length = properties.Mass()
        ids = []
        for key, source in self.sources:
            if all((dist := BRepExtrema_DistShapeShape(p, source)).IsDone() and dist.Value() < 1e-6 for p in points):
                # Sampling is only a broad phase. A crossing spline can pass
                # through all three samples without containing this edge.
                common = build_boolean(BRepAlgoAPI_Common, edge, source)
                if not common.IsDone():
                    raise ValueError("Impossible de vérifier la provenance d'une arête du profil.")
                overlap = GProp_GProps()
                brepgprop.LinearProperties(common.Shape(), overlap)
                if length > 0 and abs(length-overlap.Mass()) <= max(1e-7, length*1e-10):
                    ids.append(key)
        return ",".join(sorted(ids)) if ids else "geometry:"+_digest(descriptor(edge, "Edge"))

    def prism(self, builder, face, result):
        edges = [(edge, self._source(edge, face)) for edge in subshapes(face, "Edge")]
        region = _digest(sorted(key for _, key in edges))
        from OCC.Core.BRepTools import breptools
        outer = subshapes(breptools.OuterWire(face), "Edge")
        boundary = _digest(sorted(key for edge, key in edges if any(edge.IsSame(e) for e in outer)))
        prefix = self.feature_id+":prism:"+boundary
        old_prefix = self.feature_id+":"+region
        named = [(prefix+":start", builder.FirstShape()), (prefix+":end", builder.LastShape())]
        legacy = [(old_prefix+":start", builder.FirstShape()), (old_prefix+":end", builder.LastShape())]
        from OCC.Core.TopAbs import TopAbs_FACE
        for edge, key in edges:
            generated = tuple(s for s in builder.Generated(edge) if s.ShapeType() == TopAbs_FACE)
            named.extend((self.feature_id+":prism:side:"+key, s) for s in generated)
            legacy.extend((old_prefix+":side:"+key, s) for s in generated)
        self._record(result, named, legacy=legacy)

    def revolution(self, builder, face, result):
        from OCC.Core.TopAbs import TopAbs_FACE
        edges = [(edge, self._source(edge, face)) for edge in subshapes(face, "Edge")]
        from OCC.Core.BRepTools import breptools
        outer = subshapes(breptools.OuterWire(face), "Edge")
        prefix = self.feature_id+":revolution:"+_digest(sorted(key for edge, key in edges if any(edge.IsSame(e) for e in outer)))
        old_prefix = self.feature_id+":revolution:"+_digest(sorted(key for _, key in edges))
        named = [(prefix+":start", builder.FirstShape()), (prefix+":end", builder.LastShape())]
        legacy = [(old_prefix+":start", builder.FirstShape()), (old_prefix+":end", builder.LastShape())]
        for edge, key in edges:
            generated = tuple(s for s in builder.Generated(edge) if s.ShapeType() == TopAbs_FACE)
            named.extend((self.feature_id+":revolution:side:"+key, s) for s in generated)
            legacy.extend((old_prefix+":side:"+key, s) for s in generated)
        self._record(result, named, legacy=legacy)

    def sweep(self, builder, wire, result, source_keys):
        from OCC.Core.TopAbs import TopAbs_FACE
        edges = tuple((e, next(key for source, key in source_keys if e.IsSame(source))) for e in subshapes(wire, "Edge"))
        prefix = self.feature_id+":sweep:"+_digest(sorted(key for _, key in edges))
        named = []
        for edge, key in edges:
            named.extend((prefix+":side:"+key, s) for s in builder.Generated(edge) if s.ShapeType() == TopAbs_FACE)
        # MakePipeShell exposes cap wires. Match their exact topological edge
        # sets to the native cap faces, without a position or an index fallback.
        for label, cap in (("start", builder.FirstShape()), ("end", builder.LastShape())):
            if cap.IsNull():
                continue
            boundary = subshapes(cap, "Edge")
            for face in subshapes(result, "Face"):
                contour = subshapes(face, "Edge")
                if boundary and len(contour) == len(boundary) and all(any(e.IsSame(c) for c in contour) for e in boundary):
                    named.append((prefix+":"+label, face))
        self._record(result, named)

    def loft(self, builder, wires, parameters, result):
        from OCC.Core.TopAbs import TopAbs_FACE
        named = []
        for index, (_, edges) in enumerate(wires):
            section = parameters.sections[index]
            prefix = self.feature_id+":loft:"+section.source.sketch_id
            if index == 0 or index == len(wires)-1:
                cap = builder.FirstShape() if index == 0 else builder.LastShape()
                named.append((prefix+":cap:"+_digest(sorted(key for _, key in edges)), cap))
            if index == len(wires)-1 or parameters.mode != "ruled" and index != 0:
                continue
            for edge, key in edges:
                # Compatibility may split an input edge. Generated retains
                # all descendants; a split label then deliberately refuses
                # ambiguous references instead of choosing one face.
                child = builder.GeneratedFace(edge)
                children = (child,) if child is not None and not child.IsNull() else tuple(builder.Generated(edge))
                named.extend((prefix+":side:"+key, f) for child in children
                             for f in subshapes(child, "Face") if f.ShapeType() == TopAbs_FACE)
        self._record(result, named)

    def transformed(self, builder, topology, result, occurrence):
        named = [(self.feature_id+f":instance:{occurrence}:"+label, builder.ModifiedShape(face))
                 for label, face in topology.faces]
        legacy = [(self.feature_id+f":instance:{occurrence}:"+label, builder.ModifiedShape(face))
                  for label, face in topology.entries(legacy=True)]
        self._record(result, named, legacy=legacy)

    def operation(self, builder, inputs, result):
        from OCC.Core.TopAbs import TopAbs_FACE
        final = subshapes(result, "Face")
        final_map = _shape_map(final)
        named, legacy = [], []
        for shape in inputs:
            topology = next((t for s, t in reversed(self.known) if s.IsSame(shape)), None)
            if topology is None:
                parts = subshapes(shape, "Face")
                part_map = _shape_map(parts)
                entries = tuple((label, face) for _, t in self.known for label, face in t.faces
                                if part_map.FindIndex(face))
                old_entries = tuple((label, face) for _, t in self.known for label, face in t.entries(legacy=True)
                                    if part_map.FindIndex(face))
                topology = NamedTopology(entries, old_entries) if entries else static_topology(shape)
            for use_legacy, output in ((False, named), (True, legacy)):
                for label, face in topology.entries(legacy=use_legacy):
                    descendants = (face, *builder.Modified(face))
                    output.extend((label, final[index-1]) for d in descendants
                                  if (index := final_map.FindIndex(d)))
                for edge in subshapes(shape, "Edge"):
                    labels = topology.labels(edge, "Edge", legacy=use_legacy)
                    if labels:
                        label = self.feature_id+":generated:"+_digest(labels)
                        output.extend((label, s) for s in builder.Generated(edge) if s.ShapeType() == TopAbs_FACE)
        self._record(result, named, legacy=legacy)

    def modified_shape(self, builder, source, result):
        """BRepBuilderAPI_ModifyShape supplies ModifiedShape, not history lists."""
        topology = next((t for s, t in reversed(self.known) if s.IsSame(source)), None)
        topology = topology or static_topology(source)
        named, legacy = [], []
        for use_legacy, output in ((False, named), (True, legacy)):
            for label, face in topology.entries(legacy=use_legacy):
                changed = builder.ModifiedShape(face)
                if not changed.IsNull():
                    output.extend((label, child) for child in subshapes(changed, "Face"))
        self._record(result, named, legacy=legacy)

    def thick_solid(self, builder, source, result):
        """Distinguish retained source faces from their offset counterparts."""
        from OCC.Core.TopAbs import TopAbs_FACE
        topology = next((t for s, t in reversed(self.known) if s.IsSame(source)), None)
        topology = topology or static_topology(source)
        named, legacy = [], []
        for use_legacy, output in ((False, named), (True, legacy)):
            for label, face in topology.entries(legacy=use_legacy):
                output.append((label, face))
                for child in (*builder.Modified(face), *builder.Generated(face)):
                    if child.ShapeType() == TopAbs_FACE and not child.IsSame(face):
                        output.append((self.feature_id+":offset:"+label, child))
            for edge in subshapes(source, "Edge"):
                labels = topology.labels(edge, "Edge", legacy=use_legacy)
                if labels:
                    output.extend((self.feature_id+":rim:"+_digest(labels), child)
                                  for child in builder.Generated(edge) if child.ShapeType() == TopAbs_FACE)
        self._record(result, named, legacy=legacy)

    def _record(self, result, named, *, legacy=None):
        entries = self._entries(result, named)
        aliases = () if legacy is None else self._entries(result, legacy)
        self.topology = NamedTopology(entries, aliases)
        self.known.append((result, self.topology))

    def _entries(self, result, named):
        final, entries = subshapes(result, "Face"), []
        mapping, seen, tracked = _shape_map(final), set(), set()
        for label, face in named:
            index = 0 if face.IsNull() else mapping.FindIndex(face)
            if index and (label, index) not in seen:
                entries.append((label, final[index-1]))
                seen.add((label, index))
                tracked.add(index)
        for index, face in enumerate(final, 1):
            if index not in tracked:
                entries.append((self.feature_id+":untracked:"+_digest(descriptor(face, "Face")), face))
        return tuple(entries)
