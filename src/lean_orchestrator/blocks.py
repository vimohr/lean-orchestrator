"""Build the problem-specific sections that fill the prompt templates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import ProgressConfig
from .paths import ProblemPaths, WorkspacePaths
from .portfolio import ACTIVE, CANDIDATE, SUSPENDED, Portfolio
from .recording import similarity
from .state import ResearchState

BRANCH_GUIDANCE = {
    "prove": (
        "You are on a PROVE branch. Aim for a rigorous argument. Split it into lemmas, "
        "formalize the key lemmas in Lean where feasible, and when a step fails, identify the "
        "precise obstruction: it may indicate that the statement is false."
    ),
    "disprove": (
        "You are on a DISPROVE branch. Search for a counterexample, starting with the smallest "
        "cases. Use numerical optimization (see-saw iterations, semidefinite programs, gradient or "
        "random search) to find candidates, then certify a candidate exactly (rational or algebraic "
        "entries) with a verification script and, if feasible, a Lean proof that the witness violates "
        "the statement. Record the regions you searched even when nothing is found."
    ),
    "explore": (
        "You are on an EXPLORE branch. Gather structural insight (special cases, reformulations, "
        "numerical experiments) that suggests which direction is true. Record precise observations "
        "and conjectures, clearly marked as such."
    ),
    "reformulate": (
        "You are on a REFORMULATE branch. Find an equivalent, stronger, or weaker precise formulation "
        "that is easier to attack, and prove the exact relation between the formulations."
    ),
    "formalize": (
        "You are on a FORMALIZE branch. Express the statement or key lemmas in Lean 4 with Mathlib. "
        "Faithfulness matters more than elegance: keep definitions minimal and standard, and state "
        "every hypothesis of the informal statement."
    ),
}


def format_plan(plan: dict[str, Any]) -> str:
    lines = [
        f"- Branch: {plan.get('branch', '?')} ({plan.get('branch_kind', 'unknown kind')})"
        + (f": {plan['branch_goal']}" if plan.get("branch_goal") else ""),
        f"- Task type: {plan.get('task', 'research')}",
        f"- Goal: {plan.get('goal', '')}",
        f"- Instructions: {plan.get('instructions', '')}",
        f"- Success test: {plan.get('success_test', '')}",
    ]
    if plan.get("novelty"):
        lines.append(f"- How this differs from earlier attempts: {plan['novelty']}")
    if plan.get("deliverables"):
        lines.append("- Deliverables: " + "; ".join(plan["deliverables"]))
    return "\n".join(lines)


def branch_guidance(plan: dict[str, Any]) -> str:
    return BRANCH_GUIDANCE.get(plan.get("branch_kind", ""), "")


def branch_list(state: ResearchState) -> str:
    if not state.branches:
        return "- none yet (create them with `branch_updates`)"
    return "\n".join(
        f"- {branch.id} [{branch.kind}, {branch.status}, {branch.iterations} iterations, "
        f"stagnation {branch.stagnation}]: {branch.goal}"
        for branch in state.branches
    )


def neglected_branches(state: ResearchState, threshold: int) -> str:
    """Open branches that other branches have outpaced for ``threshold`` iterations."""
    total = len(state.attempts)
    notes = []
    for branch in state.open_branches():
        last = max((index for index, attempt in enumerate(state.attempts) if attempt.branch == branch.id), default=-1)
        idle = total - 1 - last
        if idle >= threshold and total >= threshold:
            notes.append(f"Branch {branch.id} ({branch.kind}) has not been advanced for {idle} iterations: "
                         "advance it, or close or suspend it with a reason.")
    return " ".join(notes)


def escalation_block(state: ResearchState, progress: ProgressConfig) -> str:
    if state.stagnation_count < progress.escalate_after:
        return ""
    remaining = max(0, progress.suspend_after - state.stagnation_count)
    return (
        f"\n**Stagnation warning.** The last {state.stagnation_count} iterations made no meaningful progress; "
        f"the problem is suspended automatically after {remaining} more. Do not continue the current line "
        "unchanged: choose a new decomposition, switch to the opposite branch, reformulate, or suspend."
    )


def hints_block(state: ResearchState) -> str:
    if not state.hints:
        return ""
    return "\n**Human hints** (take these seriously):\n" + "\n".join(f"- {hint.text}" for hint in state.hints)


def warnings_block(state: ResearchState, plan: dict[str, Any], violations: list[str]) -> str:
    lines = []
    text = f"{plan.get('goal', '')} {plan.get('instructions', '')}"
    for attempt in state.attempts:
        if attempt.outcome == "success":
            continue
        if similarity(text, f"{attempt.fingerprint} {attempt.description}") >= 0.35:
            lines.append(f"- This step resembles failed attempt {attempt.id} ({attempt.approach}): "
                         f"{attempt.failure_reason or attempt.lesson}. Do not repeat it; address that obstruction.")
    for violation in violations[-5:]:
        lines.append(f"- Integrity: {violation}. Protected files must not be modified.")
    if not lines:
        return ""
    return "## Warnings\n\n" + "\n".join(lines)


def formal_statement_note(state: ResearchState) -> str:
    formal = state.active_formal_statement
    if formal is None:
        return "- No formal statement is locked yet."
    return f"- Locked formal statement: `{formal.decl}` in `{formal.file}`."


def iteration_materials(paths: WorkspacePaths, iteration_dir: Path | None) -> str:
    if iteration_dir is None:
        return ("No iteration has run yet in this epoch. Plan the first step. Set `assessment` to null.")
    relative = paths.relative(iteration_dir)
    return (
        "## The iteration just recorded\n\n"
        f"- Plan: `{relative}/plan.json`\n"
        f"- Researcher report: `{relative}/report.json`\n"
        f"- Machine verification: `{relative}/verification.json`\n"
        f"- Critic review: `{relative}/critique.json`\n"
        f"- Recorded outcome (trust levels assigned by the orchestrator): `{relative}/outcome.json`"
    )


ASSESSMENT_INSTRUCTIONS = """\
## Assess the iteration just recorded

Score its information gain honestly in `assessment.progress_score`:

- 0: nothing new, or a variation of an approach that already failed.
- 1: minor: small observations, inconclusive experiments, clarifications.
- 2: meaningful: a new accepted lemma, a Lean-verified step, a reduction, a counterexample to a
  subsidiary conjecture, a relevant theorem found in the literature, an eliminated class of
  approaches, an improved formal statement, or a genuinely new line of attack.
- 3: major: a Lean-verified significant part of the argument, a main reduction, or a resolution.

The orchestrator caps scores that are not backed by accepted, verified artifacts, so do not inflate.
Set `repeated_approach` when the iteration repeated earlier failed work."""


def epoch_end_instructions(ending: bool) -> str:
    if not ending:
        return ""
    return (
        "\n**This epoch ends after your decision.** Write `epoch_report`: a Markdown progress report "
        "(what was attempted, what was established and how it was verified, what failed and why, and "
        "the recommended next steps). Still give `next_step` for the next epoch unless you suspend or close."
    )


def relevance_block(state: ResearchState, portfolio: Portfolio, incoming: list[dict[str, Any]],
                    limit: int = 40) -> str:
    lines = []
    if incoming:
        lines.append("Results from other problems flagged as relevant to this one since it last ran:")
        for link in incoming[-10:]:
            lines.append(f"- from `{link.get('from')}` (claims {', '.join(link.get('claims', [])) or 'n/a'}): "
                         f"{link.get('why', '')}")
        lines.append("See knowledge/RESULTS.md for the statements.")
        lines.append("")
    others = [
        entry for entry in portfolio.entries.values()
        if entry.id != state.problem_id and entry.status in (ACTIVE, CANDIDATE, SUSPENDED)
    ]
    others.sort(key=lambda entry: (-(entry.triage.suitability if entry.triage else 0), entry.id))
    if others:
        lines.append("If a result recorded here could help another problem in the portfolio, list it in "
                     "`cross_problem_relevance` (problem ID, claim IDs, and why). Other problems:")
        lines += [f"- `{entry.id}`: {entry.title}" for entry in others[:limit]]
    else:
        lines.append("No other problems are active in the portfolio.")
    return "\n".join(lines)


def previous_literature(state: ResearchState) -> str:
    if state.literature.checked is None:
        return ""
    return (f"- A previous literature check (epoch {state.literature.checked.epoch}) concluded still_open = "
            f"{state.literature.still_open}: {state.literature.summary} Update it; look for newer work.")


def problem_values(paths: WorkspacePaths, problem: ProblemPaths, state: ResearchState) -> dict[str, str]:
    return {
        "problem_title": state.title,
        "problem_id": state.problem_id,
        "problem_dir": paths.relative(problem.root),
    }


def dump(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)
