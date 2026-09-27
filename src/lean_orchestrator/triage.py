"""Assess untriaged problems against the selection criteria of the design."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .agents import AgentError, AgentStopped
from .catalogue.base import CatalogueEntry
from .config import TriageConfig
from .context import Services
from .jsonio import atomic_write_text, utc_now
from .portfolio import CANDIDATE, DEFERRED, UNEXPLORED, PortfolioEntry, TriageResult
from .render import tex_to_markdown
from .roles import RoleOutputError


@dataclass
class TriageReport:
    assessed: int = 0
    activated: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    failed_batches: int = 0


def suitability(scores: dict[str, float], config: TriageConfig) -> float:
    """Weighted mean of the criterion scores, on the same 0 to 5 scale."""
    total_weight = sum(config.weights.get(name, 0.0) for name in scores)
    if total_weight <= 0:
        return 0.0
    return sum(config.weights.get(name, 0.0) * value for name, value in scores.items()) / total_weight


def batch_markdown(entries: list[CatalogueEntry]) -> str:
    sections = ["# Problems to triage", ""]
    for entry in entries:
        sections += [
            f"## `{entry.id}`: {entry.title}",
            "",
            f"Fields: {', '.join(entry.fields) or 'n/a'}. Topics: {', '.join(entry.topics) or 'n/a'}.",
            "",
            "### Statement",
            "",
            tex_to_markdown(str(entry.readable("statement"))),
            "",
        ]
        progress = entry.readable("progress")
        if progress:
            sections += ["### Known progress", "", *[f"- {tex_to_markdown(item)}" for item in progress], ""]
        comment = str(entry.readable("comment"))
        if comment.strip():
            sections += ["### Comment", "", tex_to_markdown(comment), ""]
    return "\n".join(sections)


def triage_pending(services: Services, *, limit: int | None = None,
                   problem_ids: list[str] | None = None) -> TriageReport:
    report = TriageReport()
    snapshot = services.portfolio.snapshot()
    if problem_ids:
        pending = [snapshot.entries[pid] for pid in problem_ids if pid in snapshot.entries]
    else:
        preferred = set(services.config.triage.preferred_topics)

        def order(entry: PortfolioEntry) -> tuple[int, str]:
            topics = set(services.catalogue.load(entry.source, entry.id).topics)
            return (-len(topics & preferred), entry.id)

        pending = sorted((entry for entry in snapshot.entries.values() if entry.status == UNEXPLORED), key=order)
    if limit is not None:
        pending = pending[:limit]
    size = services.config.triage.batch_size
    batches = [pending[index:index + size] for index in range(0, len(pending), size)]
    directory = services.paths.internal_dir / "triage"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for number, batch in enumerate(batches, start=1):
        if services.should_stop():
            break
        entries = [services.catalogue.load(entry.source, entry.id) for entry in batch]
        batch_path = directory / f"{stamp}-batch-{number:02d}.md"
        atomic_write_text(batch_path, batch_markdown(entries))
        ids = [entry.id for entry in batch]
        services.console.info(f"triage batch {number}/{len(batches)} ({len(ids)} problems)", tag="triage")
        try:
            result = services.roles.call(
                "triage", tag="triage", problem_id=None,
                output_path=directory / f"{stamp}-batch-{number:02d}.json",
                values={"batch_path": services.paths.relative(batch_path), "problem_ids": ", ".join(ids)},
                context={"problem_ids": ids},
            )
        except AgentStopped:
            raise
        except (RoleOutputError, AgentError) as error:
            report.failed_batches += 1
            services.console.warn(f"triage batch {number} failed: {error}", tag="triage")
            services.events.emit("triage_failed", batch=number, error=str(error))
            continue
        _apply(services, result.data["problems"], report)
    services.events.emit("triage_finished", assessed=report.assessed, activated=report.activated,
                         deferred=report.deferred, failed_batches=report.failed_batches)
    return report


def _apply(services: Services, assessments: list[dict], report: TriageReport) -> None:
    config = services.config
    for item in assessments:
        scores = {name: float(value) for name, value in item["scores"].items()}
        value = suitability(scores, config.triage)
        if item.get("likely_resolved"):
            value *= 0.5
        activate = item["recommendation"] == "activate" and value >= config.scheduler.min_suitability
        triage = TriageResult(
            scores=scores, suitability=round(value, 3), recommendation=item["recommendation"],
            modes=list(item.get("modes", [])), rationale=item.get("rationale", ""),
            first_steps=item.get("first_steps", ""), likely_resolved=bool(item.get("likely_resolved")),
            time=utc_now(),
        )

        def change(entry: PortfolioEntry, triage: TriageResult = triage, activate: bool = activate) -> None:
            entry.triage = triage
            if entry.status in (UNEXPLORED, CANDIDATE, DEFERRED) and entry.epochs == 0:
                entry.status = CANDIDATE if activate else DEFERRED

        services.portfolio.update_entry(item["id"], change)
        report.assessed += 1
        (report.activated if activate else report.deferred).append(item["id"])
