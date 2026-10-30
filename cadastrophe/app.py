from __future__ import annotations

import logging
from dataclasses import asdict, replace
import sys
from functools import partial
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QSize, QSignalBlocker, QItemSelectionModel, QByteArray
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (QApplication, QMainWindow, QTreeWidget,
    QLabel, QMessageBox, QColorDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QAbstractItemView, QMenu, QMenuBar, QToolButton, QSizePolicy, QSplitter, QTabBar, QStackedWidget)
from OCC.Display.backend import load_backend

load_backend("pyside6")
from .camera import STANDARD_VIEWS
from .document import ObjectType, SelectionItem
from .interaction import Gesture
from .scene import new_document
from .viewport import CadViewport
from .ui.tool_ribbon import ToolRibbon
from .ui.command_palette import CommandPalette, PALETTE_COMMAND
from .ui.document_tree import DocumentTreeAdapter
from .ui.features import FEATURE_GROUPS, FEATURE_TOOLS
from .ui.workspace import ActionStrip, ManagerPane, ViewTools
from .core.commands import CommandRegistry
from .core.clipboard import ObjectClipboard, ClipboardError
from .core.documents import DocumentManager
from .core.history import HistoryBusyError
from .core.preferences import PreferenceStore, PANEL_IDS, default_preferences_path, validate_preferences
from .core.material_library import MaterialLibrary
from .core.recovery import RecoveryService, default_recovery_directory
from .core.tool_sessions import ToolSessionManager
from .core.object_types import ObjectTypeRegistry
from .core.presentations import PresentationRegistry
from .core.plugins import PluginManager
from .plugin_api import ApplicationServices
from .ui.plugin_host import PluginUiHost
from .ui.plugin_dialog import PluginDialog
from .ui.preferences_dialog import PreferencesDialog
from .ui.recovery_dialog import RecoveryDialog
from .ui.tool_host import ToolUiHost
from .ui.file_dialogs import FileDialogs, suggested_filename, file_error_text
from .core.persistence import DocumentFileError, EXTENSION, FILE_FILTER, SAVE_FILE_FILTER, load_document, save_document
from .ui.themes import LIGHT, DARK, apply_theme, theme_tokens
from .ui.icons import icon
from .logging_setup import DiagnosticSession, default_log_path

log = logging.getLogger(__name__)
RENDER_COMMANDS = {"Ombré avec arêtes": "view.render.edges", "Ombré": "view.render.shaded",
                   "Filaire": "view.render.wireframe"}


class MainWindow(QMainWindow):
    def __init__(self, *, plugin_directory=None, preferences_path=None, recovery_directory=None):
        super().__init__()
        self.preference_store = PreferenceStore(preferences_path)
        self.preferences, self._saved_layout = self.preference_store.load()
        self.file_dialogs = FileDialogs(self._saved_layout.get("file_directories"),self)
        apply_theme(QApplication.instance(), DARK if self.preferences.theme == "dark" else LIGHT)
        # Build our embedded bar directly: QMainWindow.setMenuWidget() deletes
        # its previous menu widget, even if that widget has been reparented.
        self._integrated_menu_bar = QMenuBar(self)
        self.setWindowTitle("CADastrophe")
        available = self.screen().availableGeometry()
        self.resize(min(1440, round(available.width() * .95)), min(920, round(available.height() * .9)))
        self.move(available.center() - self.rect().center())
        self.documents = DocumentManager(self)
        self.services = ApplicationServices(self.documents)
        self.services._material_library = MaterialLibrary(
            self.preference_store.path.with_name('materials.json') if self.preference_store.path is not None else None)
        from .ui.computation import Computations
        self.computations = Computations(self)
        self.services._computations = self.computations
        self.services._file_dialogs = self.file_dialogs
        self.file_dialogs.warning.connect(self.services.report_problem)
        self.services._display_units = self.preferences.units
        self.object_types = ObjectTypeRegistry()
        self.object_presentations = PresentationRegistry(self)
        self.object_clipboard = ObjectClipboard(QApplication.clipboard(), self.object_types)
        self.services._clipboard = self.object_clipboard.transport
        self.plugins = None
        self.recovery = None
        initial_document = self.documents.add(new_document())
        self.viewport = CadViewport(self, document=initial_document, presentation_registry=self.object_presentations)
        self.services._selection_provider = self._selection_items
        self.services._selection_setter = self.tree_selection_changed
        self.viewport.apply_preferences(self.preferences)
        self._views = {initial_document.id: self.viewport}
        self._document_number = 0
        self._closing = False
        workspace = QWidget(self)
        workspace.setObjectName("workspace")
        self.workspace_layout = QVBoxLayout(workspace)
        self.workspace_layout.setContentsMargins(0, 0, 0, 0)
        self.workspace_layout.setSpacing(0)
        self.setCentralWidget(workspace)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Construction"])
        self.tree.setHeaderHidden(True)
        self.tree.setAccessibleName("Arbre de construction")
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(theme_tokens().tree_indent)
        self.tree.setIconSize(QSize(theme_tokens().icon_small, theme_tokens().icon_small))
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.tree_menu)
        self.manager_pane = ManagerPane(self.tree)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("documentSplitter")
        self.splitter.setHandleWidth(4)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.manager_pane)
        self.graphics_area = QWidget()
        graphics_layout = QVBoxLayout(self.graphics_area)
        graphics_layout.setContentsMargins(0, 0, 0, 0)
        graphics_layout.setSpacing(0)
        self.view_stack = QStackedWidget()
        self.empty_workspace = QWidget()
        empty_layout = QVBoxLayout(self.empty_workspace)
        empty_label = QLabel("Aucun document ouvert\nCtrl+N pour créer une pièce")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_label.setProperty("role", "secondaryText")
        empty_layout.addWidget(empty_label)
        self.view_stack.addWidget(self.empty_workspace)
        self.view_stack.addWidget(self.viewport)
        self.view_stack.setCurrentWidget(self.viewport)
        graphics_layout.addWidget(self.view_stack, 1)
        self.document_tabs = QTabBar()
        self.document_tabs.setObjectName("documentTabs")
        self.document_tabs.setTabsClosable(True)
        self.document_tabs.setAccessibleName("Documents ouverts")
        self.document_tabs.setExpanding(False)
        self.document_tabs.setDrawBase(False)
        graphics_layout.addWidget(self.document_tabs)
        self.splitter.addWidget(self.graphics_area)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([260, 1180])
        self._splitter_sizes = [260, 1180]
        self.workspace_layout.addWidget(self.splitter, 1)
        self.tree_adapter = DocumentTreeAdapter(self.tree, self, presentation_registry=self.object_presentations)
        self.tree_items = self.tree_adapter.items
        self.tree_adapter.visibility_requested.connect(lambda key, visible: self.viewport.set_visible(key, visible))
        self.tree_adapter.selection_requested.connect(self.tree_selection_changed)
        self.tree_adapter.rename_requested.connect(self.rename_object)
        self.tree.itemDoubleClicked.connect(self.activate_tree_object)
        from .ui.feedback import FeedbackLabel, set_feedback
        self.hint = FeedbackLabel("Initialisation de la vue 3D…")
        self.hint.setMinimumWidth(0)
        self.hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.coords = QLabel("")
        self.statusBar().addWidget(self.hint, 1)
        self.statusBar().addPermanentWidget(self.coords)
        units = self.units = QLabel("MMGS")
        units.setToolTip("Millimètres, grammes, secondes")
        units.setProperty("role", "units")
        self.statusBar().addPermanentWidget(units)
        self.command_registry = CommandRegistry(self)
        self.command_palette = None
        self.tool_host = ToolUiHost(self)
        self.tool_sessions = ToolSessionManager(self.documents, self.tool_host, self)
        self.services._tools = self.tool_sessions
        self.build_commands()
        self.manager_pane.configurations.install(self.command_registry, self.services)
        configuration_menu = self.menuBar().addMenu("Configurations")
        configuration_menu.addActions(list(self.manager_pane.configurations.actions.values()))
        self.computations.changed.connect(self.update_action_states)
        self.computations.changed.connect(self.services.state_changed.emit)
        QApplication.clipboard().dataChanged.connect(self.update_action_states)
        self.tool_sessions.changed.connect(self.tool_host.refresh)
        self.tool_sessions.changed.connect(self.update_action_states)
        self.tool_sessions.changed.connect(self.services.state_changed.emit)
        self.tool_sessions.error.connect(self.services.report_problem)
        self.tree_adapter.state_changed.connect(self.document_state_changed)
        self.tree_adapter.bind(self.viewport.document)
        self._history = None
        self._history_document = None
        self.bind_history(self.viewport.document)
        self._connect_viewport(self.viewport)
        self.documents.document_added.connect(self._document_added)
        self.documents.document_removed.connect(self._document_removed)
        self.documents.document_replaced.connect(self._document_replaced)
        self.documents.active_changed.connect(self._activate_document)
        self.documents.changed.connect(self._documents_changed)
        self.document_tabs.currentChanged.connect(self._tab_activated)
        self.document_tabs.tabCloseRequested.connect(self._tab_closed)
        self._documents_changed()
        self.services.message.connect(self.hint.setText)
        self.services.problem.connect(lambda text: set_feedback(self.hint, text))
        plugin_directory = Path(plugin_directory) if plugin_directory is not None else Path(__file__).resolve().parent.parent / "plugins"
        host = PluginUiHost(self.command_registry, self.command_manager, self.manager_pane, self.plugins_menu,
                            self.object_presentations, status_bar=self.statusBar())
        self.plugins = PluginManager(plugin_directory, self.services, host, self.object_types, self.documents)
        self.plugins.startup()
        self.recovery = RecoveryService(self.documents, self.object_types, recovery_directory, self)
        self.recovery.message.connect(self.services.report_problem)
        self.recovery.configure(self.preferences.autosave_enabled, self.preferences.autosave_interval_minutes)
        try:
            self.command_registry.set_shortcuts_batch(self._preference_bindings(self.preferences))
        except ValueError as exc:
            self.services.report_problem(f"Raccourcis non appliqués : {exc}")
            log.warning("Raccourcis de préférences : %s", exc)
        self.restore_layout(self._saved_layout)
        self.file_dialogs.changed.connect(self._save_file_directories)
        if self.file_dialogs.startup_warning:
            self.services.report_problem(self.file_dialogs.startup_warning)
        if self.preference_store.error:
            self.services.report_problem("Préférences non chargées : " + self.preference_store.error)
            log.warning("Préférences %s : %s", self.preference_store.path, self.preference_store.error)
        self.update_action_states()

    def bind_history(self, document):
        if self._history is not None:
            self._history.changed.disconnect(self.update_action_states)
        self._history = document.history if document is not None else None
        # Keep the QObject parent alive until its signal has been disconnected.
        self._history_document = document
        if self._history is not None:
            self._history.changed.connect(self.update_action_states)
        self.update_action_states()

    @property
    def selected_ids(self):
        return list(self.viewport.document.selected_ids) if self.viewport is not None else []

    def document_state_changed(self):
        self.manager_pane.filter_tree(self.manager_pane.tree_filter.text())
        self.update_action_states()

    def show_equations(self):
        from .ui.equations import EquationsDialog
        document = self.services.active_document
        if document is None:
            self.statusBar().showMessage("Ouvrez un document pour définir des variables.")
            return
        dialog = EquationsDialog(document, self)
        dialog.setReadOnly(document.history.blocked)
        dialog.exec()

    def build_commands(self):
        register = self.command_registry.register
        filemenu = self.menuBar().addMenu("Fichier")
        filemenu.addAction(register("file.new", "Nouveau", "Fichier", self.create_document,
                                    shortcuts=("Ctrl+N",)))
        filemenu.addAction(register("file.close", "Fermer le document", "Fichier", self.close_document,
                                    shortcuts=("Ctrl+W",)))
        filemenu.addSeparator()
        filemenu.addAction(register("file.recover", "Récupérer des documents…", "Fichier", self.manage_recovery,
                                    enabled=False))
        for command_id, label, shortcut, callback in (
            ("file.open", "Ouvrir…", "Ctrl+O", self.open_document_interactive),
            ("file.save", "Enregistrer", "Ctrl+S", self.save_document),
            ("file.save_as", "Enregistrer sous…", "Ctrl+Shift+S", partial(self.save_document, save_as=True))):
            filemenu.addAction(register(command_id, label, "Fichier", callback, shortcuts=(shortcut,)))
        filemenu.addSeparator()
        filemenu.addAction(register("file.export", "Exporter…", "Fichier",
                                   reason="Activez l’extension d’import/export pour exporter le modèle."))
        editmenu = self.menuBar().addMenu("Édition")
        editmenu.addAction(register("edit.equations", "Variables et équations…", "Édition", self.show_equations))
        editmenu.addAction(register("edit.undo", "Annuler", "Édition", self.undo_document,
                                    shortcuts=("Ctrl+Z",), enabled=False))
        editmenu.addAction(register("edit.redo", "Rétablir", "Édition", self.redo_document,
                                    shortcuts=("Ctrl+Y", "Ctrl+Shift+Z"), enabled=False))
        editmenu.addSeparator()
        for command_id, label, shortcut, callback in (
            ("edit.copy", "Copier", "Ctrl+C", self.copy_selected),
            ("edit.cut", "Couper", "Ctrl+X", partial(self.copy_selected, cut=True)),
            ("edit.paste", "Coller", "Ctrl+V", self.paste_objects)):
            editmenu.addAction(register(command_id, label, "Édition", callback,
                                        shortcuts=(shortcut,), enabled=False))
        editmenu.addAction(register("edit.select_all", "Tout sélectionner · à venir", "Édition",
            shortcuts=("Ctrl+A",), reason="La sélection globale d’objets n’est pas implémentée."))
        editmenu.addSeparator()
        editmenu.addAction(register(PALETTE_COMMAND, "Rechercher une commande…", "Fenêtre",
                                    self.show_command_palette, shortcuts=("Ctrl+K",)))
        viewmenu = self.menuBar().addMenu("Vue")
        standard_actions = []
        for index, (name, key) in enumerate(zip(STANDARD_VIEWS,
                ("front", "back", "left", "right", "top", "bottom", "isometric")), 1):
            shortcuts = (f"Ctrl+{index}", "0") if key == "isometric" else (f"Ctrl+{index}",)
            action = register(f"view.standard.{key}", name, "Affichage", partial(self.standard_view, name),
                              shortcuts=shortcuts)
            viewmenu.addAction(action)
            standard_actions.append(action)
        iso = standard_actions[-1]
        fit = register("view.fit", "Ajuster", "Affichage", self.fit_all, shortcuts=("F",))
        zoom = register("view.zoom_window", "Zoom fenêtre", "Affichage", self.zoom_window, shortcuts=("Z",))
        self.normal_action = register("view.normal_to_plane", "Vue normale au plan", "Esquisse",
                                      self.normal_to_selected, shortcuts=("Ctrl+8",))
        self.normal_action.setEnabled(False)
        back = register("view.cancel", "Retour 3D", "Affichage", self.cancel_view,
                        shortcuts=("Escape",), reason="Échap : annuler le geste et revenir à la vue 3D.")
        register("tool.commit", "Valider l’outil", "Outils", self.tool_sessions.commit_interactive,
                 shortcuts=("Return", "Enter"), enabled=False)
        objectmenu = self.menuBar().addMenu("Objet")
        frame = register("object.frame", "Cadrer la sélection", "Objet", self.frame_selected, shortcuts=("Shift+F",))
        visibility = register("object.visibility", "Afficher / masquer", "Objet", self.toggle_selected, shortcuts=("Space",))
        delete = register("object.delete", "Supprimer", "Objet", self.delete_selected, shortcuts=("Delete",))
        rename = register("object.rename", "Renommer…", "Objet", self.edit_selected_name, shortcuts=("F2",))
        objectmenu.addActions([frame, visibility, rename, delete])
        show = register("object.show", "Afficher la sélection", "Objet", partial(self.set_selected_visibility, True))
        hide = register("object.hide", "Masquer la sélection", "Objet", partial(self.set_selected_visibility, False))
        isolate = register("object.isolate", "Isoler les corps sélectionnés", "Objet", self.isolate_selected)
        show_all = register("object.show_all", "Afficher tous les corps", "Objet", self.show_all_bodies)
        transparent = register("object.transparency", "Transparence 50 % des corps", "Objet", self.toggle_transparency)
        transparent.setCheckable(True)
        properties = register("object.properties", "Propriétés de la sélection", "Objet", self.show_properties)
        objectmenu.addSeparator()
        objectmenu.addActions([show, hide, isolate, show_all, transparent, properties])
        background = register("view.background", "Couleur du fond…", "Affichage", self.background)
        viewmenu.addSeparator()
        viewmenu.addActions([fit, zoom, frame, self.normal_action, back, background])
        self.render_menu = viewmenu.addMenu("Rendu")
        group = QActionGroup(self)
        for index, (name, command_id) in enumerate(RENDER_COMMANDS.items()):
            action = register(command_id, name, "Affichage", partial(self.apply_render, name))
            action.setCheckable(True)
            group.addAction(action)
            action.setChecked(index == 0)
            self.render_menu.addAction(action)

        brand_mark = QLabel()
        brand_mark.setObjectName("applicationMark")
        brand_mark.setPixmap(icon("cadastrophe").pixmap(QSize(28, 28)))
        brand_mark.setAccessibleName("CADastrophe")
        self.setWindowIcon(icon("cadastrophe"))
        brand = QLabel("CADastrophe")
        brand.setObjectName("applicationBrand")
        self.quick_access = ActionStrip("quickAccess", self)
        for command_id in ("file.new", "file.open", "file.save", "edit.undo", "edit.redo"):
            self.quick_access.add_action(self.command_registry.action(command_id))
        tree_toggle = self.panel_toggle("window.tree", "Gestionnaire du document", self.manager_pane)

        manager = self.command_manager = ToolRibbon(self)
        self.workspace_layout.insertWidget(0, manager)

        tools = self.view_tools = ViewTools(self.viewport)
        for action in (fit, zoom, frame):
            tools.add_action(action)
        tools.add_separator()
        self.orientation_menu = QMenu("Orientation", self)
        self.orientation_menu.addActions(standard_actions)
        tools.add_menu("Orientation de la vue", "body", self.orientation_menu)
        tools.add_action(self.normal_action)
        tools.add_separator()
        self.render_button = tools.add_menu("Style d’affichage : Ombré avec arêtes", "body", self.render_menu)
        tools.add_action(visibility)
        tools.add_action(background)
        tools.reposition()

        manager.add_action(manager.add_group("Affichage", "Panneaux"), tree_toggle)
        row = manager.add_group("Affichage", "Objets")
        for action in (visibility, delete):
            manager.add_action(row, action)
        note = QLabel("Orientation, zoom et rendu\nen bas à droite de la vue 3D")
        note.setProperty("role", "secondaryText")
        manager.add_group("Affichage", "Vue 3D").addWidget(note)
        appearance = self.manager_pane.appearance_layout
        render_button = QToolButton()
        render_button.setText("Style d’affichage")
        render_button.setIcon(icon("body"))
        render_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        render_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        render_button.setMenu(self.render_menu)
        appearance.addWidget(render_button)
        background_button = QToolButton()
        background_button.setDefaultAction(background)
        background_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        appearance.addWidget(background_button)
        appearance.addStretch()

        features = {tool.key: register(f"modeling.{tool.key}", f"{tool.title} · à venir", "Fonctions",
                    reason=f"{tool.title} : opération de modélisation à venir.") for tool in FEATURE_TOOLS}
        for title, columns in FEATURE_GROUPS:
            row = manager.add_group("Fonctions", title)
            for keys in columns:
                if len(keys) == 1:
                    button = manager.add_action(row, features[keys[0]])
                    related = {"linear_pattern": "circular_pattern"}.get(keys[0])
                    if related:
                        menu = QMenu(button)
                        menu.addActions([features[keys[0]], features[related]])
                        button.setMenu(menu)
                        button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
                        button.setProperty("hasToolMenu",True)
                        button.setMinimumWidth(max(100,button.minimumWidth()))
                else:
                    manager.add_column(row, tuple(features[key] for key in keys))
        panels = self.menuBar().addMenu("Fenêtre")
        panels.addActions([tree_toggle,
            self.panel_toggle("window.quick_access", "Accès rapide", self.quick_access),
            self.panel_toggle("window.command_manager", "Ateliers de conception", manager),
            self.panel_toggle("window.view_tools", "Outils de vue", tools)])
        panels.addSeparator()
        panels.addAction(register("settings.preferences", "Préférences…", "Paramètres", self.manage_preferences))
        panels.addAction(register("window.reset_layout", "Réinitialiser la disposition", "Fenêtre", self.reset_layout))
        header = QWidget(self)
        header.setObjectName("applicationHeader")
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(0)
        menu_bar = self.menuBar()
        header_row.addWidget(brand_mark)
        header_row.addWidget(brand)
        header_row.addWidget(menu_bar, 1)
        header_row.addWidget(self.quick_access)
        self.setMenuWidget(header)
        self.plugins_menu = self.menuBar().addMenu("Extensions")
        self.plugins_menu.addAction(register("plugins.manage", "Gestion des extensions…", "Extensions", self.manage_plugins))

    def show_command_palette(self):
        if self.command_palette is None:
            self.command_palette = CommandPalette(self.command_registry, self)
        self.command_palette.open_palette()

    def manage_plugins(self):
        dialog = PluginDialog(self.plugins, self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def manage_preferences(self):
        dialog = PreferencesDialog(self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def manage_recovery(self, *, only_if_available=False):
        if self.recovery is None or self.recovery.directory is None or self.documents.blocked:
            return
        if only_if_available and not self.recovery.entries():
            return
        dialog = RecoveryDialog(self.recovery, self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _preference_bindings(self, preferences):
        return {key: preferences.shortcuts.get(key, defaults)
                for key, defaults in self.command_registry.default_shortcuts().items()}

    def apply_preferences(self, preferences):
        if self.documents.blocked:
            raise ValueError("Terminez l’opération documentaire avant de modifier les préférences.")
        preferences = validate_preferences(asdict(preferences))
        bindings = self._preference_bindings(preferences)
        self.command_registry.validate_shortcuts(bindings)
        self.preference_store.save(preferences, self.capture_layout())
        self.command_registry.set_shortcuts_batch(bindings)
        self.preferences = preferences
        if self.recovery is not None:
            self.recovery.configure(preferences.autosave_enabled, preferences.autosave_interval_minutes)
        self.services._display_units = preferences.units
        apply_theme(QApplication.instance(), DARK if preferences.theme == "dark" else LIGHT)
        self.tree_adapter.refresh_style()
        for viewport in self._views.values():
            viewport.apply_preferences(preferences)
        self.coords.clear()
        self._documents_changed()
        self.services.state_changed.emit()

    def capture_layout(self):
        sizes = self.splitter.sizes()
        if all(size > 0 for size in sizes):
            self._splitter_sizes = sizes
        return dict(geometry=bytes(self.saveGeometry().toBase64()).decode("ascii"), splitter=self._splitter_sizes,
                    panels={key: self.command_registry.action(key).isChecked() for key in PANEL_IDS},
                    command_tab=self.command_manager.currentIndex(),
                    manager_tab=self.manager_pane.currentIndex() if self.manager_pane.currentIndex() < 4 else 0,
                    file_directories=dict(self.file_dialogs.directories))

    def _save_file_directories(self):
        try:
            self.preference_store.save(self.preferences,self.capture_layout())
        except (ValueError,OSError) as error:
            log.warning("Dossiers de fichiers non mémorisés : %s",error)
            self.services.report_problem("Le dernier dossier est conservé pour cette session, mais sa préférence n’a pas pu être enregistrée. "+file_error_text(error))

    def restore_layout(self, layout):
        if layout.get("geometry"):
            if not self.restoreGeometry(QByteArray.fromBase64(layout["geometry"].encode("ascii"))):
                log.warning("Géométrie de fenêtre ignorée : état Qt invalide.")
        if "splitter" in layout:
            self._splitter_sizes = layout["splitter"]
            self.splitter.setSizes(self._splitter_sizes)
        for key, visible in layout.get("panels", {}).items():
            self.command_registry.action(key).setChecked(visible)
        self.command_manager.setCurrentIndex(layout.get("command_tab", 0))
        self.manager_pane.setCurrentIndex(layout.get("manager_tab", 0))

    def reset_layout(self):
        self.restore_layout(dict(splitter=[260, 1180], panels={key: True for key in PANEL_IDS}))

    def _connect_viewport(self, viewport):
        viewport.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        viewport.customContextMenuRequested.connect(partial(self.viewport_menu, viewport))
        viewport.mode_changed.connect(lambda text: self.hint.setText(text) if viewport is self.viewport else None)
        viewport.coordinates_changed.connect(lambda text: self.coords.setText(text) if viewport is self.viewport else None)
        viewport.selection_changed.connect(lambda items: self.selection_changed(items) if viewport is self.viewport else None)
        viewport.failed.connect(lambda text: self.initialization_failed(text) if viewport is self.viewport else None)
        viewport.presentation_error.connect(self.services.report_problem)
        viewport.ready.connect(lambda: self.update_action_states() if viewport is self.viewport else None)
        viewport.document_changed.connect(partial(self._viewport_document_changed, viewport))
        viewport.document_load_guard = partial(self._validate_view_document, viewport)

    def _validate_view_document(self, viewport, document):
        if document is viewport.document:
            return
        if viewport is self.viewport and self.tool_sessions.editing:
            self.tool_sessions.end_edit()
        # DocumentManager.replace has already reserved this view for its new
        # document before asking it to reload its native presentations.
        if self._views.get(document.id) is viewport:
            return
        if self.documents.blocked:
            raise HistoryBusyError("Terminez la transaction avant de remplacer un document.")
        if any(doc.id == document.id for doc in self.documents.documents):
            raise ValueError("Ce document est déjà ouvert ; activez son onglet.")

    def _viewport_document_changed(self, viewport, document):
        previous_id = next((key for key, view in self._views.items() if view is viewport), None)
        if previous_id is not None and previous_id != document.id:
            self.documents.replace(previous_id, document)

    def _document_added(self, document):
        viewport = CadViewport(document=document, presentation_registry=self.object_presentations)
        viewport.apply_preferences(self.preferences)
        self._views[document.id] = viewport
        self._connect_viewport(viewport)
        self.view_stack.addWidget(viewport)

    def _document_replaced(self, previous, document):
        viewport = self._views.pop(previous.id)
        self._views[document.id] = viewport
        if viewport.document is not document:
            if viewport.camera is not None:
                viewport.load_document(document)
            else:
                viewport.document = document

    def _activate_document(self, document):
        if self.viewport is not None:
            self.viewport.cancel_gesture()
        self.viewport = self._views.get(document.id) if document is not None else None
        self.coords.clear()
        if self.viewport is None:
            self.view_tools.bind_viewport(self.graphics_area)
            self.view_tools.hide()
            self.view_stack.setCurrentWidget(self.empty_workspace)
            self.tree_adapter.detach()
            self.tree_adapter.rebuild()
            self.bind_history(None)
            self.hint.setText("Aucun document ouvert · Ctrl+N : nouvelle pièce")
        else:
            self.tree_adapter.bind(document)
            self.bind_history(document)
            self.view_tools.bind_viewport(self.viewport)
            self.view_stack.setCurrentWidget(self.viewport)
            self.view_tools.setVisible(self.command_registry.action("window.view_tools").isChecked())
            self.viewport.setFocus()
            self.selection_changed(self.viewport.selection.items() if self.viewport.selection else [])
        self._documents_changed()

    def _document_removed(self, document):
        viewport = self._views.pop(document.id)
        viewport.document_load_guard = None
        # Release Python QUndoCommand groups and their native shape receipts
        # before Qt tears down the viewport/owner graph. Do not leave populated
        # stacks and their Python command wrappers to cyclic GC during native
        # owner destruction.
        document.history.clear()
        self.view_stack.removeWidget(viewport)
        viewport.close()
        viewport.deleteLater()

    def _documents_changed(self):
        documents = self.documents.documents
        blocker = QSignalBlocker(self.document_tabs)
        while self.document_tabs.count() > len(documents):
            self.document_tabs.removeTab(self.document_tabs.count() - 1)
        for index, document in enumerate(documents):
            if index == self.document_tabs.count():
                self.document_tabs.addTab("")
            self.document_tabs.setTabData(index, document.id)
            self.document_tabs.setTabText(index, document.display_name + (" *" if document.is_modified else ""))
            self.document_tabs.setTabToolTip(index, str(document.file_path or document.name) + (" — modifications non enregistrées" if document.is_modified else ""))
            if document is self.documents.active_document:
                self.document_tabs.setCurrentIndex(index)
        del blocker
        document = self.documents.active_document
        self.setWindowModified(document.is_modified if document is not None else False)
        self.setWindowTitle(f"CADastrophe — {document.display_name}[*]" if document is not None else "CADastrophe")
        self.units.setText(("MMGS" if self.preferences.length_unit == "mm" else self.preferences.length_unit) if document is not None else "—")
        self.units.setToolTip(f"Longueurs affichées en {self.preferences.length_unit} ; unité interne : mm")
        self.manager_pane.configurations.refresh()
        self.update_action_states()

    def _tab_activated(self, index):
        if index < 0:
            return
        try:
            self.documents.activate(self.document_tabs.tabData(index))
        except HistoryBusyError:
            self._documents_changed()
            self.hint.setText("Terminez l’opération documentaire avant de changer d’onglet.")

    def _tab_closed(self, index):
        self.close_document(self.document_tabs.tabData(index))

    def create_document(self):
        if self.documents.blocked:
            return None
        self._document_number += 1
        return self.documents.add(new_document(f"Pièce{self._document_number}"))

    def choose_open_path(self):
        return self.file_dialogs.choose(self,purpose="project",title="Ouvrir un projet CADastrophe",
                                        name_filter=FILE_FILTER)

    def choose_save_path(self, document):
        name = document.file_path.with_suffix(EXTENSION).name if document.file_path is not None else suggested_filename(document.name,EXTENSION)
        return self.file_dialogs.choose(self,purpose="project",title="Enregistrer le projet sous",
                                        name_filter=SAVE_FILE_FILTER,save=True,suffix=EXTENSION,suggested_name=name)

    def report_file_error(self, title, error):
        log.warning("%s: %s", title, error, exc_info=(type(error), error, error.__traceback__))
        QMessageBox.warning(self, title, file_error_text(error))

    def open_document(self, path=None):
        if self.documents.blocked:
            return None
        try:
            path = path if path is not None else self.choose_open_path()
            if path is None:
                return None
            path = Path(path).resolve()
            if self.documents.blocked:
                return None
            for document in self.documents.documents:
                if document.file_path == path:
                    self.documents.activate(document.id)
                    self.file_dialogs.remember("project",path.parent)
                    return document
            document = load_document(path, registry=self.object_types)
            if any(other.id == document.id for other in self.documents.documents):
                raise DocumentFileError("Un document portant le même identifiant est déjà ouvert sous un autre nom. Fermez-le avant d’ouvrir cette copie.")
            self.documents.add(document)
            self.file_dialogs.remember("project",path.parent)
            self.hint.setText(f"Ouvert : {path.name}")
            return document
        except (DocumentFileError, OSError, ValueError) as exc:
            self.report_file_error("Ouverture impossible", exc)
            return None

    def open_document_interactive(self, path=None):
        """Stage an open off the manager; publish only after all restorers finish."""
        if self.documents.blocked or self.computations.busy:
            return None
        staged = previous = owner = steps = None
        try:
            path = path if path is not None else self.choose_open_path()
            if path is None:
                return None
            path = Path(path).resolve()
            for document in self.documents.documents:
                if document.file_path == path:
                    self.documents.activate(document.id)
                    return document
            staged = load_document(path, registry=self.object_types, reconstruct=False)
            if any(document.id == staged.id for document in self.documents.documents):
                raise DocumentFileError("Un document portant le même identifiant est déjà ouvert sous un autre nom. Fermez-le avant d’ouvrir cette copie.")
            previous, owner = self.documents.active_document, object()
            if previous is not None:
                previous.history.reserve(owner)
            steps = self.object_types.restore_document_steps(staged)
            def release():
                if previous is not None:
                    previous.history.release(owner)
            def completed(_):
                release()
                self.documents.add(staged)
                self.file_dialogs.remember("project", path.parent)
                self.hint.setText(f"Ouvert : {path.name}")
            def failed(error):
                release()
                staged.deleteLater()
                self.report_file_error("Ouverture impossible", error)
            def cancelled():
                release()
                staged.deleteLater()
                self.hint.setText("Ouverture annulée.")
            return self.computations.start(steps, f"Ouverture · {path.name}",
                                           completed=completed, failed=failed, cancelled=cancelled)
        except (DocumentFileError, OSError, ValueError) as error:
            if steps is not None:
                steps.close()
            if previous is not None and previous.history._reservation is owner:
                previous.history.release(owner)
            if staged is not None:
                staged.deleteLater()
            self.report_file_error("Ouverture impossible", error)
            return None

    def save_document(self, *, document=None, path=None, save_as=False):
        if self.documents.blocked:
            return False
        document = document if document is not None else self.documents.active_document
        if document is None or document not in self.documents.documents:
            return False
        try:
            selected_path = path is None and (save_as or document.file_path is None)
            if path is None:
                path = self.choose_save_path(document) if selected_path else document.file_path
            if path is None or self.documents.blocked:
                return False
            path = Path(path).resolve()
            if any(other is not document and other.file_path == path for other in self.documents.documents):
                raise DocumentFileError("Ce fichier est déjà utilisé par un autre document ouvert.")
            if (selected_path or save_as or path != document.file_path) and not self.file_dialogs.confirm_overwrite(self,path):
                return False
            save_document(document, path, registry=self.object_types)
            self.file_dialogs.remember("project",document.file_path.parent)
            self.hint.setText(f"Enregistré : {path.name}")
            return True
        except (DocumentFileError, OSError, ValueError) as exc:
            self.report_file_error("Enregistrement impossible", exc)
            return False

    def confirm_discard(self, document):
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Modifications non enregistrées")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(f"Le document « {document.display_name} » a été modifié.")
        dialog.setInformativeText("Voulez-vous enregistrer les modifications avant de continuer ?")
        dialog.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        dialog.button(QMessageBox.StandardButton.Save).setText("Enregistrer")
        dialog.button(QMessageBox.StandardButton.Discard).setText("Ne pas enregistrer")
        dialog.button(QMessageBox.StandardButton.Cancel).setText("Annuler")
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        dialog.setEscapeButton(QMessageBox.StandardButton.Cancel)
        try:
            result = dialog.exec()
        finally:
            dialog.deleteLater()
        if result == QMessageBox.StandardButton.Save:
            return self.save_document(document=document)
        return result == QMessageBox.StandardButton.Discard

    def close_document(self, document_id=None):
        document = (next((doc for doc in self.documents.documents if doc.id == document_id), None)
                    if document_id is not None else self.documents.active_document)
        if document is None:
            return False
        if self.tool_sessions.document is document and self.tool_sessions._preview_task is not None:
            self.tool_sessions.cancel()
        task = self.computations.active
        if task is not None and getattr(task.steps, "document", None) is document:
            task.cancel()
        if self.documents.blocked:
            self.hint.setText("Terminez l’opération documentaire avant de fermer.")
            return False
        if document.is_modified and not self.confirm_discard(document):
            return False
        if self.documents.blocked:
            return False
        self.documents.remove(document.id)
        return True

    def menuBar(self):
        # QMainWindow would replace our header if asked to create another bar.
        # Keep its public menu API pointing at the bar embedded in that header.
        return self._integrated_menu_bar

    def panel_toggle(self, command_id, title, widget):
        action = QAction(title, self)
        action.setCheckable(True)
        action.setChecked(True)
        action.toggled.connect(widget.setVisible)
        return self.command_registry.adopt(command_id, action, "Fenêtre")

    def initialization_failed(self, message):
        self.services.report_problem("Échec d'initialisation du viewport")
        QMessageBox.critical(self, "Initialisation de la vue 3D", message)

    def rebuild_tree(self):
        self.tree_adapter.rebuild()

    def tree_selection_changed(self, object_ids):
        if self.tool_sessions.editing:
            return
        if self.viewport is None:
            return
        if self.viewport.selection:
            # Hidden reference planes remain usable from the tree.
            self.viewport.selection.from_tree(object_ids)
        else:
            self.viewport.document.select_objects(object_ids)

    def activate_tree_object(self, item, column=0):
        if self.tool_sessions.editing or self.viewport is None:
            return
        key = item.data(0, Qt.ItemDataRole.UserRole)
        obj = self.viewport.document.objects.get(key)
        if obj is None:
            return
        self.tree_selection_changed([key])
        binding = self.object_presentations.binding(obj)
        self.command_registry.execute(binding.edit_command if binding and binding.edit_command else "object.frame")

    def _selection_items(self, document):
        if self.viewport is not None and self.viewport.document is document and self.viewport.selection:
            return self.viewport.selection.items()
        return tuple(SelectionItem(key, "Plan" if document.objects[key].plane else "Objet",
                                   document.objects[key].shape, document.objects[key].revision)
                     for key in document.selected_ids)

    def selection_changed(self, items):
        self.update_action_states()
        self.services.selection_changed.emit(tuple(items))
        if items:
            text = " · ".join(f"{self.viewport.document.objects[i.object_id].name} : {i.topology}" for i in items)
            self.hint.setText(text)
        elif self.viewport is not None and self.viewport.workplane is None:
            self.hint.setText("3D · Milieu : rotation · Alt+milieu : roulis · Ctrl+milieu : panoramique · Molette : zoom")

    def update_action_states(self):
        viewport = self.viewport
        document = viewport.document if viewport is not None else None
        ready = viewport is not None and viewport.camera is not None and not viewport._failed
        editing = self.tool_sessions.editing
        history = document.history if document is not None else None
        editable = ready and not self.services.busy
        self.command_registry.set_enabled("settings.preferences", not self.documents.blocked)
        self.command_registry.set_enabled("window.reset_layout", not self.documents.blocked)
        self.tree.setEnabled(ready and not editing and (not self.documents.blocked or self.tool_sessions.active))
        self.tree_adapter.set_mutable(editable and not editing)
        self.command_registry.set_enabled("tool.commit", self.tool_sessions.active)
        self.document_tabs.setEnabled(not self.services.busy)
        self.command_registry.set_enabled("file.new", not self.services.busy)
        self.command_registry.set_enabled("file.open", not self.services.busy)
        self.command_registry.set_enabled("file.save", document is not None and not self.services.busy)
        self.command_registry.set_enabled("file.save_as", document is not None and not self.services.busy)
        calculation = self.computations.active
        cancellable = (calculation is not None and getattr(calculation.steps, "document", None) is document
                       or self.tool_sessions.document is document and self.tool_sessions._preview_task is not None)
        self.command_registry.set_enabled("file.close", document is not None and (not self.services.busy or cancellable))
        self.command_registry.set_enabled("file.recover", self.recovery is not None and self.recovery.directory is not None
                                          and not self.documents.blocked)
        objects = [document.objects[key] for key in self.selected_ids if key in document.objects] if document is not None else []
        copy_reason = self.clipboard_selection_error()
        for command_id in ("edit.copy", "edit.cut"):
            self.command_registry.set_enabled(command_id, editable and not copy_reason)
            verb = "Couper" if command_id == "edit.cut" else "Copier"
            self.command_registry.action(command_id).setToolTip(copy_reason or f"{verb} les corps entiers sélectionnés")
        self.command_registry.set_enabled("edit.paste", editable and not editing and self.object_clipboard.available)
        for command in self.command_registry:
            if command.implemented and command.category in ("Affichage", "Sélection"):
                self.command_registry.set_enabled(command.id, ready or command.id == "view.cancel")
        self.command_registry.set_enabled("object.frame", ready and any(obj.shape is not None for obj in objects))
        self.command_registry.set_enabled("view.background", editable)
        self.command_registry.set_enabled("object.visibility", editable and bool(objects))
        self.command_registry.set_enabled("object.rename", editable and len(objects) == 1)
        from .core.reference_objects import DatumPlaneObject
        self.command_registry.set_enabled("object.delete", editable and any(obj.plane is None or isinstance(obj, DatumPlaneObject) for obj in objects))
        bodies = [obj for obj in objects if obj.kind is ObjectType.BODY]
        body_ids = {obj.id for obj in bodies}
        self.command_registry.set_enabled("object.show", editable and any(not obj.visible for obj in objects))
        self.command_registry.set_enabled("object.hide", editable and any(obj.visible for obj in objects))
        self.command_registry.set_enabled("object.isolate", editable and bool(bodies) and any(
            obj.visible != (obj.id in body_ids) for obj in document.objects.values()))
        self.command_registry.set_enabled("object.show_all", editable and any(
            not obj.visible and obj.kind is ObjectType.BODY for obj in document.objects.values()))
        self.command_registry.set_enabled("object.transparency", editable and bool(bodies))
        self.command_registry.action("object.transparency").setChecked(bool(bodies) and all(obj.transparency == 0.5 for obj in bodies))
        self.command_registry.set_enabled("object.properties", ready and bool(objects) and not self.tool_sessions.active)
        for command_id, available, label, detail in (
                ("edit.undo", history.can_undo if history is not None else False, "Annuler", history.undo_text if history is not None else ""),
                ("edit.redo", history.can_redo if history is not None else False, "Rétablir", history.redo_text if history is not None else "")):
            action = self.command_registry.action(command_id)
            action.setText(f"{label} — {detail}" if detail else label)
            action.setToolTip(action.text())
            self.command_registry.set_enabled(command_id, editable and available)
        self.command_registry.set_enabled("view.normal_to_plane",
            ready and (editing or (len(objects) == 1 and objects[0].plane is not None)))
        if editing:
            # Final policy pass: regular availability refreshes must not unlock
            # commands that mutate parent 3D objects. Camera navigation stays free.
            for command in self.command_registry:
                if (command.id.startswith("object.") or
                        command.id in ("view.background",
                                       "edit.copy", "edit.cut", "edit.paste", "settings.preferences", "window.reset_layout")):
                    command.action.setEnabled(False)
            for command_id in ("object.delete", "edit.copy", "edit.cut", "edit.paste"):
                self.command_registry.set_enabled(command_id, self.tool_sessions.editor_command_enabled(command_id))
            for command_id in ("edit.copy", "edit.cut"):
                self.command_registry.action(command_id).setToolTip("Entités de l’éditeur actif ; champs texte prioritaires.")
        self.view_tools.setEnabled(ready)
        self.command_registry.set_enabled("window.view_tools", document is not None)
        if viewport is not None:
            self.command_registry.action(RENDER_COMMANDS[viewport.render_mode]).setChecked(True)
            self.render_button.setIcon(icon("wireframe" if viewport.render_mode == "Filaire" else "body"))
            self.render_button.setToolTip(f"Style d’affichage : {viewport.render_mode}")
        self.manager_pane.update_selection(objects, document.length_unit if document is not None else "mm",
                                           viewport.selection.items() if viewport is not None and viewport.selection else (),
                                           self.preferences.length_unit)
        if self.plugins is not None:
            self.plugins.refresh()

    def cancel_view(self):
        if self.tool_sessions.active:
            self.tool_sessions.cancel()
            return
        if self.viewport is not None:
            self.viewport.cancel_gesture()
            self.viewport.exit_plane()

    def standard_view(self, name):
        if self.viewport is not None and self.viewport.camera:
            self.viewport.stop_camera_animation()
            self.viewport.cancel_gesture()
            if not self.tool_sessions.editing:
                self.viewport.exit_plane()
            self.viewport.camera.standard(name)

    def fit_all(self):
        if self.viewport is not None and self.viewport.camera:
            self.viewport.stop_camera_animation()
            self.viewport.cancel_gesture()
            self.viewport.camera.fit()

    def zoom_window(self):
        self.viewport.stop_camera_animation()
        self.viewport.cancel_gesture()
        self.viewport.window_zoom = True
        self.viewport.setCursor(Qt.CursorShape.CrossCursor)
        self.hint.setText("Zoom fenêtre : tracer un rectangle avec le bouton gauche")

    def frame_selected(self):
        if self.selected_ids:
            self.viewport.frame_objects(self.selected_ids)

    def toggle_selected(self):
        document = self.viewport.document
        document.set_visibilities({key: not document.objects[key].visible for key in self.selected_ids})

    def set_selected_visibility(self, visible):
        self.viewport.document.set_visibilities({key: visible for key in self.selected_ids})

    def isolate_selected(self):
        document = self.viewport.document
        document.isolate([key for key in self.selected_ids if document.objects[key].kind is ObjectType.BODY])

    def show_all_bodies(self):
        document = self.viewport.document
        document.set_visibilities({key: True for key, obj in document.objects.items() if obj.kind is ObjectType.BODY},
                                  title="Afficher tous les corps")

    def toggle_transparency(self):
        document = self.viewport.document
        bodies = [document.objects[key] for key in self.selected_ids if document.objects[key].kind is ObjectType.BODY]
        value = 0. if all(obj.transparency == 0.5 for obj in bodies) else 0.5
        document.set_transparencies({obj.id: value for obj in bodies})

    def show_properties(self):
        self.command_registry.action("window.tree").setChecked(True)
        self.manager_pane.setCurrentWidget(self.manager_pane.properties_stack)

    def delete_selected(self):
        if self.tool_sessions.editing:
            self.tool_sessions.execute_editor_command("object.delete")
            return
        document = self.viewport.document
        from .core.reference_objects import DatumPlaneObject
        document.remove_objects([key for key in self.selected_ids if document.objects[key].plane is None or isinstance(document.objects[key], DatumPlaneObject)])

    def clipboard_selection_error(self):
        if self.viewport is None or not self.selected_ids:
            return "Sélectionnez des corps entiers à copier."
        items = self.viewport.selection.items() if self.viewport.selection else ()
        if any(item.topology not in ("Objet", "Solide") or
               (item.topology == "Solide" and not item.shape.IsSame(
                   self.viewport.document.objects[item.object_id].shape)) for item in items):
            return "La copie de sous-formes n’est pas prise en charge ; sélectionnez les objets entiers."
        for key in self.selected_ids:
            obj = self.viewport.document.objects[key]
            if obj.plane is not None:
                return "Les plans de référence ne peuvent pas être copiés ou coupés."
            if obj.kind is not ObjectType.BODY:
                return "La copie d’objets logiques n’est pas encore disponible."
            try:
                self.object_types.check_clipboard_object(obj)
            except ValueError as exc:
                return str(exc)
        return ""

    def copy_selected(self, *, cut=False):
        if self.tool_sessions.editing:
            self.tool_sessions.execute_editor_command("edit.cut" if cut else "edit.copy")
            return
        if self.documents.blocked or self.viewport is None:
            return
        try:
            reason = self.clipboard_selection_error()
            if reason:
                raise ClipboardError(reason)
            count = self.object_clipboard.copy(self.viewport.document, self.selected_ids, cut=cut)
            self.hint.setText(f"{count} objet(s) {'coupé(s)' if cut else 'copié(s)'} dans le presse-papiers.")
        except ClipboardError as exc:
            self.services.report_problem(str(exc))
        finally:
            # Native clipboard notifications may arrive on the next Qt turn.
            self.update_action_states()

    def paste_objects(self):
        if self.tool_sessions.editing:
            self.tool_sessions.execute_editor_command("edit.paste")
            return
        if self.documents.blocked or self.viewport is None:
            return
        try:
            ids = self.object_clipboard.paste(self.viewport.document)
            self.hint.setText(f"{len(ids)} objet(s) collé(s). " + self.object_clipboard.last_message)
        except ClipboardError as exc:
            self.services.report_problem(str(exc))

    def undo_document(self):
        self.viewport.document.history.undo()

    def redo_document(self):
        self.viewport.document.history.redo()

    def edit_selected_name(self):
        if len(self.selected_ids) == 1:
            self.command_registry.action("window.tree").setChecked(True)
            self.manager_pane.setCurrentIndex(0)
            self.manager_pane.tree_filter.clear()
            item = self.tree_items[self.selected_ids[0]]
            self.tree.setCurrentItem(item, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
            self.tree.scrollToItem(item)
            self.tree.editItem(item, 0)

    def rename_object(self, object_id, name):
        try:
            self.viewport.document.rename(object_id, name)
        except (ValueError, RuntimeError):
            self.tree_adapter.refresh(object_id)
            self.services.report_problem("Renommage refusé : nom vide ou opération documentaire en cours.")

    def normal_to_selected(self):
        if self.tool_sessions.editing:
            self.viewport.stop_camera_animation()
            self.viewport.cancel_gesture()
            self.viewport.camera.normal_to(self.tool_sessions.editor.workplane, align_axes=True)
            return
        if len(self.selected_ids) == 1:
            plane = self.viewport.document.objects[self.selected_ids[0]].plane
            if plane:
                self.viewport.enter_plane(plane)

    def apply_render(self, name):
        self.command_registry.action(RENDER_COMMANDS[name]).setChecked(True)
        self.viewport.set_render_mode(name)
        self.render_button.setIcon(icon("wireframe" if name == "Filaire" else "body"))
        self.render_button.setToolTip(f"Style d’affichage : {name}")

    def background(self):
        selected = QColorDialog.getColor(parent=self, title="Fond du viewport",
            options=QColorDialog.ColorDialogOption.DontUseNativeDialog)
        if selected.isValid() and self.viewport.camera:
            self.apply_preferences(replace(self.preferences, background_top=selected.name(), background_bottom=selected.name()))

    def object_context_menu(self, *, selection=True):
        menu = QMenu(self)
        document = self.documents.active_document
        objects = [document.objects[key] for key in self.selected_ids] if document is not None and selection else []
        if not objects:
            commands = ("view.fit", "object.show_all", "edit.paste")
        else:
            items = self.viewport.selection.items() if self.viewport.selection else ()
            if any(item.topology in ("Face", "Arête", "Sommet") for item in items):
                menu.addSection("Actions sur les objets parents")
            commands = ["object.frame", "object.show", "object.hide"]
            from .core.feature_objects import ConstructionFeature
            if all(isinstance(obj, ConstructionFeature) for obj in objects):
                for key in ("disable_features", "enable_features", "history_before", "history_after"):
                    command_id = "cadastrophe.modeling." + key
                    try:
                        self.command_registry.action(command_id)
                    except KeyError:
                        continue
                    commands.append(command_id)
            if any(obj.kind is ObjectType.BODY for obj in objects):
                commands += ["object.isolate", "object.transparency"]
                try:
                    self.command_registry.action("cadastrophe.measurements.material")
                except KeyError:
                    pass
                else:
                    commands.append("cadastrophe.measurements.material")
                from .core.feature_objects import BodyObject
                for body in objects:
                    if not isinstance(body, BodyObject) or not body.features:
                        continue
                    functions = menu.addMenu(f"Fonctions de {body.name}")
                    for feature_id in body.features:
                        feature = document.objects.get(feature_id)
                        if not isinstance(feature, ConstructionFeature):
                            continue
                        feature_menu = functions.addMenu(feature.name)
                        for key in ("edit_feature", "history_before", "history_after"):
                            command_id = "cadastrophe.modeling."+key
                            try:
                                shared = self.command_registry.action(command_id)
                            except KeyError:
                                continue
                            # Bind an explicit recipe target, then invoke the shared command.
                            action = feature_menu.addAction(shared.icon(), shared.text())
                            action.setData((feature_id, command_id))
                            available = self.services.can_start_tool
                            if key == "edit_feature":
                                available = available and feature.enabled and feature.id not in document.history_context.display()[1]
                            action.setEnabled(available)
                            def invoke(_checked=False, target=feature_id, command=command_id, source=document):
                                if self.services.active_document is source and target in source.objects and self.services.can_start_tool:
                                    self.services.select_objects((target,))
                                    self.command_registry.execute(command)
                            action.triggered.connect(invoke)
            from .core.reference_objects import DatumPlaneObject
            if any(obj.kind is not ObjectType.REFERENCE_PLANE or isinstance(obj, DatumPlaneObject) for obj in objects):
                commands += ["object.delete"]
            if len(objects) == 1:
                commands += ["object.rename"]
                binding = self.object_presentations.binding(objects[0])
                if binding is not None and binding.edit_command:
                    commands += [binding.edit_command]
                from .core.feature_objects import ConstructionFeature, BodyObject, SketchSourceObject
                if isinstance(objects[0], (ConstructionFeature, BodyObject, SketchSourceObject, DatumPlaneObject)):
                    for key in ("repair_references", "select_parents", "select_consumers", "select_error_sources"):
                        command_id = "cadastrophe.modeling." + key
                        try:
                            self.command_registry.action(command_id)
                        except KeyError:
                            continue
                        commands += [command_id]
                if objects[0].plane is not None:
                    commands += ["view.normal_to_plane"]
            commands += ["edit.copy", "edit.cut", "edit.paste", "object.properties", "object.show_all"]
        try:
            self.command_registry.action("cadastrophe.modeling.history_end")
        except KeyError:
            pass
        else:
            commands = [*commands, "cadastrophe.modeling.history_end"]
        for command_id in commands:
            menu.addAction(self.command_registry.action(command_id))
        return menu

    def tree_menu(self, point):
        if self.viewport is None or self.tool_sessions.editing:
            return
        item = self.tree.itemAt(point)
        object_id = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        targets_object = object_id in self.viewport.document.objects
        if targets_object:
            if object_id not in self.selected_ids:
                self.tree_selection_changed([object_id])
            self.tree.setCurrentItem(item, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
        self._show_context_menu(self.object_context_menu(selection=targets_object), self.tree.viewport().mapToGlobal(point))

    def viewport_menu(self, viewport, point):
        if (self.tool_sessions.editing or viewport is not self.viewport or viewport.selection is None
                or viewport.state.gesture is not Gesture.IDLE):
            return
        targets_object = viewport.selection.prepare_context(*viewport.pixel(point))
        self._show_context_menu(self.object_context_menu(selection=targets_object), viewport.mapToGlobal(point))

    @staticmethod
    def _show_context_menu(menu, global_point):
        try:
            menu.exec(global_point)
        finally:
            menu.deleteLater()

    def closeEvent(self, event):
        if self._closing:
            super().closeEvent(event)
            return
        self.computations.cancel()
        if self.tool_sessions.active:
            self.tool_sessions.end_edit() if self.tool_sessions.editing else self.tool_sessions.cancel()
        if self.documents.blocked:
            self.hint.setText("Terminez ou annulez l’opération documentaire avant de fermer.")
            event.ignore()
            return
        # Resolve every confirmation first: Cancel leaves all tabs and views intact.
        for document in self.documents.documents:
            if document.is_modified and not self.confirm_discard(document):
                event.ignore()
                return
        if self.documents.blocked:
            event.ignore()
            return
        if self.preference_store.writable:
            try:
                self.preference_store.save(self.preferences, self.capture_layout())
            except (ValueError, OSError) as exc:
                log.warning("Disposition non enregistrée : %s", exc)
        self._closing = True
        for document in self.documents.documents:
            self.documents.remove(document.id)
        if self.recovery is not None:
            self.recovery.shutdown()
        if self.plugins is not None:
            self.plugins.shutdown()
        super().closeEvent(event)


def run():
    with DiagnosticSession(default_log_path()) as diagnostics:
        app = QApplication(sys.argv)
        apply_theme(app)
        app.setApplicationName("CADastrophe")
        window = MainWindow(preferences_path=default_preferences_path(),
                            recovery_directory=default_recovery_directory())
        diagnostics.notify = window.services.report_problem
        window.show()
        QTimer.singleShot(0, partial(window.manage_recovery, only_if_available=True))
        return app.exec()
