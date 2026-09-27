from __future__ import annotations

import math

from support import fake_config

from lean_orchestrator.portfolio import (
    ACTIVE, CANDIDATE, DEFERRED, PENDING_REVIEW, SUSPENDED, UNEXPLORED, Portfolio, PortfolioEntry, TriageResult,
)
from lean_orchestrator.scheduler import Scheduler, update_ewma


def entry(problem_id: str, *, status: str = CANDIDATE, suitability: float | None = 4.0, epochs: int = 0,
          ewma: float | None = None, stagnation: int = 0, **extra) -> PortfolioEntry:
    triage = None if suitability is None else TriageResult(scores={}, suitability=suitability,
                                                           recommendation="activate")
    return PortfolioEntry(id=problem_id, source="local", title=problem_id, status=status, triage=triage,
                          epochs=epochs, ewma_progress=ewma, stagnation=stagnation, **extra)


def portfolio(*entries: PortfolioEntry, global_epochs: int = 0, exploration: int = 0) -> Portfolio:
    return Portfolio(global_epochs=global_epochs, exploration_epochs=exploration,
                     entries={item.id: item for item in entries})


def test_only_schedulable_statuses_are_eligible():
    scheduler = Scheduler(fake_config())
    for status, expected in ((CANDIDATE, True), (ACTIVE, True), (SUSPENDED, True), (DEFERRED, False),
                             (PENDING_REVIEW, False), (UNEXPLORED, False)):
        assert scheduler.eligible(entry("p", status=status)) is expected
    assert Scheduler(fake_config({"triage": {"enabled": False}})).eligible(entry("p", status=UNEXPLORED))
    assert not scheduler.eligible(entry("p", consecutive_failures=3))


def test_priority_components_follow_the_design_formula():
    scheduler = Scheduler(fake_config(), relevance=lambda problem, after: 2 if problem == "b" else 0)
    worked = entry("a", status=ACTIVE, epochs=2, ewma=0.5, stagnation=2)
    fresh = entry("b", suitability=3.0)
    book = portfolio(worked, fresh, global_epochs=4)
    a, b = scheduler.priority(worked, book), scheduler.priority(fresh, book)
    assert math.isclose(a.expected_progress, (0.5 * 2 + 0.8 * 1) / 3)
    assert math.isclose(a.exploration_bonus, 0.4 * math.sqrt(math.log(5) / 3))
    assert math.isclose(a.stagnation_penalty, 0.3 * 2 / 4)
    assert math.isclose(b.expected_progress, 0.6)
    assert math.isclose(b.relevance, 0.3 * 2)
    assert b.exploration_bonus > a.exploration_bonus


def test_exploration_reserve_picks_untried_or_suspended_problems():
    scheduler = Scheduler(fake_config())
    strong = entry("strong", status=ACTIVE, epochs=5, ewma=1.0)
    weak_new = entry("weak", suitability=1.0)
    book = portfolio(strong, weak_new, global_epochs=10, exploration=0)
    selection = scheduler.select(book)
    assert selection.problem_id == "weak" and selection.exploration
    book.exploration_epochs = 5
    selection = scheduler.select(book)
    assert selection.problem_id == "strong" and not selection.exploration


def test_suspension_penalty_decays_with_elapsed_epochs():
    scheduler = Scheduler(fake_config())
    suspended = entry("s", status=SUSPENDED, epochs=3, ewma=0.2, suspended_at=10)
    recent = scheduler.priority(suspended, portfolio(suspended, global_epochs=10)).suspension_penalty
    later = scheduler.priority(suspended, portfolio(suspended, global_epochs=30)).suspension_penalty
    assert math.isclose(recent, 0.6) and later < recent * 0.2


def test_selection_is_deterministic_and_respects_exclusions_and_pins():
    scheduler = Scheduler(fake_config({"scheduler": {"exploration_reserve": 0.0}}))
    book = portfolio(entry("b"), entry("a"), entry("c", pinned=True))
    assert scheduler.select(book).problem_id == "c"
    assert scheduler.select(book, exclude={"c"}).problem_id == "a"
    assert scheduler.select(book, only={"b"}).problem_id == "b"
    assert scheduler.select(portfolio()) is None


def test_update_ewma():
    assert update_ewma(None, 0.6, 0.5) == 0.6
    assert math.isclose(update_ewma(0.2, 0.6, 0.5), 0.4)
