"""Window-scoped action registry. This is not document undo/history."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
import logging
import re
import unicodedata

from PySide6.QtCore import QObject, QEvent, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QWidget, QLineEdit, QTextEdit,
    QPlainTextEdit, QAbstractSpinBox, QComboBox)

log = logging.getLogger(__name__)


def _search_text(value):
    value = unicodedata.normalize("NFKD", value.casefold().replace("œ", "oe").replace("æ", "ae"))
    return " ".join(re.sub(r"[^\w]+", " ", "".join(
        char for char in value if not unicodedata.combining(char))).split())


class ShortcutConflictError(ValueError):
    """A binding would shadow another command or a multi-key prefix."""


@dataclass(frozen=True)
class Command:
    id: str
    category: str
    action: QAction
    callback: Callable[[], None] | None
    implemented: bool

    @property
    def label(self) -> str:
        return self.action.text()

    @property
    def enabled(self) -> bool:
        return self.action.isEnabled()

    @property
    def shortcuts(self) -> tuple[str, ...]:
        return tuple(key.toString(QKeySequence.SequenceFormat.PortableText)
                     for key in self.action.shortcuts())


class CommandRegistry(QObject):
    """Own one QAction per id; reserve shortcuts even for unavailable tools.

    Bind/rebind through this registry, not QAction.setShortcuts. Conflicts are
    rejected atomically (including prefixes), never resolved by stealing keys.
    """
    changed = Signal()

    def __init__(self, window: QWidget):
        super().__init__(window)
        self.window = window
        self._commands: dict[str, Command] = {}
        self._bindings: dict[str, tuple[QKeySequence, ...]] = {}
        self._defaults = {}
        QApplication.instance().installEventFilter(self)

    def __iter__(self):
        return iter(self._commands.values())

    def action(self, command_id: str) -> QAction:
        return self._commands[command_id].action

    def command(self, command_id: str) -> Command:
        return self._commands[command_id]

    def bind_placeholder(self, command_id, implementation):
        """Temporarily implement a shell slot, retaining its ribbon QAction."""
        previous = self.command(command_id)
        if previous.implemented:
            raise ValueError("Cette commande possède déjà une implémentation.")
        action = previous.action
        old = action.text(), action.toolTip(), action.statusTip()
        self._commands[command_id] = replace(previous, implemented=True, callback=implementation.trigger)

        def refresh():
            action.setEnabled(implementation.isEnabled())
            action.setText(implementation.text())
            action.setToolTip(implementation.toolTip())
            action.setStatusTip(implementation.statusTip())

        implementation.changed.connect(refresh)
        refresh()

        def cleanup():
            implementation.changed.disconnect(refresh)
            self._commands[command_id] = previous
            action.setEnabled(False)
            action.setText(old[0])
            action.setToolTip(old[1])
            action.setStatusTip(old[2])
            self.changed.emit()

        return cleanup

    def search(self, query: str = "") -> tuple[Command, ...]:
        """Search current metadata, including disabled commands and plugins."""
        normalized = _search_text(query)
        terms, matches = normalized.split(), []
        for command in self:
            label = _search_text(command.label.replace("&", ""))
            category = _search_text(command.category)
            haystack = _search_text(" ".join((label, category, command.id, *command.shortcuts)))
            if all(term in haystack for term in terms):
                relevance = (0 if label == normalized else 1 if label.startswith(normalized)
                             else 2 if normalized in label else 3) if terms else 0
                matches.append(((relevance, not (command.implemented and command.enabled),
                                 category, label, command.id), command))
        return tuple(command for _, command in sorted(matches, key=lambda item: item[0]))

    def register(self, command_id: str, label: str, category: str,
                 callback: Callable[[], None] | None = None, *,
                 shortcuts: Iterable[str] = (), enabled: bool = True,
                 reason: str = "") -> QAction:
        self._validate_id(command_id)
        keys = self._validated_keys(command_id, shortcuts)
        action = QAction(label, self)
        action.setToolTip(reason or label)
        action.setStatusTip(reason or label)
        action.setEnabled(enabled and callback is not None)
        command = Command(command_id, category, action, callback, callback is not None)
        self._install(command, keys)
        action.triggered.connect(lambda checked=False: self._invoke(command_id))
        return action

    def adopt(self, command_id: str, action: QAction, category: str) -> QAction:
        """Register a native Qt toggle without duplicating its built-in slot."""
        self._validate_id(command_id)
        if any(command.action is action for command in self):
            raise ValueError("QAction already registered")
        keys = self._validated_keys(command_id, [key.toString(
            QKeySequence.SequenceFormat.PortableText) for key in action.shortcuts()])
        self._install(Command(command_id, category, action, None, True), keys)
        return action

    def _validate_id(self, command_id: str):
        if not command_id or command_id in self._commands:
            raise ValueError(f"Empty or duplicate command id: {command_id!r}")

    def _install(self, command: Command, keys: tuple[QKeySequence, ...]):
        self._commands[command.id] = command
        command.action.setObjectName(command.id)
        command.action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        command.action.setShortcuts(list(keys))
        self._bindings[command.id] = keys
        self._defaults[command.id] = keys
        self.window.addAction(command.action)
        command.action.changed.connect(self.changed.emit)
        self.changed.emit()

    @staticmethod
    def _overlap(first: QKeySequence, second: QKeySequence) -> bool:
        no_match = QKeySequence.SequenceMatch.NoMatch
        return first.matches(second) != no_match or second.matches(first) != no_match

    def _validated_keys(self, command_id: str, shortcuts: Iterable[str], *, bindings=None) -> tuple[QKeySequence, ...]:
        if isinstance(shortcuts, str):
            raise TypeError("shortcuts must be a sequence, e.g. ('Ctrl+S',)")
        keys: list[QKeySequence] = []
        for text in shortcuts:
            key = QKeySequence.fromString(text, QKeySequence.SequenceFormat.PortableText)
            if key.isEmpty() or any(key[index].key() == Qt.Key.Key_unknown
                                    for index in range(key.count())):
                raise ValueError(f"Invalid shortcut: {text!r}")
            if key in keys:
                continue  # canonical aliases of the same binding
            for other_id, candidates in (self._bindings if bindings is None else bindings).items():
                if other_id != command_id and any(self._overlap(key, other) for other in candidates):
                    message = f"Shortcut {text!r}: {command_id} conflicts with {other_id}"
                    log.warning(message)
                    raise ShortcutConflictError(message)
            if any(self._overlap(key, other) for other in keys):
                raise ShortcutConflictError(f"Overlapping aliases for {command_id}: {text!r}")
            keys.append(key)
        return tuple(keys)

    def set_shortcuts(self, command_id: str, shortcuts: Iterable[str]):
        self.set_shortcuts_batch({command_id: shortcuts})

    def validate_shortcuts(self, changes):
        planned = dict(self._bindings)
        for command_id, keys in changes.items():
            self.action(command_id)
            planned[command_id] = self._validated_keys(command_id, keys, bindings={})
        for command_id in changes:
            self._validated_keys(command_id, [key.toString(QKeySequence.SequenceFormat.PortableText)
                                             for key in planned[command_id]], bindings=planned)
        return planned

    def set_shortcuts_batch(self, changes):
        planned = self.validate_shortcuts(changes)
        for command_id in changes:
            self._bindings[command_id] = planned[command_id]
            self.action(command_id).setShortcuts(list(planned[command_id]))

    def default_shortcuts(self):
        return {command_id: [key.toString(QKeySequence.SequenceFormat.PortableText) for key in keys]
                for command_id, keys in self._defaults.items()}

    def unregister(self, command_id: str):
        """Remove an owned contribution and release its shortcut reservations."""
        command = self._commands.pop(command_id)
        self._bindings.pop(command_id)
        self._defaults.pop(command_id)
        action = command.action
        action.setEnabled(False)
        action.setShortcuts([])
        action.triggered.disconnect()
        for widget in tuple(action.associatedObjects()):
            if isinstance(widget, QWidget):
                widget.removeAction(action)
        action.deleteLater()
        self.changed.emit()

    def set_enabled(self, command_id: str, enabled: bool):
        command = self.command(command_id)
        if enabled and not command.implemented:
            raise ValueError(f"Command not implemented: {command_id}")
        command.action.setEnabled(enabled)

    def execute(self, command_id: str) -> bool:
        command = self.command(command_id)
        if not command.enabled or not command.implemented:
            return False
        command.action.trigger()
        return True

    def _invoke(self, command_id: str):
        command = self.command(command_id)
        if command.enabled and command.callback is not None:
            try:
                command.callback()
            except Exception:
                log.exception("Command failed: %s", command_id)
                raise

    @staticmethod
    def _text_input(widget: QWidget) -> bool:
        while widget is not None:
            if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
                return True
            if isinstance(widget, QComboBox) and widget.isEditable():
                return True
            widget = widget.parentWidget()
        return False

    def eventFilter(self, watched, event):
        # Accept ShortcutOverride, NOT KeyPress: Qt still edits the text itself.
        # Scope this guard to our window, including its owned dialogs/docks.
        if event.type() == QEvent.Type.ShortcutOverride:
            focus = QApplication.focusWidget()
            if (focus is not None and (focus is self.window or self.window.isAncestorOf(focus))
                    and self._text_input(focus)):
                modifiers = event.modifiers()
                text_edit_key = (not modifiers & (Qt.KeyboardModifier.ControlModifier |
                    Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier))
                native_edit = (modifiers & Qt.KeyboardModifier.ControlModifier and
                    not modifiers & Qt.KeyboardModifier.AltModifier and event.key() in (
                        Qt.Key.Key_A, Qt.Key.Key_C, Qt.Key.Key_X, Qt.Key.Key_V,
                        Qt.Key.Key_Z, Qt.Key.Key_Y, Qt.Key.Key_Insert, Qt.Key.Key_Delete,
                        Qt.Key.Key_Backspace))
                if text_edit_key or native_edit:
                    event.accept()
                    return True
        return super().eventFilter(watched, event)
