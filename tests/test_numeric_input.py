"""Real decimal keystrokes, locale independence and retained exact model units."""
import pytest
from PySide6.QtCore import QLocale, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout

from cadastrophe.ui.numeric_input import DecimalSpinBox
from cadastrophe.ui.equation_input import EquationSpinBox
from cadastrophe.ui.length_input import LengthSpinBox
from cadastrophe.core.units import DisplayUnits
from cadastrophe.scene import new_document


@pytest.mark.parametrize("locale", ("fr_FR", "en_US"))
@pytest.mark.parametrize("separator", (".", ","))
def test_decimal_fields_accept_both_separators_with_native_typing(locale, separator):
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    layout = QVBoxLayout(window)
    fields = (DecimalSpinBox(), EquationSpinBox(), LengthSpinBox(DisplayUnits('cm', 3), -10000., 10000., 10.))
    for field in fields:
        field.setLocale(QLocale(locale))
        field.setDecimals(6)
        field.setRange(-1000., 1000.)
        layout.addWidget(field)
    window.show()
    app.processEvents()
    try:
        for field in fields:
            field.setFocus()
            field.lineEdit().selectAll()
            QTest.keyClicks(field.lineEdit(), '12'+separator+'75')
            QTest.keyClick(field, Qt.Key.Key_Tab)
            field.interpretText()
            assert field.value() == pytest.approx(12.75), field.lineEdit().text()
        assert fields[-1].internal_value == pytest.approx(127.5)
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("separator", (".", ","))
def test_equation_decimal_separator_recalculates_without_changing_saved_expression(separator):
    app = QApplication.instance() or QApplication([])
    doc = new_document()
    field = EquationSpinBox()
    field.setRange(-100., 100.)
    field.setDecimals(6)
    field.bind_equations(doc, 'depth')
    text = '=2'+separator+'5/2'
    field.lineEdit().setText(text)
    field.lineEdit().textEdited.emit(text)
    assert field.hasAcceptableInput()
    field.interpretText()
    assert field.value() == pytest.approx(1.25)
    assert field.equation_expression().text == text
    field.deleteLater()
    doc.deleteLater()
    app.processEvents()
