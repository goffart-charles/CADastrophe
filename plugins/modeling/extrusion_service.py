"""Read-only X3 bridge from the public X2 provider to the native CAD engine."""
from dataclasses import replace

from cadastrophe.core.extrusion import extrude_face, failure, validate_options
from cadastrophe.core.boss import fuse_boss
from cadastrophe.core.feature_objects import BodyObject
from cadastrophe.core.feature_model import Operation, EvaluationResult, ResultStatus
from cadastrophe.core.body_history import body_result
from cadastrophe.document import ObjectType, SceneObject


class Extrusions:
    def __init__(self, profiles):
        self._profiles = profiles
        self.closed = False

    def region_faces(self, *args, **kwargs):
        if not kwargs.get("contour_ids"):
            # Existing recipes with no explicit selection retain E7 hole parity.
            # The interactive picker requests every cell through to_region_faces.
            try:
                return self._profiles.to_faces(*args, **kwargs)
            except ValueError:
                pass  # Networks need the arrangement provider below.
        provider = getattr(self._profiles, "to_region_faces", self._profiles.to_faces)
        return provider(*args, **kwargs)

    def build(self, document, sketch_id, depth, *, reversed=False, contour_ids=(), tolerance=1e-7, support_shapes=None, trace=None):
        if self.closed:
            raise RuntimeError("Le service d'extrusion est fermé.")
        error = validate_options(depth, reversed, tolerance)
        if error is not None:
            return error
        snapshots = dict(document.objects)
        try:
            options = {} if support_shapes is None else {"support_shapes": support_shapes}
            profiles = self.region_faces(document, sketch_id, contour_ids=contour_ids, tolerance=tolerance, **options)
        except (ValueError, RuntimeError) as exc:
            return failure("profile_error", str(exc))
        if len(profiles.regions) != 1:
            return failure("multiple_regions", "Le profil contient plusieurs régions : sélectionnez une seule région pour ce corps.",
                           (region.id for region in profiles.regions))
        region = profiles.regions[0]
        result = extrude_face(region.face, depth, reversed=reversed, tolerance=profiles.tolerance, trace=trace)
        if set(document.objects) != set(snapshots) or any(document.objects[key] is not obj for key, obj in snapshots.items()):
            return failure("stale_input", "Le document a changé pendant le calcul ; l'extrusion est abandonnée.")
        if not result.success:
            result = replace(result, diagnostics=tuple(replace(diagnostic, region_ids=(region.id,))
                                                      for diagnostic in result.diagnostics))
        return result

    def build_boss(self, document, sketch_id, target_id, depth, **options):
        """Read-only preview against one explicitly selected current body."""
        if self.closed:
            raise RuntimeError("Le service d'extrusion est fermé.")
        target = document.objects.get(target_id)
        if target is None or target.kind is not ObjectType.BODY or target.shape is None:
            return failure("missing_target", "Sélectionnez un corps cible valide pour la fusion.")
        if type(target) is not SceneObject and not isinstance(target, BodyObject):
            return failure("unsupported_target", "Ce type de corps appartient à un autre plugin et ne peut pas être adopté.")
        if isinstance(target, BodyObject) and body_result(document, target_id).status is not ResultStatus.READY:
            return failure("dirty_target", "Le corps cible doit être recalculé avant la fusion.")
        snapshots = dict(document.objects)
        if len(options.get("contour_ids", ())) > 1:
            from .extrusion_plan import prisms, combine
            try:
                result = combine(target.shape, prisms(self, document, sketch_id, depth, **options))
            except (ValueError, RuntimeError) as exc:
                return failure("profile_error", str(exc))
        else:
            prism = self.build(document, sketch_id, depth, **options)
            result = self._combine(target.shape, prism)
        if set(document.objects) != set(snapshots) or any(document.objects[key] is not obj for key, obj in snapshots.items()):
            return failure("stale_input", "Le document a changé pendant la fusion ; résultat abandonné.")
        return result

    def plan(self, document, sketch_id, depth, **options):
        from .extrusion_plan import plan
        return plan(self, document, sketch_id, depth, **options)

    def build_cut(self, document, sketch_id, target_id, depth, **options):
        from .cut_service import build_cut
        if self.closed:
            raise RuntimeError("Le service d’extrusion est fermé.")
        return build_cut(self, document, sketch_id, target_id, depth, **options)

    def cut_plan(self, document, sketch_id, depth, **options):
        from .cut_service import cut_plan
        if self.closed:
            raise RuntimeError("Le service d’extrusion est fermé.")
        return cut_plan(self, document, sketch_id, depth, **options)

    @staticmethod
    def _combine(previous, prism):
        if not prism.success:
            return prism
        from OCC.Core.TopAbs import TopAbs_COMPOUND
        return fuse_boss(previous, prism.shape, tolerance=prism.tolerance,
                         allow_disconnected=previous.ShapeType() == TopAbs_COMPOUND)

    def evaluator(self, document):
        """Adapter for ordered, incremental evaluation and file restoration."""
        return _Evaluator(self, document)

    def close(self):
        self.closed = True
        self._profiles = None


class _Evaluator:
    def __init__(self, extrusions, document):
        self.extrusions, self.document = extrusions, document
        self.cache_key = extrusions
        self.previous = None
        self.supports = {}

    def prepare_feature(self, feature, previous, supports):
        self.previous, self.supports = previous, supports

    @staticmethod
    def accept_cached_result(result):
        # Document receipts already certify validity and exact semantic inputs.
        # Do not reuse test-only symbolic shapes with the native evaluator.
        from OCC.Core.TopoDS import TopoDS_Shape
        from OCC.Core.TopAbs import TopAbs_SOLID, TopAbs_COMPOUND
        return (isinstance(result.shape, TopoDS_Shape) and not result.shape.IsNull()
                and result.shape.ShapeType() in (TopAbs_SOLID, TopAbs_COMPOUND))

    def evaluate(self, feature, sketch, previous_shape, supports):
        try:
            return self._evaluate(feature,sketch,previous_shape,supports)
        except (ValueError,RuntimeError,TypeError,ArithmeticError) as exc:
            return self._failure(feature, exc)

    def evaluate_steps(self, feature, sketch, previous_shape, supports):
        from cadastrophe.core.feature_objects import SweepFeature, ReplayFeature
        if not isinstance(feature, (SweepFeature, ReplayFeature)):
            return self.evaluate(feature, sketch, previous_shape, supports)
        from .operation_replay import generating_volumes_steps, replay_steps
        from cadastrophe.core.operation_effects import apply_effects
        try:
            context, trace = self._context(feature, sketch, supports)
            if isinstance(feature, ReplayFeature):
                shape, effects = yield from replay_steps(self.extrusions, context, feature, previous_shape, trace)
            else:
                effects = yield from generating_volumes_steps(self.extrusions, context, feature, previous_shape, trace)
                yield 0, 1, "Application du balayage au corps"
                shape = apply_effects(previous_shape, effects, trace=trace)
            return EvaluationResult(ResultStatus.READY, shape, topology=trace.topology, effects=effects)
        except (ValueError,RuntimeError,TypeError,ArithmeticError) as exc:
            return self._failure(feature, exc)

    def _failure(self, feature, exc):
        from cadastrophe.core.feature_model import EvaluationDiagnostic
        from cadastrophe.core.reference_repair import reference_entries, entry_issue
        from cadastrophe.core.construction_state import feature_dependencies
        states = dict(self.supports)
        predecessor = feature_dependencies(self.document.objects, feature).predecessor
        if predecessor is not None and self.previous is not None:
            states[predecessor] = self.previous
        context = _CalculationDocument(self.document, states)
        broken = None
        try:
            entries = sorted(reference_entries(context, feature.id), key=lambda e: e.role == "sketch")
            broken = next((entry.slot for entry in entries
                           if entry_issue(context, entry, self.extrusions._profiles)), None)
        except (ValueError, RuntimeError, KeyError):
            pass  # Keep the original geometry error if no exact slot can be established.
        from cadastrophe.core.feature_model import InactiveReferenceError
        inactive = isinstance(exc, InactiveReferenceError)
        return EvaluationResult(ResultStatus.BLOCKED if inactive else ResultStatus.ERROR, message=str(exc), feature_id=feature.id,
            diagnostics=(EvaluationDiagnostic("inactive_reference" if inactive else "reference_invalid" if broken else "calculation_failed",
                                              str(exc), feature.id, exc.root_ids if inactive else (feature.id,), broken),))

    def _context(self, feature, sketch, supports):
        from cadastrophe.core.native_naming import NamingTrace
        from cadastrophe.core.body_history import result_at
        states = dict(self.supports)
        for ref,shape in supports.items():
            if ref not in states:
                state = result_at(self.document,ref)
                states[ref] = state if state.shape is not None and state.shape.IsSame(shape) else EvaluationResult(ResultStatus.READY,shape)
        context = _CalculationDocument(self.document, states)
        sources = ()
        if sketch is not None:
            provider = getattr(self.extrusions._profiles, "source_edges", None)
            if callable(provider):
                sources = provider(context, sketch.id)
        trace = NamingTrace(feature.id, self.previous, sources)
        return context, trace

    def _evaluate(self, feature, sketch, previous_shape, supports):
        from cadastrophe.core.feature_objects import ReplayFeature
        from cadastrophe.core.operation_effects import apply_effects
        from .operation_replay import generating_volumes, replay
        context, trace = self._context(feature, sketch, supports)
        if feature.operation is Operation.DRAFT:
            from .draft_geometry import apply_draft
            from cadastrophe.core.reference_geometry import resolve_reference, resolve_axis_reference
            shape = apply_draft(previous_shape, feature.parameters,
                                resolve_reference(context, feature.parameters.neutral),
                                resolve_axis_reference(context, feature.parameters.direction)[1],
                                topology=None if self.previous is None else self.previous.topology, trace=trace)
            return EvaluationResult(ResultStatus.READY, shape, topology=trace.topology)
        if feature.operation is Operation.SHELL:
            from .shell_geometry import apply_shell
            shape = apply_shell(previous_shape, feature.parameters,
                                topology=None if self.previous is None else self.previous.topology, trace=trace)
            return EvaluationResult(ResultStatus.READY, shape, topology=trace.topology)
        if feature.operation in (Operation.FILLET, Operation.CHAMFER):
            from .edge_geometry import apply_edges
            shape = apply_edges(previous_shape, feature.operation, feature.parameters,
                                topology=None if self.previous is None else self.previous.topology, trace=trace)
            return EvaluationResult(ResultStatus.READY, shape, topology=trace.topology)
        if isinstance(feature, ReplayFeature):
            shape, effects = replay(self.extrusions, context, feature, previous_shape, trace)
            return EvaluationResult(ResultStatus.READY, shape, topology=trace.topology, effects=effects)
        effects = generating_volumes(self.extrusions, context, feature, previous_shape, trace)
        shape = apply_effects(previous_shape, effects, trace=trace)
        return EvaluationResult(ResultStatus.READY, shape, topology=trace.topology, effects=effects)


class _CalculationDocument:
    """Read-only view of the same recipe graph with this evaluation's inputs."""
    def __init__(self, document, states):
        self._document = document
        self.rebuild_states = states

    def __getattr__(self, name):
        return getattr(self._document, name)
