"""End-to-end epochs driven by the scripted fake agent."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from support import add_problem, fake_calls, set_scenarios

from lean_orchestrator import inbox
from lean_orchestrator.jsonio import read_jsonl
from lean_orchestrator.portfolio import ACTIVE, PENDING_REVIEW, RESOLVED_EXTERNALLY, SOLVED, SUSPENDED
from lean_orchestrator.state import load_state


def _problem_dir(orchestrator, problem_id: str) -> Path:
    entry = orchestrator.services.portfolio.get(problem_id)
    assert entry.folder, "the problem folder should exist after an epoch"
    return orchestrator.paths.problems_dir / entry.folder


def test_progress_epoch_builds_the_problem_folder_and_records_verified_results(workspace, make_orchestrator):
    orchestrator = make_orchestrator()
    add_problem(orchestrator, "local_alpha", "Nonnegativity of Q")
    set_scenarios(workspace, {"local_alpha": "progress"})

    summary = orchestrator.run(max_epochs=1, sync=False, triage=False)

    assert summary.errors == []
    assert [result.problem_id for result in summary.epochs] == ["local_alpha"]
    folder = _problem_dir(orchestrator, "local_alpha")
    assert folder.name.startswith("nonnegativity-of-q-")
    for name in ("PROBLEM.md", "PROGRESS.md", "DOSSIER.md", "state.json", "source.json", "reports/epoch-001.md"):
        assert (folder / name).is_file(), name
    assert (folder / "lean").is_symlink()
    first = folder / "iterations" / "e001-i01"
    for name in ("plan.json", "report.json", "verification.json", "critique.json", "decision.json", "outcome.json"):
        assert (first / name).is_file(), name
    assert (folder / "iterations" / "e001-i00" / "literature.json").is_file()

    state = load_state(folder / "state.json")
    assert [claim.trust for claim in state.claims] == ["reproduced"] * 3
    assert state.stagnation_count == 0
    assert len(state.attempts) == 3 and len(state.dead_ends) == 3
    assert state.literature.still_open == "yes"
    assert state.statement_versions[-1].reason == "precise statement from the literature check"
    assert [branch.kind for branch in state.branches] == ["prove", "disprove"]
    assert [record.effective_score for record in state.progress] == [2, 2, 2]

    entry = orchestrator.services.portfolio.get("local_alpha")
    assert entry.status == ACTIVE
    assert (entry.epochs, entry.iterations) == (1, 3)
    assert abs(entry.ewma_progress - 2 / 3) < 1e-9

    results = read_jsonl(orchestrator.paths.knowledge_results)
    assert [item["trust"] for item in results] == ["reproduced"] * 3
    assert "Accepted results across problems" in orchestrator.paths.knowledge_md.read_text()
    progress = (folder / "PROGRESS.md").read_text()
    assert "## Verified and accepted results" in progress and "`reproduced`" in progress
    assert "The fake epoch went fine." in (folder / "reports" / "epoch-001.md").read_text()

    roles = [call["role"] for call in fake_calls(workspace)]
    assert roles[:5] == ["literature", "supervisor", "researcher", "critic", "supervisor"]
    assert state.violations == [], "the orchestrator's own writes must never look like tampering"
    assert "integrity_violation" not in [event["kind"] for event in orchestrator.services.events.read()]
    log = subprocess.run(["git", "log", "--format=%s"], cwd=workspace, capture_output=True, text=True).stdout
    assert "research(" in log and "iteration 1, score 2" in log


def test_stagnation_escalates_then_suspends(workspace, make_orchestrator):
    orchestrator = make_orchestrator({"loop": {"iterations_per_epoch": 6}})
    add_problem(orchestrator, "local_beta", "A stubborn problem")
    set_scenarios(workspace, {"local_beta": "stagnate"})

    summary = orchestrator.run(max_epochs=1, sync=False, triage=False)

    result = summary.epochs[0]
    assert result.decision == "suspend"
    assert result.iterations == 4
    assert orchestrator.services.portfolio.get("local_beta").status == SUSPENDED
    state = load_state(_problem_dir(orchestrator, "local_beta") / "state.json")
    assert state.stagnation_count == 4
    supervisor_prompts = sorted(
        path.read_text() for path in orchestrator.paths.runs_dir.glob("*supervisor*/prompt.md")
    )
    assert any("Stagnation warning" in prompt for prompt in supervisor_prompts)
    repeats = [attempt.repeat_of for attempt in state.attempts]
    assert repeats[0] == [] and repeats[1] == ["A1"]


def test_resolution_claim_awaits_human_review_and_can_be_accepted(workspace, make_orchestrator):
    orchestrator = make_orchestrator()
    add_problem(orchestrator, "local_gamma", "An easy problem")
    set_scenarios(workspace, {"local_gamma": "resolve"})

    summary = orchestrator.run(max_epochs=3, sync=False, triage=False)

    assert len(summary.epochs) == 1, "a problem awaiting review is not scheduled again"
    assert summary.epochs[0].decision == "resolution claimed (proves)"
    assert orchestrator.services.portfolio.get("local_gamma").status == PENDING_REVIEW

    inbox.post(orchestrator.paths, "review", "local_gamma", decision="accept", reason="checked by hand")
    orchestrator.apply_inbox()
    assert orchestrator.services.portfolio.get("local_gamma").status == SOLVED


def test_tampering_with_the_state_is_undone_and_recorded(workspace, make_orchestrator):
    orchestrator = make_orchestrator({"loop": {"iterations_per_epoch": 1}})
    add_problem(orchestrator, "local_delta", "Tamper target")
    set_scenarios(workspace, {"local_delta": "tamper"})

    orchestrator.run(max_epochs=1, sync=False, triage=False)

    state = load_state(_problem_dir(orchestrator, "local_delta") / "state.json")
    assert state.stagnation_count == 0
    assert any("state.json was modified" in violation for violation in state.violations)
    kinds = [event["kind"] for event in orchestrator.services.events.read()]
    assert "integrity_violation" in kinds


def test_invalid_reports_are_repaired_once_then_the_epoch_fails(workspace, make_orchestrator):
    orchestrator = make_orchestrator()
    add_problem(orchestrator, "local_eps", "Garbage producer")
    set_scenarios(workspace, {"local_eps": "garbage"})

    summary = orchestrator.run(max_epochs=1, sync=False, triage=False)

    result = summary.epochs[0]
    assert result.decision == "failed"
    researcher_calls = [call for call in fake_calls(workspace) if call["role"] == "researcher"]
    assert len(researcher_calls) == 4, "two iterations, each with one repair attempt"
    entry = orchestrator.services.portfolio.get("local_eps")
    assert entry.consecutive_failures == 1
    state = load_state(_problem_dir(orchestrator, "local_eps") / "state.json")
    assert [attempt.outcome for attempt in state.attempts] == ["inconclusive", "inconclusive"]


def test_known_result_marks_the_problem_resolved_externally(workspace, make_orchestrator):
    orchestrator = make_orchestrator()
    add_problem(orchestrator, "local_zeta", "Already solved")
    set_scenarios(workspace, {"local_zeta": "known"})

    summary = orchestrator.run(max_epochs=2, sync=False, triage=False)

    assert [result.decision for result in summary.epochs] == ["resolved_externally"]
    assert orchestrator.services.portfolio.get("local_zeta").status == RESOLVED_EXTERNALLY
    assert [call["role"] for call in fake_calls(workspace)] == ["literature"]


def test_outcome_file_reports_orchestrator_assigned_trust(workspace, make_orchestrator):
    orchestrator = make_orchestrator({"loop": {"iterations_per_epoch": 1}})
    add_problem(orchestrator, "local_eta", "Outcome check")
    set_scenarios(workspace, {"local_eta": "progress"})

    orchestrator.run(max_epochs=1, sync=False, triage=False)

    outcome = json.loads((_problem_dir(orchestrator, "local_eta") / "iterations" / "e001-i01" / "outcome.json").read_text())
    assert outcome["claims"]["c1"]["trust"] == "reproduced"
    assert "experiment: reproduced" in outcome["claims"]["c1"]["verification"]
    assert outcome["weighted_gain"] == 1.0 + 0.5
