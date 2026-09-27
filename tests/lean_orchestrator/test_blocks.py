from __future__ import annotations

from lean_orchestrator import blocks
from lean_orchestrator.config import ProgressConfig
from lean_orchestrator.portfolio import CANDIDATE, DEFERRED, Portfolio, PortfolioEntry
from lean_orchestrator.state import Attempt, Branch, FormalStatement, Hint, ResearchState


def state() -> ResearchState:
    current = ResearchState(problem_id="p", title="P", folder="p", lean_namespace="P")
    current.branches += [Branch(id="B1", kind="prove", goal="prove"), Branch(id="B2", kind="disprove", goal="refute")]
    current.attempts += [Attempt(id=f"A{index}", approach="a", description="x", outcome="failure", branch="B1",
                                 fingerprint="convexity :: main statement", failure_reason="too weak")
                         for index in range(1, 5)]
    return current


def test_neglected_branches_are_reported():
    assert "Branch B2 (disprove) has not been advanced for 4 iterations" in blocks.neglected_branches(state(), 4)
    assert blocks.neglected_branches(state(), 5) == ""


def test_escalation_starts_at_the_configured_count():
    current = state()
    current.stagnation_count = 1
    assert blocks.escalation_block(current, ProgressConfig()) == ""
    current.stagnation_count = 3
    assert "suspended automatically after 1 more" in blocks.escalation_block(current, ProgressConfig())


def test_warnings_flag_similar_failed_attempts_and_violations():
    plan = {"goal": "use convexity on the main statement", "instructions": "convexity :: main statement"}
    text = blocks.warnings_block(state(), plan, ["state.json was modified; restored"])
    assert "resembles failed attempt A1" in text and "Integrity: state.json was modified" in text
    assert blocks.warnings_block(state(), {"goal": "numerical search", "instructions": "SDP"}, []) == ""


def test_plan_formatting_and_guidance():
    plan = {"branch": "B2", "branch_kind": "disprove", "branch_goal": "refute", "task": "experiment", "goal": "g",
            "instructions": "i", "success_test": "s", "novelty": "n", "deliverables": ["script"]}
    text = blocks.format_plan(plan)
    assert "- Branch: B2 (disprove): refute" in text and "Deliverables: script" in text
    assert "counterexample" in blocks.branch_guidance(plan)
    assert blocks.branch_guidance({"branch_kind": "unknown"}) == ""


def test_relevance_block_lists_other_schedulable_problems_and_incoming_links():
    portfolio = Portfolio(entries={
        "p": PortfolioEntry(id="p", source="local", title="Self", status=CANDIDATE),
        "q": PortfolioEntry(id="q", source="local", title="Other", status=CANDIDATE),
        "r": PortfolioEntry(id="r", source="local", title="Deferred", status=DEFERRED),
    })
    text = blocks.relevance_block(state(), portfolio, [{"from": "q", "claims": ["C3"], "why": "same SDP"}])
    assert "from `q` (claims C3): same SDP" in text
    assert "- `q`: Other" in text and "Deferred" not in text and "Self" not in text


def test_small_blocks():
    current = state()
    assert blocks.formal_statement_note(current) == "- No formal statement is locked yet."
    current.formal_statements.append(FormalStatement(file="f.lean", decl="D", claim="C1"))
    assert "`D` in `f.lean`" in blocks.formal_statement_note(current)
    current.hints.append(Hint(text="look at d = 3"))
    assert "look at d = 3" in blocks.hints_block(current)
    assert "B1 [prove, open" in blocks.branch_list(current)
    assert blocks.epoch_end_instructions(False) == "" and "epoch_report" in blocks.epoch_end_instructions(True)
