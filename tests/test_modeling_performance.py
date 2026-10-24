"""Opt-in native growth measurements; JSON evidence, no simulated rendering."""
from dataclasses import replace
from datetime import datetime, timezone
from math import pi
import json
import os
from pathlib import Path
from time import perf_counter

import pytest

from cadastrophe.core.body_history import evaluate_body
from cadastrophe.core.feature_model import Operation, ExtrusionParameters, ResultStatus
from cadastrophe.core.operation_effects import checked_solids
from cadastrophe.core.persistence import load_document, save_document
from examples.parametric_example import ParametricExample, polygon, registry, mass


@pytest.mark.skipif(os.environ.get("CADASTROPHE_PROFILE_GROWTH") != "1", reason="Mesure explicite des modèles croissants")
def test_native_model_growth_open_rebuild_save(tmp_path):
    measurements = []
    for count in (8, 32, 128):
        start = perf_counter()
        model = ParametricExample(f"Mesure · {count} bossages")
        xy = model.plane("XY")
        base = model.sketch("base_sketch", "Semelle", xy, polygon(((0, 0), (100, 0), (100, 70), (0, 70))))
        model.append("base", "Semelle", Operation.NEW_BODY, ExtrusionParameters(depth=10), base)
        for index in range(count):
            sketch = model.circle(f"sketch_{index}", f"Plot {index+1}", xy, (5+6*(index%16), 5+8*(index//16)), 2)
            feature = model.append(f"plot_{index}", f"Bossage {index+1}", Operation.BOSS, ExtrusionParameters(depth=12), sketch)
        build = perf_counter()-start
        expected = 70000+count*8*pi
        assert mass(model.body.shape) == pytest.approx(expected, abs=1e-4)
        assert len(checked_solids(model.body.shape)) == 1
        ids, order = set(model.document.objects), model.body.features
        start = perf_counter()
        result = evaluate_body(model.document, model.body.id, model.service.evaluator(model.document), reuse=False)
        rebuild = perf_counter()-start
        assert result.status is ResultStatus.READY, result.message
        assert mass(result.shape) == pytest.approx(expected, abs=1e-4)
        model.document.replace_feature(replace(feature, parameters=replace(feature.parameters, depth=14)))
        start = perf_counter()
        model.rebuild()
        edit = perf_counter()-start
        assert mass(model.body.shape) == pytest.approx(expected+8*pi, abs=1e-4)
        model.document.history.undo()
        model.rebuild()
        assert mass(model.body.shape) == pytest.approx(expected, abs=1e-4)
        path = tmp_path/f"growth_{count}.cada"
        codecs = registry(model.service)
        start = perf_counter()
        save_document(model.document, path, registry=codecs)
        save = perf_counter()-start
        start = perf_counter()
        restored = load_document(path, registry=codecs)
        opening = perf_counter()-start
        assert set(restored.objects) == ids and restored.objects[model.body.id].features == order
        assert mass(restored.objects[model.body.id].shape) == pytest.approx(expected, abs=1e-4)
        measurements.append(dict(bosses=count, features=len(order), objects=len(ids), file_bytes=path.stat().st_size,
                                 seconds=dict(build=build, rebuild=rebuild, incremental=edit, save=save, open=opening)))
    output = Path("artifacts/modeling-growth.json")
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(dict(measured_at_utc=datetime.now(timezone.utc).isoformat(), native_engine="OCCT",
                                     status="passed", measurements=measurements), indent=2)+"\n", encoding="utf-8")
