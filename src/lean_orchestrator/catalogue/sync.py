"""Synchronize the local catalogue and portfolio with upstream sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..context import Services
from ..jsonio import read_json, utc_now
from ..portfolio import RESOLVED_EXTERNALLY, RETIRED, TERMINAL, UNEXPLORED, PortfolioEntry
from . import qiqcop


@dataclass
class SyncReport:
    source: str
    unchanged: bool = False
    skipped: bool = False
    fetched: int = 0
    new: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    resolved_upstream: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)

    def describe(self) -> str:
        if self.skipped:
            return f"{self.source}: synchronized recently; skipped"
        if self.unchanged:
            return f"{self.source}: unchanged"
        return (f"{self.source}: {self.fetched} records, {len(self.new)} new, {len(self.updated)} updated, "
                f"{len(self.resolved_upstream)} newly solved upstream, {len(self.retired)} retired")


def _sources(services: Services) -> dict:
    path = services.paths.catalogue_sources
    return read_json(path) if path.is_file() else {}


def _hours_since(timestamp: str | None) -> float:
    if not timestamp:
        return float("inf")
    moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return (datetime.now(UTC) - moment).total_seconds() / 3600


def sync_qiqcop(services: Services, *, force: bool = False, respect_interval: bool = False) -> SyncReport:
    """Import new QIQCOP Zoo problems and propagate upstream changes to the portfolio."""
    settings = services.config.catalogue.qiqcop
    report = SyncReport(source=qiqcop.SOURCE)
    if not settings.enabled:
        report.skipped = True
        return report
    sources = _sources(services)
    record = sources.get(qiqcop.SOURCE, {})
    if respect_interval and not force and _hours_since(record.get("synced_at")) < settings.sync_interval_hours:
        report.skipped = True
        return report
    release = qiqcop.fetch_release(settings.release_url, settings.timeout_seconds)
    digest = release["catalogDigest"]
    if digest == record.get("digest") and not force:
        report.unchanged = True
        sources[qiqcop.SOURCE] = {**record, "synced_at": utc_now()}
        services.guard.write_json(services.paths.catalogue_sources, sources)
        return report

    records = qiqcop.fetch_records(settings.jsonl_url, settings.timeout_seconds)
    report.fetched = len(records)
    seen: set[str] = set()
    notes: dict[str, str] = {}
    with services.portfolio.edit() as portfolio:
        for raw in records:
            entry = qiqcop.normalize(raw)
            seen.add(entry.id)
            stored = services.catalogue.load(entry.source, entry.id) if services.catalogue.exists(entry.source, entry.id) else None
            if stored is None or stored.digest != entry.digest:
                services.catalogue.save(entry)
            existing = portfolio.entries.get(entry.id)
            if existing is None:
                if qiqcop.keep(entry, settings.include_status, settings.include_fields, settings.exclude_topics):
                    portfolio.entries[entry.id] = PortfolioEntry(
                        id=entry.id, source=entry.source, title=entry.title, status=UNEXPLORED,
                        upstream_status=entry.status, upstream_digest=entry.digest, updated=utc_now(),
                    )
                    report.new.append(entry.id)
                continue
            if existing.upstream_digest and existing.upstream_digest != entry.digest:
                report.updated.append(entry.id)
                if existing.folder:
                    notes[entry.id] = f"The upstream record changed on {entry.updated or utc_now()}; re-check PROBLEM.md."
            if entry.status == "Solved" and existing.upstream_status != "Solved" and existing.status not in TERMINAL:
                existing.status = RESOLVED_EXTERNALLY
                existing.note = "marked Solved in the QIQCOP Zoo"
                report.resolved_upstream.append(entry.id)
            existing.title = entry.title
            existing.upstream_status = entry.status
            existing.upstream_digest = entry.digest
            existing.updated = utc_now()
        for problem_id, existing in portfolio.entries.items():
            if (existing.source == qiqcop.SOURCE and problem_id not in seen and existing.status not in TERMINAL
                    and existing.epochs == 0):
                existing.status = RETIRED
                existing.note = "no longer listed upstream"
                report.retired.append(problem_id)
    sources[qiqcop.SOURCE] = {
        "digest": digest, "synced_at": utc_now(), "release_updated": release.get("updated"),
        "records": report.fetched, "counts": release.get("counts", {}),
    }
    services.guard.write_json(services.paths.catalogue_sources, sources)
    for problem_id, note in notes.items():
        services.control.push_note(problem_id, note)
    for problem_id in report.resolved_upstream:
        entry = services.portfolio.get(problem_id)
        services.notifier.notify("resolved_externally", f"{entry.title} is marked Solved upstream",
                                 f"The QIQCOP Zoo now lists problem {problem_id} as Solved.")
    services.events.emit("catalogue_synced", source=qiqcop.SOURCE, new=len(report.new), updated=len(report.updated),
                         resolved=report.resolved_upstream, retired=report.retired, digest=digest)
    return report
