"""Turn agent outputs and verification results into persistent research state.

The rules here decide what counts as knowledge. The critic's verdict can only
lower a claim's standing, never raise it past what the machine checks support:

* ``lean_verified``: Lean verified the declaration and the critic confirmed that
  the Lean statement faithfully formalizes the claim;
* ``reproduced``: an independent re-run of the experiment matched and the critic
  accepted the interpretation;
* ``critic_accepted``: the critic accepted the claim and every artifact the claim
  cites passed its check;
* ``proposed``: anything else that was not rejected;
* ``rejected``: the critic rejected the claim with a concrete issue.

Progress is scored by the supervisor but grounded here. Accepted artifacts are
weighted by how they were checked, and an iteration scores as meaningful progress
only when the weighted gain reaches ``progress.meaningful_gain``. A new
Lean-verified result is always meaningful, and repeating a failed approach
counts as stagnation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .config import ProgressConfig
from .state import (
    ACCEPTED_TRUST, CRITIC_ACCEPTED, LEAN_VERIFIED, PROPOSED, REJECTED, REPRODUCED, RETRACTED,
    Attempt, Branch, Claim, DeadEnd, Experiment, FormalStatement, KnownResult, ProgressRecord,
    Reference, ResearchState, Search, Stamp, StatementVersion, Subgoal,
)
from .verification import ClaimVerification, IterationVerification
from .verify.citations import NOT_FOUND, TITLE_MISMATCH, VERIFIED

REPEAT_SIMILARITY = 0.6


@dataclass
class IterationOutcome:
    attempt_id: str
    claim_ids: dict[str, str] = field(default_factory=dict)
    accepted: list[str] = field(default_factory=list)
    lean_verified: list[str] = field(default_factory=list)
    reproduced: list[str] = field(default_factory=list)
    informal_accepted: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    retracted: list[str] = field(default_factory=list)
    dead_ends_added: int = 0
    references_verified: int = 0
    formal_statement_locked: str | None = None
    main_result: tuple[str, str, str] | None = None
    repeat_of: list[str] = field(default_factory=list)
    lock_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def weighted_gain(self, weights: ProgressConfig) -> float:
        """Accepted artifacts weighted by the strength of their verification."""
        return (
            weights.weight_lean_verified * len(self.lean_verified)
            + weights.weight_reproduced * len(self.reproduced)
            + weights.weight_informal_claim * len(self.informal_accepted)
            + weights.weight_formal_statement * (1 if self.formal_statement_locked else 0)
            + weights.weight_dead_end * self.dead_ends_added
            + weights.weight_reference * self.references_verified
        )


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if len(word) > 2}


def similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_repeats(state: ResearchState, fingerprint: str, description: str) -> list[str]:
    """Earlier unsuccessful attempts whose approach closely matches this one."""
    repeats = []
    for attempt in state.attempts:
        if attempt.outcome == "success":
            continue
        score = max(
            similarity(fingerprint, attempt.fingerprint),
            similarity(f"{fingerprint} {description}", f"{attempt.fingerprint} {attempt.description}"),
        )
        if score >= REPEAT_SIMILARITY:
            repeats.append(attempt.id)
    return repeats


def decide_trust(claim: dict[str, Any], verification: ClaimVerification | None,
                 review: dict[str, Any] | None) -> tuple[str, list[str]]:
    """Combine machine checks and the critic's review into a trust level."""
    notes: list[str] = []
    verdict = review.get("verdict") if review else None
    faithful = review.get("formalization_faithful") if review else None
    lean = verification.lean if verification else None
    experiment = verification.experiment if verification else None
    kind = claim.get("kind")

    if review is None:
        notes.append("no critic review was recorded")
    if verdict == "reject":
        return REJECTED, notes
    if kind == "conjecture":
        return PROPOSED, notes
    if kind == "formal_statement":
        trivial = lean is not None and lean.triviality is not None and lean.triviality.trivial
        if trivial:
            notes.append(f"triviality probe: {lean.triviality.detail}; the formalization cannot be locked")
        if lean is not None and lean.ok and faithful == "yes" and verdict == "accept" and not trivial:
            return CRITIC_ACCEPTED, notes
        if lean is None:
            notes.append("a formal statement needs a Lean file and declaration")
        elif not lean.ok:
            notes.append("the Lean statement did not compile as a definition of type Prop")
        if faithful != "yes":
            notes.append("the critic did not confirm the formalization is faithful")
        return PROPOSED, notes
    if lean is not None and lean.ok and faithful == "yes":
        return LEAN_VERIFIED, notes
    if lean is not None and lean.ok and faithful == "no":
        notes.append("Lean verified the declaration, but it does not formalize the stated claim")
    if lean is not None and not lean.ok:
        notes.append("the cited Lean proof did not verify")
    if experiment is not None and not experiment.ok:
        notes.append("the cited experiment was not reproduced")
    citation = verification.citation if verification else None
    if citation is not None and citation.status in (NOT_FOUND, TITLE_MISMATCH):
        notes.append(f"the cited reference could not be confirmed ({citation.status})")
        return PROPOSED, notes
    artifacts_ok = (lean is None or lean.ok) and (experiment is None or experiment.ok)
    if verdict == "accept" and artifacts_ok:
        if experiment is not None and experiment.ok:
            return REPRODUCED, notes
        return CRITIC_ACCEPTED, notes
    return PROPOSED, notes


def record_iteration(
    state: ResearchState,
    *,
    stamp: Stamp,
    plan: dict[str, Any] | None,
    report: dict[str, Any],
    verification: IterationVerification,
    critique: dict[str, Any] | None,
) -> IterationOutcome:
    """Merge one iteration into ``state`` and report what changed."""
    branch_id = (plan or {}).get("branch")
    approach = report.get("approach", {})
    outcome = IterationOutcome(attempt_id=state.next_id("A"))
    reviews = {review["claim"]: review for review in (critique or {}).get("reviews", [])}

    for claim in report.get("claims", []):
        global_id = state.next_id("C")
        outcome.claim_ids[claim["id"]] = global_id
    for claim in report.get("claims", []):
        global_id = outcome.claim_ids[claim["id"]]
        claim_verification = verification.claims.get(claim["id"])
        review = reviews.get(claim["id"])
        trust, notes = decide_trust(claim, claim_verification, review)
        lean_ref = claim.get("lean") or {}
        lean_check = claim_verification.lean if claim_verification else None
        record = Claim(
            id=global_id,
            kind=claim["kind"],
            statement=claim["statement"],
            argument=claim.get("argument", ""),
            trust=trust,
            branch=branch_id,
            lean_file=lean_check.file if lean_check else lean_ref.get("file"),
            lean_decl=lean_ref.get("declaration"),
            lean_type=lean_check.decl_type if lean_check else None,
            experiment=claim.get("experiment"),
            depends_on=[outcome.claim_ids.get(item, item) for item in claim.get("depends_on", [])],
            resolves_main=claim.get("resolves_main"),
            critic_verdict=review.get("verdict") if review else None,
            critic_notes=_review_text(review),
            verification="; ".join(filter(None, [
                claim_verification.summary() if claim_verification else "", *notes,
            ])),
            created=stamp,
        )
        state.claims.append(record)
        if trust in ACCEPTED_TRUST:
            outcome.accepted.append(global_id)
            if record.kind != "formal_statement":
                _close_subgoal(state, claim.get("subgoal"), global_id)
        if trust == LEAN_VERIFIED:
            outcome.lean_verified.append(global_id)
        elif trust == REPRODUCED:
            outcome.reproduced.append(global_id)
        elif trust == CRITIC_ACCEPTED and record.kind != "formal_statement":
            outcome.informal_accepted.append(global_id)
        if trust == REJECTED:
            outcome.rejected.append(global_id)
        if trust in (LEAN_VERIFIED,) or (record.kind == "formal_statement" and trust == CRITIC_ACCEPTED):
            if lean_check is not None:
                outcome.lock_files.append(lean_check.file)
        if record.kind == "formal_statement" and trust == CRITIC_ACCEPTED and lean_check is not None:
            for previous in state.formal_statements:
                previous.active = False
            state.formal_statements.append(FormalStatement(
                file=lean_check.file, decl=record.lean_decl or "", claim=global_id, created=stamp,
            ))
            outcome.formal_statement_locked = global_id
        if record.kind == "main_result" and record.resolves_main and trust in ACCEPTED_TRUST:
            outcome.main_result = (global_id, record.resolves_main, trust)

    for retraction in (critique or {}).get("retractions", []):
        target = state.claim(retraction.get("claim", ""))
        if target is None:
            continue
        if target.trust == LEAN_VERIFIED:
            target.critic_notes = (target.critic_notes + f"\nDisputed at {stamp.epoch}.{stamp.iteration}: "
                                   f"{retraction['reason']}").strip()
            outcome.notes.append(f"{target.id} is Lean-verified; the dispute was recorded without retraction")
        elif target.trust in ACCEPTED_TRUST or target.trust == PROPOSED:
            target.trust = RETRACTED
            target.critic_notes = (target.critic_notes + f"\nRetracted: {retraction['reason']}").strip()
            target.updated = stamp
            outcome.retracted.append(target.id)

    disputed = {item.get("index") for item in (critique or {}).get("dead_end_disputes", [])}
    for index, dead_end in enumerate(report.get("dead_ends", [])):
        if index in disputed:
            continue
        state.dead_ends.append(DeadEnd(
            approach=dead_end["approach"], reason=dead_end["reason"], scope=dead_end.get("scope", ""),
            attempt=outcome.attempt_id, created=stamp,
        ))
        outcome.dead_ends_added += 1

    for item in report.get("searches", []):
        state.searches.append(Search(
            space=item["space"], method=item["method"], result=item["result"],
            experiment=item.get("experiment"), created=stamp,
        ))
    for item in report.get("experiments", []):
        reproduced = "unchecked"
        for key, check in verification.experiments.items():
            if key.endswith(item["path"].rstrip("/")):
                reproduced = "yes" if check.ok else "no"
        state.experiments.append(Experiment(
            path=item["path"], purpose=item["purpose"], result_summary=item.get("result_summary", ""),
            reproduced=reproduced, created=stamp,
        ))
    for text in report.get("new_subgoals", []):
        state.subgoals.append(Subgoal(id=state.next_id("G"), statement=text, branch=branch_id, created=stamp))
    for reference in verification.references:
        matching = next((item for item in report.get("references", []) if item["citation"] == reference.citation), {})
        state.references.append(Reference(
            id=state.next_id("R"), citation=reference.citation, url=matching.get("url"),
            arxiv=matching.get("arxiv"), doi=matching.get("doi"), relevance=matching.get("relevance", ""),
            check=reference.check.status, created=stamp,
        ))
        if reference.check.status == VERIFIED:
            outcome.references_verified += 1

    fingerprint = approach.get("fingerprint", "")
    description = approach.get("description", "")
    outcome.repeat_of = sorted(set(find_repeats(state, fingerprint, description))
                               | set((critique or {}).get("repeat_of", [])))
    state.attempts.append(Attempt(
        id=outcome.attempt_id,
        approach=approach.get("name", ""),
        description=description,
        outcome=report.get("outcome", "inconclusive"),
        branch=branch_id,
        fingerprint=fingerprint,
        failure_reason=report.get("failure_reason") or "",
        lesson=report.get("lessons", "") or (critique or {}).get("overall", ""),
        claims=list(outcome.claim_ids.values()),
        repeat_of=outcome.repeat_of,
        created=stamp,
    ))
    branch = state.branch(branch_id)
    if branch is not None:
        branch.iterations += 1
        branch.last_worked = stamp
    return outcome


def record_failed_iteration(state: ResearchState, *, stamp: Stamp, plan: dict[str, Any] | None, reason: str) -> str:
    attempt_id = state.next_id("A")
    state.attempts.append(Attempt(
        id=attempt_id, approach="(no report)", description=reason, outcome="inconclusive",
        branch=(plan or {}).get("branch"), failure_reason=reason, created=stamp,
    ))
    return attempt_id


def _review_text(review: dict[str, Any] | None) -> str:
    if not review:
        return ""
    lines = [f"{issue['severity']}: {issue['location']}: {issue['problem']}" for issue in review.get("issues", [])]
    lines.extend(f"hidden assumption: {item}" for item in review.get("hidden_assumptions", []))
    return "\n".join(lines)


def _close_subgoal(state: ResearchState, subgoal_id: str | None, claim_id: str) -> None:
    goal = state.subgoal(subgoal_id)
    if goal is not None and goal.status == "open":
        goal.status = "closed"
        goal.closed_by = claim_id


def score_progress(
    state: ResearchState,
    *,
    stamp: Stamp,
    assessment: dict[str, Any] | None,
    outcome: IterationOutcome | None,
    progress: ProgressConfig,
) -> ProgressRecord:
    """Ground the supervisor's score in recorded artifacts and update stagnation."""
    supervisor_score = int(assessment.get("progress_score", 0)) if assessment else 0
    score = supervisor_score
    adjustments: list[str] = []
    repeated = bool(assessment and assessment.get("repeated_approach")) or bool(outcome and outcome.repeat_of)
    if outcome is None:
        score = 0
        adjustments.append("the iteration produced no usable report")
    else:
        gain = outcome.weighted_gain(progress)
        if repeated and score > 0:
            score = 0
            adjustments.append("repeats a previously failed approach: counted as stagnation")
        if gain < progress.meaningful_gain and score >= 2:
            score = 1
            adjustments.append(
                f"weighted artifact gain {gain:g} is below {progress.meaningful_gain:g}: capped at 1"
            )
        if outcome.lean_verified and score < 2 and not repeated:
            score = 2
            adjustments.append("a new Lean-verified result: raised to 2")
    threshold = progress.threshold
    record = ProgressRecord(
        epoch=stamp.epoch,
        iteration=stamp.iteration,
        supervisor_score=supervisor_score,
        effective_score=score,
        categories=list(assessment.get("categories", [])) if assessment else [],
        justification=assessment.get("justification", "") if assessment else "",
        adjustments=adjustments,
    )
    state.progress.append(record)
    if outcome is not None:
        attempt = next((item for item in state.attempts if item.id == outcome.attempt_id), None)
        if attempt is not None:
            attempt.effective_score = score
    branch = state.branch(state.attempts[-1].branch) if state.attempts else None
    if score >= threshold:
        state.stagnation_count = 0
        if branch is not None:
            branch.stagnation = 0
    else:
        state.stagnation_count += 1
        if branch is not None:
            branch.stagnation += 1
    return record


@dataclass
class DecisionEffects:
    plan: dict[str, Any] | None
    epoch_report: str | None
    relevance: list[dict[str, Any]]
    new_branches: list[str]


def apply_decision(state: ResearchState, decision: dict[str, Any], *, stamp: Stamp) -> DecisionEffects:
    """Apply the supervisor's branch, subgoal, and statement updates; return the next plan."""
    labels: dict[str, str] = {}
    new_branches: list[str] = []
    for update in decision.get("branch_updates", []):
        existing = state.branch(update.get("id"))
        if existing is None:
            branch = Branch(
                id=state.next_id("B"), kind=update.get("kind", "explore"), goal=update["goal"],
                status=update.get("status", "open"), note=update.get("reason", ""), created=stamp,
            )
            state.branches.append(branch)
            new_branches.append(branch.id)
            if update.get("label"):
                labels[update["label"]] = branch.id
        else:
            existing.status = update.get("status", existing.status)
            existing.goal = update.get("goal") or existing.goal
            if update.get("kind"):
                existing.kind = update["kind"]
            if update.get("reason"):
                existing.note = update["reason"]
    for update in decision.get("subgoal_updates", []):
        goal = state.subgoal(update.get("id"))
        if goal is None:
            state.subgoals.append(Subgoal(
                id=state.next_id("G"), statement=update["statement"], status=update["status"],
                branch=labels.get(update.get("branch") or "", update.get("branch")),
                closed_by=update.get("closed_by"), created=stamp,
            ))
        else:
            goal.status = update["status"]
            goal.closed_by = update.get("closed_by") or goal.closed_by
    if decision.get("promising_directions") is not None:
        state.promising_directions = list(decision.get("promising_directions", []))
    if decision.get("assessment_text"):
        state.supervisor_assessment = decision["assessment_text"]
    refinement = decision.get("statement_refinement")
    if refinement:
        state.statement_versions.append(StatementVersion(
            version=len(state.statement_versions) + 1, text=refinement["text"], reason=refinement["reason"],
            created=stamp,
        ))
    if decision.get("success_criteria"):
        state.success_criteria = decision["success_criteria"]
    plan = None
    step = decision.get("next_step")
    if step:
        plan = dict(step)
        plan["branch"] = labels.get(step["branch"], step["branch"])
        branch = state.branch(plan["branch"])
        if branch is not None:
            plan["branch_kind"] = branch.kind
            plan["branch_goal"] = branch.goal
    state.next_plan = plan
    return DecisionEffects(
        plan=plan,
        epoch_report=decision.get("epoch_report"),
        relevance=list(decision.get("cross_problem_relevance", [])),
        new_branches=new_branches,
    )


def record_literature(state: ResearchState, literature: dict[str, Any], checks: list[Any], *, stamp: Stamp) -> int:
    """Record a literature check; return the number of verified references."""
    state.literature.still_open = literature["still_open"]
    state.literature.summary = literature["summary"]
    state.literature.resolution = literature.get("resolution", "")
    state.literature.checked = stamp
    if literature.get("precise_statement") and literature["precise_statement"] != state.current_statement:
        state.statement_versions.append(StatementVersion(
            version=len(state.statement_versions) + 1, text=literature["precise_statement"],
            reason="precise statement from the literature check", created=stamp,
        ))
    if literature.get("success_criteria"):
        state.success_criteria = literature["success_criteria"]
    for assumption in literature.get("assumptions", []):
        if assumption not in state.assumptions:
            state.assumptions.append(assumption)
    for technique in literature.get("techniques", []):
        if technique not in state.techniques:
            state.techniques.append(technique)
    verified = 0
    known_citations = {reference.citation for reference in state.references}
    for reference, check in zip(literature.get("references", []), checks):
        if reference["citation"] in known_citations:
            continue
        state.references.append(Reference(
            id=state.next_id("R"), citation=reference["citation"], url=reference.get("url"),
            arxiv=reference.get("arxiv"), doi=reference.get("doi"), relevance=reference.get("relevance", ""),
            check=check.status, created=stamp,
        ))
        verified += check.status == VERIFIED
    for result in literature.get("known_results", []):
        state.known_results.append(KnownResult(statement=result["statement"], reference=result.get("reference", ""),
                                               created=stamp))
    if not state.branches:
        for suggestion in literature.get("suggested_branches", []):
            state.branches.append(Branch(
                id=state.next_id("B"), kind=suggestion["kind"], goal=suggestion["goal"],
                note=suggestion.get("rationale", ""), created=stamp,
            ))
    return verified
