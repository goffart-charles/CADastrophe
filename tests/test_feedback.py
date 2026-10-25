"""Inline errors stay distinguishable in both themes and reset after recovery."""
import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from cadastrophe.ui.feedback import ErrorLabel, FeedbackLabel, set_feedback
from cadastrophe.ui.property_panel import PropertyPanel
from cadastrophe.ui.themes import LIGHT, DARK, apply_theme


@pytest.mark.parametrize('theme', (LIGHT, DARK), ids=('light', 'dark'))
def test_inline_problem_colors_and_explicit_recovery(theme):
    app = QApplication.instance() or QApplication([])
    apply_theme(app, theme)
    panel = PropertyPanel('Essai')
    label = ErrorLabel()
    hint = FeedbackLabel()
    try:
        panel.set_validation(False, 'Géométrie impossible', 'error')
        panel.show()
        label.setText('Paramètre invalide')
        label.show()
        set_feedback(hint, 'Validation refusée')
        hint.show()
        app.processEvents()
        for widget in (panel.status, label, hint):
            assert widget.property('severity') == 'error'
            assert widget.palette().color(QPalette.ColorRole.WindowText).name() == theme.negative
        panel.set_validation(True, 'Entrée : valider', 'info')
        hint.setText('Sélection du corps')
        label.clear()
        assert panel.status.property('severity') == hint.property('severity') == label.property('severity') == 'info'
    finally:
        for widget in (panel, label, hint):
            widget.close()
            widget.deleteLater()
        apply_theme(app, LIGHT)
        app.processEvents()
