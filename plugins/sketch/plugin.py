"""Owned sketch model, native presentation and interactive drawing commands."""
from cadastrophe.plugin_api import ObjectType
from .codec import CODEC
from .commands import make_sketch
from .editor import SketchEditSession, TITLES
from .drawing import TOOLS
from .objects import SketchObject, support_status
from .presentation import SketchPresentation
from .ribbon import GROUPS, PLACEHOLDERS
from .supports import from_selection
from pathlib import Path
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu
from .profile_service import SketchProfiles


class SketchPlugin:
    def activate(self, context):
        self.context = context
        self.profiles = SketchProfiles()
        context.register_service("profiles",self.profiles)
        context.register_type(CODEC)
        def repair_support():
            from .support_tool import SupportRepairSession
            return context.start_tool(SupportRepairSession(context.services, self.profiles, self.selected(context.services)))
        context.register_action("reattach_support", "Réassocier le support", repair_support, icon="plane",
            enabled_when=lambda s: s.can_start_tool and isinstance(self.selected(s), SketchObject))
        for key, label, callback, predicate, icon_name in (
                ("new", "Nouvelle esquisse", self.create, self.can_create, "sketch"),
                ("edit", "Modifier l’esquisse", self.edit, self.can_edit, "sketch"),
                ("finish", "Quitter l’esquisse", context.finish_edit,
                 lambda services: services.edit_owner == context.owner, "check")):
            action = context.register_action(key, label, callback, requires_document=key != "finish",
                                             enabled_when=predicate, icon=icon_name)
            action.setToolTip({"new": "Sélectionner un plan de référence ou cliquer sur une face plane d’un solide, puis créer une esquisse.",
                               "edit": "Sélectionner une esquisse visible dans l’arbre pour l’éditer sur son plan.",
                               "finish": "Quitter l’édition en conservant la vue courante."}[key])
        self.tool_actions = {}
        for tool in TOOLS:
            action = context.register_action(tool, TITLES[tool], lambda key=tool: self.draw(key),
                                             requires_document=False, enabled_when=self.can_draw,
                                             icon="arc" if tool.startswith("arc_") else tool if not Path(__file__).with_name("icons").joinpath(tool+".svg").exists() else None)
            asset = Path(__file__).with_name("icons") / (tool+".svg")
            if asset.exists():
                action.setIcon(QIcon(str(asset)))
            action.setProperty("iconOnly", True)
            action.setProperty("sketchTool", True)
            action.setCheckable(True)
            action.setToolTip({"point": "Cliquer pour placer des points dans l’esquisse ; Échap quitte l’outil.",
                               "line": "Départ puis fins successives ; Échap quitte la chaîne.",
                               "construction_line": "Ligne discontinue de référence, exclue des profils. Départ puis fins successives ; Échap quitte la chaîne.",
                               "rectangle": "Appuyer au premier coin, glisser et relâcher ; quatre segments, une annulation.",
                               "circle": "Centre puis point du cercle.",
                               "arc": "Centre, départ, direction de fin ; sens antihoraire U/V.",
                               "arc_three": "Départ, fin, puis un point sur l’arc.",
                               "arc_tangent": "Partir de l’extrémité d’un segment ou arc sélectionné.",
                               "slot": "Deux centres puis la demi-largeur.", "polygon": "Centre puis sommet ; nombre de côtés dans le panneau.",
                               "ellipse": "Centre, extrémité du grand axe puis largeur.",
                               "spline": "Cliquer les pôles de contrôle ; Entrée termine la spline."}[tool])
            self.tool_actions[tool] = action
        self.line_menu = QMenu("Ligne")
        self.line_menu.addAction(self.tool_actions["line"])
        self.line_menu.addAction(self.tool_actions["construction_line"])
        self.tool_actions["line"].setProperty("commandMenu", self.line_menu)
        self.arc_menu = QMenu("Arc")
        center = self.arc_menu.addAction(self.tool_actions["arc"].icon(), "Par centre")
        center.triggered.connect(lambda: self.draw("arc"))
        self.arc_menu.addAction(self.tool_actions["arc_tangent"])
        self.arc_menu.addAction(self.tool_actions["arc_three"])
        self.tool_actions["arc"].setProperty("commandMenu", self.arc_menu)
        self.selection_action = context.register_action("select", "Sélectionner", lambda: self.draw(None),
                                                        requires_document=False, enabled_when=self.can_draw, icon="select")
        self.selection_action.setCheckable(True)
        self.selection_action.setToolTip("Sélection, Ctrl+clic, fenêtre, poignées et déplacements d’entités.")
        self.snap_action = context.register_action("snap", "Accrochages rapides", self.toggle_snap,
            requires_document=False, enabled_when=self.can_draw, icon="snap")
        self.snap_action.setCheckable(True)
        self.snap_action.setToolTip("Activer/désactiver les points d’accrochage pour cette édition. Réglages initiaux dans Préférences > Esquisse.")
        self.constraint_actions = {}
        captions = {"dimension": "Cotes\nau clic", "relations": "Contraintes",
                    "trim": "Rogner", "convert": "Projeter", "offset": "Décaler",
                    "mirror": "Symétriser", "move": "Déplacer", "linear_pattern": "Répétition linéaire"}
        for key, title, icon in PLACEHOLDERS:
            if key in self.tool_actions:
                continue
            asset = Path(__file__).with_name("icons") / f"{icon}.svg"
            from .reshape_panel import TITLES as RESHAPE_TOOLS
            implemented = key in {"dimension", "relations", "repair", "fillet", "chamfer"} or key in RESHAPE_TOOLS
            action = context.register_action(key, title if implemented else title + " · à venir",
                ((lambda mode=key: self.open_reshape(mode)) if key in RESHAPE_TOOLS else self.check_profiles if key == "repair" else (lambda mode=key: self.open_corner(mode)) if key in {"fillet", "chamfer"}
                 else (lambda dimension=key == "dimension": self.open_constraints(dimension))) if implemented else None,
                requires_document=False, enabled_when=self.can_draw if implemented else None,
                icon=None if asset.exists() else icon)
            if asset.exists():
                action.setIcon(QIcon(str(asset)))
            action.setProperty("iconOnly", key in {"fillet", "chamfer", "point", "text"})
            action.setProperty("sketchTool", key in {"fillet", "chamfer", "point", "text"})
            if implemented:
                if key not in {"repair", "fillet", "chamfer"} and key not in RESHAPE_TOOLS:
                    action.setCheckable(True)
                    self.constraint_actions[key] = action
            action.setIconText(captions.get(key, title))
            action.setToolTip(title + (" : détecter les ouvertures, intersections et contours fermés." if key == "repair" else " : ajouter ou modifier les contraintes persistantes dans le panneau."
                                      if implemented else " : fonction à venir ; aucune opération n’est exécutée."))
            if key in RESHAPE_TOOLS:
                action.setToolTip(RESHAPE_TOOLS[key] + " : choisir les paramètres dans le panneau, prévisualiser puis appliquer. Une seule annulation.")
        for index, (title, columns) in enumerate(GROUPS):
            context.register_ribbon(f"group{index}", "Esquisse", title,
                tuple(key for column in columns for key in column), columns=columns)
        context.connect(context.services.state_changed, self.refresh_tool_states)
        context.register_presentation(SketchObject, SketchPresentation, label="Esquisse", icon="sketch", edit_action="edit")

    def open_reshape(self, mode):
        if self.can_draw(self.context.services):
            from .reshape_panel import open_reshape
            open_reshape(self.context.edit_session, mode)

    def selected(self, services):
        document = services.active_document
        return document.objects.get(services.selected_ids[0]) if document and len(services.selected_ids) == 1 else None

    def can_create(self, services):
        if not services.can_start_tool:
            return False
        try:
            from_selection(services.active_document, services.selection_items)
            return True
        except (ValueError, RuntimeError):
            return False

    def can_edit(self, services):
        obj = self.selected(services)
        return (services.can_start_tool and isinstance(obj, SketchObject) and obj.visible
                and not support_status(obj, services.active_document))

    def create(self):
        services = self.context.services
        if not self.can_create(services):
            return
        document = services.active_document
        names = {obj.name for obj in document.objects.values()}
        index = 1
        while f"Esquisse{index}" in names:
            index += 1
        support = from_selection(document, services.selection_items)
        obj = make_sketch(document, support, name=f"Esquisse{index}")
        self.context.start_edit(self.make_editor(obj, created=True), new_object=obj)

    def edit(self):
        if self.can_edit(self.context.services):
            self.context.start_edit(self.make_editor(self.selected(self.context.services)))

    def make_editor(self, obj, *, created=False):
        if not created:
            from .construction_context import resolve_object
            obj = resolve_object(obj, self.context.services.active_document)
        return SketchEditSession(obj, actions=tuple(self.tool_actions.values()), tool_changed=self.refresh_tool_states, created=created, profiles=self.profiles)

    def check_profiles(self):
        if self.can_draw(self.context.services):
            self.context.edit_session.check_profiles()

    def open_corner(self, mode):
        if self.can_draw(self.context.services):
            from .corner_tool import open_corner
            open_corner(self.context.edit_session, mode)

    def toggle_snap(self):
        if self.can_draw(self.context.services):
            self.context.edit_session.toggle_snapping()

    def open_constraints(self, dimension=False):
        if self.can_draw(self.context.services):
            session = self.context.edit_session
            session.close_corner()
            if session.draft.tool is not None:
                session.select_tool(None)
            else:
                session.surface.cancel_pointer()
            session.view_tools.start(dimension)
            session.constraint_panel.focus_relations(dimension=dimension)
            session.surface.focus()

    def can_draw(self, services):
        return services.edit_owner == self.context.owner and isinstance(self.context.edit_session, SketchEditSession)

    def draw(self, tool):
        if self.can_draw(self.context.services):
            self.context.edit_session.select_tool(tool)

    def refresh_tool_states(self):
        session = self.context.edit_session
        selected = session.draft.tool if isinstance(session, SketchEditSession) else None
        for tool, action in self.tool_actions.items():
            action.setChecked(tool == selected)
        mode = session.view_tools.mode if isinstance(session,SketchEditSession) and session.view_tools is not None else None
        for key,action in self.constraint_actions.items():
            action.setChecked(mode == key)
        self.selection_action.setChecked(isinstance(session, SketchEditSession) and selected is None and mode is None)
        self.snap_action.setChecked(isinstance(session, SketchEditSession) and session.snap_settings.enabled)

    def deactivate(self):
        self.line_menu.deleteLater()
        self.arc_menu.deleteLater()
        self.profiles.close()
        self.tool_actions = {}
        self.constraint_actions = {}
        self.context = None


def create_plugin():
    return SketchPlugin()
