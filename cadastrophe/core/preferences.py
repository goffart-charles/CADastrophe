"""Versioned user preferences, separate from documents and plugin activation."""
from dataclasses import asdict, dataclass, field
import base64
import json
from math import isfinite
import os
from pathlib import Path
import re
import tempfile

from .units import DisplayUnits

LOCKED_SHORTCUTS = {"view.cancel", "tool.commit"}
PANEL_IDS = ("window.tree", "window.quick_access", "window.command_manager", "window.view_tools")


@dataclass(frozen=True)
class Preferences:
    theme: str = "light"
    length_unit: str = "mm"
    decimals: int = 3
    rotation_speed: float = 1.
    zoom_speed: float = 1.
    invert_zoom: bool = False
    background_top: str | None = None
    background_bottom: str | None = None
    preselection: str = "#1fd9f2"
    selection: str = "#ff940f"
    shortcuts: dict = field(default_factory=dict)
    autosave_enabled: bool = True
    autosave_interval_minutes: int = 5
    sketch_snapping: bool = True
    sketch_inference: bool = True
    sketch_grid: bool = False
    sketch_grid_step: float = 10.

    @property
    def units(self):
        return DisplayUnits(self.length_unit, self.decimals)

    @property
    def backgrounds(self):
        defaults = ("#eceff3", "#fdfdfe") if self.theme == "light" else ("#242b35", "#424c5a")
        return self.background_top or defaults[0], self.background_bottom or defaults[1]


def rgb(hex_color):
    return tuple(int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5))


def validate_preferences(value):
    if type(value) is not dict or set(value) != set(asdict(Preferences())):
        raise ValueError("Champs de préférences manquants ou inconnus.")
    if value["theme"] not in ("light", "dark"):
        raise ValueError("Thème inconnu.")
    DisplayUnits(value["length_unit"], value["decimals"])
    if any(type(value[key]) is not bool for key in ("sketch_snapping", "sketch_inference", "sketch_grid")):
        raise ValueError("Options d’accrochage invalides.")
    step = value["sketch_grid_step"]
    if type(step) not in (int, float) or not isfinite(step) or not .001 <= step <= 1_000_000:
        raise ValueError("Pas de grille attendu entre 0,001 et 1 000 000 mm.")
    for key in ("rotation_speed", "zoom_speed"):
        item = value[key]
        if type(item) not in (int, float) or not 0.25 <= item <= 4 or not isfinite(item):
            raise ValueError("Sensibilité attendue entre 0,25 et 4.")
    if type(value["invert_zoom"]) is not bool:
        raise ValueError("Inversion de zoom invalide.")
    if (type(value["autosave_enabled"]) is not bool or type(value["autosave_interval_minutes"]) is not int
            or not 1 <= value["autosave_interval_minutes"] <= 60):
        raise ValueError("La sauvegarde automatique exige un intervalle entier de 1 à 60 minutes.")
    for key in ("background_top", "background_bottom", "selection", "preselection"):
        item = value[key]
        if key.startswith("background") and item is None:
            continue
        if type(item) is not str or not re.fullmatch(r"#[0-9a-fA-F]{6}", item):
            raise ValueError("Couleur attendue au format #RRGGBB.")
    shortcuts = value["shortcuts"]
    if type(shortcuts) is not dict or len(shortcuts) > 1000:
        raise ValueError("Raccourcis invalides.")
    for key, keys in shortcuts.items():
        if (type(key) is not str or not re.fullmatch(r"[a-z][a-z0-9_.-]*", key)
                or key in LOCKED_SHORTCUTS or type(keys) is not list or len(keys) > 8
                or any(type(text) is not str or not text.strip() or len(text) > 100 for text in keys)):
            raise ValueError("Déclaration de raccourci invalide ou touche de session réservée.")
    return Preferences(**json.loads(json.dumps(value, allow_nan=False)))


def validate_layout(value):
    if type(value) is not dict or set(value) - {"geometry", "splitter", "panels", "command_tab", "manager_tab", "file_directories"}:
        raise ValueError("Disposition invalide.")
    if "geometry" in value:
        if type(value["geometry"]) is not str or len(value["geometry"]) > 16384:
            raise ValueError("Géométrie de fenêtre invalide.")
        base64.b64decode(value["geometry"], validate=True)
    if "splitter" in value:
        sizes = value["splitter"]
        if type(sizes) is not list or len(sizes) != 2 or any(type(size) is not int or not 1 <= size <= 100000 for size in sizes):
            raise ValueError("Dimensions de panneaux invalides.")
    if "panels" in value and (type(value["panels"]) is not dict or set(value["panels"]) - set(PANEL_IDS)
                              or any(type(item) is not bool for item in value["panels"].values())):
        raise ValueError("Visibilité de panneaux invalide.")
    for key in ("command_tab", "manager_tab"):
        if key in value and (type(value[key]) is not int or not 0 <= value[key] <= 3):
            raise ValueError("Onglet de panneau invalide.")
    directories = value.get("file_directories",{})
    if (type(directories) is not dict or set(directories)-{"project","import","export","directory"}
            or any(type(path) is not str or not path or len(path) > 32768 or "\0" in path or not Path(path).is_absolute()
                   for path in directories.values())):
        raise ValueError("Dossiers de fichiers invalides.")
    return json.loads(json.dumps(value))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Clé de préférence dupliquée.")
        result[key] = value
    return result


class PreferenceStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else None
        self.error = ""
        self.writable = True

    def load(self):
        try:
            source = self.path
            if source is None or not source.exists():
                return Preferences(), {}
            if source.stat().st_size > 256 * 1024:
                raise ValueError("Fichier de préférences trop volumineux.")
            data = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=_unique)
            if type(data) is not dict or set(data) != {"schema_version", "preferences", "layout"}:
                raise ValueError("Structure de préférences invalide.")
            if type(data["schema_version"]) is not int or data["schema_version"] != 3:
                raise ValueError("Version de préférences non prise en charge.")
            return validate_preferences(data["preferences"]), validate_layout(data["layout"])
        except (OSError, ValueError, TypeError, OverflowError, RecursionError) as exc:
            self.error, self.writable = str(exc), False
            return Preferences(), {}

    def save(self, preferences, layout):
        validate_preferences(asdict(preferences))
        validate_layout(layout)
        if not self.writable:
            raise ValueError("Le fichier de préférences invalide a été conservé : " + self.error)
        if self.path is None:
            return
        payload = json.dumps(dict(schema_version=3, preferences=asdict(preferences), layout=layout), ensure_ascii=False, indent=2, allow_nan=False)
        if len(payload.encode("utf-8")) > 256 * 1024:
            raise ValueError("Fichier de préférences trop volumineux.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, prefix=".preferences-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def default_preferences_path():
    from PySide6.QtCore import QStandardPaths
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericConfigLocation)) / "CADastrophe" / "preferences.json"
