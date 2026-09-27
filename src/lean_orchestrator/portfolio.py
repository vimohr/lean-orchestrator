"""Portfolio of problems: lifecycle status and the statistics the scheduler uses."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .jsonio import read_json, utc_now, write_json
from .paths import WorkspacePaths
from .serde import from_dict, to_dict

UNEXPLORED = "unexplored"
CANDIDATE = "candidate"
DEFERRED = "deferred"
ACTIVE = "active"
SUSPENDED = "suspended"
PENDING_REVIEW = "pending_review"
SOLVED = "solved"
DISPROVED = "disproved"
RESOLVED_EXTERNALLY = "resolved_externally"
RETIRED = "retired"

STATUSES = (
    UNEXPLORED, CANDIDATE, DEFERRED, ACTIVE, SUSPENDED, PENDING_REVIEW,
    SOLVED, DISPROVED, RESOLVED_EXTERNALLY, RETIRED,
)
TERMINAL = frozenset({SOLVED, DISPROVED, RESOLVED_EXTERNALLY, RETIRED})
NOT_SCHEDULABLE = TERMINAL | {PENDING_REVIEW, DEFERRED}


@dataclass
class TriageResult:
    scores: dict[str, float]
    suitability: float
    recommendation: str
    modes: list[str] = field(default_factory=list)
    rationale: str = ""
    first_steps: str = ""
    likely_resolved: bool = False
    time: str = ""


@dataclass
class PortfolioEntry:
    id: str
    source: str
    title: str
    status: str = UNEXPLORED
    upstream_status: str = ""
    upstream_digest: str = ""
    triage: TriageResult | None = None
    folder: str | None = None
    lean_namespace: str | None = None
    epochs: int = 0
    iterations: int = 0
    last_global_epoch: int | None = None
    ewma_progress: float | None = None
    stagnation: int = 0
    suspended_at: int | None = None
    consecutive_failures: int = 0
    pinned: bool = False
    note: str = ""
    updated: str = ""

    @property
    def is_promising(self) -> bool:
        return self.status == ACTIVE and (self.ewma_progress or 0.0) >= 0.5

    @property
    def display_status(self) -> str:
        return "promising" if self.is_promising else self.status


@dataclass
class Portfolio:
    schema: str = "lean-orch/portfolio/1"
    global_epochs: int = 0
    exploration_epochs: int = 0
    entries: dict[str, PortfolioEntry] = field(default_factory=dict)


class PortfolioStore:
    """Thread-safe access to ``.lean-orch/portfolio.json``.

    ``writer`` lets the orchestrator route writes through its integrity guard, so
    that its own updates are never mistaken for tampering.
    """

    def __init__(self, paths: WorkspacePaths, writer: Callable[[Path, Any], None] | None = None) -> None:
        self.paths = paths
        self._write = writer or write_json
        self._lock = threading.RLock()
        self._portfolio = self._load()

    def _load(self) -> Portfolio:
        if self.paths.portfolio.is_file():
            return from_dict(Portfolio, read_json(self.paths.portfolio))
        return Portfolio()

    def save(self) -> None:
        with self._lock:
            self._write(self.paths.portfolio, to_dict(self._portfolio))

    @contextmanager
    def edit(self) -> Iterator[Portfolio]:
        """Mutate the portfolio under the lock and persist the result."""
        with self._lock:
            yield self._portfolio
            self._write(self.paths.portfolio, to_dict(self._portfolio))

    def update_entry(self, problem_id: str, change: Callable[[PortfolioEntry], None]) -> PortfolioEntry:
        with self.edit() as portfolio:
            entry = portfolio.entries[problem_id]
            change(entry)
            entry.updated = utc_now()
            return from_dict(PortfolioEntry, to_dict(entry))

    def snapshot(self) -> Portfolio:
        """Return a deep copy that callers may read without holding the lock."""
        with self._lock:
            return from_dict(Portfolio, to_dict(self._portfolio))

    def get(self, problem_id: str) -> PortfolioEntry:
        with self._lock:
            return from_dict(PortfolioEntry, to_dict(self._portfolio.entries[problem_id]))

    def resolve(self, identifier: str) -> str:
        """Resolve an ID, alias-free prefix, or folder name to a problem ID."""
        with self._lock:
            entries = self._portfolio.entries
            if identifier in entries:
                return identifier
            matches = [
                pid for pid, entry in entries.items()
                if entry.folder == identifier or pid.startswith(identifier)
                or (entry.folder or "").startswith(identifier)
            ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise KeyError(f"no problem matches {identifier!r}")
        raise KeyError(f"{identifier!r} is ambiguous: {', '.join(sorted(matches)[:5])}")
