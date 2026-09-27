"""Independent re-execution of numerical experiments.

An experiment lives in its own directory with an ``experiment.json`` manifest::

    {
      "script": "verify.py",          # run with the configured Python command
      "args": [],
      "result": "result.json",        # JSON written by the script
      "compare": true,                # compare with the committed result file
      "timeout_minutes": 10,
      "float_rtol": 1e-6,
      "ignore_keys": ["runtime_seconds"]
    }

The orchestrator copies the directory to a scratch location, runs the script
there, and compares the fresh result with the recorded one. A top-level
``"passed"`` key in the result must be ``true``. Expensive searches should keep
the search in a separate script and register a cheap verification script that
checks the witness, ideally in exact arithmetic.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import ExperimentsConfig, expand_placeholders
from ..paths import WorkspacePaths
from ..procutil import run_captured

MANIFEST = "experiment.json"


@dataclass
class ExperimentCheck:
    path: str
    ok: bool = False
    ran: bool = False
    exit_code: int | None = None
    compared: bool = False
    matches: bool | None = None
    differences: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    output_tail: str = ""
    summary: str = ""


def compare_values(expected: Any, actual: Any, *, rtol: float, atol: float, ignore: set[str],
                   path: str = "$", limit: int = 20) -> list[str]:
    """List differences between two JSON values, with tolerance for floats."""
    differences: list[str] = []

    def walk(left: Any, right: Any, where: str) -> None:
        if len(differences) >= limit:
            return
        if isinstance(left, bool) or isinstance(right, bool):
            if left is not right:
                differences.append(f"{where}: {left!r} != {right!r}")
            return
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if isinstance(left, int) and isinstance(right, int):
                if left != right:
                    differences.append(f"{where}: {left} != {right}")
            elif not (math.isclose(left, right, rel_tol=rtol, abs_tol=atol)
                      or (math.isnan(left) and math.isnan(right))):
                differences.append(f"{where}: {left!r} != {right!r}")
            return
        if isinstance(left, dict) and isinstance(right, dict):
            keys = (set(left) | set(right)) - ignore
            for key in sorted(keys, key=str):
                if key not in left:
                    differences.append(f"{where}.{key}: unexpected key in new result")
                elif key not in right:
                    differences.append(f"{where}.{key}: missing from new result")
                else:
                    walk(left[key], right[key], f"{where}.{key}")
            return
        if isinstance(left, list) and isinstance(right, list):
            if len(left) != len(right):
                differences.append(f"{where}: length {len(left)} != {len(right)}")
                return
            for index, (a, b) in enumerate(zip(left, right)):
                walk(a, b, f"{where}[{index}]")
            return
        if left != right:
            differences.append(f"{where}: {left!r} != {right!r}")

    walk(expected, actual, path)
    return differences


def _directory_size(directory: Path) -> int:
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


class ExperimentVerifier:
    def __init__(self, paths: WorkspacePaths, config: ExperimentsConfig) -> None:
        self.paths = paths
        self.config = config

    def check(self, directory: Path, *, allowed_root: Path | None = None) -> ExperimentCheck:
        result = ExperimentCheck(path=self._display(directory))
        try:
            self._check(result, directory, allowed_root)
        except (OSError, ValueError) as error:
            result.errors.append(str(error))
        result.summary = self._summarize(result)
        return result

    def _display(self, directory: Path) -> str:
        try:
            return self.paths.relative(directory)
        except ValueError:
            return str(directory)

    def _check(self, result: ExperimentCheck, directory: Path, allowed_root: Path | None) -> None:
        if allowed_root is not None:
            try:
                directory.resolve().relative_to(allowed_root.resolve())
            except ValueError:
                result.errors.append("experiment directory is outside the problem's experiments folder")
                return
        manifest_path = directory / MANIFEST
        if not manifest_path.is_file():
            result.errors.append(f"missing {MANIFEST}")
            return
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            result.errors.append(f"{MANIFEST} must contain a JSON object")
            return
        script = str(manifest.get("script", ""))
        result_name = str(manifest.get("result", "result.json"))
        script_path = (directory / script).resolve()
        if not script or directory.resolve() not in script_path.parents or not script_path.is_file():
            result.errors.append(f"script {script!r} must be an existing file inside the experiment directory")
            return
        if Path(result_name).is_absolute() or ".." in Path(result_name).parts:
            result.errors.append("result must be a relative path inside the experiment directory")
            return
        size_limit = self.config.max_copy_megabytes * 1024 * 1024
        if _directory_size(directory) > size_limit:
            result.errors.append(f"experiment directory exceeds {self.config.max_copy_megabytes:g} MB")
            return
        timeout = min(float(manifest.get("timeout_minutes", self.config.timeout_minutes)),
                      self.config.timeout_minutes) * 60
        arguments = [str(argument) for argument in manifest.get("args", [])]

        self.paths.tmp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.paths.tmp_dir, prefix="experiment-") as scratch:
            copy = Path(scratch) / directory.name
            shutil.copytree(directory, copy, symlinks=False)
            fresh_result = copy / result_name
            fresh_result.unlink(missing_ok=True)
            command = expand_placeholders(self.config.python, {"workspace": str(self.paths.root)})
            environment = {**os.environ, "PYTHONHASHSEED": "0", "LEAN_ORCH_REPRODUCTION": "1"}
            completed = run_captured([*command, script, *arguments], cwd=copy, timeout=timeout, env=environment)
            result.ran = True
            result.exit_code = completed.returncode
            result.duration_seconds = round(completed.duration_seconds, 1)
            result.output_tail = completed.output.strip()[-1500:]
            if completed.timed_out:
                result.errors.append(f"timed out after {timeout:g}s")
                return
            if completed.returncode != 0:
                result.errors.append(f"script exited with code {completed.returncode}")
                return
            if not fresh_result.is_file():
                result.errors.append(f"script did not write {result_name}")
                return
            fresh = json.loads(fresh_result.read_text(encoding="utf-8"))

        if isinstance(fresh, dict) and "passed" in fresh and fresh["passed"] is not True:
            result.errors.append("result reports passed = false")
        if manifest.get("compare", True):
            recorded_path = directory / result_name
            if not recorded_path.is_file():
                result.errors.append(f"no recorded {result_name} to compare with")
                return
            recorded = json.loads(recorded_path.read_text(encoding="utf-8"))
            result.compared = True
            result.differences = compare_values(
                recorded, fresh,
                rtol=float(manifest.get("float_rtol", self.config.float_rtol)),
                atol=float(manifest.get("float_atol", self.config.float_atol)),
                ignore=set(manifest.get("ignore_keys", [])),
            )
            result.matches = not result.differences
        result.ok = not result.errors and (result.matches is not False)

    @staticmethod
    def _summarize(result: ExperimentCheck) -> str:
        if result.ok:
            detail = "reproduced; results match" if result.compared else "re-ran successfully"
            return f"{detail} ({result.duration_seconds:g}s)"
        if result.differences:
            return f"not reproduced: {len(result.differences)} difference(s), first: {result.differences[0]}"
        return "not reproduced: " + ("; ".join(result.errors) or "unknown reason")
