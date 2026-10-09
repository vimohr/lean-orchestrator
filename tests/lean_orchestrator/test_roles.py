from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from support import fake_config, quiet_console

from lean_orchestrator.agents import AgentError, AgentRunner, AgentStopped
from lean_orchestrator.config import parse_config
from lean_orchestrator.events import EventLog
from lean_orchestrator.integrity import IntegrityGuard
from lean_orchestrator.paths import WorkspacePaths
from lean_orchestrator.prompts import PromptLibrary
from lean_orchestrator.roles import RoleOutputError, RoleRunner
from lean_orchestrator.schemas import write_schema_files

VALID_TRIAGE = {"problems": [{
    "id": "p1", "scores": {"precision": 3, "finite_dimensional": 3, "formalizability": 3, "closability": 3,
                           "tractable_subcases": 3, "background": 3},
    "recommendation": "activate", "modes": ["prove"], "likely_resolved": False, "rationale": "r",
}]}


def role_runner(tmp_path: Path, body: str, max_repairs: int = 1) -> tuple[RoleRunner, IntegrityGuard]:
    paths = WorkspacePaths(tmp_path)
    write_schema_files(paths.schemas_dir)
    agent_script = tmp_path / "agent.py"
    agent_script.write_text(body)
    config = fake_config({"agents": {"triage": {"command": [sys.executable, str(agent_script)]}}})
    events = EventLog(paths.events)
    guard = IntegrityGuard(paths)
    agents = AgentRunner(paths, config, quiet_console(), events)
    return RoleRunner(paths, agents, PromptLibrary(None), guard, quiet_console(), events, max_repairs=max_repairs), guard


def call(runner: RoleRunner, tmp_path: Path):
    return runner.call("triage", tag="t", problem_id=None, output_path=tmp_path / "out" / "triage.json",
                       values={"batch_path": "batch.md", "problem_ids": "p1"}, context={"problem_ids": ["p1"]})


def test_valid_output_is_returned_and_protected(tmp_path):
    body = f"import json, os\njson.dump({VALID_TRIAGE!r}, open(os.environ['LEAN_ORCH_OUTPUT'], 'w'))\n"
    runner, guard = role_runner(tmp_path, body)
    result = call(runner, tmp_path)
    assert result.data == VALID_TRIAGE and result.violations == []
    assert guard.is_protected(tmp_path / "out" / "triage.json")


def test_json_in_the_final_message_is_accepted_when_no_file_is_written(tmp_path):
    body = f"import json\nprint('Result:')\nprint('```json')\nprint(json.dumps({VALID_TRIAGE!r}))\nprint('```')\n"
    runner, _ = role_runner(tmp_path, body)
    assert call(runner, tmp_path).data == VALID_TRIAGE


def test_invalid_output_gets_one_repair_run(tmp_path):
    body = (
        "import json, os, sys\n"
        "path = os.environ['LEAN_ORCH_OUTPUT']\n"
        "if 'repair an output file' in sys.argv[-1]:\n"
        f"    json.dump({VALID_TRIAGE!r}, open(path, 'w'))\n"
        "else:\n"
        "    json.dump({'problems': [{'id': 'p1'}]}, open(path, 'w'))\n"
    )
    runner, _ = role_runner(tmp_path, body)
    result = call(runner, tmp_path)
    assert result.data == VALID_TRIAGE and len(result.run_dirs) == 2
    assert "missing required key 'scores'" in (result.run_dirs[1] / "prompt.md").read_text()


def test_persistent_invalid_output_raises(tmp_path):
    runner, _ = role_runner(tmp_path, "import os\nopen(os.environ['LEAN_ORCH_OUTPUT'], 'w').write('[1, 2]')\n")
    with pytest.raises(RoleOutputError, match="must be a JSON object"):
        call(runner, tmp_path)


def test_violations_during_a_call_are_reported(tmp_path):
    protected = tmp_path / "PORTFOLIO.md"
    body = (f"import json, os\nopen({str(protected)!r}, 'w').write('hacked')\n"
            f"json.dump({VALID_TRIAGE!r}, open(os.environ['LEAN_ORCH_OUTPUT'], 'w'))\n")
    runner, guard = role_runner(tmp_path, body)
    guard.write_text(protected, "original")
    result = call(runner, tmp_path)
    assert [violation.path for violation in result.violations] == ["PORTFOLIO.md"]
    assert protected.read_text() == "original"
    assert json.loads((tmp_path / "out" / "triage.json").read_text()) == VALID_TRIAGE
    assert parse_config({}).agents.max_repair_attempts == 1


@pytest.mark.parametrize("stopped", [False, True])
def test_complete_researcher_report_survives_failure_but_stop_is_respected(tmp_path, monkeypatch, stopped):
    runner, guard = role_runner(tmp_path, "")
    monkeypatch.setattr(runner.prompts, "render", lambda *args, **kwargs: "submit")
    output = tmp_path / "report.json"
    report = {"summary": "Saved work", "approach": {"name": "x", "description": "x", "fingerprint": "x"},
              "outcome": "partial", "claims": []}
    run_dir = tmp_path / "failed-run"
    run_dir.mkdir()
    def fail(*args, **kwargs):
        output.write_text(json.dumps(report))
        error = AgentStopped if stopped else AgentError
        raise error("transport failed after saving", run_dir)
    monkeypatch.setattr(runner.agents, "run", fail)
    kwargs = dict(tag="t", problem_id="p", output_path=output, values={})
    if stopped:
        with pytest.raises(AgentStopped):
            runner.call("researcher", **kwargs)
    else:
        result = runner.call("researcher", **kwargs)
        assert result.data == report and result.recovered_from
        assert guard.is_protected(output) and output.with_name("recovery.json").exists()


def test_execution_failure_with_partial_json_is_not_salvaged(tmp_path, monkeypatch):
    runner, _ = role_runner(tmp_path, "")
    monkeypatch.setattr(runner.prompts, "render", lambda *args, **kwargs: "submit")
    run_dir = tmp_path / "failed-run"
    run_dir.mkdir()
    output = tmp_path / "report.json"
    def fail(*args, **kwargs):
        output.write_text('{"summary":')
        raise AgentError("timeout", run_dir)
    monkeypatch.setattr(runner.agents, "run", fail)
    with pytest.raises(AgentError, match="timeout"):
        runner.call("researcher", tag="t", problem_id="p", output_path=output, values={})
