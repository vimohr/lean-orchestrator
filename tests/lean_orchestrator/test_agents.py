from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest
from support import fake_config, quiet_console

from lean_orchestrator.agents import (
    AgentError, AgentRunner, AgentStopped, AgentTimeoutError, AgentTransientError, AgentUsageLimitError,
    classify_failure, extract_json_block,
)
from lean_orchestrator.config import AgentConfig
from lean_orchestrator.events import EventLog
from lean_orchestrator.paths import WorkspacePaths


def runner(tmp_path: Path, stop: threading.Event | None = None, **overrides) -> AgentRunner:
    paths = WorkspacePaths(tmp_path)
    return AgentRunner(paths, fake_config(overrides), quiet_console(), EventLog(paths.events), stop)


def script(tmp_path: Path, body: str) -> AgentConfig:
    path = tmp_path / "agent.py"
    path.write_text(body)
    return AgentConfig(command=(sys.executable, str(path)))


def test_prompt_is_passed_and_the_run_is_logged(tmp_path):
    agent = script(tmp_path, "import os, sys\nopen(os.environ['LEAN_ORCH_OUTPUT'], 'w').write(sys.argv[-1])\nprint('done')\n")
    output = tmp_path / "out.txt"
    result = runner(tmp_path).run("critic", "hello prompt", tag="t", problem_id="p1", output_path=output, agent=agent)
    assert output.read_text() == "hello prompt"
    assert result.stdout == "done\n" and result.exit_code == 0
    assert (result.run_dir / "prompt.md").read_text() == "hello prompt"
    meta = json.loads((result.run_dir / "meta.json").read_text())
    assert meta["exit_code"] == 0 and meta["command"].endswith("<prompt>")
    assert (result.run_dir / "stdout.log").read_text() == "done\n"


def test_stdin_mode(tmp_path):
    agent = AgentConfig(command=(sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"), prompt_via="stdin")
    result = runner(tmp_path).run("critic", "quiet", tag="t", agent=agent)
    assert result.stdout.strip() == "QUIET"


def test_capacity_errors_are_retried_then_reported(tmp_path):
    counter = tmp_path / "count"
    agent = script(tmp_path, f"from pathlib import Path\np = Path({str(counter)!r})\n"
                             "n = int(p.read_text()) if p.exists() else 0\np.write_text(str(n + 1))\n"
                             "import sys\nif n < 1:\n    print('Error: model is at capacity', file=sys.stderr); sys.exit(1)\n")
    runner(tmp_path).run("critic", "p", tag="t", agent=agent)
    assert counter.read_text() == "2"
    always = script(tmp_path, "import sys\nprint('overloaded_error', file=sys.stderr)\nsys.exit(1)\n")
    with pytest.raises(AgentError, match="stayed at capacity"):
        runner(tmp_path).run("critic", "p", tag="t", agent=always)


def test_other_failures_and_timeouts(tmp_path):
    failing = script(tmp_path, "import sys\nprint('Traceback: boom', file=sys.stderr)\nsys.exit(2)\n")
    with pytest.raises(AgentError, match="exited with code 2") as caught:
        runner(tmp_path).run("critic", "p", tag="t", agent=failing)
    assert type(caught.value) is AgentError
    slow = AgentConfig(command=(sys.executable, "-c", "import time; time.sleep(30)"), timeout_minutes=0.01)
    with pytest.raises(AgentTimeoutError):
        runner(tmp_path).run("critic", "p", tag="t", agent=slow)


def test_stop_event_interrupts_waiting_and_running(tmp_path):
    stop = threading.Event()
    stop.set()
    agent = script(tmp_path, "print('never')\n")
    with pytest.raises(AgentStopped):
        runner(tmp_path, stop).run("critic", "p", tag="t", agent=agent)


def test_failure_classification_and_json_recovery():
    assert classify_failure("Claude AI usage limit reached") is AgentUsageLimitError
    assert classify_failure("HTTP 529 overloaded") is AgentTransientError
    assert classify_failure("syntax error") is AgentError
    assert classify_failure("You exceeded your current quota (insufficient_quota)") is AgentUsageLimitError
    assert classify_failure("failed to write session: Quota exceeded (os error 122)") is AgentError
    assert classify_failure("write error: Disk quota exceeded") is AgentError
    assert classify_failure("OSError: [Errno 28] No space left on device") is AgentError
    assert extract_json_block('Here you go:\n```json\n{"a": 1}\n```\nbye') == {"a": 1}
    assert extract_json_block('{"b": [1, 2]}') == {"b": [1, 2]}
    assert extract_json_block("text\n{\"c\": true}") == {"c": True}
    assert extract_json_block("no json here") is None
