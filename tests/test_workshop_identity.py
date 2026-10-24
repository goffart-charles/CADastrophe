"""Native previews and action continuity for CADastrophe's visual identity."""
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QToolButton

from cadastrophe.ui.themes import LIGHT, apply_theme
from test_shell import qt_app, window


def capture(window, path):
    """Capture Qt plus the native WGL viewport, which QWidget.grab omits."""
    viewport = window.viewport
    native = path.with_name(path.stem + "-viewport.png")
    assert viewport.view.Dump(str(native))
    image = window.grab()
    painter = QPainter(image)
    painter.drawImage(QRect(viewport.mapTo(window, QPoint()), viewport.size()), QImage(str(native)))
    tools = window.view_tools
    if tools.isVisible():
        painter.drawPixmap(tools.mapTo(window, QPoint()), tools.grab())
    painter.end()
    assert image.save(str(path))


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_workshops_keep_actions_and_previews_in_both_themes(window, qt_app, theme):
    window.apply_preferences(replace(window.preferences, theme=theme))
    window.resize(1440, 900)
    window.show()
    qt_app.processEvents()
    output = Path("artifacts")
    output.mkdir(exist_ok=True)
    try:
        text = " ".join(command.action.text() for command in window.command_registry)
        assert all(name not in text for name in ("Instant2D", "Instant3D", "CommandManager", "Cotation intelligente",
                                                "Esquisse rapide", "Cotes en mouvement"))
        assert window.command_registry.command("modeling.extrude").label == "Ajout par extrusion"
        assert window.command_registry.command("modeling.cut_extrude").label == "Découpe extrudée"
        assert "modeling.instant3d" not in {command.id for command in window.command_registry}
        volumes = window.command_manager.widget(0).widget()
        assert "Historique" not in [volumes.layout().itemAt(i).widget().accessibleName()
                                   for i in range(volumes.layout().count()-1)]
        assert {"cadastrophe.sketch.instant2d", "cadastrophe.sketch.rapid"}.isdisjoint(
            command.id for command in window.command_registry)
        page = window.command_manager.widget(1).widget()
        groups = [page.layout().itemAt(i).widget().accessibleName() for i in range(page.layout().count()-1)]
        assert groups == ["Session", "Tracer", "Définir", "Finitions", "Motifs"]
        capture(window, output / f"cadastrophe-volumes-{theme}.png")

        plane = next(obj for obj in window.viewport.document.objects.values() if obj.plane)
        window.tree.setCurrentItem(window.tree_items[plane.id])
        assert window.command_registry.execute("cadastrophe.sketch.new")
        qt_app.processEvents()
        for mode, expected in (("dimension", "Cotes au clic"), ("relations", "Contraintes d’esquisse")):
            action = window.command_registry.action("cadastrophe.sketch." + mode)
            button = next(b for b in window.command_manager.findChildren(QToolButton) if b.defaultAction() is action)
            window.command_manager.currentWidget().ensureWidgetVisible(button)
            button.click()
            qt_app.processEvents()
            assert window.tool_sessions.editor.context_title.text() == expected
        window.command_manager.currentWidget().horizontalScrollBar().setValue(0)
        capture(window, output / f"cadastrophe-esquisse-{theme}.png")
        assert window.command_registry.execute("cadastrophe.sketch.finish")
    finally:
        apply_theme(qt_app, LIGHT)


def test_plugin_icons_are_valid_svg():
    # The existing HiDPI suite covers shell icons; include contributed artwork.
    for path in Path("plugins").glob("*/icons/*.svg"):
        assert QSvgRenderer(str(path)).isValid(), str(path)
