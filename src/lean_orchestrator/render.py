"""Human-readable views of the catalogue, research state, and portfolio.

``PROBLEM.md`` describes a problem, ``PROGRESS.md`` is its full progress report,
and ``DOSSIER.md`` is the compact summary every agent reads first. All three are
regenerated from structured data; nobody edits them by hand.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .catalogue.base import CatalogueEntry
from .jsonio import utc_now
from .portfolio import Portfolio, PortfolioEntry
from .state import ACCEPTED_TRUST, LEAN_VERIFIED, TRUST_ORDER, Claim, ResearchState

_DISPLAY_ENVIRONMENTS = ("equation", "equation*", "displaymath", "gather", "gather*", "multline", "multline*")
_ALIGN_ENVIRONMENTS = ("align", "align*", "eqnarray", "eqnarray*")


def tex_to_markdown(text: str) -> str:
    """Best-effort conversion of catalogue TeX into GitHub-flavored Markdown with math."""
    text = re.sub(r"[ \t]*\\label\{[^}]*\}", "", text)
    for environment in _DISPLAY_ENVIRONMENTS:
        name = re.escape(environment)
        text = re.sub(rf"\\begin\{{{name}\}}(.*?)\\end\{{{name}\}}",
                      lambda match: "\n$$\n" + match.group(1).strip() + "\n$$\n", text, flags=re.S)
    for environment in _ALIGN_ENVIRONMENTS:
        name = re.escape(environment)
        text = re.sub(rf"\\begin\{{{name}\}}(.*?)\\end\{{{name}\}}",
                      lambda match: "\n$$\n\\begin{aligned}\n" + match.group(1).strip() + "\n\\end{aligned}\n$$\n",
                      text, flags=re.S)
    text = re.sub(r"\\href\{([^}]*)\}\{([^}]*)\}", r"[\2](\1)", text)
    text = re.sub(r"\\sourcecite\{[^}]*\}\{([^}]*)\}", r"[\1]", text)
    text = re.sub(r"\\eqref\{([^}]*)\}", r"(\1)", text)
    text = re.sub(r"\\emph\{([^}]*)\}", r"*\1*", text)
    text = re.sub(r"\\textbf\{([^}]*)\}", r"**\1**", text)
    text = re.sub(r"\\texttt\{([^}]*)\}", r"`\1`", text)
    text = text.replace("``", "\u201c").replace("''", "\u201d").replace("~", " ")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _one_line(text: str, limit: int = 240) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _cell(text: str, limit: int = 160) -> str:
    return _one_line(text, limit).replace("|", "\\|")


def render_problem(entry: CatalogueEntry, titles: dict[str, str] | None = None) -> str:
    titles = titles or {}
    lines = [f"# {entry.title}", ""]
    rows = [
        ("Problem ID", f"`{entry.id}`"),
        ("Source", f"[{entry.source}]({entry.url})" if entry.url else entry.source),
        ("Upstream status", entry.status),
        ("Fields", ", ".join(entry.fields) or "n/a"),
        ("Topics", ", ".join(entry.topics) or "n/a"),
        ("Upstream last updated", entry.updated or "n/a"),
    ]
    lines += ["| | |", "| --- | --- |", *(f"| {key} | {value} |" for key, value in rows), ""]
    lines += ["## Statement", "", tex_to_markdown(str(entry.readable("statement"))), ""]
    attribution = str(entry.readable("attribution"))
    if attribution.strip():
        lines += ["## Origin", "", tex_to_markdown(attribution), ""]
    progress = entry.readable("progress")
    if progress:
        lines += ["## Known progress (upstream)", ""]
        lines += [f"- {tex_to_markdown(item)}" for item in progress]
        lines.append("")
    comment = str(entry.readable("comment"))
    if comment.strip():
        lines += ["## Upstream comment", "", tex_to_markdown(comment), ""]
    if entry.references:
        lines += ["## References", ""]
        for reference in entry.references:
            links = []
            if reference.arxiv:
                links.append(f"[arXiv:{reference.arxiv}](https://arxiv.org/abs/{reference.arxiv})")
            if reference.doi:
                links.append(f"[doi:{reference.doi}](https://doi.org/{reference.doi})")
            suffix = f" ({', '.join(links)})" if links else ""
            lines.append(f"- [{reference.key}] {_one_line(tex_to_markdown(reference.text), 600)}{suffix}")
        lines.append("")
    if entry.related:
        lines += ["## Related problems (upstream)", ""]
        lines += [f"- `{related}` {titles.get(related, '')}".rstrip() for related in entry.related]
        lines.append("")
    if entry.source == "qiqcop":
        lines += [
            "---",
            "",
            "Imported from the [QIQCOP Zoo](https://qiqc-op.com/). Catalogue text belongs to its contributors;",
            "see the zoo's licensing notes before redistributing it, and cite the original research sources.",
            "",
        ]
    return "\n".join(lines)


def _claim_line(claim: Claim) -> str:
    parts = [f"**{claim.id}** ({claim.kind}, `{claim.trust}`): {_one_line(claim.statement, 400)}"]
    if claim.lean_decl and claim.trust == LEAN_VERIFIED:
        parts.append(f"Lean: `{claim.lean_decl}` in `{claim.lean_file}`")
    if claim.experiment:
        parts.append(f"experiment: `{claim.experiment}`")
    if claim.depends_on:
        parts.append("depends on " + ", ".join(claim.depends_on))
    return "- " + "; ".join(parts)


def _sorted_claims(claims: Iterable[Claim]) -> list[Claim]:
    order = {trust: index for index, trust in enumerate(TRUST_ORDER)}
    return sorted(claims, key=lambda claim: (order.get(claim.trust, 99), int(claim.id[1:] or 0)))


def render_progress(state: ResearchState, entry: PortfolioEntry, problem_md_link: str = "PROBLEM.md") -> str:
    """The full progress report of one problem."""
    epoch = state.current_epoch
    lines = [
        f"# Progress report: {state.title}",
        "",
        f"Status: **{entry.display_status}** · stagnation {state.stagnation_count} · "
        f"epochs {len(state.epochs)} · iterations {sum(record.iterations for record in state.epochs)} · "
        f"updated {utc_now()}",
        "",
        f"Problem description: [{problem_md_link}]({problem_md_link}) · compact summary: [DOSSIER.md](DOSSIER.md)",
        "",
        "## Supervisor assessment",
        "",
        state.supervisor_assessment or "_No assessment yet._",
        "",
    ]
    if state.literature.checked is not None:
        lines += [
            "## Literature status",
            "",
            f"Still open: **{state.literature.still_open}** (checked in epoch {state.literature.checked.epoch}). "
            + state.literature.summary,
            "",
        ]
        if state.literature.resolution:
            lines += [f"Resolution reported: {state.literature.resolution}", ""]
    lines += ["## Precise statement", ""]
    if state.statement_versions:
        current = state.statement_versions[-1]
        lines += [f"Version {current.version} ({current.reason or 'initial'}):", "", tex_to_markdown(current.text), ""]
    else:
        lines += ["_See PROBLEM.md._", ""]
    if state.success_criteria:
        lines += ["**Success criteria.** " + state.success_criteria, ""]
    if state.assumptions:
        lines += ["**Known assumptions.**", "", *[f"- {item}" for item in state.assumptions], ""]
    formal = state.active_formal_statement
    lines += ["## Formal statement (Lean)", ""]
    if formal is None:
        lines += ["_No locked formal statement yet._", ""]
    else:
        lines += [f"`{formal.decl}` in `{formal.file}` (locked, from claim {formal.claim}).", ""]

    accepted = [claim for claim in state.claims if claim.trust in ACCEPTED_TRUST and claim.kind != "formal_statement"]
    lines += ["## Verified and accepted results", ""]
    lines += [_claim_line(claim) for claim in _sorted_claims(accepted)] or ["_None yet._"]
    lines.append("")
    reductions = [claim for claim in accepted if claim.kind == "reduction"]
    if reductions:
        lines += ["## Current reductions", "", *[_claim_line(claim) for claim in reductions], ""]
    conjectures = [claim for claim in state.claims if claim.kind == "conjecture" and claim.trust == "proposed"]
    if conjectures:
        lines += ["## New conjectures (unverified)", "", *[_claim_line(claim) for claim in conjectures], ""]
    open_goals = [goal for goal in state.subgoals if goal.status == "open"]
    lines += ["## Open subgoals", ""]
    lines += [f"- **{goal.id}**{f' ({goal.branch})' if goal.branch else ''}: {_one_line(goal.statement, 300)}"
              for goal in open_goals] or ["_None recorded._"]
    lines.append("")
    closed_goals = [goal for goal in state.subgoals if goal.status != "open"]
    if closed_goals:
        lines += ["<details><summary>Closed or abandoned subgoals</summary>", ""]
        lines += [f"- **{goal.id}** {goal.status}{f' by {goal.closed_by}' if goal.closed_by else ''}: "
                  f"{_one_line(goal.statement, 200)}" for goal in closed_goals]
        lines += ["", "</details>", ""]
    lines += ["## Branches", "", "| Branch | Kind | Status | Iterations | Stagnation | Goal |", "| --- | --- | --- | --- | --- | --- |"]
    lines += [f"| {branch.id} | {branch.kind} | {branch.status} | {branch.iterations} | {branch.stagnation} | "
              f"{_cell(branch.goal)} |" for branch in state.branches]
    lines.append("")
    lines += ["## Promising directions", ""]
    lines += [f"- {item}" for item in state.promising_directions] or ["_None recorded._"]
    lines.append("")
    lines += ["## Known dead ends", ""]
    lines += [f"- {_one_line(item.approach, 160)}: {_one_line(item.reason, 300)}"
              + (f" (scope: {_one_line(item.scope, 120)})" if item.scope else "") for item in state.dead_ends] \
        or ["_None recorded._"]
    lines.append("")
    lines += ["## Attempted approaches", "", "| Attempt | Epoch.Iter | Branch | Approach | Outcome | Score | Failure reason / lesson |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for attempt in reversed(state.attempts):
        stamp = f"{attempt.created.epoch}.{attempt.created.iteration}" if attempt.created else ""
        note = attempt.failure_reason or attempt.lesson
        if attempt.repeat_of:
            note = f"repeat of {', '.join(attempt.repeat_of)}. {note}"
        lines.append(f"| {attempt.id} | {stamp} | {attempt.branch or ''} | {_cell(attempt.approach, 80)} | "
                     f"{attempt.outcome} | {'' if attempt.effective_score is None else attempt.effective_score} | "
                     f"{_cell(note, 200)} |")
    lines.append("")
    if state.searches:
        lines += ["## Counterexample searches", ""]
        lines += [f"- {_one_line(item.space, 160)} via {_one_line(item.method, 120)}: {_one_line(item.result, 200)}"
                  for item in state.searches]
        lines.append("")
    if state.experiments:
        lines += ["## Numerical experiments", ""]
        lines += [f"- `{item.path}` (reproduced: {item.reproduced}): {_one_line(item.purpose, 160)}"
                  + (f"; {_one_line(item.result_summary, 200)}" if item.result_summary else "")
                  for item in state.experiments]
        lines.append("")
    rejected = [claim for claim in state.claims if claim.trust in ("rejected", "retracted")]
    if rejected:
        lines += ["## Rejected or retracted claims", ""]
        for claim in rejected:
            lines.append(_claim_line(claim))
            if claim.critic_notes:
                lines.append(f"  - critic: {_one_line(claim.critic_notes, 300)}")
        lines.append("")
    pending = [claim for claim in state.claims if claim.trust == "proposed" and claim.kind != "conjecture"]
    if pending:
        lines += ["## Unconfirmed claims", ""]
        for claim in pending:
            lines.append(_claim_line(claim))
            if claim.verification:
                lines.append(f"  - verification: {_one_line(claim.verification, 300)}")
        lines.append("")
    if state.known_results or state.references:
        lines += ["## Relevant literature", ""]
        lines += [f"- {_one_line(result.statement, 300)} [{result.reference}]" for result in state.known_results]
        for reference in state.references:
            identifier = (f" arXiv:{reference.arxiv}" if reference.arxiv else "") + (f" doi:{reference.doi}" if reference.doi else "")
            lines.append(f"- **{reference.id}** {_one_line(reference.citation, 300)}{identifier} (check: {reference.check})"
                         + (f": {_one_line(reference.relevance, 160)}" if reference.relevance else ""))
        lines.append("")
    if state.hints:
        lines += ["## Human hints", "", *[f"- ({hint.time}) {hint.text}" for hint in state.hints], ""]
    lines += ["## Timeline", "", "| Epoch | Global | Started | Iterations | Mean score | Decision | Report |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for record in state.epochs:
        mean = "" if record.mean_score is None else f"{record.mean_score:.2f}"
        report = f"[report]({record.report})" if record.report else ""
        lines.append(f"| {record.index}{' (explore)' if record.exploration else ''} | {record.global_index} | "
                     f"{record.started} | {record.iterations} | {mean} | {record.decision} | {report} |")
    lines.append("")
    if state.violations:
        lines += ["## Integrity events", "", *[f"- {item}" for item in state.violations[-20:]], ""]
    if epoch is not None and epoch.ended is None:
        lines += [f"_Epoch {epoch.index} is in progress._", ""]
    return "\n".join(lines)


def render_dossier(state: ResearchState, entry: PortfolioEntry, *, max_items: int = 15) -> str:
    """Compact summary for agents: what is known, what failed, what is open."""
    lines = [
        f"# Dossier: {state.title}",
        "",
        f"Problem `{state.problem_id}` · status {entry.display_status} · stagnation {state.stagnation_count} · "
        f"Lean namespace `OpenQ.Problems.{state.lean_namespace}`",
        "",
        "## Working statement",
        "",
        tex_to_markdown(state.current_statement) if state.current_statement else "See PROBLEM.md.",
        "",
    ]
    if state.success_criteria:
        lines += ["Success criteria: " + state.success_criteria, ""]
    formal = state.active_formal_statement
    lines += ["## Formal statement", "",
              f"`{formal.decl}` in `{formal.file}` (locked). Main results must have exactly this type or its negation."
              if formal else "None locked yet.", ""]
    accepted = _sorted_claims(claim for claim in state.claims if claim.trust in ACCEPTED_TRUST)
    lines += ["## Established results", ""]
    lines += [_claim_line(claim) for claim in accepted[-max_items:]] or ["None yet."]
    lines.append("")
    open_goals = [goal for goal in state.subgoals if goal.status == "open"][:max_items]
    lines += ["## Open subgoals", ""]
    lines += [f"- {goal.id}: {_one_line(goal.statement, 200)}" for goal in open_goals] or ["None recorded."]
    lines.append("")
    lines += ["## Branches", ""]
    lines += [f"- {branch.id} [{branch.kind}, {branch.status}, stagnation {branch.stagnation}]: "
              f"{_one_line(branch.goal, 200)}" for branch in state.branches] or ["None yet."]
    lines.append("")
    do_not_retry = [f"- {_one_line(item.approach, 120)}: {_one_line(item.reason, 180)}" for item in state.dead_ends]
    do_not_retry += [f"- {attempt.id} `{_one_line(attempt.fingerprint or attempt.approach, 120)}`: "
                     f"{_one_line(attempt.failure_reason or attempt.lesson, 180)}"
                     for attempt in state.attempts if attempt.outcome in ("failure", "inconclusive")]
    lines += ["## Do not retry", ""]
    lines += do_not_retry[-2 * max_items:] or ["Nothing recorded."]
    lines.append("")
    lessons = []
    for attempt in reversed(state.attempts):
        if attempt.lesson and attempt.lesson not in lessons:
            lessons.append(attempt.lesson)
        if len(lessons) >= 8:
            break
    if lessons:
        lines += ["## Recent lessons", "", *[f"- {_one_line(lesson, 300)}" for lesson in lessons], ""]
    if state.promising_directions:
        lines += ["## Promising directions", "", *[f"- {_one_line(item, 240)}" for item in state.promising_directions], ""]
    if state.known_results:
        lines += ["## Known results from the literature", ""]
        lines += [f"- {_one_line(item.statement, 240)} [{item.reference}]" for item in state.known_results[:max_items]]
        lines.append("")
    if state.hints:
        lines += ["## Human hints (take these seriously)", "", *[f"- {hint.text}" for hint in state.hints], ""]
    return "\n".join(lines)


def render_portfolio(portfolio: Portfolio, priorities: dict[str, float]) -> str:
    entries = list(portfolio.entries.values())
    counts: dict[str, int] = {}
    for item in entries:
        counts[item.status] = counts.get(item.status, 0) + 1
    lines = [
        "# Research portfolio",
        "",
        f"Updated {utc_now()} · global epochs {portfolio.global_epochs} "
        f"(exploration {portfolio.exploration_epochs}) · "
        + ", ".join(f"{status}: {count}" for status, count in sorted(counts.items())),
        "",
    ]

    def link(item: PortfolioEntry) -> str:
        return f"[{_cell(item.title, 90)}](problems/{item.folder}/PROGRESS.md)" if item.folder else _cell(item.title, 90)

    def table(items: list[PortfolioEntry], title: str, with_priority: bool = False) -> None:
        if not items:
            return
        lines.extend([f"## {title}", ""])
        header = "| Priority | " if with_priority else "| "
        lines.append(header + "Status | Problem | Suitability | Epochs | Iterations | Progress (EWMA) | Stagnation |")
        lines.append("| --- " * (8 if with_priority else 7) + "|")
        for item in items:
            suitability = f"{item.triage.suitability:.1f}" if item.triage else ""
            ewma = "" if item.ewma_progress is None else f"{item.ewma_progress:.2f}"
            prefix = f"| {priorities.get(item.id, 0.0):.2f} | " if with_priority else "| "
            lines.append(prefix + f"{item.display_status}{' (pinned)' if item.pinned else ''} | {link(item)} | "
                         f"{suitability} | {item.epochs} | {item.iterations} | {ewma} | {item.stagnation} |")
        lines.append("")

    schedulable = sorted(
        (item for item in entries if item.status in ("active", "candidate", "suspended")),
        key=lambda item: -priorities.get(item.id, 0.0),
    )
    table([item for item in entries if item.status == "pending_review"], "Awaiting human review")
    table(schedulable, "Scheduled problems (highest priority first)", with_priority=True)
    table([item for item in entries if item.status in ("solved", "disproved", "resolved_externally")], "Resolved")
    deferred = [item for item in entries if item.status == "deferred"]
    if deferred:
        lines += [f"## Deferred by triage ({len(deferred)})", "",
                  "Activate one with `lean-orch activate <id>`.", ""]
        lines += [f"- `{item.id}` {_one_line(item.title, 120)}"
                  + (f" (suitability {item.triage.suitability:.1f})" if item.triage else "")
                  for item in sorted(deferred, key=lambda item: -(item.triage.suitability if item.triage else 0))]
        lines.append("")
    unexplored = [item for item in entries if item.status == "unexplored"]
    if unexplored:
        lines += [f"## Not yet triaged ({len(unexplored)})", "", "Run `lean-orch triage` to assess them.", ""]
    return "\n".join(lines)


def render_epoch_report_fallback(state: ResearchState, epoch_index: int) -> str:
    """Deterministic epoch report when the supervisor did not write one."""
    records = [record for record in state.progress if record.epoch == epoch_index]
    attempts = [attempt for attempt in state.attempts if attempt.created and attempt.created.epoch == epoch_index]
    lines = [f"# Epoch {epoch_index} report: {state.title}", "", "_Generated by the orchestrator._", ""]
    lines += [f"- Iterations: {len(attempts)}",
              f"- Scores: {', '.join(str(record.effective_score) for record in records) or 'none'}",
              f"- Stagnation count: {state.stagnation_count}", ""]
    lines += ["## Attempts", ""]
    lines += [f"- {attempt.id}: {attempt.approach} ({attempt.outcome}). {attempt.failure_reason or attempt.lesson}"
              for attempt in attempts] or ["None."]
    return "\n".join(lines) + "\n"
