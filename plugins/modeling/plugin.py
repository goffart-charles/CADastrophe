"""Construction recipes, extrusion tools and owned X7 reconstruction."""
from cadastrophe.core.body_history import BodyHistory
from .codec import BODY_CODEC, FEATURE_CODEC, EDGE_CODEC
from .extrusion_service import Extrusions
from .extrusion_tool import ExtrusionSession, CutSession
from .restoration import restore_extrusions_steps
from .presentation import ExtrusionPresentation
from .rebuild import Rebuilder
from .feature_edit import FeatureEditSession
from cadastrophe.core.feature_objects import ExtrusionFeature, ConstructionFeature, EdgeFeature
from cadastrophe.core.feature_model import Operation
from .edge_tool import EdgeSession
from .operation_codec import PLANE_CODEC, REVOLUTION_CODEC, REPLAY_CODEC, SHELL_CODEC, DRAFT_CODEC, SWEEP_CODEC, LOFT_CODEC, HOLE_CODEC, THREAD_CODEC
from .hole_tool import HoleSession
from .thread_tool import ThreadSession
from cadastrophe.core.feature_objects import ThreadFeature
from cadastrophe.core.feature_objects import HoleFeature
from .shell_tool import ShellSession
from .draft_tool import DraftSession
from .plane_tool import PlaneSession
from .revolution_tool import RevolutionSession
from .sweep_tool import SweepSession
from .loft_tool import LoftSession
from .replay_tool import ReplaySession
from cadastrophe.core.feature_objects import RevolutionFeature, ReplayFeature, ShellFeature, DraftFeature, SweepFeature, LoftFeature
from cadastrophe.core.reference_objects import DatumPlaneObject


class ModelingPlugin:
    def activate(self, context):
        self.history = BodyHistory()
        self.extrusions = Extrusions(context.services.service("cadastrophe.sketch.profiles"))
        context.register_type(BODY_CODEC)
        context.register_type(FEATURE_CODEC)
        context.register_type(EDGE_CODEC)
        for codec in (PLANE_CODEC, REVOLUTION_CODEC, REPLAY_CODEC, SHELL_CODEC, DRAFT_CODEC, SWEEP_CODEC, LOFT_CODEC, HOLE_CODEC, THREAD_CODEC):
            context.register_type(codec)
        context.register_service("history", self.history)
        context.register_service("extrusions", self.extrusions)
        profiles = context.services.service("cadastrophe.sketch.profiles")
        context.register_document_restorer("initial_extrusions",
            lambda document: restore_extrusions_steps(document, self.extrusions))
        def extrude():
            factory = lambda: ExtrusionSession(context.services, self.extrusions, profiles)
            if context.services.editing:
                return context.start_tool_from_sketch(factory)
            return context.start_tool(factory())
        context.register_action("extrude", "Ajout par extrusion",
            extrude, enabled_when=lambda services: services.can_start_tool or services.can_start_from_sketch,
            icon="extrude", from_sketch=True)
        if not context.bind_action_slot("modeling.extrude", "extrude"):
            context.register_ribbon("extrusion", "Fonctions", "Bossages et bases", ("extrude",))
        def cut():
            factory = lambda: CutSession(context.services, self.extrusions, profiles)
            if context.services.editing:
                return context.start_tool_from_sketch(factory)
            return context.start_tool(factory())
        context.register_action("cut_extrude", "Découpe extrudée", cut,
            enabled_when=lambda services: services.can_start_tool or services.can_start_from_sketch,
            icon="cut-extrude", from_sketch=True)
        if not context.bind_action_slot("modeling.cut_extrude", "cut_extrude"):
            context.register_ribbon("cut_extrusion", "Fonctions", "Enlèvements de matière", ("cut_extrude",))
        def selected_feature(services):
            return (services.active_document.objects.get(services.selected_ids[0])
                    if services.active_document is not None and len(services.selected_ids) == 1 else None)
        from cadastrophe.core.feature_activation import set_enabled
        for key, label, after in (("history_before", "Revenir avant cette fonction", False),
                                  ("history_after", "Revenir après cette fonction", True)):
            context.register_action(key, label,
                lambda value=after: context.services.active_document.history_context.set_position(
                    selected_feature(context.services).id, after=value),
                enabled_when=lambda s: s.can_start_tool and isinstance(selected_feature(s), ConstructionFeature), icon="tree")
        context.register_action("history_end", "Revenir à la fin",
            lambda: context.services.active_document.history_context.set_position(),
            enabled_when=lambda s: s.can_start_tool and s.active_document.history_context.position is not None, icon="tree")
        def activation_available(services, enabled):
            doc = services.active_document
            selected = () if doc is None else tuple(doc.objects.get(key) for key in services.selected_ids)
            return (services.can_start_tool and bool(selected)
                    and all(isinstance(obj, ConstructionFeature) for obj in selected)
                    and any(obj.enabled != enabled for obj in selected))
        for key, label, enabled in (("disable_features", "Désactiver", False), ("enable_features", "Réactiver", True)):
            context.register_action(key, label,
                lambda active=enabled: set_enabled(context.services.active_document, context.services.selected_ids,
                                                  active, self.extrusions.evaluator),
                enabled_when=lambda services, active=enabled: activation_available(services, active), icon="properties")
        from cadastrophe.core.reference_repair import related_objects, error_sources
        from cadastrophe.core.feature_objects import SketchSourceObject, BodyObject
        from .reference_repair_tool import ReferenceRepairSession
        def inspectable(services):
            return services.can_start_tool and isinstance(selected_feature(services),
                (ConstructionFeature, BodyObject, SketchSourceObject, DatumPlaneObject))
        context.register_action("repair_references", "Réparer les références",
            lambda: context.start_tool(ReferenceRepairSession(context.services, self.extrusions, profiles, selected_feature(context.services))),
            enabled_when=inspectable, icon="reference-geometry")
        def select_related(kind):
            doc = context.services.active_document
            selected = selected_feature(context.services)
            if selected is None:
                return
            keys = error_sources(doc, selected.id) if kind == "errors" else related_objects(doc, selected.id)[kind]
            doc.select_objects(keys)
        for key, label, kind in (("select_parents", "Sélectionner les parents", 0),
                                 ("select_consumers", "Sélectionner les consommateurs", 1),
                                 ("select_error_sources", "Sélectionner les causes initiales", "errors")):
            context.register_action(key, label, lambda value=kind: select_related(value),
                                    enabled_when=inspectable, icon="tree")
        def editor_for(feature):
            if isinstance(feature, ThreadFeature):
                return ThreadSession(context.services, self.extrusions, feature)
            if isinstance(feature, HoleFeature):
                return HoleSession(context.services, self.extrusions, profiles, feature)
            if isinstance(feature, LoftFeature):
                return LoftSession(context.services, self.extrusions, profiles, feature=feature)
            if isinstance(feature, SweepFeature):
                return SweepSession(context.services, self.extrusions, profiles, feature=feature)
            if isinstance(feature, DraftFeature):
                return DraftSession(context.services, self.extrusions, feature)
            if isinstance(feature, ShellFeature):
                return ShellSession(context.services, self.extrusions, feature)
            if isinstance(feature, EdgeFeature):
                return EdgeSession(context.services,self.extrusions,feature.operation,feature)
            if isinstance(feature, RevolutionFeature):
                return RevolutionSession(context.services,self.extrusions,profiles,feature=feature)
            if isinstance(feature, ReplayFeature):
                return ReplaySession(context.services,self.extrusions,feature.operation,feature)
            return FeatureEditSession(context.services,self.extrusions,feature)
        context.register_action("edit_feature", "Modifier la fonction",
            lambda: context.start_tool(editor_for(selected_feature(context.services))),
            enabled_when=lambda services: services.can_start_tool and isinstance(selected_feature(services), ConstructionFeature)
                                          and selected_feature(services).enabled
                                          and selected_feature(services).id not in services.active_document.history_context.display()[1],
            icon="properties")
        context.register_presentation(ExtrusionFeature, ExtrusionPresentation,
                                      label="Extrusion (bossage ou enlèvement)", icon="extrude", edit_action="edit_feature")
        context.register_presentation(HoleFeature, ExtrusionPresentation, label="Perçage guidé", icon="hole-wizard", edit_action="edit_feature")
        def start_hole():
            factory = lambda: HoleSession(context.services, self.extrusions, profiles)
            return context.start_tool_from_sketch(factory) if context.services.editing else context.start_tool(factory())
        context.register_action("hole_wizard", "Perçage guidé", start_hole, icon="hole-wizard", from_sketch=True,
                                enabled_when=lambda s: s.can_start_tool or s.can_start_from_sketch)
        context.bind_action_slot("modeling.hole_wizard", "hole_wizard")
        context.register_presentation(ThreadFeature, ExtrusionPresentation, label="Filetage / taraudage", icon="sweep", edit_action="edit_feature")
        context.register_action("thread", "Filetage / taraudage", lambda: context.start_tool(ThreadSession(context.services, self.extrusions)),
                                enabled_when=lambda s: s.can_start_tool, icon="sweep")
        context.bind_action_slot("modeling.thread", "thread")
        context.register_presentation(EdgeFeature, ExtrusionPresentation, label="Congé ou chanfrein", icon="fillet", edit_action="edit_feature")
        context.register_presentation(RevolutionFeature,ExtrusionPresentation,label="Révolution",icon="revolve",edit_action="edit_feature")
        context.register_presentation(SweepFeature, ExtrusionPresentation, label="Balayage", icon="sweep", edit_action="edit_feature")
        context.register_presentation(LoftFeature, ExtrusionPresentation, label="Lissage", icon="loft", edit_action="edit_feature")
        context.register_presentation(ReplayFeature,ExtrusionPresentation,label="Symétrie ou répétition",icon="linear-pattern",edit_action="edit_feature")
        context.register_presentation(ShellFeature, ExtrusionPresentation, label="Coque", icon="shell", edit_action="edit_feature")
        context.register_action("shell", "Coque", lambda: context.start_tool(ShellSession(context.services, self.extrusions)),
                                enabled_when=lambda s: s.can_start_tool, icon="shell")
        context.bind_action_slot("modeling.shell", "shell")
        context.register_presentation(DraftFeature, ExtrusionPresentation, label="Dépouille", icon="draft", edit_action="edit_feature")
        context.register_action("draft", "Dépouille", lambda: context.start_tool(DraftSession(context.services, self.extrusions)),
                                enabled_when=lambda s: s.can_start_tool, icon="draft")
        context.bind_action_slot("modeling.draft", "draft")
        context.register_action("edit_plane","Modifier le plan",lambda: context.start_tool(PlaneSession(context.services,self.extrusions,selected_feature(context.services))),
            enabled_when=lambda s:s.can_start_tool and isinstance(selected_feature(s),DatumPlaneObject),icon="plane")
        context.register_presentation(DatumPlaneObject,ExtrusionPresentation,label="Plan paramétrique",icon="plane",edit_action="edit_plane")
        context.register_action("reference_plane","Plan de référence",lambda: context.start_tool(PlaneSession(context.services,self.extrusions)),
            enabled_when=lambda s:s.can_start_tool,icon="plane")
        context.bind_action_slot("modeling.reference_geometry","reference_plane")
        for key,label,is_cut in (("revolve","Révolution additive",False),("cut_revolve","Révolution soustractive",True)):
            def start_revolution(cut=is_cut):
                factory = lambda: RevolutionSession(context.services,self.extrusions,profiles,cut=cut)
                return context.start_tool_from_sketch(factory) if context.services.editing else context.start_tool(factory())
            context.register_action(key,label,start_revolution,icon="cut-revolve" if is_cut else "revolve",from_sketch=True,
                enabled_when=lambda s:s.can_start_tool or s.can_start_from_sketch)
            context.bind_action_slot("modeling."+key,key)
        for key, label, is_cut in (("sweep", "Balayage additif", False), ("cut_sweep", "Balayage soustractif", True)):
            def start_sweep(cut=is_cut):
                factory = lambda: SweepSession(context.services, self.extrusions, profiles, cut=cut)
                return context.start_tool_from_sketch(factory) if context.services.editing else context.start_tool(factory())
            context.register_action(key, label, start_sweep, icon="cut-sweep" if is_cut else "sweep", from_sketch=True,
                                    enabled_when=lambda s: s.can_start_tool or s.can_start_from_sketch)
            context.bind_action_slot("modeling."+key, key)
        for key, label, is_cut in (("loft", "Lissage additif", False), ("cut_loft", "Lissage soustractif", True)):
            def start_loft(cut=is_cut):
                factory = lambda: LoftSession(context.services, self.extrusions, profiles, cut=cut)
                return context.start_tool_from_sketch(factory) if context.services.editing else context.start_tool(factory())
            context.register_action(key, label, start_loft, icon="cut-loft" if is_cut else "loft", from_sketch=True,
                                    enabled_when=lambda s: s.can_start_tool or s.can_start_from_sketch)
            context.bind_action_slot("modeling."+key, key)
        for key,label,operation in (("mirror","Symétrie",Operation.MIRROR),("linear_pattern","Répétition linéaire",Operation.LINEAR_PATTERN),
                                    ("circular_pattern","Répétition circulaire",Operation.CIRCULAR_PATTERN)):
            context.register_action(key,label,lambda op=operation:context.start_tool(ReplaySession(context.services,self.extrusions,op)),
                enabled_when=lambda s:s.can_start_tool,icon=key.replace("_","-"))
            context.bind_action_slot("modeling."+key,key)
        for name, label, operation in (("fillet", "Congé", Operation.FILLET), ("chamfer", "Chanfrein", Operation.CHAMFER)):
            context.register_action(name, label, lambda op=operation: context.start_tool(EdgeSession(context.services, self.extrusions, op)),
                                    enabled_when=lambda services: services.can_start_tool, icon=name)
            context.bind_action_slot("modeling."+name, name)
        self.rebuilder = Rebuilder(context.services, self.extrusions)
        context.register_action("rebuild", "Recalculer", self.rebuilder.retry, shortcuts=("F5",),
                                enabled_when=lambda services: services.editable, icon="reset")
        context.connect(context.services.state_changed, self.rebuilder.sync)
        self.rebuilder.sync()

    def deactivate(self):
        if hasattr(self, "rebuilder"):
            self.rebuilder.close()
        self.history.close()
        if hasattr(self, "extrusions"):
            self.extrusions.close()


def create_plugin():
    return ModelingPlugin()
