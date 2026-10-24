"""Advanced drawing tools, native edge treatments and history integration."""
from dataclasses import replace
from math import pi, sin, cos
from pathlib import Path
import gc

import pytest
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.BRepCheck import BRepCheck_Analyzer

from cadastrophe.core.feature_model import Operation, EdgeParameters
from cadastrophe.geometry import REFERENCE_PLANES
from plugins.modeling.edge_geometry import edges_of, edge_reference, apply_edges
from plugins.sketch.model import Ellipse, Spline, Vec2, Segment
from plugins.sketch.drawing import DrawingController
from plugins.sketch.native_regions import arrangement
from plugins.sketch.entity_codec import _encode_entity, _decode_entity
from plugins.sketch.corner_tool import rounded_corner
from plugins.sketch.profiles import analyze
from test_sketch_profiles import source, rectangle
from test_extrusion_tool import extrusion_system
from test_primitives import volume
from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QToolButton, QLabel


@pytest.fixture(autouse=True)
def collect_native_tool_sessions():
    yield
    # Exercise native resource destruction in this module, including cycles
    # retained by editor callbacks, while failures can still name their test.
    if QCoreApplication.instance() is not None:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


@pytest.mark.parametrize("operation,mode", [(Operation.FILLET, "symmetric"), (Operation.FILLET, "variable"),
    (Operation.CHAMFER, "symmetric"), (Operation.CHAMFER, "two_distances"), (Operation.CHAMFER, "distance_angle")])
def test_native_edge_treatments_are_valid_and_change_box_volume(operation, mode):
    shape = BRepPrimAPI_MakeBox(30., 20., 10.).Shape()
    ref = edge_reference(shape, edges_of(shape)[0])
    result = apply_edges(shape, operation, EdgeParameters((ref,), 2., 3., mode))
    assert BRepCheck_Analyzer(result, True).IsValid()
    assert 0 < volume(result) < volume(shape)


@pytest.mark.parametrize("curve", [Ellipse(Vec2(0, 0), 20, 10, .3),
    Spline((Vec2(0, 0), Vec2(20, 0), Vec2(20, 20), Vec2(0, 20)), True)])
@pytest.mark.parametrize("plane", REFERENCE_PLANES)
def test_advanced_curve_roundtrip_and_exact_closed_faces(curve, plane):
    assert _decode_entity(_encode_entity(curve)) == curve
    sketch = source((curve,))
    sketch = replace(sketch, support=replace(sketch.support, frame=plane))
    faces, _ = arrangement(sketch)
    assert len(faces.regions) == 1
    assert BRepCheck_Analyzer(faces.faces[0], True).IsValid()
    assert faces.area > 0
    from cadastrophe.core.extrusion import extrude_face
    prism = extrude_face(faces.faces[0], 20.)
    assert prism.success, prism.message
    assert prism.volume == pytest.approx(faces.area*20, rel=1e-7)
    from cadastrophe.geometry import Vec3
    assert Vec3(*prism.translation).dot(plane.normal) == pytest.approx(20)
    if type(curve) is Ellipse:
        assert faces.area == pytest.approx(200*pi)


def test_ellipse_intersections_create_independently_selectable_cells():
    ellipse = Ellipse(Vec2(0, 0), 20, 10)
    faces, _ = arrangement(source((ellipse, Segment(Vec2(-30, 0), Vec2(30, 0)))))
    assert len(faces.regions) == 2
    assert [r.area for r in faces.regions] == pytest.approx([100*pi]*2)
    from plugins.sketch.model import Circle
    nested, _ = arrangement(source((ellipse, Circle(Vec2(0, 0), 5))))
    assert sorted(r.area for r in nested.regions) == pytest.approx([25*pi, 175*pi])


@pytest.mark.parametrize("tool,points,count", [
    ("slot", ((0, 0), (30, 0), (30, 5)), 4),
    ("polygon", ((0, 0), (20, 0)), 6),
    ("ellipse", ((0, 0), (20, 0), (0, 10)), 1),
    ("arc_three", ((0, 0), (20, 0), (10, 10)), 1),
])
def test_new_drawing_controllers_return_native_ready_geometry(tool, points, count):
    controller = DrawingController()
    controller.select(tool)
    for point in points:
        result = controller.click(Vec2(*point))
    assert len(result) == count
    if tool in ("slot", "polygon"):
        assert analyze(source(result)).valid


@pytest.mark.parametrize("mode", ["fillet", "chamfer"])
def test_sketch_corner_is_trimmed_and_closed_with_one_new_curve(mode):
    sketch = source(rectangle(width=30, height=20))
    original = sketch.entities
    result = rounded_corner(sketch, tuple(e.id for e in sketch.entities[:2]), mode, 3.)
    assert len(result.entities) == 5 and sketch.entities is original
    from plugins.sketch.solver import solve
    solved = solve(result)
    assert solved.success, solved.message
    assert analyze(solved.sketch).valid


@pytest.mark.parametrize("operation", [Operation.FILLET, Operation.CHAMFER])
def test_edge_feature_preview_commit_undo_save_and_reopen(extrusion_system, tmp_path, operation):
    from cadastrophe.document import SceneObject
    from cadastrophe.core.feature_objects import EdgeFeature, BodyObject
    from cadastrophe.core.persistence import save_document, load_document
    w, _, _ = extrusion_system
    doc = w.viewport.document
    obj = SceneObject("Bloc", BRepPrimAPI_MakeBox(30., 20., 10.).Shape(), (.4, .6, .8))
    doc.add(obj)
    doc.history.clear()
    assert w.command_registry.execute("cadastrophe.modeling."+operation.value)
    tool = w.tool_sessions.tool
    selected = next(e for e in edges_of(obj.shape) if edge_reference(obj.shape, e)[4:7] == (1., 0., .5))
    tool.add_edge(obj.id, selected)
    tool._update()
    w.tool_sessions.update_preview()
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    from OCC.Core.AIS import AIS_ColoredShape
    assert isinstance(w.tool_host.overlay.presentation, AIS_ColoredShape)
    assert tool.preview_parts[0][2]
    from OCC.Core.Quantity import Quantity_TOC_RGB
    for face in tool.preview_parts[0][2]:
        aspects = w.tool_host.overlay.presentation.CustomAspects(face)
        assert aspects.ShadingAspect().Color().Values(Quantity_TOC_RGB) == pytest.approx(obj.color)
    assert w.command_registry.execute("view.fit")
    w.viewport.view.Redraw()
    assert w.viewport.view.Dump(str(Path("artifacts")/f"edge-{operation.value}-preview.png"))
    assert doc.objects[obj.id] is obj and not doc.history.count
    assert w.tool_sessions.commit(), w.tool_sessions.message
    body = doc.objects[obj.id]
    assert isinstance(body, BodyObject) and isinstance(doc.objects[body.features[0]], EdgeFeature)
    assert doc.history.count == 1
    expected = volume(body.shape)
    doc.history.undo()
    assert type(doc.objects[obj.id]) is SceneObject
    doc.history.redo()
    assert volume(doc.objects[obj.id].shape) == pytest.approx(expected)
    path = tmp_path / "edges.cada"
    save_document(doc, path, registry=w.object_types)
    loaded = load_document(path, registry=w.object_types)
    assert volume(loaded.objects[obj.id].shape) == pytest.approx(expected)
    w.tree_selection_changed([body.features[0]])
    assert w.command_registry.execute("cadastrophe.modeling.edit_feature")
    w.tool_sessions.tool.size.setValue(1.)
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert volume(doc.objects[obj.id].shape) > expected


@pytest.mark.parametrize("tool,points,count", [
    ("slot", ((-30, -20), (0, -20), (0, -12)), 4),
    ("polygon", ((-25, -20), (0, -20)), 5),
    ("ellipse", ((-20, -10), (5, -10), (-20, 5)), 1),
    ("spline", ((-30, -20), (-10, 20), (10, 20), (30, -20)), 1),
])
def test_new_tools_draw_with_mouse_and_extrude_save_and_reopen(extrusion_system, tmp_path, tool, points, count):
    from test_sketch_drawing_ui import begin, choose, click, move
    from test_extrusion_tool import start
    from cadastrophe.core.persistence import save_document, load_document
    w, commands, m = extrusion_system
    system = w, commands, m, None
    session = begin(system)
    choose(system, tool)
    if tool == "polygon":
        session.polygon_count.setValue(5)
    if tool == "spline":
        session.spline_closed.setChecked(True)
    doc, key = w.viewport.document, session.object_id
    before = doc.history.count
    for point in points:
        move(system, point)
        click(system, point)
    if tool == "spline":
        QTest.keyClick(w.viewport, Qt.Key.Key_Return)
    assert not session.error, session.error
    assert len(doc.objects[key].sketch.entities) == count
    assert doc.history.count == before+1
    curve = doc.objects[key].sketch.entities[0]
    assert w.command_registry.execute("cadastrophe.sketch.select")
    session.interaction.set_selected((curve.id,))
    assert session.entity_panel.fields
    assert w.command_registry.execute("cadastrophe.sketch.finish")
    tool = start(w, doc.objects[key])
    assert len(tool.regions) == 1, tool._profile_error
    assert w.tool_sessions.commit(), w.tool_sessions.message
    from cadastrophe.core.feature_objects import BodyObject
    body, = (o for o in doc.objects.values() if isinstance(o, BodyObject))
    assert BRepCheck_Analyzer(body.shape, True).IsValid()
    expected = volume(body.shape)
    path = tmp_path / "drawn.cada"
    save_document(doc, path, registry=w.object_types)
    reopened = load_document(path, registry=w.object_types)
    assert volume(reopened.objects[body.id].shape) == pytest.approx(expected)


def test_sketch_buttons_are_icons_and_arc_menu_contains_three_modes(extrusion_system):
    w = extrusion_system[0]
    for key in ("line", "rectangle", "circle", "arc", "slot", "polygon", "ellipse", "spline"):
        action = w.command_registry.action("cadastrophe.sketch."+key)
        buttons = [b for b in w.command_manager.findChildren(QToolButton) if b.defaultAction() is action]
        assert buttons, (key, [(b.defaultAction().objectName() if b.defaultAction() else None) for b in w.command_manager.findChildren(QToolButton)])
        button = buttons[0]
        assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        assert not button.icon().isNull() and button.accessibleName()
    action = w.command_registry.action("cadastrophe.sketch.arc")
    button = next(b for b in w.command_manager.findChildren(QToolButton) if b.defaultAction() is action)
    assert len(button.menu().actions()) == 3
    for key in ("new", "edit", "finish", "dimension"):
        action = w.command_registry.action("cadastrophe.sketch."+key)
        button = next(b for b in w.command_manager.findChildren(QToolButton) if b.defaultAction() is action)
        assert button.toolButtonStyle() != Qt.ToolButtonStyle.ToolButtonIconOnly


@pytest.mark.parametrize("index,mode", [(0, "arc"), (1, "arc_tangent"), (2, "arc_three")])
def test_arc_flyout_opens_from_arrow_and_selects_its_mode(extrusion_system, index, mode):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QStyle, QStyleOptionToolButton
    from test_sketch_drawing_ui import begin
    w = extrusion_system[0]
    session = begin(extrusion_system)
    action = w.command_registry.action("cadastrophe.sketch.arc")
    button = next(b for b in w.command_manager.findChildren(QToolButton) if b.defaultAction() is action)
    menu = button.menu()
    assert menu.isWindow() and menu.windowFlags() & Qt.WindowType.Popup
    option = QStyleOptionToolButton()
    button.initStyleOption(option)
    arrow = button.style().subControlRect(QStyle.ComplexControl.CC_ToolButton, option,
                                         QStyle.SubControl.SC_ToolButtonMenu, button)
    observed = []
    def choose_mode():
        observed.append(menu.isVisible())
        QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(menu.actions()[index]).center())
    timer = QTimer(menu)
    timer.setSingleShot(True)
    timer.timeout.connect(menu.close)
    timer.start(1000)
    QTimer.singleShot(50, menu, choose_mode)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=arrow.center())
    timer.stop()
    assert observed == [True]
    assert session.draft.tool == mode


@pytest.mark.parametrize("mode", ["fillet", "chamfer"])
def test_sketch_corner_panel_trims_in_one_undo_and_extrudes(extrusion_system, mode):
    from importlib import import_module
    from PySide6.QtWidgets import QDialogButtonBox
    from test_sketch_drawing_ui import begin
    from test_extrusion_tool import start
    w, commands, m = extrusion_system
    session = begin((w, commands, m, None))
    doc = w.viewport.document
    points = tuple(m.Vec2(*p) for p in ((-30, -20), (10, -20), (10, 10), (-30, 10)))
    segments = tuple(m.Segment(p, points[(i+1)%4]) for i, p in enumerate(points))
    creation = import_module(w.plugins._record("cadastrophe.sketch").namespace+".creation_relations")
    rules = creation.creation_relations(doc.objects[session.object_id].sketch, segments, rectangle=True)
    commands.add_entities(doc, session.object_id, segments, title="Rectangle", constraints=rules)
    before = doc.history.count
    session.interaction.set_selected(tuple(e.id for e in segments[:2]))
    assert w.command_registry.execute("cadastrophe.sketch."+mode)
    panel = session.corner_panel
    buttons = panel.findChild(QDialogButtonBox)
    QTest.mouseClick(buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
    assert session.corner_panel is None and not panel.isVisible(), [label.text() for label in panel.findChildren(QLabel)]
    assert len(doc.objects[session.object_id].sketch.entities) == 5
    assert doc.history.count == before+1
    doc.history.undo()
    assert len(doc.objects[session.object_id].sketch.entities) == 4
    doc.history.redo()
    key = session.object_id
    assert w.command_registry.execute("cadastrophe.sketch.finish")
    tool = start(w, doc.objects[key])
    assert w.tool_sessions.commit(), w.tool_sessions.message


def test_arc_modes_draw_in_native_view(extrusion_system):
    from test_sketch_drawing_ui import begin, choose, click
    w, commands, m = extrusion_system
    system = w, commands, m, None
    session = begin(system)
    choose(system, "line")
    click(system, (-40, -20))
    click(system, (0, -20))
    segment = session.document.objects[session.object_id].sketch.entities[-1]
    choose(system, "arc_tangent")
    click(system, (segment.end.u, segment.end.v))
    click(system, (20, 0))
    assert not session.error, session.error
    arc = session.document.objects[session.object_id].sketch.entities[-1]
    assert type(arc) is m.Arc
    tangent = (-sin(arc.start_angle), cos(arc.start_angle))
    assert abs(tangent[1]) < 1e-6
    choose(system, "arc_three")
    for point in ((-40, 10), (10, 10), (-15, 35)):
        click(system, point)
    assert not session.error, session.error
    assert type(session.document.objects[session.object_id].sketch.entities[-1]) is m.Arc


def test_multiple_body_edges_can_be_picked_after_preview_hides_source(extrusion_system):
    from cadastrophe.document import SceneObject
    from cadastrophe.presentation import PresentationSurface
    from cadastrophe.geometry import Vec3
    from PySide6.QtCore import QPoint
    from test_extrusion_interaction import orient
    from OCC.Core.gp import gp_Pnt
    w, _, _ = extrusion_system
    doc = w.viewport.document
    obj = SceneObject("Bloc", BRepPrimAPI_MakeBox(30., 20., 10.).Shape(), (.4, .6, .8))
    doc.add(obj)
    other = SceneObject("Autre bloc", BRepPrimAPI_MakeBox(gp_Pnt(60, 0, 0), 30., 20., 10.).Shape(), (.6, .4, .8))
    doc.add(other)
    orient(w, REFERENCE_PLANES[0])
    assert w.command_registry.execute("cadastrophe.modeling.fillet")
    tool = w.tool_sessions.tool
    def click_edge(y):
        x, sy = PresentationSurface(w.viewport).project(Vec3(15, y, 10))
        QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=QPoint(round(x), round(sy)))
        w.tool_sessions.update_preview()
    click_edge(0)
    assert len(tool.references) == 1, w.tool_sessions.message
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert not w.viewport.context.IsDisplayed(w.viewport.presentations[obj.id])
    click_edge(20)
    assert len(tool.references) == 2, w.tool_sessions.message
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    click_edge(0)
    assert len(tool.references) == 1
    assert w.tool_sessions.commit(), w.tool_sessions.message
    assert w.command_registry.execute("cadastrophe.modeling.chamfer")
    tool = w.tool_sessions.tool
    click_edge(0)
    assert w.tool_sessions.can_commit, w.tool_sessions.message
    assert w.tool_sessions.commit(), w.tool_sessions.message
    for key, point in ((obj.id, Vec3(15, 10, 10)), (other.id, Vec3(75, 10, 10))):
        x, y = PresentationSurface(w.viewport).project(point)
        QTest.mouseClick(w.viewport, Qt.MouseButton.LeftButton, pos=QPoint(round(x), round(y)))
        assert doc.selected_ids == (key,)
        assert w.services.selection_items[0].topology == "Face"
