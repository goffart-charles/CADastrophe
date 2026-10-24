"""Read-only plugin service consumed through ApplicationServices.service()."""
from dataclasses import replace
from .objects import SketchObject,support_status
from .profiles import analyze,Diagnostic
from .profile_native import to_wires


class SketchProfiles:
    def __init__(self):
        self.closed = False

    def _object(self,document,sketch_id):
        if self.closed:
            raise RuntimeError("Le service de profils est fermé.")
        obj = document.objects.get(sketch_id)
        if not isinstance(obj,SketchObject):
            raise ValueError("Sélectionnez une esquisse de ce document.")
        return obj

    def analyze(self,document,sketch_id,*,support_shapes=None,**options):
        obj = self._object(document,sketch_id)
        error = ""
        from .construction_context import resolve_object
        try:
            obj = resolve_object(obj, document, support_shapes)
        except ValueError as exc:
            error = str(exc)
        report = analyze(obj.sketch,**options)
        from .native_regions import extended
        if extended(obj.sketch) and all(d.code == "native_curve" for d in report.diagnostics):
            from .native_regions import report as native_report
            report = native_report(obj.sketch, **options)
        from .construction_context import support_objects
        try:
            objects = support_objects(obj, document, support_shapes)
            error = error or support_status(obj,objects)
        except ValueError as exc:
            objects, error = document.objects, str(exc)
        if not error and obj.sketch.external:
            from .external import status
            error = status(obj.sketch,objects)
        if error:
            report = replace(report,diagnostics=(*report.diagnostics,Diagnostic("unresolved_reference",error)))
        return report

    def to_native(self,document,sketch_id,*,analysis=None,tolerance=1e-7):
        obj,fresh = self._fresh(document,sketch_id,analysis,tolerance)
        from .native_regions import extended, wires
        if extended(obj.sketch):
            return wires(obj.sketch, fresh)
        return to_wires(obj.sketch,fresh)

    def analyze_regions(self,document,sketch_id,*,support_shapes=None,**options):
        report = self.analyze(document, sketch_id, support_shapes=support_shapes, **options)
        from .native_regions import extended
        if extended(report.sketch):
            return report
        from .profile_regions import analyze_regions
        return analyze_regions(report.sketch, report=report, **options)

    def to_region_faces(self,document,sketch_id,*,analysis=None,tolerance=1e-7,contour_ids=(),support_shapes=None):
        # Keep the established normal-profile path and its error/staleness checks.
        report = self.analyze_regions(document, sketch_id, support_shapes=support_shapes,
                                     tolerance=analysis.tolerance if analysis is not None else tolerance)
        if analysis is not None and analysis.sketch != report.sketch:
            from .profile_native import ProfileError
            raise ProfileError("Analyse périmée : vérifiez à nouveau l’esquisse.", analysis)
        _check_pattern_selection(report, contour_ids)
        from .native_regions import extended, faces
        if extended(report.sketch):
            return faces(report.sketch, report, contour_ids)
        if report.profile_sketch is None:
            return self.to_faces(document, sketch_id, analysis=analysis, tolerance=tolerance,
                                 contour_ids=contour_ids, support_shapes=support_shapes)
        from .profile_faces import to_faces
        return to_faces(report.sketch, report, contour_ids=contour_ids)

    def to_faces(self,document,sketch_id,*,analysis=None,tolerance=1e-7,contour_ids=(),support_shapes=None):
        obj,fresh = self._fresh(document,sketch_id,analysis,tolerance,support_shapes=support_shapes)
        _check_pattern_selection(fresh, contour_ids)
        from .native_regions import extended, faces
        if extended(obj.sketch):
            return faces(obj.sketch, fresh, contour_ids)
        from .profile_faces import to_faces
        return to_faces(obj.sketch,fresh,contour_ids=contour_ids)

    def _fresh(self,document,sketch_id,analysis,tolerance,*,support_shapes=None):
        obj = self._object(document,sketch_id)
        fresh = self.analyze(document,sketch_id,tolerance=analysis.tolerance if analysis is not None else tolerance,
                             support_shapes=support_shapes)
        if analysis is not None and analysis.sketch != fresh.sketch:
            from .profile_native import ProfileError
            raise ProfileError("Analyse périmée : vérifiez à nouveau l’esquisse.",analysis)
        return replace(obj, sketch=fresh.sketch),fresh

    def source_edges(self, document, sketch_id):
        from .construction_context import resolve_object
        from .presentation import entity_shape
        from .model import Point
        obj = resolve_object(self._object(document, sketch_id), document)
        return tuple((e.id, entity_shape(obj.sketch, e)) for e in obj.sketch.profile_entities if type(e) is not Point)

    def migrate_references(self, document):
        from .construction_context import freeze_legacy_references
        return freeze_legacy_references(document)

    def path_edges(self, document, sketch_id, entity_ids):
        """Resolve explicit curve UUIDs, including construction curves, afresh."""
        from .construction_context import resolve_object
        from .presentation import entity_shape
        from .model import Point, Arc
        obj = resolve_object(self._object(document, sketch_id), document)
        entities = {e.id: e for e in obj.sketch.all_entities}
        result = []
        for key in entity_ids:
            entity = entities.get(key)
            if entity is None or type(entity) is Point:
                raise ValueError("Courbe du parcours absente ; sélectionnez-la à nouveau.")
            edge = entity_shape(obj.sketch, entity)
            if type(entity) is Arc and entity.sweep < 0:
                from OCC.Core.TopoDS import topods
                edge = topods.Edge(edge.Reversed())
            result.append(edge)
        return tuple(result)

    def position_points(self, document, sketch_id, entity_ids=()):
        """Resolve point identities and placement after constraints and motifs."""
        from .construction_context import resolve_object
        obj = resolve_object(self._object(document, sketch_id), document)
        error = support_status(obj, document)
        if error:
            raise ValueError(error)
        points = {e.id: e for e in obj.sketch.all_entities if e.kind == "point"}
        keys = entity_ids or tuple(points)
        if not keys or len(keys) > 512:
            raise ValueError("L’esquisse doit fournir de 1 à 512 points de positionnement.")
        if any(key not in points for key in keys):
            raise ValueError("Point de positionnement absent ; réparez sa référence.")
        return obj.sketch.support.frame, tuple((key, obj.sketch.to_world(points[key].position)) for key in keys)

    def close(self):
        self.closed = True

    def selectable_contours(self, document, sketch_id):
        report = self.analyze_regions(document, sketch_id)
        return tuple(contour.id for contour in report.contours) if report.valid else ()

    def reference_status(self, document, sketch_id, path):
        from cadastrophe.core.reference_geometry import state_for, resolve_plane, resolve_topology
        from cadastrophe.core.feature_model import BodyStateRef
        from cadastrophe.core.native_naming import resolve
        from .external import project
        obj = self._object(document, sketch_id)
        if path == ("sketch", "support"):
            support = obj.sketch.support
            target = document.objects.get(support.plane_id)
            if support.reference is not None:
                state = state_for(document, BodyStateRef(support.plane_id, support.producer_id))
                from .supports import face_frame
                face_frame(resolve_topology(document, BodyStateRef(support.plane_id, support.producer_id), support.reference, state=state), "Support", natural=False)
            elif support.face_index is None and target is not None and target.plane is not None:
                resolve_plane(document, target)
            else:
                error = support_status(obj, document)
                if error:
                    raise ValueError(error)
            return
        item = obj.sketch.external[path[-1]]
        if item.reference is None:
            raise ValueError("Ancienne projection sans nom persistant : choisissez une nouvelle référence.")
        state = state_for(document, BodyStateRef(item.body_id, item.producer_id))
        geometry = project(resolve_topology(document, BodyStateRef(item.body_id, item.producer_id), item.reference, state=state), item.topology, obj.sketch.support.frame)
        if type(geometry) is not type(item.geometry):
            raise ValueError("La projection a changé de type géométrique.")

    def replace_reference(self, document, sketch_id, path, reference, *, preserve_frame=True):
        from cadastrophe.core.operation_model import GeometryReference
        from cadastrophe.core.reference_geometry import state_for, resolve_plane, resolve_topology
        from cadastrophe.core.feature_model import BodyStateRef
        from cadastrophe.core.native_naming import resolve
        from cadastrophe.core.feature_objects import ConstructionFeature
        from .supports import from_face_state
        from .model import PlaneSupport
        from .support_repair import reassociation_objects
        obj = self._object(document, sketch_id)
        if not isinstance(reference, GeometryReference):
            raise ValueError("Choisissez une référence persistante.")
        if path == ("sketch", "support"):
            if reference.kind == "plane":
                target = document.objects[reference.object_id]
                frame = resolve_plane(document, target)
                support = PlaneSupport(target.id, frame, target.revision, attachment=frame)
            elif reference.kind == "face":
                state = state_for(document, BodyStateRef(reference.object_id, reference.producer_id))
                face = resolve_topology(document, BodyStateRef(reference.object_id, reference.producer_id), reference.topology, state=state)
                support = from_face_state(document, reference.object_id, face, reference.producer_id)
            else:
                raise ValueError("Le support doit être un plan ou une face plane.")
            return reassociation_objects(document, sketch_id, support, preserve_frame=preserve_frame, validate=False)
        old = obj.sketch.external[path[-1]]
        expected = {"Edge": "edge", "Vertex": "vertex"}[old.topology]
        if reference.kind != expected:
            raise ValueError("La projection doit conserver son type de référence.")
        state = state_for(document, BodyStateRef(reference.object_id, reference.producer_id))
        shape = resolve_topology(document, BodyStateRef(reference.object_id, reference.producer_id), reference.topology, state=state)
        from .external import project, shape_map
        geometry = project(shape, old.topology, obj.sketch.support.frame)
        if type(geometry) is not type(old.geometry):
            raise ValueError("La nouvelle projection doit conserver le type géométrique utilisé par les contraintes.")
        geometry = replace(geometry, id=old.geometry.id)
        fresh_item = replace(old, body_id=reference.object_id, producer_id=reference.producer_id,
                             reference=reference.topology, revision=document.objects[reference.object_id].revision,
                             index=shape_map(state.shape, old.topology).FindIndex(shape), geometry=geometry)
        fresh = replace(obj, sketch=replace(obj.sketch, external=tuple(fresh_item if i == path[-1] else e
                                                                    for i, e in enumerate(obj.sketch.external))))
        if fresh.sketch.constraints:
            from .solver import solve
            solved = solve(fresh.sketch)
            if not solved.success:
                raise ValueError("La nouvelle projection est incompatible avec les relations : " + solved.message)
            fresh = replace(fresh, sketch=solved.sketch)
        updates = [fresh]
        for feature in document.objects.values():
            for source, _ in getattr(feature, "profile_sections", ()):
                if isinstance(feature, ConstructionFeature) and source.sketch_id == obj.id:
                    refs = {r.body_id: r for r in source.supports}
                    refs.update({r.body_id: r for r in fresh.producer_refs})
                    supports = tuple(refs[key] for key in fresh.body_dependencies if key in refs)
                    updates.append(feature.with_profile_supports(obj.id, supports))
                    break
        return tuple(updates)


def _check_pattern_selection(report, contour_ids):
    if (report.valid and report.sketch.patterns and type(contour_ids) in (tuple, list)
            and all(type(key) is str for key in contour_ids)
            and set(contour_ids)-{c.id for c in report.contours}):
        from .profile_native import ProfileError
        raise ProfileError("Occurrence ou région de motif absente : vérifiez le nombre d’occurrences et les sources, puis réparez la sélection du contour.", report)
