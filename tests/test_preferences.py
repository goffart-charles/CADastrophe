"""Preference files, display-only units and atomic shortcut rebinding."""
from dataclasses import asdict, replace
import json

import pytest
from PySide6.QtWidgets import QApplication, QMainWindow

from cadastrophe.core.commands import CommandRegistry, ShortcutConflictError
from cadastrophe.core.preferences import Preferences, PreferenceStore, validate_preferences
from cadastrophe.core.units import DisplayUnits
from cadastrophe.ui.length_input import LengthSpinBox


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_preferences_round_trip_and_ephemeral_store(tmp_path):
    path = tmp_path / "settings/preferences.json"
    store = PreferenceStore(path)
    assert store.load() == (Preferences(), {})
    prefs = replace(Preferences(), theme="dark", length_unit="in", rotation_speed=1.5,
                    shortcuts={"view.fit": ["Ctrl+Alt+F"]})
    layout = dict(splitter=[300, 1100], panels={"window.tree": False}, command_tab=2, manager_tab=1)
    store.save(prefs, layout)
    assert PreferenceStore(path).load() == (prefs, layout)
    assert json.loads(path.read_text())["schema_version"] == 3
    ephemeral = PreferenceStore()
    ephemeral.save(prefs, layout)
    assert ephemeral.load() == (Preferences(), {})


@pytest.mark.parametrize("change", [
    {"theme": "unknown"}, {"length_unit": "feet"}, {"decimals": True}, {"decimals": 7},
    {"zoom_speed": 0}, {"rotation_speed": float("nan")}, {"invert_zoom": 1},
    {"selection": "red"}, {"background_top": "#fff"}, {"shortcuts": {"view.cancel": ["F9"]}},
])
def test_invalid_values_are_rejected(change):
    data = asdict(Preferences())
    data.update(change)
    with pytest.raises((ValueError, TypeError)):
        validate_preferences(data)


@pytest.mark.parametrize("damage", ["version", "json", "duplicate", "layout"])
def test_invalid_file_falls_back_without_overwriting_it(tmp_path, damage):
    path = tmp_path / "preferences.json"
    PreferenceStore(path).save(Preferences(), {})
    data = json.loads(path.read_text())
    if damage == "version":
        data["schema_version"] = 99
    elif damage == "layout":
        data["layout"] = {"splitter": [0, -1]}
    text = json.dumps(data)
    if damage == "json":
        text = "broken JSON"
    elif damage == "duplicate":
        text = text.replace('"schema_version": 3', '"schema_version": 3, "schema_version": 3')
    path.write_text(text)
    store = PreferenceStore(path)
    assert store.load() == (Preferences(), {}) and store.error and not store.writable
    with pytest.raises(ValueError):
        store.save(Preferences(), {})
    assert path.read_text() == text


def test_failed_atomic_write_keeps_previous_preferences_and_removes_temporary(tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    store = PreferenceStore(path)
    store.save(Preferences(), {})
    original = path.read_bytes()

    def fail(*_):
        raise OSError("replace failed")

    monkeypatch.setattr("cadastrophe.core.preferences.os.replace", fail)
    with pytest.raises(OSError):
        store.save(replace(Preferences(), theme="dark"), {})
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("unit,factor", [("mm", 1), ("cm", 10), ("m", 1000), ("in", 25.4)])
def test_display_units_round_trip_and_editor_preserves_unedited_internal_value(app, unit, factor):
    units = DisplayUnits(unit, 2)
    assert units.to_internal(1) == factor
    assert units.to_internal(units.to_display(123.456)) == pytest.approx(123.456)
    assert units.format_length(factor) == "1.00 " + unit
    editor = LengthSpinBox(units, 0.001, 1_000_000., 20.)
    try:
        assert editor.internal_value == 20.
        assert editor.minimum() > 0 and editor.suffix() == " " + unit
        editor.setValue(1.)
        assert editor.internal_value == factor
    finally:
        editor.deleteLater()


def test_shortcuts_swap_atomically_and_conflicts_leave_every_binding_intact(app):
    window = QMainWindow()
    registry = CommandRegistry(window)
    registry.register("first", "First", "Test", lambda: None, shortcuts=("F",))
    registry.register("second", "Second", "Test", lambda: None, shortcuts=("Z",))
    registry.set_shortcuts_batch({"first": ["Z"], "second": ["F"]})
    assert registry.command("first").shortcuts == ("Z",) and registry.command("second").shortcuts == ("F",)
    with pytest.raises(ShortcutConflictError):
        registry.set_shortcuts_batch({"first": ["Ctrl+K"], "second": ["Ctrl+K, Ctrl+C"]})
    assert registry.command("first").shortcuts == ("Z",) and registry.command("second").shortcuts == ("F",)
    assert registry.default_shortcuts() == {"first": ["F"], "second": ["Z"]}
    window.deleteLater()
