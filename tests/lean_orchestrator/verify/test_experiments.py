from __future__ import annotations

import json
import sys
from pathlib import Path

from lean_orchestrator.config import ExperimentsConfig
from lean_orchestrator.paths import WorkspacePaths
from lean_orchestrator.verify.experiments import ExperimentVerifier, compare_values


def test_compare_values_uses_tolerances_and_reports_paths():
    options = {"rtol": 1e-6, "atol": 1e-9, "ignore": {"runtime"}}
    assert compare_values({"x": 1.0, "runtime": 3}, {"x": 1.0 + 1e-8, "runtime": 9}, **options) == []
    differences = compare_values({"a": [1, 2], "b": True, "c": "s", "d": 1.0},
                                 {"a": [1, 3], "b": 1, "c": "t", "e": 0}, **options)
    assert "$.a[1]: 2 != 3" in differences
    assert "$.b: True != 1" in differences
    assert "$.d: missing from new result" in differences
    assert "$.e: unexpected key in new result" in differences
    assert compare_values(float("nan"), float("nan"), **options) == []


def _experiment(root: Path, script: str, recorded: dict | None, **manifest) -> Path:
    directory = root / "problems" / "p" / "experiments" / "e1"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "run.py").write_text(script)
    (directory / "experiment.json").write_text(json.dumps({"script": "run.py", "result": "result.json", **manifest}))
    if recorded is not None:
        (directory / "result.json").write_text(json.dumps(recorded))
    return directory


def _verifier(root: Path, timeout_minutes: float = 1.0) -> ExperimentVerifier:
    return ExperimentVerifier(WorkspacePaths(root), ExperimentsConfig(python=(sys.executable,),
                                                                      timeout_minutes=timeout_minutes))


WRITE = "import json\njson.dump({'value': 0.1 + 0.2, 'passed': True}, open('result.json', 'w'))\n"


def test_reproduced_experiment_matches(tmp_path):
    directory = _experiment(tmp_path, WRITE, {"value": 0.3, "passed": True})
    result = _verifier(tmp_path).check(directory, allowed_root=tmp_path / "problems" / "p" / "experiments")
    assert result.ok and result.compared and result.matches, result.summary
    assert (directory / "result.json").read_text() == json.dumps({"value": 0.3, "passed": True}), "original untouched"


def test_mismatch_failure_and_passed_false_are_detected(tmp_path):
    mismatch = _verifier(tmp_path).check(_experiment(tmp_path, WRITE, {"value": 0.4, "passed": True}))
    assert not mismatch.ok and mismatch.differences == ["$.value: 0.4 != 0.30000000000000004"]
    failing = _verifier(tmp_path).check(_experiment(tmp_path, "raise SystemExit(3)\n", {"passed": True}))
    assert not failing.ok and failing.exit_code == 3
    negative = _verifier(tmp_path).check(_experiment(
        tmp_path, "import json\njson.dump({'passed': False}, open('result.json', 'w'))\n", None, compare=False))
    assert not negative.ok and "passed = false" in negative.summary


def test_timeouts_and_path_escapes(tmp_path):
    slow = _verifier(tmp_path, timeout_minutes=0.02).check(
        _experiment(tmp_path, "import time\ntime.sleep(30)\n", {"passed": True}))
    assert not slow.ok and "timed out" in slow.summary
    directory = _experiment(tmp_path, WRITE, {"passed": True})
    (directory / "experiment.json").write_text(json.dumps({"script": "../../../../etc/hosts"}))
    escaped = _verifier(tmp_path).check(directory)
    assert not escaped.ok and "inside the experiment directory" in escaped.summary
    outside = _verifier(tmp_path).check(directory, allowed_root=tmp_path / "elsewhere")
    assert not outside.ok
