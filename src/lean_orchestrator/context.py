"""Services shared by the orchestrator and its concurrent epochs."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from .agents import AgentRunner
from .catalogue.base import CatalogueStore
from .config import Config
from .events import Console, EventLog
from .gitops import GitRepo
from .integrity import IntegrityGuard
from .knowledge import KnowledgeBase
from .notify import Notifier
from .paths import WorkspacePaths
from .portfolio import PortfolioStore
from .prompts import PromptLibrary
from .roles import RoleRunner
from .verification import Verifier
from .verify.lean import LeanVerifier


class ControlCenter:
    """Hand human instructions to epochs that are currently running."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: set[str] = set()
        self._hints: dict[str, list[str]] = {}
        self._notes: dict[str, list[str]] = {}
        self._status: dict[str, str] = {}

    def mark_running(self, problem_id: str) -> None:
        with self._lock:
            self._running.add(problem_id)

    def mark_done(self, problem_id: str) -> None:
        with self._lock:
            self._running.discard(problem_id)

    def is_running(self, problem_id: str) -> bool:
        with self._lock:
            return problem_id in self._running

    def running(self) -> set[str]:
        with self._lock:
            return set(self._running)

    def push_hint(self, problem_id: str, text: str) -> None:
        with self._lock:
            self._hints.setdefault(problem_id, []).append(text)

    def take_hints(self, problem_id: str) -> list[str]:
        with self._lock:
            return self._hints.pop(problem_id, [])

    def push_note(self, problem_id: str, text: str) -> None:
        with self._lock:
            self._notes.setdefault(problem_id, []).append(text)

    def take_notes(self, problem_id: str) -> list[str]:
        with self._lock:
            return self._notes.pop(problem_id, [])

    def request_status(self, problem_id: str, status: str) -> None:
        with self._lock:
            self._status[problem_id] = status

    def take_status(self, problem_id: str) -> str | None:
        with self._lock:
            return self._status.pop(problem_id, None)


@dataclass
class Services:
    paths: WorkspacePaths
    config: Config
    console: Console
    events: EventLog
    guard: IntegrityGuard
    portfolio: PortfolioStore
    catalogue: CatalogueStore
    knowledge: KnowledgeBase
    prompts: PromptLibrary
    agents: AgentRunner
    roles: RoleRunner
    lean: LeanVerifier
    verifier: Verifier
    git: GitRepo
    notifier: Notifier
    self_check: str
    stop_event: threading.Event = field(default_factory=threading.Event)
    drain_event: threading.Event = field(default_factory=threading.Event)
    control: ControlCenter = field(default_factory=ControlCenter)

    def should_stop(self) -> bool:
        return self.stop_event.is_set() or self.drain_event.is_set() or self.paths.stop_file.exists()
