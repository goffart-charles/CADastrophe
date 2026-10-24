"""UI adapter for plugin contributions, with no plugin imports of MainWindow."""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt

from .icons import icon


class PluginUiHost:
    def __init__(self, registry, command_manager, manager_pane, menu, presentations=None, status_bar=None):
        self.registry, self.command_manager = registry, command_manager
        self.manager_pane, self.menu = manager_pane, menu
        self._panels = {}
        self.presentations = presentations
        self.status_bar = status_bar

    def add_status(self):
        label = QLabel(self.status_bar)
        label.setTextFormat(Qt.TextFormat.PlainText)
        if self.status_bar is not None:
            self.status_bar.addPermanentWidget(label)
        def cleanup():
            if self.status_bar is not None:
                self.status_bar.removeWidget(label)
            label.hide()
            label.deleteLater()
        return label, cleanup

    def add_action(self, command_id, label, callback, shortcuts):
        action = self.registry.register(command_id, label, "Extensions", callback, shortcuts=shortcuts)
        return action

    def remove_action(self, command_id):
        self.registry.unregister(command_id)

    def bind_action_slot(self, slot, action):
        if not any(command.id == slot for command in self.registry):
            return None
        return self.registry.bind_placeholder(slot, action)

    @staticmethod
    def action_icon(name):
        return icon(name)

    def add_ribbon(self, tab, title, actions, *, columns=None):
        row = self.command_manager.add_group(tab, title)
        try:
            for column in columns or tuple((action,) for action in actions):
                if len(column) == 1:
                    self.command_manager.add_action(row, column[0])
                else:
                    self.command_manager.add_column(row, column)
        except Exception:
            self.command_manager.remove_group(row)
            raise
        return lambda: self.command_manager.remove_group(row)

    def add_panel(self, title, factory, services, *, panel_id=None):
        container = QWidget(self.manager_pane)
        row = QVBoxLayout(container)
        try:
            widget = factory(services, container)
            if not isinstance(widget, QWidget) or widget is container:
                raise ValueError("La fabrique du panneau doit retourner un QWidget enfant.")
            row.addWidget(widget)
            index = self.manager_pane.addTab(container, icon("properties"), "")
            self.manager_pane.setTabToolTip(index, title)
            self.manager_pane.tabBar().setAccessibleTabName(index, title)
            if panel_id is not None:
                self._panels[panel_id] = container
        except Exception:
            container.deleteLater()
            raise

        def remove():
            self._panels.pop(panel_id, None)
            self.manager_pane.removeTab(self.manager_pane.indexOf(container))
            container.hide()
            container.deleteLater()

        return remove

    def show_panel(self, panel_id):
        container = self._panels[panel_id]
        if any(command.id == "window.tree" for command in self.registry):
            self.registry.action("window.tree").setChecked(True)
        else:
            self.manager_pane.show()
        self.manager_pane.setCurrentWidget(container)
