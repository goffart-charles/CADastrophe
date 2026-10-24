"""Public plugin API v1. Import this module, never MainWindow internals."""
from dataclasses import dataclass
from contextlib import contextmanager
import logging
from typing import Protocol

from PySide6.QtCore import QObject, Signal

from .core.object_types import ObjectCodec
from .core.construction_state import ConfigurationIdentity, FeatureDependencies
from .core.configuration_model import Configuration, ConfigurationState, ParameterSetting
from .core.configurations import (create as create_configuration, rename as rename_configuration,
    remove as remove_configuration, activate as activate_configuration,
    set_override as set_configuration_override, validate_all as validate_configurations)
from .core.feature_model import ReferenceSlot, EvaluationDiagnostic, ReferenceResolutionError
from .core.tool_sessions import ToolSession, EditSession
from .core.units import DisplayUnits
from .core.materials import Material
from .core.variable_files import VariableSource, bind_library, export_variables, reload_variables
from .core.curve_model import CurveObject, CurveParameters
from .ui.length_input import LengthSpinBox
from .ui.equation_input import EquationSpinBox, EquationIntSpinBox, bind_fields, equation_recipe
from .core.hole_model import HoleParameters
from .core.feature_objects import HoleFeature
from .core.thread_model import ThreadParameters
from .core.feature_objects import ThreadFeature
from .core.fastener_catalog import CatalogChoice
from .ui.file_dialogs import suggested_filename, file_error_text
from .document import SceneObject, ObjectType, SelectionItem

API_VERSION = 1
log = logging.getLogger(__name__)


class Plugin(Protocol):
    def activate(self, context: "PluginContext") -> None: ...
    def deactivate(self) -> None: ...


class ApplicationServices(QObject):
    active_document_changed = Signal(object)
    state_changed = Signal()
    selection_changed = Signal(object)  # transient tuple of SelectionItem, including subshapes
    message = Signal(str)

    def __init__(self, documents):
        super().__init__(documents)
        self._documents = documents
        self._tools = None
        self._display_units = DisplayUnits()
        self._selection_provider = None
        self._clipboard = None
        self._file_dialogs = None
        self._providers = {}
        self._computations = None
        documents.active_changed.connect(self.active_document_changed.emit)
        documents.changed.connect(self.state_changed.emit)

    @property
    def active_document(self):
        return self._documents.active_document

    @property
    def documents(self):
        """Read-only open-document snapshot; state_changed signals lifetime changes."""
        return self._documents.documents

    def service(self,name):
        """Read an owned plugin service by its qualified name; never window internals."""
        try:
            return self._providers[name][1]
        except KeyError:
            raise LookupError(f"Service indisponible : {name}") from None

    @property
    def selected_ids(self):
        doc = self.active_document
        return doc.selected_ids if doc is not None else ()

    @property
    def editable(self):
        return self.active_document is not None and not self.busy and not self.editing

    @property
    def busy(self):
        return self._documents.blocked or (self._computations is not None and self._computations.busy)

    def run_computation(self, steps, title, **callbacks):
        """Run closeable native steps on successive Qt turns, with cancellation."""
        if self._computations is None:
            close = getattr(steps, "close", None)
            if close is not None:
                close()
            raise LookupError("Le service de calcul interactif est indisponible.")
        return self._computations.start(steps, title, **callbacks)

    @property
    def computation_available(self):
        return self._computations is not None and not self._computations.busy

    @property
    def computation_busy(self):
        return self._computations is not None and self._computations.busy

    @property
    def editing(self):
        return self._tools is not None and self._tools.editing

    @property
    def edit_owner(self):
        return self._tools.owner if self.editing else None

    @property
    def can_start_tool(self):
        return self._tools is not None and self._tools.available

    @property
    def can_start_from_sketch(self):
        return self._tools is not None and self._tools.can_start_from_sketch

    @property
    def display_units(self):
        return self._display_units

    @property
    def clipboard(self):
        """Verified MIME transport shared with the application's object clipboard."""
        return self._clipboard

    @property
    def file_dialogs(self):
        """Common native file pickers and project/import/export locations."""
        return self._file_dialogs

    @property
    def selection_items(self):
        """Actual native subshapes, valid only for this document/object revision."""
        document = self.active_document
        if document is None:
            return ()
        if self._selection_provider is not None:
            return tuple(self._selection_provider(document))
        return tuple(SelectionItem(key, "Plan" if document.objects[key].plane else "Objet",
                                   document.objects[key].shape, document.objects[key].revision)
                     for key in document.selected_ids)


@dataclass
class _ActionState:
    action: object
    predicate: object
    requires_document: bool
    failed: bool = False
    from_sketch: bool = False


class DocumentReservation:
    """Keep a captured document stable while an owned asynchronous job runs."""
    def __init__(self, context):
        self._context = context
        self.document = context.services.active_document
        self._closed = False
        self.document.history.reserve(self)

    @contextmanager
    def transaction(self, title):
        if self._closed:
            raise ValueError("Réservation fermée.")
        with self.document.history.commit_reserved(self, title):
            yield self.document

    def close(self):
        if not self._closed:
            self._closed = True
            self._context._reservations.discard(self)
            self.document.history.release(self)


class PluginContext:
    """Owned declarations, rolled back in reverse order on failure/unload."""
    def __init__(self, owner, services, host, types, report_error):
        self.owner, self.services = owner, services
        self._host, self._types, self._report_error = host, types, report_error
        self._cleanup, self._actions, self._keys = [], {}, set()
        self._panels = {}
        self._reservations = set()
        self._closed = False

    def _key(self, kind, key):
        from .core.object_types import identifier
        identifier(key)
        if self._closed or (kind, key) in self._keys:
            raise ValueError("Contribution fermée ou déjà déclarée.")
        self._keys.add((kind, key))
        return f"{self.owner}.{key}"

    def register_action(self, key, label, callback, *, shortcuts=(), mutates=False,
                        requires_document=True, enabled_when=None, icon=None, from_sketch=False):
        command_id = self._key("action", key)
        if not label or (callback is not None and not callable(callback)) or (enabled_when is not None and not callable(enabled_when)):
            raise ValueError("Contrat d’action invalide.")
        if mutates and not requires_document:
            raise ValueError("Une action mutante exige un document.")
        if from_sketch and (mutates or not requires_document):
            raise ValueError("Une transition d’esquisse doit lancer un outil documentaire sans mutation directe.")

        def invoke():
            state = self._actions[key]
            try:
                if mutates:
                    with self.services.active_document.history.transaction(label):
                        callback()
                else:
                    callback()
            except (Exception, SystemExit) as exc:
                log.exception("Échec de l’action %s", command_id)
                state.failed = True
                state.action.setEnabled(False)
                self._report_error(f"Action {command_id} : {exc}")

        action = self._host.add_action(command_id, label, invoke if callback is not None else None, shortcuts)
        self._actions[key] = _ActionState(action, enabled_when, requires_document, failed=callback is None, from_sketch=from_sketch)
        self._cleanup.append(lambda: self._host.remove_action(command_id))
        if icon is not None:
            action.setIcon(self._host.action_icon(icon))
        return action

    def register_ribbon(self, key, tab, title, action_keys, *, columns=None):
        self._key("ribbon", key)
        actions = tuple(self._actions[item].action for item in action_keys)
        layout = tuple(tuple(self._actions[key].action for key in column) for column in columns) if columns else None
        self._cleanup.append(self._host.add_ribbon(tab, title, actions, columns=layout))

    def bind_action_slot(self, slot, action_key):
        """Fill an existing shell ribbon position, with owned unload cleanup."""
        self._key("slot", slot)
        cleanup = self._host.bind_action_slot(slot, self._actions[action_key].action)
        if cleanup is not None:
            self._cleanup.append(cleanup)
        return cleanup is not None

    def register_document_restorer(self, key, callback):
        """Restore derived caches after all native resources and recipes load."""
        name = self._key("restorer", key)
        self._types.register_document_restorer(name, callback)
        self._cleanup.append(lambda: self._types.unregister_document_restorer(name))

    def register_status(self, key):
        """Owned permanent status label, independent of transient shell messages."""
        self._key("status", key)
        label, cleanup = self._host.add_status()
        self._cleanup.append(cleanup)
        return label

    def register_service(self,key,provider):
        """Own a named read API and remove it on failed activation or unload."""
        name = self._key("service",key)
        if provider is None or name in self.services._providers:
            raise ValueError("Service absent ou déjà enregistré.")
        self.services._providers[name] = self.owner,provider
        def cleanup():
            current = self.services._providers.get(name)
            if current is not None and current[0] == self.owner and current[1] is provider:
                del self.services._providers[name]
        self._cleanup.append(cleanup)

    def register_panel(self, key, title, factory):
        panel_id = self._key("panel", key)
        self._cleanup.append(self._host.add_panel(title, factory, self.services, panel_id=panel_id))
        self._panels[key] = panel_id

    def show_panel(self, key):
        """Reveal an owned panel without reaching into the application window."""
        if self._closed:
            raise ValueError("Contexte fermé.")
        self._host.show_panel(self._panels[key])

    def register_type(self, codec: ObjectCodec):
        if self._closed:
            raise ValueError("Contexte fermé.")
        self._types.register(self.owner, codec)
        self._cleanup.append(lambda: self._types.unregister(codec.type_id, self.owner))

    def register_presentation(self, object_class, factory, *, label, icon, edit_action=None):
        """Own a derived native presenter and optional primary tree action."""
        if self._closed:
            raise ValueError("Contexte fermé.")
        if object_class not in self._types.owned_classes(self.owner):
            raise ValueError("Une présentation doit appartenir à un type enregistré par ce plugin.")
        command_id = self._actions[edit_action].action.objectName() if edit_action is not None else None
        self._host.action_icon(icon)  # Validate assets before emitting UI changes.
        registry = self._host.presentations
        registry.register(self.owner, object_class, factory, label=label, icon=icon, edit_command=command_id)
        self._cleanup.append(lambda: registry.unregister(self.owner, object_class))

    def start_edit(self, editor: EditSession, *, new_object=None):
        if self._closed or self.services._tools is None:
            raise RuntimeError("L’hôte d’édition n’est pas disponible.")
        return self.services._tools.start_edit(self.owner, editor, new_object=new_object)

    def finish_edit(self):
        tools = self.services._tools
        return bool(tools is not None and tools.owner == self.owner and tools.end_edit())

    @property
    def edit_session(self):
        """The active persistent editor owned by this plugin, never another's."""
        tools = self.services._tools
        return tools.editor if not self._closed and tools is not None and tools.owner == self.owner else None

    def start_tool(self, tool: ToolSession):
        """Start an owned, exclusive session; use a non-mutating launch action."""
        if self._closed:
            raise ValueError("Contexte fermé.")
        if self.services._tools is None:
            raise RuntimeError("Les sessions interactives ne sont pas disponibles dans cet hôte.")
        return self.services._tools.start(self.owner, tool)

    def start_tool_from_sketch(self, factory):
        if self._closed or self.services._tools is None:
            return False
        return self.services._tools.start_from_sketch(self.owner, factory)

    def connect(self, signal, slot):
        if self._closed:
            raise ValueError("Contexte fermé.")
        signal.connect(slot)
        self._cleanup.append(lambda: signal.disconnect(slot))

    def reserve_document(self):
        """Reserve the active document; close after success, failure or cancellation."""
        if self._closed or not self.services.editable:
            raise RuntimeError("Terminez l’opération documentaire en cours.")
        reservation = DocumentReservation(self)
        self._reservations.add(reservation)
        return reservation

    def refresh(self):
        if self._closed:
            return
        for state in tuple(self._actions.values()):
            enabled = not state.failed
            try:
                enabled = enabled and (not state.requires_document or self.services.editable
                                       or (state.from_sketch and self.services.can_start_from_sketch))
                if enabled and state.predicate is not None:
                    enabled = bool(state.predicate(self.services))
            except (Exception, SystemExit) as exc:
                log.exception("Échec du prédicat de disponibilité, extension %s", self.owner)
                state.failed, enabled = True, False
                self._report_error(f"Disponibilité d’action : {exc}")
            state.action.setEnabled(enabled)

    def close(self):
        if self._closed:
            return
        if self.services._tools is not None:
            self.services._tools.cancel_owner(self.owner)
        self._closed = True
        errors = []
        cleanups = [reservation.close for reservation in tuple(self._reservations)] + list(reversed(self._cleanup))
        for cleanup in cleanups:
            try:
                cleanup()
            except Exception as exc:
                log.exception("Échec du nettoyage, extension %s", self.owner)
                errors.append(str(exc))
        self._cleanup.clear()
        self._actions.clear()
        self._panels.clear()
        if errors:
            self._report_error("Nettoyage : " + "; ".join(errors))
