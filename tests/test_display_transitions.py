"""Native viewer/input checks while moving between the available real screens."""
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from test_extrusion_tool import extrusion_system


def test_native_viewport_selection_survives_real_screen_moves(extrusion_system):
    w, _, _ = extrusion_system
    screens = QGuiApplication.screens()
    if len(screens) < 2:
        pytest.skip("Deux écrans réels sont nécessaires pour cette transition.")
    doc = w.viewport.document
    # The isolated fixture starts with an empty document. Add one known native
    # primitive without replacing the viewer or simulating its projection.
    from cadastrophe.document import SceneObject
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    body = SceneObject("Bloc de qualification", BRepPrimAPI_MakeBox(40., 40., 40.).Shape(), (.3, .6, .7))
    doc.add(body)
    ids = set(doc.objects)
    w.resize(900, 700)
    w.viewport.view.SetProj(1., -1., 1.)
    w.viewport.frame_objects((body.id,))
    observed = []
    for screen in (*screens, screens[0]):
        w.move(screen.availableGeometry().center()-w.rect().center())
        QTest.qWait(250)
        QApplication.processEvents()
        assert w.screen() is screen
        ratio = w.viewport.devicePixelRatioF()
        x, y = w.viewport.view.Convert(20., 20., 40.)
        point = QPoint(round(x/ratio), round(y/ratio))
        QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=point)
        items = w.viewport.selection.items()
        assert len(items) == 1 and items[0].object_id == body.id
        assert items[0].topology == "Face"
        assert set(doc.objects) == ids and doc.objects[body.id] is body
        observed.append(dict(screen=screen.name(), ratio=ratio, logical_dpi=screen.logicalDotsPerInch(),
                             viewport_pixels=list(w.viewport.pixel(point))))
    path = Path("artifacts/display-transitions.json")
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(dict(status="passed", checks=observed), indent=2)+"\n", encoding="utf-8")
