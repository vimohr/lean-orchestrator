"""The portfolio loop: schedule epochs, run them concurrently, and keep the record."""

from __future__ import annotations

import fcntl
import os
import shlex
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

from . import inbox
from .agents import AgentRunner
from .catalogue.base import CatalogueStore
from .catalogue.qiqcop import CatalogueFetchError
from .catalogue.sync import SyncReport, sync_qiqcop
from .config import Config, load_config
from .context import Services
from .epoch import EpochResult, EpochRunner
from .events import Console, EventLog
from .gitops import GitRepo
from .integrity import IntegrityGuard
from .knowledge import KnowledgeBase
from .notify import Notifier
from .paths import WorkspacePaths
from .portfolio import (
    ACTIVE, CANDIDATE, DISPROVED, PENDING_REVIEW, SOLVED, STATUSES, SUSPENDED, UNEXPLORED, PortfolioEntry,
    PortfolioStore,
)
from .problem_setup import ensure_problem_folder, problem_paths, protect_problem, write_views
from .prompts import PromptLibrary
from .render import render_portfolio
from .roles import RoleRunner
from .scheduler import Scheduler
from .schemas import write_schema_files
from .state import ACCEPTED_TRUST, RETRACTED, Hint, load_state
from .triage import TriageReport, triage_pending
from .verification import Verifier
from .verify.citations import CitationChecker
from .verify.experiments import ExperimentVerifier
from .verify.lean import LeanVerifier
from .workspace import write_mcp_config


class WorkspaceBusy(RuntimeError):
    """Another orchestrator process is running in this workspace."""


class RunLock:
    """Allow one orchestrator process per workspace."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.close()
            raise WorkspaceBusy(f"another lean-orch process is running in {self.path.parent.parent}") from error
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


def self_check_command() -> str:
    """How agents invoke this installation's helper commands."""
    return shlex.join([sys.executable, "-m", "lean_orchestrator"])


@dataclass
class RunSummary:
    epochs: list[EpochResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stopped_reason: str = ""


def build_services(root: Path, *, config: Config | None = None, verbose: bool = False,
                   email: str | None = None, console: Console | None = None) -> Services:
    paths = WorkspacePaths(root.resolve())
    config = config or load_config(paths.root)
    console = console or Console()
    events = EventLog(paths.events)
    guard = IntegrityGuard(paths)
    stop_event = threading.Event()
    agents = AgentRunner(paths, config, console, events, stop_event, stream_output=verbose)
    prompts = PromptLibrary(paths.prompts_dir)
    lean = LeanVerifier(paths, config.verification.lean)
    return Services(
        paths=paths,
        config=config,
        console=console,
        events=events,
        guard=guard,
        portfolio=PortfolioStore(paths, writer=guard.write_json),
        catalogue=CatalogueStore(paths, writer=guard.write_json),
        knowledge=KnowledgeBase(paths, guard),
        prompts=prompts,
        agents=agents,
        roles=RoleRunner(paths, agents, prompts, guard, console, events,
                         max_repairs=config.agents.max_repair_attempts),
        lean=lean,
        verifier=Verifier(paths, lean, ExperimentVerifier(paths, config.verification.experiments),
                          CitationChecker(config.verification.citations.timeout_seconds,
                                          config.verification.citations.enabled)),
        git=GitRepo(paths.root, config.git),
        notifier=Notifier(email if email is not None else config.notify.email, config.notify.events, console,
                          events, paths.root),
        self_check=self_check_command(),
        stop_event=stop_event,
    )


class Orchestrator:
    def __init__(self, services: Services) -> None:
        self.services = services
        self.config = services.config
        self.paths = services.paths
        self.scheduler = Scheduler(
            self.config,
            relevance=lambda problem_id, after: len(services.knowledge.relevance_to(problem_id, after_global_epoch=after)),
        )
        self._lock = RunLock(self.paths.lock_file)
        self._prepared = False

    # ----------------------------------------------------------------- setup
    def prepare(self) -> None:
        """Take the workspace lock and register every orchestrator-owned file with the guard."""
        services, paths, guard = self.services, self.paths, self.services.guard
        self._lock.acquire()
        paths.stop_file.unlink(missing_ok=True)
        guard.reset_store()
        write_schema_files(paths.schemas_dir)  # derived from code; must match the validator
        for path in (paths.config, paths.portfolio, paths.catalogue_sources, paths.portfolio_md):
            if path.is_file():
                guard.protect(path)
        for directory in (paths.prompts_dir, paths.schemas_dir, paths.catalogue_dir, paths.knowledge_dir):
            guard.protect_tree(directory, watch=True)
        lean_project = services.lean.project
        for name in ("lakefile.toml", "lakefile.lean", "lean-toolchain", "lake-manifest.json", "OpenQ.lean"):
            if (lean_project / name).is_file():
                guard.protect(lean_project / name)
        guard.protect_tree(lean_project / "OpenQ" / "Foundations", watch=True)
        for entry in services.portfolio.snapshot().entries.values():
            if not entry.folder or not entry.lean_namespace:
                continue
            problem = problem_paths(services, entry)
            if not problem.state.is_file():
                continue
            for warning in protect_problem(services, problem, load_state(problem.state)):
                services.console.warn(warning, tag=entry.folder)
                services.events.emit("integrity_warning", problem=entry.id, warning=warning)
        write_mcp_config(paths, self.config)
        services.git.ensure()
        self._prepared = True

    def close(self) -> None:
        self._lock.release()

    # ------------------------------------------------------------ catalogue
    def sync(self, *, force: bool = False, respect_interval: bool = False) -> SyncReport:
        report = sync_qiqcop(self.services, force=force, respect_interval=respect_interval)
        self.services.console.info(report.describe(), tag="catalogue")
        self.render_portfolio()
        self._commit("catalogue: sync " + report.source)
        return report

    def triage(self, *, limit: int | None = None, problem_ids: list[str] | None = None) -> TriageReport:
        report = triage_pending(self.services, limit=limit, problem_ids=problem_ids)
        self.services.console.info(
            f"triage: {report.assessed} assessed, {len(report.activated)} candidates, {len(report.deferred)} deferred"
            + (f", {report.failed_batches} failed batch(es)" if report.failed_batches else ""), tag="triage")
        self.render_portfolio()
        self._commit("catalogue: triage")
        return report

    # ------------------------------------------------------------- control
    def apply_inbox(self) -> int:
        """Apply queued human commands; problems that are running receive them between iterations."""
        services = self.services
        applied = 0
        for message in inbox.pending(self.paths):
            try:
                problem_id = services.portfolio.resolve(message.problem)
            except KeyError as error:
                services.console.warn(f"ignoring {message.kind} command: {error}")
                inbox.acknowledge(message)
                continue
            if message.kind == "hint":
                self._apply_hint(problem_id, str(message.payload.get("text", "")))
            elif message.kind == "set_status":
                self._apply_status(problem_id, str(message.payload.get("status", "")))
            elif message.kind == "pin":
                pinned = bool(message.payload.get("pinned", True))
                services.portfolio.update_entry(problem_id, lambda entry, value=pinned: setattr(entry, "pinned", value))
            elif message.kind == "review":
                self._apply_review(problem_id, message.payload)
            services.events.emit("human_command", command=message.kind, problem=problem_id, payload=message.payload)
            inbox.acknowledge(message)
            applied += 1
        if applied:
            self.render_portfolio()
            self._commit("control: apply human commands")
        return applied

    def _apply_hint(self, problem_id: str, text: str) -> None:
        services = self.services
        if not text.strip():
            return
        if services.control.is_running(problem_id):
            services.control.push_hint(problem_id, text)
            return
        entry = services.portfolio.get(problem_id)
        source = services.catalogue.load(entry.source, entry.id)
        problem, state = ensure_problem_folder(services, entry, source)
        state.hints.append(Hint(text=text))
        state.next_plan = None  # a new human hint warrants a fresh plan from the supervisor
        if entry.status == SUSPENDED:
            services.portfolio.update_entry(problem_id, self._reactivate)
        write_views(services, problem, state, services.portfolio.get(problem_id))

    @staticmethod
    def _reactivate(entry: PortfolioEntry) -> None:
        entry.status = ACTIVE if entry.epochs else CANDIDATE
        entry.stagnation = 0
        entry.consecutive_failures = 0
        entry.suspended_at = None

    def _apply_status(self, problem_id: str, status: str) -> None:
        services = self.services
        if status not in STATUSES:
            services.console.warn(f"unknown status {status!r}")
            return
        if services.control.is_running(problem_id):
            services.control.request_status(problem_id, status)
            return

        def change(entry: PortfolioEntry) -> None:
            if status in (ACTIVE, CANDIDATE):
                self._reactivate(entry)
            else:
                entry.status = status
                if status == SUSPENDED:
                    entry.suspended_at = services.portfolio.snapshot().global_epochs
            entry.note = "set by a human"

        entry = services.portfolio.update_entry(problem_id, change)
        if entry.folder and entry.lean_namespace:
            problem = problem_paths(services, entry)
            if problem.state.is_file():
                state = load_state(problem.state)
                if status in (ACTIVE, CANDIDATE):
                    state.stagnation_count = 0
                state.status = entry.status
                write_views(services, problem, state, entry)

    def _apply_review(self, problem_id: str, payload: dict) -> None:
        services = self.services
        entry = services.portfolio.get(problem_id)
        if entry.status != PENDING_REVIEW or not entry.folder:
            services.console.warn(f"{problem_id} is not awaiting review")
            return
        problem = problem_paths(services, entry)
        state = load_state(problem.state)
        claims = [claim for claim in state.claims
                  if claim.kind == "main_result" and claim.resolves_main and claim.trust in ACCEPTED_TRUST]
        if not claims:
            services.console.warn(f"{problem_id} has no accepted main result to review")
            return
        claim = claims[-1]
        reason = str(payload.get("reason", "")).strip()
        if payload.get("decision") == "accept":
            status = SOLVED if claim.resolves_main == "proves" else DISPROVED
            state.hints.append(Hint(text=f"Human review accepted {claim.id}. {reason}".strip()))
        else:
            status = ACTIVE
            claim.trust = RETRACTED
            claim.critic_notes = (claim.critic_notes + f"\nRejected in human review: {reason}").strip()
            state.hints.append(Hint(text=f"Human review rejected {claim.id}: {reason}"))
            state.stagnation_count = 0
        state.status = status

        def change(item: PortfolioEntry) -> None:
            item.status = status
            item.note = f"human review: {payload.get('decision')} {reason}".strip()
            item.stagnation = state.stagnation_count

        updated = services.portfolio.update_entry(problem_id, change)
        write_views(services, problem, state, updated)
        services.events.emit("human_review", problem=problem_id, claim=claim.id, decision=payload.get("decision"),
                             reason=reason)

    # ----------------------------------------------------------------- loop
    def run(self, *, max_epochs: int | None = None, max_hours: float | None = None, parallel: int | None = None,
            only: list[str] | None = None, sync: bool = True, triage: bool = True) -> RunSummary:
        if not self._prepared:
            self.prepare()
        services, config = self.services, self.config
        summary = RunSummary()
        max_epochs = config.loop.max_epochs if max_epochs is None else max_epochs
        max_hours = config.loop.max_hours if max_hours is None else max_hours
        parallel = parallel or config.loop.max_parallel
        only_ids = {services.portfolio.resolve(item) for item in only} if only else None
        started = time.monotonic()
        self.apply_inbox()
        if sync:
            self._safe_sync(respect_interval=True)
        triage_active = triage and config.triage.enabled
        if triage_active:
            triage_active = self.triage(limit=config.triage.max_per_run).assessed > 0 or not self._has_untriaged()
        services.events.emit("run_started", parallel=parallel, max_epochs=max_epochs, max_hours=max_hours,
                             only=sorted(only_ids) if only_ids else None)
        executor = ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="epoch")
        running: dict[Future, str] = {}
        launched = 0
        failures_in_a_row = 0
        interrupts = 0
        last_sync = time.monotonic()

        def budget_left() -> bool:
            return (max_epochs == 0 or launched < max_epochs) and (
                max_hours == 0 or time.monotonic() - started < max_hours * 3600)

        try:
            while True:
                try:
                    self.apply_inbox()
                    if sync and time.monotonic() - last_sync > config.catalogue.qiqcop.sync_interval_hours * 3600:
                        self._safe_sync(respect_interval=False)
                        last_sync = time.monotonic()
                    if not budget_left() and not summary.stopped_reason:
                        summary.stopped_reason = "budget exhausted"
                    while budget_left() and not services.should_stop() and len(running) < parallel:
                        if not self._launch(executor, running, only_ids):
                            break
                        launched += 1
                    if (not running and triage_active and only_ids is None and budget_left()
                            and not services.should_stop() and self._has_untriaged()):
                        # Candidates ran out: assess more problems; stop trying if nothing gets assessed.
                        triage_active = self.triage(limit=config.triage.max_per_run).assessed > 0
                        continue
                    if not running:
                        if services.should_stop():
                            summary.stopped_reason = summary.stopped_reason or "stop requested"
                        elif not summary.stopped_reason:
                            summary.stopped_reason = "no eligible problems"
                        break
                    done, _ = wait(list(running), timeout=5, return_when=FIRST_COMPLETED)
                    for future in done:
                        problem_id = running.pop(future)
                        try:
                            result = future.result()
                        except Exception as error:  # boundary: one failed epoch must not stop the portfolio
                            message = f"{problem_id}: {type(error).__name__}: {error}"
                            summary.errors.append(message)
                            services.events.emit("epoch_crashed", problem=problem_id, error=message)
                            services.console.warn(f"epoch crashed: {message}")
                            failures_in_a_row += 1
                        else:
                            summary.epochs.append(result)
                            failures_in_a_row = failures_in_a_row + 1 if result.failed else 0
                        self.render_portfolio()
                        self._commit("portfolio: update")
                    if failures_in_a_row >= config.loop.max_consecutive_failures:
                        summary.stopped_reason = f"{failures_in_a_row} consecutive failed epochs"
                        services.drain_event.set()
                except KeyboardInterrupt:
                    interrupts += 1
                    if interrupts == 1:
                        services.drain_event.set()
                        summary.stopped_reason = "interrupted"
                        services.console.warn("finishing the current iterations; press Ctrl-C again to stop now")
                    else:
                        services.stop_event.set()
                        services.console.warn("stopping running agents")
        finally:
            executor.shutdown(wait=True)
            self.render_portfolio()
            self._commit("portfolio: run finished")
            services.events.emit("run_finished", epochs=len(summary.epochs), errors=len(summary.errors),
                                 reason=summary.stopped_reason)
            if summary.stopped_reason not in ("budget exhausted", ""):
                services.notifier.notify("run_stopped", f"run stopped: {summary.stopped_reason}",
                                         f"{len(summary.epochs)} epoch(s) completed.\nErrors:\n"
                                         + "\n".join(summary.errors[-10:]))
        return summary

    def _has_untriaged(self) -> bool:
        return any(entry.status == UNEXPLORED for entry in self.services.portfolio.snapshot().entries.values())

    def _launch(self, executor: ThreadPoolExecutor, running: dict[Future, str], only: set[str] | None) -> bool:
        services = self.services
        with services.portfolio.edit() as portfolio:
            selection = self.scheduler.select(portfolio, exclude=set(running.values()), only=only)
            if selection is None:
                return False
            portfolio.global_epochs += 1
            if selection.exploration:
                portfolio.exploration_epochs += 1
            global_epoch = portfolio.global_epochs
        priority = selection.priority
        services.events.emit(
            "epoch_scheduled", problem=selection.problem_id, global_epoch=global_epoch,
            exploration=selection.exploration, priority=round(priority.total, 4),
            components={"expected": round(priority.expected_progress, 4),
                        "exploration": round(priority.exploration_bonus, 4),
                        "relevance": round(priority.relevance, 4),
                        "stagnation": round(priority.stagnation_penalty, 4),
                        "suspension": round(priority.suspension_penalty, 4)},
        )
        runner = EpochRunner(services, selection.problem_id, global_epoch, exploration=selection.exploration)
        running[executor.submit(runner.run)] = selection.problem_id
        return True

    def _safe_sync(self, *, respect_interval: bool) -> None:
        try:
            self.sync(respect_interval=respect_interval)
        except CatalogueFetchError as error:
            self.services.console.warn(f"catalogue sync failed; continuing with the local catalogue: {error}",
                                       tag="catalogue")
            self.services.events.emit("catalogue_sync_failed", error=str(error))

    # --------------------------------------------------------------- output
    def render_portfolio(self) -> None:
        snapshot = self.services.portfolio.snapshot()
        priorities = {problem_id: priority.total for problem_id, priority in self.scheduler.priorities(snapshot).items()}
        self.services.guard.write_text(self.paths.portfolio_md, render_portfolio(snapshot, priorities))

    def _commit(self, message: str) -> None:
        paths = self.paths
        try:
            self.services.git.commit(
                [paths.portfolio, paths.portfolio_md, paths.catalogue_dir, paths.knowledge_dir, paths.events,
                 paths.internal_dir / "triage"],
                message,
            )
        except Exception as error:  # a failed commit must not stop research; the files are on disk
            self.services.console.warn(f"git commit failed: {error}")
            self.services.events.emit("git_failed", error=str(error))
