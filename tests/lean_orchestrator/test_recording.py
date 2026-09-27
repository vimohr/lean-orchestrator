from __future__ import annotations

import pytest

from lean_orchestrator.config import ProgressConfig
from lean_orchestrator.recording import (
    IterationOutcome, apply_decision, decide_trust, find_repeats, record_iteration, record_literature, score_progress,
    similarity,
)
from lean_orchestrator.state import (
    CRITIC_ACCEPTED, LEAN_VERIFIED, PROPOSED, REJECTED, REPRODUCED, RETRACTED, Attempt, Branch, Claim, ResearchState,
    Stamp, Subgoal,
)
from lean_orchestrator.verification import ClaimVerification, IterationVerification
from lean_orchestrator.verify.citations import CitationCheck
from lean_orchestrator.verify.experiments import ExperimentCheck
from lean_orchestrator.verify.lean import LeanCheck, TrivialityResult


def lean(ok: bool = True, triviality: TrivialityResult | None = None) -> LeanCheck:
    return LeanCheck(file="lean/OpenQ/Problems/X/A.lean", declaration="OpenQ.Problems.X.a", ok=ok,
                     decl_type="True", triviality=triviality)


def review(verdict: str = "accept", faithful: str = "yes", issues=None) -> dict:
    return {"claim": "c1", "verdict": verdict, "formalization_faithful": faithful, "issues": issues or [],
            "confidence": 0.9, "checks_performed": ["recomputed"], "weakest_step": "none"}


def state() -> ResearchState:
    result = ResearchState(problem_id="p", title="P", folder="p", lean_namespace="P")
    result.branches.append(Branch(id="B1", kind="prove", goal="prove"))
    result.counters["B"] = 1
    return result


@pytest.mark.parametrize(
    ("claim", "verification", "critic", "expected"),
    [
        ({"kind": "lemma"}, ClaimVerification("c1", lean=lean()), review(), LEAN_VERIFIED),
        ({"kind": "lemma"}, ClaimVerification("c1", lean=lean()), review(verdict="uncertain"), LEAN_VERIFIED),
        ({"kind": "lemma"}, ClaimVerification("c1", lean=lean()), review(faithful="no"), CRITIC_ACCEPTED),
        ({"kind": "lemma"}, ClaimVerification("c1", lean=lean(ok=False)), review(), PROPOSED),
        ({"kind": "lemma"}, ClaimVerification("c1", lean=lean()), review(verdict="reject"), REJECTED),
        ({"kind": "lemma"}, None, review(), CRITIC_ACCEPTED),
        ({"kind": "lemma"}, None, None, PROPOSED),
        ({"kind": "counterexample"}, ClaimVerification("c1", experiment=ExperimentCheck("e", ok=True)), review(),
         REPRODUCED),
        ({"kind": "counterexample"}, ClaimVerification("c1", experiment=ExperimentCheck("e", ok=False)), review(),
         PROPOSED),
        ({"kind": "conjecture"}, None, review(), PROPOSED),
        ({"kind": "literature"}, ClaimVerification("c1", citation=CitationCheck("not_found")), review(), PROPOSED),
        ({"kind": "formal_statement"}, ClaimVerification("c1", lean=lean()), review(), CRITIC_ACCEPTED),
        ({"kind": "formal_statement"}, ClaimVerification("c1", lean=lean()), review(faithful="not_applicable"),
         PROPOSED),
        ({"kind": "formal_statement"},
         ClaimVerification("c1", lean=lean(triviality=TrivialityResult(statement_provable=True, ran=True))),
         review(), PROPOSED),
    ],
)
def test_trust_combines_machine_checks_with_the_critic(claim, verification, critic, expected):
    trust, _notes = decide_trust(claim, verification, critic)
    assert trust == expected


def _report(**overrides) -> dict:
    report = {
        "summary": "s",
        "approach": {"name": "idea", "description": "use convexity of the map", "fingerprint": "convexity :: main"},
        "outcome": "partial",
        "claims": [{"id": "c1", "kind": "lemma", "statement": "L1", "argument": "a", "subgoal": "G1"},
                   {"id": "c2", "kind": "lemma", "statement": "L2", "argument": "b", "depends_on": ["c1"]}],
        "dead_ends": [{"approach": "d1", "reason": "r1"}, {"approach": "d2", "reason": "r2"}],
        "new_subgoals": ["new goal"],
    }
    report.update(overrides)
    return report


def test_record_iteration_assigns_ids_trust_subgoals_and_dead_ends():
    current = state()
    current.subgoals.append(Subgoal(id="G1", statement="g"))
    current.counters["G"] = 1
    critique = {"reviews": [review(), dict(review(verdict="reject", issues=[
        {"severity": "major", "location": "step 2", "problem": "gap"}]), claim="c2")],
                "overall": "ok", "dead_end_disputes": [{"index": 1, "reason": "not ruled out"}]}
    outcome = record_iteration(current, stamp=Stamp(1, 1), plan={"branch": "B1"}, report=_report(),
                               verification=IterationVerification(), critique=critique)
    assert outcome.claim_ids == {"c1": "C1", "c2": "C2"}
    assert [claim.trust for claim in current.claims] == [CRITIC_ACCEPTED, REJECTED]
    assert current.claim("C2").depends_on == ["C1"]
    assert current.subgoal("G1").status == "closed" and current.subgoal("G1").closed_by == "C1"
    assert [dead_end.approach for dead_end in current.dead_ends] == ["d1"]
    assert [goal.statement for goal in current.subgoals] == ["g", "new goal"]
    assert current.branch("B1").iterations == 1
    assert outcome.informal_accepted == ["C1"] and outcome.rejected == ["C2"]
    assert outcome.weighted_gain(ProgressConfig()) == 0.5 + 0.5


def test_restated_subgoals_update_the_open_subgoal_instead_of_duplicating_it():
    current = state()
    current.subgoals.append(Subgoal(id="G1", statement="Check d = 4 numerically."))
    current.counters["G"] = 1
    record_iteration(current, stamp=Stamp(1, 1), plan={"branch": "B1"},
                     report=_report(claims=[], new_subgoals=["check  d = 4 numerically", "new goal"]),
                     verification=IterationVerification(), critique=None)
    assert [goal.statement for goal in current.subgoals] == ["Check d = 4 numerically.", "new goal"]
    apply_decision(current, {"subgoal_updates": [{"statement": "Check d = 4 numerically", "status": "closed",
                                                   "closed_by": "C1"}]}, stamp=Stamp(1, 2))
    assert [(goal.id, goal.status) for goal in current.subgoals] == [("G1", "closed"), ("G2", "open")]
    apply_decision(current, {"subgoal_updates": [{"statement": "Check d = 4 numerically.", "status": "open"}]},
                   stamp=Stamp(2, 0))
    assert [(goal.id, goal.status) for goal in current.subgoals][-1] == ("G3", "open"), "closed goals may reopen anew"


def test_retractions_spare_lean_verified_claims():
    current = state()
    current.claims += [Claim(id="C1", kind="lemma", statement="a", trust=CRITIC_ACCEPTED),
                       Claim(id="C2", kind="lemma", statement="b", trust=LEAN_VERIFIED)]
    critique = {"reviews": [], "overall": "x",
                "retractions": [{"claim": "C1", "reason": "wrong sign"}, {"claim": "C2", "reason": "unfaithful"}]}
    outcome = record_iteration(current, stamp=Stamp(1, 2), plan=None, report=_report(claims=[]),
                               verification=IterationVerification(), critique=critique)
    assert current.claim("C1").trust == RETRACTED
    assert current.claim("C2").trust == LEAN_VERIFIED and "Disputed" in current.claim("C2").critic_notes
    assert outcome.retracted == ["C1"]


def test_repeated_approaches_are_detected_lexically():
    current = state()
    current.attempts.append(Attempt(id="A1", approach="x", description="use convexity of the map",
                                    outcome="failure", fingerprint="convexity :: main"))
    assert find_repeats(current, "convexity :: main", "use convexity of the map") == ["A1"]
    assert find_repeats(current, "SDP dual certificate :: d=3", "numerical search") == []
    assert similarity("a b c", "") == 0.0


@pytest.mark.parametrize(
    ("supervisor", "gain_kind", "repeated", "expected"),
    [
        (3, None, False, 1),
        (2, "informal", False, 1),
        (2, "reproduced", False, 2),
        (1, "lean", False, 2),
        (2, "reproduced", True, 0),
    ],
)
def test_progress_scores_are_grounded_in_artifacts(supervisor, gain_kind, repeated, expected):
    current = state()
    outcome = IterationOutcome(attempt_id="A1")
    if gain_kind == "informal":
        outcome.informal_accepted = ["C1"]
    elif gain_kind == "reproduced":
        outcome.reproduced = ["C1"]
    elif gain_kind == "lean":
        outcome.lean_verified = ["C1"]
    if repeated:
        outcome.repeat_of = ["A0"]
    assessment = {"progress_score": supervisor, "categories": [], "justification": "j", "repeated_approach": False}
    record = score_progress(current, stamp=Stamp(1, 1), assessment=assessment, outcome=outcome,
                            progress=ProgressConfig())
    assert record.effective_score == expected
    assert current.stagnation_count == (0 if expected >= 2 else 1)


def test_stagnation_resets_on_meaningful_progress():
    current = state()
    current.stagnation_count = 3
    outcome = IterationOutcome(attempt_id="A1", lean_verified=["C1"])
    score_progress(current, stamp=Stamp(1, 1), assessment=None, outcome=outcome, progress=ProgressConfig())
    assert current.stagnation_count == 0
    score_progress(current, stamp=Stamp(1, 2), assessment=None, outcome=None, progress=ProgressConfig())
    assert current.stagnation_count == 1


def test_apply_decision_opens_labelled_branches_and_maps_the_plan():
    current = state()
    decision = {
        "assessment": None, "decision": "branch", "rationale": "r", "assessment_text": "text",
        "branch_updates": [{"label": "cex", "kind": "disprove", "goal": "find a counterexample", "status": "open"},
                           {"id": "B1", "goal": "prove", "status": "suspended", "reason": "stuck"}],
        "subgoal_updates": [{"statement": "d = 3 first", "status": "open", "branch": "cex"}],
        "next_step": {"branch": "cex", "task": "experiment", "goal": "search d = 3", "instructions": "i",
                      "success_test": "t", "novelty": "n"},
        "statement_refinement": {"text": "refined", "reason": "clarity"},
        "promising_directions": ["see-saw"],
        "epoch_report": None,
    }
    effects = apply_decision(current, decision, stamp=Stamp(1, 0))
    assert effects.new_branches == ["B2"]
    assert current.branch("B1").status == "suspended"
    assert effects.plan["branch"] == "B2" and effects.plan["branch_kind"] == "disprove"
    assert current.subgoals[0].branch == "B2"
    assert current.statement_versions[-1].text == "refined"
    assert current.promising_directions == ["see-saw"]
    assert current.next_plan == effects.plan


def test_record_literature_adds_statement_references_and_branches():
    current = state()
    current.branches.clear()
    literature = {
        "still_open": "yes", "summary": "open", "precise_statement": "precise", "success_criteria": "crit",
        "assumptions": ["a"], "references": [{"citation": "Paper", "arxiv": "2405.09613"}],
        "known_results": [{"statement": "known", "reference": "Paper"}], "techniques": ["SDP"],
        "suggested_branches": [{"kind": "disprove", "goal": "search"}],
    }
    verified = record_literature(current, literature, [CitationCheck("verified")], stamp=Stamp(1, 0))
    assert verified == 1
    assert current.current_statement == "precise" and current.success_criteria == "crit"
    assert current.references[0].check == "verified" and current.known_results[0].statement == "known"
    assert [branch.kind for branch in current.branches] == ["disprove"]
