"""Original SVG assets; Qt's SVG icon engine supplies HiDPI/disabled variants."""
from functools import lru_cache
from pathlib import Path
from PySide6.QtGui import QIcon
from .features import FEATURE_TOOLS

ASSETS = Path(__file__).with_name("icons")

ACTION_ICONS = {
    "view.fit": "fit", "view.zoom_window": "zoom", "object.frame": "fit",
    "object.visibility": "eye", "object.delete": "delete",
    "view.normal_to_plane": "normal", "view.cancel": "back", "view.background": "palette",
    "file.new": "document", "file.open": "folder", "file.save": "save", "file.save_as": "save",
    "edit.undo": "undo", "edit.redo": "redo", "edit.copy": "copy", "edit.cut": "cut",
    "edit.paste": "paste", "edit.select_all": "select", "window.tree": "tree",
    "window.quick_access": "tree", "window.command_manager": "tree",
    "sketch.new": "sketch", "sketch.line": "line", "sketch.circle": "circle",
    "sketch.dimension": "dimension", "modeling.extrude": "extrude", "modeling.revolve": "revolve",
    "modeling.fillet": "fillet",
    "view.render.wireframe": "wireframe", "window.view_tools": "eye",
    "object.rename": "properties", "file.close": "delete",
    "object.show": "eye", "object.hide": "eye", "object.show_all": "eye",
    "object.isolate": "filter", "object.transparency": "wireframe", "object.properties": "properties",
}
ACTION_ICONS.update({f"modeling.{tool.key}": tool.icon for tool in FEATURE_TOOLS})
CAPTIONS = {"object.frame": "Cadrer", "object.visibility": "Visibilité",
    "view.normal_to_plane": "Vue normale", "view.background": "Fond",
    "sketch.new": "Esquisse"}
CAPTIONS.update({f"modeling.{tool.key}": tool.caption for tool in FEATURE_TOOLS})


@lru_cache(maxsize=64)
def icon(name: str) -> QIcon:
    path = ASSETS / f"{name}.svg"
    if not path.is_file():
        raise ValueError(f"Unknown CAD icon: {name}")
    return QIcon(str(path))


def command_icon(command_id: str) -> QIcon:
    name = ACTION_ICONS.get(command_id)
    if name is None:
        name = ("filter" if command_id.startswith("selection.") else
                "measure" if command_id.startswith("measure.") else
                "body")
    return icon(name)


def command_caption(command_id: str, label: str) -> str:
    return CAPTIONS.get(command_id, label.split(" · à venir")[0])
