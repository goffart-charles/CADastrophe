"""Integrated CAD panels and native child controls above the OCCT surface."""
from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QTabWidget,
    QToolButton, QVBoxLayout, QWidget, QStackedWidget, QScrollArea)

from .icons import icon
from ..document import ObjectType


def material_text(obj):
    if obj.kind is not ObjectType.BODY:
        return ""
    material = obj.material
    return (f"\nMatériau : {material.name}\nMasse volumique : {material.density_kg_m3:g} kg/m³"
            if material is not None else "\nMatériau : non affecté")


class ActionStrip(QFrame):
    """A fixed row of shared actions, with no toolbar/docking machinery."""
    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.setObjectName(name)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(5, 2, 5, 2)
        self.row.setSpacing(1)

    def add_action(self, action):
        self.addAction(action)
        button = QToolButton(self)
        button.setDefaultAction(action)
        button.setIconSize(QSize(20, 20))
        button.setAutoRaise(True)
        self.row.addWidget(button)
        return button

    def add_menu(self, title, icon_name, menu):
        button = QToolButton(self)
        button.setIcon(icon(icon_name))
        button.setIconSize(QSize(20, 20))
        button.setToolTip(title)
        button.setAccessibleName(title)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setMenu(menu)
        self.row.addWidget(button)
        return button

    def add_separator(self):
        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.VLine)
        line.setProperty("role", "stripSeparator")
        self.row.addWidget(line)


class ViewTools(ActionStrip):
    def __init__(self, viewport):
        super().__init__("viewTools", viewport)
        # OCCT draws directly into a native window. Give the overlay its own
        # child window so its controls stay visible above the OpenGL surface.
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setAccessibleName("Outils de vue")
        viewport.installEventFilter(self)

    def bind_viewport(self, viewport):
        self.parentWidget().removeEventFilter(self)
        self.setParent(viewport)
        viewport.installEventFilter(self)
        self.reposition()

    def reposition(self):
        self.adjustSize()
        viewport = self.parentWidget()
        self.move(max(8, viewport.width() - self.width() - 12),
                  max(8, viewport.height() - self.height() - 12))
        self.raise_()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.reposition()
        return super().eventFilter(watched, event)


class ManagerPane(QTabWidget):
    def __init__(self, tree, parent=None):
        super().__init__(parent)
        self.setObjectName("managerPane")
        self.setAccessibleName("Gestionnaire du document")
        self.setDocumentMode(True)
        self.setTabPosition(QTabWidget.TabPosition.West)
        # Preserve the useful form width in addition to the vertical tab rail.
        self.setMinimumWidth(300)
        self.setIconSize(QSize(20, 20))
        self.setUsesScrollButtons(True)
        page = QWidget()
        row = QVBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.tree_filter = QLineEdit()
        self.tree_filter.setObjectName("treeFilter")
        self.tree_filter.setPlaceholderText("Filtrer l’arbre de construction")
        self.tree_filter.setAccessibleName("Filtrer l’arbre de construction")
        self.tree_filter.setClearButtonEnabled(True)
        self.tree_filter.addAction(icon("filter"), QLineEdit.ActionPosition.LeadingPosition)
        self.tree_filter.textChanged.connect(self.filter_tree)
        self.tree = tree
        row.addWidget(self.tree_filter)
        row.addWidget(tree, 1)
        self._add_page(page, "tree", "Arbre de construction")
        properties, layout = self._page("Propriétés")
        self.selection_name = QLabel("Aucune sélection")
        self.selection_name.setTextFormat(Qt.TextFormat.PlainText)
        self.selection_name.setWordWrap(True)
        self.selection_detail = QLabel("Sélectionnez un corps ou un plan dans l’arbre ou la vue 3D.")
        self.selection_detail.setTextFormat(Qt.TextFormat.PlainText)
        self.selection_detail.setWordWrap(True)
        layout.addWidget(self.selection_name)
        layout.addWidget(self.selection_detail)
        layout.addStretch()
        self.properties_stack = QStackedWidget()
        properties_scroll = QScrollArea()
        properties_scroll.setWidgetResizable(True)
        properties_scroll.setFrameShape(QFrame.Shape.NoFrame)
        properties_scroll.setWidget(properties)
        self.properties_stack.addWidget(properties_scroll)
        self._add_page(self.properties_stack, "properties", "Propriétés de la sélection")
        from .configurations import ConfigurationsPanel
        self.configurations = ConfigurationsPanel()
        configuration_scroll = QScrollArea()
        configuration_scroll.setWidgetResizable(True)
        configuration_scroll.setFrameShape(QFrame.Shape.NoFrame)
        configuration_scroll.setWidget(self.configurations)
        self._add_page(configuration_scroll, "configurations", "Configurations")
        appearance, self.appearance_layout = self._page("Affichage")
        self._add_page(appearance, "palette", "Affichage et apparences")

    def _add_page(self, page, icon_name, title):
        index = self.addTab(page, icon(icon_name), "")
        self.setTabToolTip(index, title)
        self.setTabWhatsThis(index, title)
        self.tabBar().setAccessibleTabName(index, title)

    @staticmethod
    def _page(title):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        heading = QLabel(title)
        heading.setProperty("role", "panelTitle")
        layout.addWidget(heading)
        return page, layout

    def filter_tree(self, text):
        text = text.casefold().strip()

        def visit(item, ancestor_matches=False):
            matches = ancestor_matches or text in item.text(0).casefold()
            children = [visit(item.child(i), matches) for i in range(item.childCount())]
            visible = matches or any(children)
            item.setHidden(not visible)
            return visible

        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))

    def update_selection(self, objects, length_unit="mm", items=(), display_unit="mm"):
        if not objects:
            self.selection_name.setText("Aucune sélection")
            self.selection_detail.setText("Sélectionnez un corps ou un plan dans l’arbre ou la vue 3D.")
        elif len(objects) == 1:
            obj = objects[0]
            self.selection_name.setText(obj.name)
            self.selection_detail.setText(
                f"{obj.kind.label}\nUUID : {obj.id}\n{'Visible' if obj.visible else 'Masqué'}"
                f"\nTransparence : {obj.transparency * 100:g} %"
                f"\nCouleur RGB : {', '.join(f'{component:g}' for component in obj.color)}"
                f"\nRévision : {obj.revision}\nUnité interne : {length_unit}\nAffichage : {display_unit}"
                + material_text(obj)
                + (f"\nSélection : {', '.join(dict.fromkeys(item.topology for item in items))}" if items else ""))
        else:
            self.selection_name.setText(f"{len(objects)} objets sélectionnés")
            self.selection_detail.setText("\n\n".join(
                f"{obj.name} — {obj.kind.label}\n{'Visible' if obj.visible else 'Masqué'} · Transparence {obj.transparency * 100:g} %" + material_text(obj)
                for obj in objects))
