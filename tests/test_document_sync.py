"""Real document -> tree/AIS integration and native B-Rep selection contracts."""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.gp import gp_Pnt

from cadastrophe.app import MainWindow
from cadastrophe.document import ObjectType, SceneObject
from cadastrophe.sample_scene import demo_document
from cadastrophe.ui.themes import apply_theme


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    return app


@pytest.fixture
def window(qt_app, discard_document_changes):
    window = MainWindow()
    window.documents.replace(window.viewport.document.id, demo_document())
    window.show()
    qt_app.processEvents()
    assert window.viewport.camera and not window.viewport._failed
    yield window
    window.close()
    window.deleteLater()
    qt_app.processEvents()


def bodies(window):
    return [obj for obj in window.viewport.document.objects.values() if obj.kind is ObjectType.BODY]


def test_tree_opens_bodies_and_preserves_explicit_expansion_on_rebuild(window, qt_app):
    adapter = window.tree_adapter
    assert window.tree.topLevelItem(0).isExpanded()
    assert not adapter.planes.isExpanded() and adapter.bodies.isExpanded()
    adapter.bodies.setExpanded(True)
    window.rebuild_tree()
    assert adapter.bodies.isExpanded() and not adapter.planes.isExpanded()
    adapter.bodies.setExpanded(False)
    adapter.planes.setExpanded(True)
    window.rebuild_tree()
    assert not adapter.bodies.isExpanded() and adapter.planes.isExpanded()
    window.create_document()
    qt_app.processEvents()
    assert window.tree.topLevelItem(0).isExpanded()
    assert adapter.bodies.isExpanded() and not adapter.planes.isExpanded()


def test_single_body_project_exposes_functions_and_regroups_after_add_undo_redo(window, qt_app):
    from pathlib import Path
    from cadastrophe.core.feature_objects import BodyObject
    path = Path(__file__).resolve().parents[1]/"tests/fonctions/depouille/02-apres.cada"
    document = window.open_document(path)
    qt_app.processEvents()
    body, = (o for o in document.objects.values() if isinstance(o, BodyObject))
    adapter = window.tree_adapter
    root = window.tree.topLevelItem(0)
    assert root.child(0) is adapter.planes
    assert adapter.bodies is None
    assert adapter.items[body.id].parent() is root
    assert adapter.items[body.id].isExpanded()
    assert all(not window.tree.visualItemRect(adapter.items[key]).isEmpty() for key in body.features)
    adapter.items[body.id].setExpanded(False)
    window.rebuild_tree()
    assert window.tree.topLevelItem(0).child(0) is adapter.planes
    assert not adapter.items[body.id].isExpanded()
    second = SceneObject("Deuxième corps", BRepPrimAPI_MakeBox(10., 10., 10.).Shape(), (.5, .6, .7))
    document.add(second)
    assert adapter.bodies is not None and adapter.bodies.isExpanded()
    assert window.tree.topLevelItem(0).child(0) is adapter.planes
    assert all(adapter.items[key].parent() is adapter.bodies for key in (body.id, second.id))
    assert not adapter.items[body.id].isExpanded()
    document.history.undo()
    assert adapter.bodies is None
    assert window.tree.topLevelItem(0).child(0) is adapter.planes
    assert adapter.items[body.id].parent() is window.tree.topLevelItem(0)
    assert not adapter.items[body.id].isExpanded()
    document.history.redo()
    assert adapter.bodies.isExpanded() and adapter.items[second.id].parent() is adapter.bodies


def pose(viewport):
    camera = viewport.view.Camera()
    return (*camera.Eye().Coord(), *camera.Center().Coord(), *camera.Up().Coord(), camera.Scale())


def click_face(window, world=(-55., -35., 40.), ctrl=False):
    viewport = window.viewport
    px, py = viewport.view.Convert(*world)
    scale = viewport.devicePixelRatioF()
    point = QPoint(round(px / scale), round(py / scale))
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier, point)


def test_document_visibility_updates_native_display_tree_and_selection(window):
    viewport = window.viewport
    document = viewport.document
    obj = bodies(window)[0]
    document.select_objects([obj.id])
    original_pose = pose(viewport)
    document.set_visible(obj.id, False)
    assert not viewport.context.IsDisplayed(viewport.presentations[obj.id])
    assert window.tree_items[obj.id].checkState(0) == Qt.CheckState.Unchecked
    assert not document.selected_ids and not viewport.selection.items()
    document.set_visible(obj.id, True)
    assert viewport.context.IsDisplayed(viewport.presentations[obj.id])
    assert window.tree_items[obj.id].checkState(0) == Qt.CheckState.Checked
    assert pose(viewport) == original_pose


def test_tree_checkbox_notifies_model_once_and_hidden_planes_remain_selectable(window):
    viewport = window.viewport
    document = viewport.document
    plane = next(obj for obj in document.objects.values() if obj.plane)
    events = []
    document.object_changed.connect(lambda key, fields: events.append((key, fields)))
    window.tree.setCurrentItem(window.tree_items[plane.id])
    assert document.selected_ids == (plane.id,)
    assert viewport.selection.items()[0].topology == "Plan"
    assert not viewport.context.IsDisplayed(viewport.presentations[plane.id])
    window.tree_items[plane.id].setCheckState(0, Qt.CheckState.Checked)
    assert events == [(plane.id, ("visible",))]
    assert viewport.context.IsDisplayed(viewport.presentations[plane.id])
    assert document.selected_ids == (plane.id,)
    assert window.normal_action.isEnabled()


def test_document_selection_and_tree_requests_share_parent_identity(window):
    viewport = window.viewport
    document = viewport.document
    first, second = bodies(window)[:2]
    events = []
    document.selection_changed.connect(events.append)
    document.select_objects([first.id, first.id])
    assert document.selected_ids == (first.id,)
    assert window.tree_items[first.id].isSelected()
    assert [item.object_id for item in viewport.selection.items()] == [first.id]
    assert events == [(first.id,)]
    window.tree.setCurrentItem(window.tree_items[second.id])
    assert document.selected_ids == (second.id,)
    assert [item.object_id for item in viewport.selection.items()] == [second.id]
    assert window.selected_ids == [second.id]
    document.select_objects([])
    assert not window.tree.selectedItems() and not viewport.selection.items()


def test_face_selection_survives_tree_refresh_and_metadata_edit(window):
    viewport = window.viewport
    document = viewport.document
    cube = bodies(window)[0]
    click_face(window)
    selected = viewport.selection.items()[0]
    assert selected.object_id == cube.id and selected.shape.ShapeType() == TopAbs_FACE
    assert document.selected_ids == (cube.id,) and window.tree_items[cube.id].isSelected()
    document.rename(cube.id, "Cube renommé")
    window.rebuild_tree()
    after = viewport.selection.items()[0]
    assert after.topology == "Face" and after.shape.IsSame(selected.shape)
    assert window.tree_items[cube.id].text(0) == "Cube renommé"
    assert window.manager_pane.selection_name.text() == "Cube renommé"


def test_clicking_already_highlighted_parent_row_selects_whole_object(window):
    viewport = window.viewport
    click_face(window)
    face, = viewport.selection.items()
    assert face.topology == "Face"
    item = window.tree_items[face.object_id]
    assert item.isSelected()
    # The clicked row is already selected, so itemSelectionChanged alone would
    # not fire. A real tree click must still promote the face to its parent.
    window.tree_adapter.bodies.setExpanded(True)
    rect = window.tree.visualItemRect(item)
    QTest.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton,
                     pos=QPoint(rect.right() - 12, rect.center().y()))
    whole, = viewport.selection.items()
    assert whole.topology == "Objet"
    assert whole.shape.IsSame(viewport.document.objects[face.object_id].shape)
    assert window.command_registry.action("edit.copy").isEnabled()
    click_face(window)
    assert viewport.selection.items()[0].topology == "Face"


def test_removing_one_selected_body_preserves_other_native_face_owners(window):
    viewport = window.viewport
    document = viewport.document
    cube, cylinder = bodies(window)[:2]
    click_face(window)
    click_face(window, (0., -35., 52.), ctrl=True)
    cube_face = next(item for item in viewport.selection.items() if item.object_id == cube.id)
    assert set(document.selected_ids) == {cube.id, cylinder.id}
    document.remove(cylinder.id)
    selected = viewport.selection.items()
    assert len(selected) == 1 and selected[0].shape.IsSame(cube_face.shape)
    assert selected[0].topology == "Face" and document.selected_ids == (cube.id,)
    assert cylinder.id not in viewport.presentations and cylinder.id not in window.tree_items


def test_dynamic_add_rename_remove_projects_only_document_objects(window):
    viewport = window.viewport
    document = viewport.document
    original_pose = pose(viewport)
    window.command_registry.execute("view.render.wireframe")
    obj = SceneObject("Nouvelle boîte", BRepPrimAPI_MakeBox(gp_Pnt(140, 0, 0), 10, 20, 30).Shape(),
                      (0.5, 0.6, 0.7))
    document.add(obj)
    assert set(viewport.presentations) == set(window.tree_items) == set(document.objects)
    assert viewport.context.IsDisplayed(viewport.presentations[obj.id], 0)
    window.manager_pane.tree_filter.setText("Boîte finale")
    assert window.tree_items[obj.id].isHidden()
    document.rename(obj.id, "Boîte finale")
    assert not window.tree_items[obj.id].isHidden()
    document.remove(obj.id)
    assert set(viewport.presentations) == set(window.tree_items) == set(document.objects)
    assert pose(viewport) == original_pose


def test_replacing_brep_invalidates_transient_selection_and_preserves_uuid(window):
    viewport = window.viewport
    document = viewport.document
    cube = bodies(window)[0]
    click_face(window)
    previous = viewport.selection.items()[0]
    old_ais = viewport.presentations[cube.id]
    shape = BRepPrimAPI_MakeBox(gp_Pnt(-75, -55, 0), 42, 42, 42).Shape()
    original_pose = pose(viewport)
    document.replace_shape(cube.id, shape)
    assert document.objects[cube.id].revision == previous.revision + 1
    assert document.objects[cube.id].shape.IsSame(shape)
    assert viewport.presentations[cube.id] != old_ais
    assert not document.selected_ids and not viewport.selection.items()
    assert not viewport.context.IsDisplayed(old_ais)
    assert viewport.context.IsDisplayed(viewport.presentations[cube.id])
    assert pose(viewport) == original_pose


def test_old_document_is_detached_and_clear_keeps_orientation_overlay(window):
    viewport = window.viewport
    old_document = viewport.document
    old_obj = bodies(window)[0]
    old_document.select_objects([old_obj.id])
    document = demo_document()
    viewport.load_document(document)
    old_document.set_visible(old_obj.id, False)
    old_document.select_objects([])
    old_document.add(SceneObject("Ancienne boîte", old_obj.shape, old_obj.color))
    assert viewport.document is document and window.tree_adapter.document is document
    assert set(viewport.presentations) == set(window.tree_items) == set(document.objects)
    plane = next(obj for obj in document.objects.values() if obj.plane)
    with pytest.raises(ValueError, match="protected"):
        document.remove(plane.id)
    document.select_objects([plane.id])
    document.clear()
    assert not viewport.presentations and not window.tree_items and not document.selected_ids
    assert not viewport.selection.items()
    assert viewport.context.IsDisplayed(viewport.cube)
    window.documents.replace(window.viewport.document.id, demo_document())
    assert len(viewport.presentations) == len(window.tree_items) == 7


def test_closed_views_disconnect_document_notifications(window, qt_app):
    document = window.viewport.document
    obj = bodies(window)[0]
    window.close()
    qt_app.processEvents()
    document.set_visible(obj.id, False)
    document.select_objects([obj.id])
    document.remove(obj.id)
    assert obj.id not in document.objects and not document.selected_ids
