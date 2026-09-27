"""Allocate research epochs across the portfolio.

priority(problem) = expected_progress + exploration_bonus
                    + relevance_of_new_results - stagnation_penalty - suspension_penalty

* expected progress: the discounted mean of recent epoch scores, shrunk toward
  the triage prior when little data exists;
* exploration bonus: a UCB1-style term that grows for rarely tried problems;
* relevance: links to new results from other problems since this one last ran;
* stagnation: the current count of low-progress iterations;
* suspension: a penalty for suspended problems that decays with elapsed epochs.

A fixed share of epochs (``exploration_reserve``) goes to never-tried or
suspended problems so that the portfolio keeps exploring.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from .config import Config
from .portfolio import ACTIVE, CANDIDATE, NOT_SCHEDULABLE, SUSPENDED, UNEXPLORED, Portfolio, PortfolioEntry

RelevanceLookup = Callable[[str, int | None], int]


@dataclass(frozen=True)
class Priority:
    problem_id: str
    expected_progress: float
    exploration_bonus: float
    relevance: float
    stagnation_penalty: float
    suspension_penalty: float
    pinned_bonus: float

    @property
    def total(self) -> float:
        return (
            self.expected_progress + self.exploration_bonus + self.relevance + self.pinned_bonus
            - self.stagnation_penalty - self.suspension_penalty
        )


@dataclass(frozen=True)
class Selection:
    problem_id: str
    exploration: bool
    priority: Priority


class Scheduler:
    def __init__(self, config: Config, relevance: RelevanceLookup | None = None) -> None:
        self.config = config
        self.relevance = relevance or (lambda _problem, _after: 0)

    def eligible(self, entry: PortfolioEntry) -> bool:
        if entry.status in NOT_SCHEDULABLE:
            return False
        if entry.consecutive_failures >= self.config.loop.max_consecutive_failures:
            return False
        if entry.status == UNEXPLORED:
            return not self.config.triage.enabled
        return entry.status in (CANDIDATE, ACTIVE, SUSPENDED)

    def prior(self, entry: PortfolioEntry) -> float:
        if entry.triage is None:
            return 0.5
        return max(0.0, min(1.0, entry.triage.suitability / 5.0))

    def priority(self, entry: PortfolioEntry, portfolio: Portfolio) -> Priority:
        scheduler = self.config.scheduler
        prior = self.prior(entry)
        epochs = entry.epochs
        if entry.ewma_progress is None or epochs == 0:
            expected = prior
        else:
            strength = scheduler.prior_strength
            expected = (entry.ewma_progress * epochs + prior * strength) / (epochs + strength)
        total_epochs = portfolio.global_epochs
        exploration = scheduler.exploration_weight * math.sqrt(math.log(1 + total_epochs) / (1 + epochs))
        links = self.relevance(entry.id, entry.last_global_epoch)
        relevance = scheduler.relevance_weight * min(float(links), scheduler.max_relevance_bonus)
        stagnation = scheduler.stagnation_weight * entry.stagnation / max(1, self.config.progress.suspend_after)
        suspension = 0.0
        if entry.status == SUSPENDED:
            elapsed = max(0, total_epochs - (entry.suspended_at or total_epochs))
            suspension = scheduler.suspended_penalty * math.exp(-elapsed / max(scheduler.suspended_decay_epochs, 1e-9))
        return Priority(
            problem_id=entry.id,
            expected_progress=expected,
            exploration_bonus=exploration,
            relevance=relevance,
            stagnation_penalty=stagnation,
            suspension_penalty=suspension,
            pinned_bonus=1.0 if entry.pinned else 0.0,
        )

    def priorities(self, portfolio: Portfolio) -> dict[str, Priority]:
        return {
            problem_id: self.priority(entry, portfolio)
            for problem_id, entry in portfolio.entries.items()
            if self.eligible(entry)
        }

    def select(self, portfolio: Portfolio, *, exclude: set[str] | frozenset[str] = frozenset(),
               only: set[str] | None = None) -> Selection | None:
        """Pick the next problem; ties break on the problem ID for reproducibility."""
        ranked = [
            priority for problem_id, priority in self.priorities(portfolio).items()
            if problem_id not in exclude and (only is None or problem_id in only)
        ]
        if not ranked:
            return None
        ranked.sort(key=lambda item: (-item.total, item.problem_id))
        reserve = self.config.scheduler.exploration_reserve
        pool = [
            item for item in ranked
            if portfolio.entries[item.problem_id].epochs == 0
            or portfolio.entries[item.problem_id].status == SUSPENDED
        ]
        wants_exploration = portfolio.exploration_epochs < reserve * (portfolio.global_epochs + 1)
        if wants_exploration and pool:
            return Selection(pool[0].problem_id, True, pool[0])
        best = ranked[0]
        entry = portfolio.entries[best.problem_id]
        return Selection(best.problem_id, entry.epochs == 0 or entry.status == SUSPENDED, best)


def update_ewma(previous: float | None, value: float, alpha: float) -> float:
    return value if previous is None else alpha * value + (1 - alpha) * previous
