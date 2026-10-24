"""Recipe reference inspection and detached replacement, shared with plugins."""
from dataclasses import dataclass, fields, is_dataclass, replace

from .feature_model import BodyStateRef, ReferenceSlot, SketchInput, ResultStatus
from .feature_objects import BodyObject, ConstructionFeature, EdgeFeature, ReplayFeature, SketchSourceObject, LoftFeature, HoleFeature
from .reference_objects import DatumPlaneObject
from .operation_model import GeometryReference
from .topology_refs import TopologyReference


def value_at(value, path):
    for key in path:
        value = value[key] if isinstance(key, int) else getattr(value, key)
    return value


def replaced_at(value, path, replacement):
    if not path:
        return replacement
    key, *rest = path
    if isinstance(key, int):
        return tuple(replaced_at(item, rest, replacement) if i == key else item for i, item in enumerate(value))
    return replace(value, **{key: replaced_at(getattr(value, key), rest, replacement)})


@dataclass(frozen=True)
class RepairEntry:
    slot: ReferenceSlot
    path: tuple
    role: str
    value: object
    label: str
    sketch_id: str | None = None

    @property
    def key(self):
        return self.slot.owner_id, self.path


def reference_entries(document, object_id):
    """Walk recipes only; failed topology never removes a repairable row."""
    entries, seen = [], set()

    def geometry(owner, value, path):
        if isinstance(value, GeometryReference):
            field = path[-2] if isinstance(path[-1], int) else path[-1]
            role = ("axis" if field in {"axis", "direction", "reference"} and
                    (field != "reference" or isinstance(owner, ReplayFeature) and owner.operation.value != "mirror") else
                    "curve" if field == "path" else "face" if field in {"faces", "face"} else
                    "plane" if field in {"neutral", "reference"} else
                    "point" if field == "positions" or getattr(owner.parameters, "method", None) == "three_points" else
                    "axis" if getattr(owner.parameters, "method", None) == "inclined" and path[-1] == 1 else "plane")
            state = BodyStateRef(value.object_id, value.producer_id) if value.kind in {"face", "edge", "vertex"} else None
            slot = ReferenceSlot(owner.id, ".".join(map(str, path)), value.object_id or owner.id, state,
                                 value.topology, (value.entity_id,) if value.entity_id else ())
            titles = {"axis": "Axe", "direction": "Direction", "neutral": "Plan neutre",
                      "reference": "Référence du motif", "faces": "Face", "path": "Courbe du parcours",
                      "positions": "Point de perçage", "references": "Point" if role == "point" else "Référence du plan"}
            label = titles.get(field, "Référence")
            if isinstance(path[-1], int):
                label += f" {path[-1]+1}"
            entries.append(RepairEntry(slot, path, role, value, label))
            if value.object_id and value.kind in {"plane", "sketch_line", "sketch_curve", "sketch_point", 'curve'}:
                visit(value.object_id)
        elif isinstance(value, tuple):
            for i, item in enumerate(value):
                geometry(owner, item, (*path, i))
        elif is_dataclass(value):
            for item in fields(value):
                geometry(owner, getattr(value, item.name), (*path, item.name))

    def visit(key):
        if key in seen or key not in document.objects:
            return
        seen.add(key)
        obj = document.objects[key]
        if isinstance(obj, BodyObject):
            for fid in obj.features:
                visit(fid)
        elif isinstance(obj, SketchSourceObject):
            for slot in obj.reference_slots:
                path = ("sketch", "support") if slot.path == "sketch.support" else ("sketch", "external", int(slot.path.rsplit(".", 1)[1]))
                entries.append(RepairEntry(slot, path, "support" if path[-1] == "support" else "external",
                                           value_at(obj, path), "Support" if path[-1] == "support" else "Projection"))
                visit(slot.target_id) if isinstance(document.objects.get(slot.target_id), DatumPlaneObject) else None
        elif isinstance(obj, (ConstructionFeature, DatumPlaneObject)):
            if isinstance(obj, ConstructionFeature):
                for i, (source, contours) in enumerate(obj.profile_sections):
                    source_path = ("parameters", "sections", i, "source") if isinstance(obj, LoftFeature) else ("source",)
                    contour_path = ("parameters", "sections", i, "contours") if isinstance(obj, LoftFeature) else ("parameters", "contours")
                    slot = ReferenceSlot(obj.id, ".".join(map(str, source_path)), source.sketch_id)
                    entries.append(RepairEntry(slot, source_path, "sketch", source,
                                              "Esquisse de positionnement" if isinstance(obj, HoleFeature) else f"Profil {i+1}"))
                    for j, contour in enumerate(contours):
                        path = (*contour_path, j)
                        slot = ReferenceSlot(obj.id, ".".join(map(str, path)), source.sketch_id, entity_ids=(contour,))
                        entries.append(RepairEntry(slot, path, "contour", contour, f"Contour {j+1} du profil {i+1}", source.sketch_id))
                    if isinstance(obj, LoftFeature):
                        for j, anchor in enumerate(obj.parameters.sections[i].anchors):
                            path = ("parameters", "sections", i, "anchors", j)
                            slot = ReferenceSlot(obj.id, ".".join(map(str, path)), source.sketch_id, entity_ids=(anchor,))
                            entries.append(RepairEntry(slot, path, "anchor", anchor, f"Ancre {j+1} du profil {i+1}", source.sketch_id))
                    visit(source.sketch_id)
            if isinstance(obj, EdgeFeature):
                refs = obj.parameters.references or tuple(TopologyReference("Edge", geometry=e) for e in obj.parameters.edges)
                from .construction_state import feature_dependencies
                state = feature_dependencies(document.objects, obj).predecessor
                for i, ref in enumerate(refs):
                    path = ("parameters", "references", i)
                    slot = ReferenceSlot(obj.id, ".".join(map(str, path)), obj.body_id, state, ref)
                    entries.append(RepairEntry(slot, path, "edge", ref, f"Arête {i+1}"))
            if isinstance(obj, ReplayFeature):
                for i, source in enumerate(obj.parameters.sources):
                    path = ("parameters", "sources", i)
                    slot = ReferenceSlot(obj.id, ".".join(map(str, path)), obj.body_id, BodyStateRef(obj.body_id, source))
                    entries.append(RepairEntry(slot, path, "function", source, f"Fonction source {i+1}"))
            geometry(obj, obj.parameters, ("parameters",))

    visit(object_id)
    obj = document.objects.get(object_id)
    if isinstance(obj, ConstructionFeature):
        from .body_history import result_at
        state = result_at(document, BodyStateRef(obj.body_id, obj.id))
        for diagnostic in state.diagnostics:
            for root in diagnostic.root_ids:
                visit(root)
    priority = {"support": 0, "external": 0, "sketch": 3, "contour": 2, "anchor": 2}
    return tuple(sorted(entries, key=lambda entry: priority.get(entry.role, 1)))


def entry_issue(document, entry, profiles):
    """Return the real resolution failure; never guess another target."""
    from .reference_geometry import resolve_reference, resolve_axis_reference, state_for, resolve_topology
    from .native_naming import resolve
    try:
        owner, value = document.objects[entry.slot.owner_id], entry.value
        if entry.role in {"support", "external"}:
            profiles.reference_status(document, owner.id, entry.path)
        elif entry.role == "sketch":
            sketch = document.objects.get(value.sketch_id)
            if not isinstance(sketch, SketchSourceObject):
                raise ValueError("Esquisse absente.")
            error = sketch.reference_error(document)
            if error:
                raise ValueError("Dépendance de l'esquisse : " + error)
            if isinstance(owner, HoleFeature):
                profiles.position_points(document, value.sketch_id, tuple(r.entity_id for r in owner.parameters.positions))
        elif entry.role in {"contour", "anchor"}:
            sketch = document.objects.get(entry.sketch_id)
            if not isinstance(sketch, SketchSourceObject) or value not in sketch.profile_source_ids:
                raise ValueError("Entité de contour absente.")
            error = sketch.reference_error(document)
            if error:
                raise ValueError("Dépendance de l'esquisse : " + error)
            if entry.role == "contour" and value not in profiles.selectable_contours(document, sketch.id):
                raise ValueError("Le contour sélectionné ne définit plus une région fermée.")
        elif entry.role == "function":
            source = document.objects.get(value)
            body = document.objects[owner.body_id]
            if (not isinstance(source, ConstructionFeature) or not source.replayable or not source.enabled
                    or source.body_id != owner.body_id or body.features.index(source.id) >= body.features.index(owner.id)):
                raise ValueError("Fonction source absente, désactivée ou incompatible.")
        elif entry.role == "edge":
            state = state_for(document, entry.slot.state)
            if owner.parameters.references:
                resolve_topology(document, entry.slot.state, value, state=state)
            else:
                raise ValueError("Ancienne sélection sans nom persistant : choisissez explicitement une arête.")
        elif entry.role == "curve":
            if value.kind == "edge":
                state = state_for(document, BodyStateRef(value.object_id, value.producer_id))
                resolve_topology(document, BodyStateRef(value.object_id, value.producer_id), value.topology, state=state)
            elif value.kind == "sketch_curve":
                profiles.path_edges(document, value.object_id, (value.entity_id,))
            elif value.kind == 'curve':
                from .curve_geometry import curve_edge
                curve_edge(document, document.objects.get(value.object_id))
            else:
                raise ValueError("Choisissez une courbe ou une arête nommée.")
        elif entry.role == "face":
            if value.kind != "face":
                raise ValueError("Choisissez une face du corps.")
            state = state_for(document, BodyStateRef(value.object_id, value.producer_id))
            resolve_topology(document, BodyStateRef(value.object_id, value.producer_id), value.topology, state=state)
        else:
            if isinstance(owner, HoleFeature) and entry.role == "point" and (
                    value.kind != "sketch_point" or value.object_id != owner.sketch_id or value.part != "position"):
                raise ValueError("Choisissez un point de l’esquisse de positionnement du perçage.")
            from ..geometry import WorkPlane, Vec3
            resolved = resolve_axis_reference(document, value) if entry.role == "axis" else resolve_reference(document, value)
            if entry.role in {"plane", "face"} and not isinstance(resolved, WorkPlane):
                raise ValueError("Une face plane ou un plan est requis.")
            if entry.role == "point" and not isinstance(resolved, Vec3):
                raise ValueError("Un point ou sommet est requis.")
        return ""
    except (ValueError, RuntimeError, KeyError) as exc:
        return str(exc)


def replacement_objects(document, entry, value, profiles, *, preserve_frame=True):
    """Prepare replacement snapshots. Whole-graph validation follows staging."""
    owner = document.objects[entry.slot.owner_id]
    if entry.role in {"support", "external"}:
        return profiles.replace_reference(document, owner.id, entry.path, value, preserve_frame=preserve_frame)
    if entry.role == "sketch":
        sketch_id, contours = value
        sketch = document.objects.get(sketch_id)
        if not isinstance(sketch, SketchSourceObject) or set(contours) - set(sketch.profile_source_ids):
            raise ValueError("Sélection de profil incompatible.")
        if isinstance(owner, HoleFeature):
            fresh = replace(owner, source=SketchInput(sketch_id, sketch.producer_refs),
                            parameters=replace(owner.parameters, positions=()))
        else:
            fresh = replaced_at(owner, entry.path, SketchInput(sketch_id, sketch.producer_refs))
            contour_path = (*entry.path[:-1], "contours") if isinstance(owner, LoftFeature) else ("parameters", "contours")
            fresh = replaced_at(fresh, contour_path, tuple(contours))
    elif entry.role == "edge":
        if not isinstance(value, GeometryReference) or value.kind != "edge" or BodyStateRef(value.object_id, value.producer_id) != entry.slot.state:
            raise ValueError("Choisissez une arête de l'état précédant cette fonction.")
        refs = owner.parameters.references or tuple(TopologyReference("Edge", geometry=e) for e in owner.parameters.edges)
        fresh = replace(owner, parameters=replace(owner.parameters, references=tuple(value.topology if i == entry.path[-1] else ref
                                                                                  for i, ref in enumerate(refs))))
    else:
        fresh = replaced_at(owner, entry.path, value)
    if fresh.id != owner.id or fresh.expressions != owner.expressions:
        raise ValueError("La réparation doit conserver l'identité et les équations.")
    return (fresh,)


def related_objects(document, object_id):
    """Direct parents/consumers in document order, using the existing contracts."""
    from .construction_state import feature_dependencies
    graph = {}
    for key, obj in document.objects.items():
        parents = set()
        if isinstance(obj, ConstructionFeature):
            try:
                dependencies = feature_dependencies(document.objects, obj)
                parents.update(dependencies.object_ids)
                parents.update(slot.target_id for slot in dependencies.references)
                parents.update(dependencies.explicit_producers)
                if dependencies.predecessor and dependencies.predecessor.producer_id:
                    parents.add(dependencies.predecessor.producer_id)
                parents.discard(obj.body_id)
            except (ValueError, KeyError):
                parents.update(source.sketch_id for source, _ in obj.profile_sections)
        elif isinstance(obj, SketchSourceObject):
            parents.update(obj.document_dependencies)
            parents.update(r.producer_id for r in obj.producer_refs if r.producer_id)
        elif isinstance(obj, DatumPlaneObject):
            parents.update(r.object_id for r in obj.geometry_references if r.object_id)
            parents.update(r.producer_id for r in obj.geometry_references if r.producer_id)
        elif isinstance(obj, BodyObject):
            parents.update(obj.features[-1:])
        graph[key] = parents - {key}
    return (tuple(key for key in document.objects if key in graph.get(object_id, ())),
            tuple(key for key in document.objects if object_id in graph[key]))


def error_sources(document, object_id):
    from .body_history import result_at
    obj = document.objects.get(object_id)
    features = obj.features if isinstance(obj, BodyObject) else (obj.id,) if isinstance(obj, ConstructionFeature) else ()
    roots = set()
    for key in features:
        feature = document.objects[key]
        state = result_at(document, BodyStateRef(feature.body_id, key))
        for diagnostic in state.diagnostics:
            roots.update(diagnostic.root_ids or (diagnostic.feature_id,))
    return tuple(key for key in document.objects if key in roots)
