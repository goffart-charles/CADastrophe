"""Visual contracts and reproducible Qt captures, without CAD model changes."""
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPalette, QIcon
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLineEdit, QComboBox, QDoubleSpinBox, QDialogButtonBox, QTabWidget,
    QToolButton, QLabel, QGridLayout, QPlainTextEdit, QTextEdit)

from cadastrophe.app import MainWindow
from cadastrophe.core.commands import CommandRegistry
from cadastrophe.ui.icons import ASSETS, icon
from cadastrophe.ui.themes import LIGHT, apply_theme, theme_tokens
from cadastrophe.ui.themes.light import palette, stylesheet
from cadastrophe.ui.property_panel import PropertyPanel


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    return app


def test_tokens_drive_palette_and_stylesheet():
    custom = replace(LIGHT, accent="#13579b", control_height=31)
    assert "#13579b" in stylesheet(custom)
    assert "min-height: 25px" in stylesheet(custom)
    assert palette(custom).color(QPalette.ColorRole.Link).name() == custom.accent
    assert palette(custom).color(QPalette.ColorRole.Base).name() == custom.panel
    assert "CadViewport" not in stylesheet(custom)
    assert "QWidget {" not in stylesheet(custom)  # no universal viewport background


def test_theme_is_global_and_idempotent(qt_app):
    manager = qt_app._cad_theme
    apply_theme(qt_app)
    assert manager is qt_app._cad_theme
    assert theme_tokens() == LIGHT
    assert qt_app.font().family() == LIGHT.font_family
    late_widget = QLineEdit()
    assert late_widget.palette().color(QPalette.ColorRole.Base).name() == LIGHT.panel
    late_widget.deleteLater()


@pytest.mark.parametrize("editor_type", [QLineEdit, QPlainTextEdit, QTextEdit])
def test_themed_text_undo_redo_and_edit_keys_without_system_clipboard(qt_app, editor_type):
    window = QMainWindow()
    editor = editor_type()
    window.setCentralWidget(editor)
    registry = CommandRegistry(window)
    calls = []
    for index, key in enumerate(("F", "Z", "Space", "Delete", "Escape", "Ctrl+A", "Ctrl+Z", "Ctrl+Y", "Ctrl+Shift+Z")):
        registry.register(f"theme.edit.{index}", key, "Test", lambda: calls.append(True), shortcuts=(key,))
    window.show()
    window.activateWindow()
    qt_app.processEvents()
    assert QTest.qWaitForWindowActive(window, 1000)
    editor.setFocus()
    text = editor.text if isinstance(editor, QLineEdit) else editor.toPlainText
    try:
        QTest.keyClicks(editor, "fz test")
        assert text() == "fz test"
        ctrl = Qt.KeyboardModifier.ControlModifier
        QTest.keyClick(editor, Qt.Key.Key_Z, ctrl)
        assert text() == ""
        QTest.keyClick(editor, Qt.Key.Key_Y, ctrl)
        assert text() == "fz test"
        QTest.keyClick(editor, Qt.Key.Key_A, ctrl)
        QTest.keyClick(editor, Qt.Key.Key_Delete)
        assert text() == ""
        QTest.keyClick(editor, Qt.Key.Key_Escape)
        assert calls == []
    finally:
        window.close()
        window.deleteLater()


@pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
def test_all_svg_icons_render_at_small_hidpi_sizes(qt_app, scale):
    for path in sorted(ASSETS.glob("*.svg")):
        assert QSvgRenderer(str(path)).isValid(), path.name
        for size in (LIGHT.icon_small, LIGHT.icon_command):
            for mode in (QIcon.Mode.Normal, QIcon.Mode.Disabled):
                pixmap = icon(path.stem).pixmap(QSize(size, size), scale, mode)
                assert not pixmap.isNull(), path.name
                assert pixmap.width() == round(size * scale), path.name
                assert pixmap.devicePixelRatio() == pytest.approx(scale)


def test_later_registry_contributions_inherit_visuals_without_callback_change(qt_app):
    window = QMainWindow()
    registry = CommandRegistry(window)
    calls = []
    action = registry.register("extension.operation", "Opération", "Extension", lambda: calls.append(True))
    assert not action.icon().isNull()
    assert registry.execute("extension.operation") and calls == [True]
    custom = registry.register("extension.custom", "Personnalisée", "Extension", lambda: None)
    supplied = icon("plane")
    custom.setIcon(supplied)
    custom.setIconText("Libellé court fourni")
    menu = window.menuBar().addMenu("Extension")
    menu.addAction(custom)
    assert custom.icon().cacheKey() == supplied.cacheKey()
    assert custom.iconText() == "Libellé court fourni"
    window.close()
    window.deleteLater()


def test_tree_visibility_style_does_not_disable_hidden_reference_selection(qt_app, discard_document_changes):
    window = MainWindow()
    window.show()
    qt_app.processEvents()
    try:
        plane = next(obj for obj in window.viewport.document.objects.values() if obj.plane)
        window.viewport.document.set_visible(plane.id, False)
        item = window.tree_items[plane.id]
        assert not item.icon(0).isNull() and item.font(0).italic()
        assert item.foreground(0).color().name() == LIGHT.muted
        window.tree.setCurrentItem(item)
        assert window.command_registry.command("view.normal_to_plane").enabled
        item.setCheckState(0, Qt.CheckState.Checked)
        assert window.viewport.document.objects[plane.id].visible and not item.font(0).italic()
        assert item.foreground(0).color().name() == LIGHT.text
        item.setCheckState(0, Qt.CheckState.Unchecked)
        assert not window.viewport.document.objects[plane.id].visible and item.font(0).italic()
        assert item.flags() & Qt.ItemFlag.ItemIsEnabled
    finally:
        window.close()
        window.deleteLater()


def test_property_visual_sections_collapse_and_emit_local_validation(qt_app):
    panel = PropertyPanel("Paramètres")
    section = panel.add_section("Dimensions")
    section.form.addRow("Longueur", QDoubleSpinBox())
    calls = []
    panel.accepted.connect(lambda: calls.append("accept"))
    panel.rejected.connect(lambda: calls.append("cancel"))
    panel.show()
    qt_app.processEvents()
    assert section.content.isVisible()
    section.header.click()
    assert section.content.isHidden()
    section.header.click()
    assert section.content.isVisible()
    panel.buttons.button(QDialogButtonBox.StandardButton.Ok).click()
    panel.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
    assert calls == ["accept", "cancel"]
    panel.close()
    panel.deleteLater()


def test_shell_visual_layout_at_multiple_resolutions(qt_app):
    window = MainWindow()
    window.show()
    qt_app.processEvents()
    try:
        for command in window.command_registry:
            assert not command.action.icon().isNull(), command.id
        output = Path("artifacts")
        output.mkdir(exist_ok=True)
        for width, height in ((800, 600), (1280, 800), (1440, 800)):
            window.resize(width, height)
            qt_app.processEvents()
            assert window.width() == width
            # Windows may cap the requested height to the available logical
            # screen at 200% DPI. Keep the actual viewport usable, while the
            # ribbon retains its logical control sizes on the smaller screen.
            assert window.viewport.height() > window.height() / 2
            assert window.command_manager.height() < max(height, window.height()) / 4
            page = window.command_manager.currentWidget()
            assert page.widget().height() <= page.viewport().height()  # titles above scrollbar, never clipped
            assert window.grab().save(str(output / f"theme-shell-{width}.png"))
        window.resize(1280, 800)
        qt_app.processEvents()
        plane = next(obj for obj in window.viewport.document.objects.values() if obj.plane)
        window.tree.setCurrentItem(window.tree_items[plane.id])
        window.command_manager.setCurrentIndex(1)
        qt_app.processEvents()
        assert window.grab().save(str(output / "theme-sketch.png"))
    finally:
        window.close()
        window.deleteLater()


def test_visual_component_gallery_capture(qt_app):
    gallery = QWidget()
    gallery.setWindowTitle("Aperçu du thème — composants visuels uniquement")
    row = QHBoxLayout(gallery)
    panel = PropertyPanel("Paramètres de l’outil")
    section = panel.add_section("Dimensions")
    length = QDoubleSpinBox()
    length.setRange(0, 10000)
    length.setValue(40)
    length.setSuffix(" mm")
    section.form.addRow("Longueur", length)
    combo = QComboBox()
    combo.addItems(["Symétrique", "Une direction"])
    section.form.addRow("Direction", combo)
    section = panel.add_section("Références")
    section.form.addRow("Plan", QLineEdit("XY"))
    row.addWidget(panel)
    right = QVBoxLayout()
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "Pièce 1")
    tabs.addTab(QWidget(), "Pièce 2")
    right.addWidget(tabs)
    states = QHBoxLayout()
    for label, state in (("Normal", "normal"), ("Actif", "checked"), ("Appuyé", "pressed"), ("Indisponible", "disabled")):
        button = QToolButton()
        button.setText(label)
        button.setIcon(icon("body"))
        button.setIconSize(QSize(24, 24))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setCheckable(True)
        button.setChecked(state == "checked")
        button.setDown(state == "pressed")
        button.setEnabled(state != "disabled")
        states.addWidget(button)
    right.addLayout(states)
    sheet = QGridLayout()
    for index, path in enumerate(sorted(ASSETS.glob("*.svg"))):
        label = QLabel()
        label.setPixmap(icon(path.stem).pixmap(QSize(16, 16)))
        label.setToolTip(path.stem)
        sheet.addWidget(label, index // 8, index % 8)
    right.addLayout(sheet)
    row.addLayout(right)
    gallery.resize(780, 420)
    gallery.show()
    qt_app.processEvents()
    Path("artifacts").mkdir(exist_ok=True)
    assert gallery.grab().save("artifacts/theme-components.png")
    gallery.close()
    gallery.deleteLater()
