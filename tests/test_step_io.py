"""Installed STEP bindings: physical units and B-Rep round-trips with native oracles."""
import importlib.util
from pathlib import Path

import pytest
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.GProp import GProp_GProps

from cadastrophe.brep_io import encode_shape, decode_shape
from cadastrophe.document import ObjectType

PLUGIN = Path(__file__).resolve().parents[1] / "plugins/import_export"


@pytest.fixture(scope="module")
def step_io():
    spec = importlib.util.spec_from_file_location("step_io_test", PLUGIN / "step_io.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def volume(shape):
    props = GProp_GProps()
    brepgprop.VolumeProperties(shape, props)
    return props.Mass()


@pytest.mark.parametrize("unit,token,coordinate", [
    ("mm", "SI_UNIT(.MILLI.,.METRE.)", "76.2"),
    ("cm", "SI_UNIT(.CENTI.,.METRE.)", "7.62"),
    ("m", "SI_UNIT($,.METRE.)", "7.62E-02"),
    ("in", "CONVERSION_BASED_UNIT('INCH'", "3."),
])
def test_units_are_declared_scaled_and_reimported_in_mm(step_io, tmp_path, unit, token, coordinate):
    box = BRepPrimAPI_MakeBox(25.4, 50.8, 76.2).Shape()
    (tmp_path / "0.brep").write_bytes(encode_shape(box, ObjectType.BODY))
    path = tmp_path / "échange pièce.step"
    progress = []
    step_io.write_step(tmp_path, path, 1, unit, lambda value, text: progress.append(value))
    text = path.read_text(encoding="ascii")
    assert token in text
    assert coordinate in text
    dest = tmp_path / "read"
    dest.mkdir()
    result = step_io.read_step(path, dest, lambda value, text: None)
    assert result["count"] == 1 and result["source_units"]
    assert volume(decode_shape((dest / "0.brep").read_bytes(), ObjectType.BODY)) == pytest.approx(25.4 * 50.8 * 76.2)
    assert progress == sorted(progress)


def test_multiple_roots_remain_separate_and_bad_files_fail_without_partial_result(step_io, tmp_path):
    for index, dims in enumerate(((10, 20, 30), (5, 6, 7))):
        (tmp_path / f"{index}.brep").write_bytes(encode_shape(BRepPrimAPI_MakeBox(*dims).Shape(), ObjectType.BODY))
    path = tmp_path / "two.step"
    step_io.write_step(tmp_path, path, 2, "mm", lambda *args: None)
    dest = tmp_path / "read"
    dest.mkdir()
    result = step_io.read_step(path, dest, lambda *args: None)
    assert result["count"] == 2
    assert [volume(decode_shape((dest / f"{index}.brep").read_bytes(), ObjectType.BODY))
            for index in range(2)] == pytest.approx([6000, 210])
    path.write_text("ceci n’est pas un STEP", encoding="utf-8")
    with pytest.raises(ValueError, match="Lecture STEP"):
        step_io.read_step(path, dest, lambda *args: None)
    with pytest.raises(ValueError, match="Lecture STEP"):
        step_io.read_step(tmp_path / "missing.step", dest, lambda *args: None)


@pytest.mark.parametrize("unit,count", [("yard", 1), ("mm", 0), ("mm", 10001)])
def test_bad_export_arguments_do_not_write_output(step_io, tmp_path, unit, count):
    target = tmp_path / "out.step"
    with pytest.raises(ValueError):
        step_io.write_step(tmp_path, target, count, unit, lambda *args: None)
    assert not target.exists()
