from __future__ import annotations

import pytest
from support import add_problem, fake_calls, set_scenarios

from lean_orchestrator import inbox
from lean_orchestrator.orchestrator import WorkspaceBusy
from lean_orchestrator.portfolio import ACTIVE, CANDIDATE, DEFERRED, SUSPENDED, UNEXPLORED
from lean_orchestrator.state import load_state


def test_parallel_epochs_cover_several_problems(workspace, make_orchestrator):
    orchestrator = make_orchestrator({"loop": {"iterations_per_epoch": 1}})
    for name in ("a", "b", "c"):
        add_problem(orchestrator, f"local_{name}", f"Problem {name}")
    set_scenarios(workspace, {"*": "progress"})

    summary = orchestrator.run(max_epochs=3, parallel=3, sync=False, triage=False)

    assert sorted(result.problem_id for result in summary.epochs) == ["local_a", "local_b", "local_c"]
    snapshot = orchestrator.services.portfolio.snapshot()
    assert snapshot.global_epochs == 3
    assert all(entry.folder for entry in snapshot.entries.values())
    assert "# Research portfolio" in orchestrator.paths.portfolio_md.read_text()


def test_triage_sorts_problems_into_candidates_and_deferred(workspace, make_orchestrator):
    orchestrator = make_orchestrator()
    add_problem(orchestrator, "local_keep", "Keep me", status=UNEXPLORED)
    add_problem(orchestrator, "local_defer_me", "Defer me", status=UNEXPLORED)

    report = orchestrator.triage()

    assert report.activated == ["local_keep"] and report.deferred == ["local_defer_me"]
    keep = orchestrator.services.portfolio.get("local_keep")
    assert keep.status == CANDIDATE and keep.triage.suitability == pytest.approx(22.5 / 6, abs=1e-3)
    assert orchestrator.services.portfolio.get("local_defer_me").status == DEFERRED
    assert [call["role"] for call in fake_calls(workspace)] == ["triage"]


def test_human_commands_are_applied_between_epochs(workspace, make_orchestrator):
    orchestrator = make_orchestrator({"loop": {"iterations_per_epoch": 6}})
    add_problem(orchestrator, "local_s", "Stuck problem")
    set_scenarios(workspace, {"local_s": "stagnate"})
    orchestrator.run(max_epochs=1, sync=False, triage=False)
    assert orchestrator.services.portfolio.get("local_s").status == SUSPENDED

    inbox.post(orchestrator.paths, "hint", "local_s", text="Try the symmetric subspace.")
    inbox.post(orchestrator.paths, "pin", "local_s", pinned=True)
    assert orchestrator.apply_inbox() == 2

    entry = orchestrator.services.portfolio.get("local_s")
    assert entry.status == ACTIVE and entry.pinned and entry.stagnation == 0
    state = load_state(orchestrator.paths.problems_dir / entry.folder / "state.json")
    assert state.hints[-1].text == "Try the symmetric subspace."
    assert inbox.pending(orchestrator.paths) == []

    inbox.post(orchestrator.paths, "set_status", "local_s", status=DEFERRED)
    orchestrator.apply_inbox()
    assert orchestrator.services.portfolio.get("local_s").status == DEFERRED


def test_one_orchestrator_per_workspace(make_orchestrator):
    make_orchestrator()
    with pytest.raises(WorkspaceBusy):
        make_orchestrator()


def test_agent_settings_cannot_change_during_a_run(make_orchestrator):
    orchestrator = make_orchestrator()
    agents_toml = orchestrator.paths.agents_config
    original = agents_toml.read_text()
    agents_toml.write_text(original + "\n# edited by an agent\n")
    assert [v.path for v in orchestrator.services.guard.check_and_restore()] == ["agents.toml"]
    assert agents_toml.read_text() == original


def test_stop_file_drains_the_loop(workspace, make_orchestrator):
    orchestrator = make_orchestrator()
    add_problem(orchestrator, "local_x", "Never started")
    orchestrator.paths.stop_file.touch()
    summary = orchestrator.run(max_epochs=1, sync=False, triage=False)
    assert summary.epochs == [] and summary.stopped_reason == "stop requested"


def test_runs_triage_more_problems_when_candidates_run_out(workspace, make_orchestrator):
    orchestrator = make_orchestrator({"loop": {"iterations_per_epoch": 1},
                                      "triage": {"max_per_run": 1, "batch_size": 1}})
    add_problem(orchestrator, "local_first", "First", status=UNEXPLORED)
    add_problem(orchestrator, "local_second", "Second", status=UNEXPLORED)
    set_scenarios(workspace, {"local_first": "resolve", "local_second": "progress"})

    summary = orchestrator.run(max_epochs=2, sync=False)

    assert sorted(result.problem_id for result in summary.epochs) == ["local_first", "local_second"]
    assert [call["role"] for call in fake_calls(workspace)].count("triage") == 2
