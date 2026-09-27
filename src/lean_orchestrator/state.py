"""Persistent research state of one problem.

This is the long-lived object of the system: agents are disposable, but every
claim, attempt, dead end, and verification outcome is recorded here. Only the
orchestrator writes ``state.json``; agents read it through ``PROGRESS.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .jsonio import read_json, utc_now, write_json
from .serde import from_dict, to_dict

STATE_SCHEMA = "lean-orch/state/1"

# Trust levels, strongest first. Only the orchestrator assigns them.
LEAN_VERIFIED = "lean_verified"
REPRODUCED = "reproduced"
CRITIC_ACCEPTED = "critic_accepted"
PROPOSED = "proposed"
REJECTED = "rejected"
RETRACTED = "retracted"
TRUST_ORDER = (LEAN_VERIFIED, REPRODUCED, CRITIC_ACCEPTED, PROPOSED, REJECTED, RETRACTED)
ACCEPTED_TRUST = frozenset({LEAN_VERIFIED, REPRODUCED, CRITIC_ACCEPTED})

CLAIM_KINDS = (
    "lemma", "reduction", "counterexample", "numerical_evidence", "literature",
    "conjecture", "main_result", "formal_statement",
)
BRANCH_KINDS = ("prove", "disprove", "explore", "reformulate", "formalize")


@dataclass
class Stamp:
    epoch: int
    iteration: int
    time: str = field(default_factory=utc_now)


@dataclass
class Reference:
    id: str
    citation: str
    url: str | None = None
    arxiv: str | None = None
    doi: str | None = None
    relevance: str = ""
    check: str = "unchecked"
    created: Stamp | None = None


@dataclass
class KnownResult:
    """A result reported in the literature; recorded, never promoted to verified."""

    statement: str
    reference: str = ""
    created: Stamp | None = None


@dataclass
class Claim:
    id: str
    kind: str
    statement: str
    argument: str = ""
    trust: str = PROPOSED
    branch: str | None = None
    lean_file: str | None = None
    lean_decl: str | None = None
    lean_type: str | None = None
    experiment: str | None = None
    depends_on: list[str] = field(default_factory=list)
    resolves_main: str | None = None
    critic_verdict: str | None = None
    critic_notes: str = ""
    verification: str = ""
    created: Stamp | None = None
    updated: Stamp | None = None


@dataclass
class Attempt:
    id: str
    approach: str
    description: str
    outcome: str
    branch: str | None = None
    fingerprint: str = ""
    failure_reason: str = ""
    lesson: str = ""
    claims: list[str] = field(default_factory=list)
    effective_score: int | None = None
    repeat_of: list[str] = field(default_factory=list)
    created: Stamp | None = None


@dataclass
class DeadEnd:
    approach: str
    reason: str
    scope: str = ""
    attempt: str | None = None
    created: Stamp | None = None


@dataclass
class Subgoal:
    id: str
    statement: str
    status: str = "open"
    branch: str | None = None
    closed_by: str | None = None
    created: Stamp | None = None


@dataclass
class Branch:
    id: str
    kind: str
    goal: str
    status: str = "open"
    parent: str | None = None
    iterations: int = 0
    stagnation: int = 0
    last_worked: Stamp | None = None
    note: str = ""
    created: Stamp | None = None


@dataclass
class Experiment:
    path: str
    purpose: str = ""
    result_summary: str = ""
    reproduced: str = "unchecked"
    created: Stamp | None = None


@dataclass
class Search:
    space: str
    method: str
    result: str
    experiment: str | None = None
    created: Stamp | None = None


@dataclass
class StatementVersion:
    version: int
    text: str
    reason: str = ""
    created: Stamp | None = None


@dataclass
class FormalStatement:
    """A locked Lean proposition that formalizes the main question."""

    file: str
    decl: str
    claim: str
    locked_files: dict[str, str] = field(default_factory=dict)
    active: bool = True
    created: Stamp | None = None


@dataclass
class ProgressRecord:
    epoch: int
    iteration: int
    supervisor_score: int
    effective_score: int
    categories: list[str] = field(default_factory=list)
    justification: str = ""
    adjustments: list[str] = field(default_factory=list)
    time: str = field(default_factory=utc_now)


@dataclass
class EpochRecord:
    index: int
    global_index: int
    started: str
    ended: str | None = None
    iterations: int = 0
    mean_score: float | None = None
    decision: str = ""
    exploration: bool = False
    report: str | None = None


@dataclass
class Hint:
    """Guidance from a human, shown to every later agent working on the problem."""

    text: str
    time: str = field(default_factory=utc_now)


@dataclass
class LiteratureStatus:
    still_open: str = "unchecked"
    summary: str = ""
    resolution: str = ""
    checked: Stamp | None = None


@dataclass
class ResearchState:
    problem_id: str
    title: str
    folder: str
    lean_namespace: str
    schema: str = STATE_SCHEMA
    status: str = "active"
    statement_versions: list[StatementVersion] = field(default_factory=list)
    success_criteria: str = ""
    assumptions: list[str] = field(default_factory=list)
    literature: LiteratureStatus = field(default_factory=LiteratureStatus)
    references: list[Reference] = field(default_factory=list)
    known_results: list[KnownResult] = field(default_factory=list)
    techniques: list[str] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    formal_statements: list[FormalStatement] = field(default_factory=list)
    locked_lean_files: dict[str, str] = field(default_factory=dict)
    branches: list[Branch] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    dead_ends: list[DeadEnd] = field(default_factory=list)
    subgoals: list[Subgoal] = field(default_factory=list)
    experiments: list[Experiment] = field(default_factory=list)
    searches: list[Search] = field(default_factory=list)
    promising_directions: list[str] = field(default_factory=list)
    supervisor_assessment: str = ""
    stagnation_count: int = 0
    progress: list[ProgressRecord] = field(default_factory=list)
    epochs: list[EpochRecord] = field(default_factory=list)
    next_plan: dict | None = None
    hints: list[Hint] = field(default_factory=list)
    upstream_updates: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)

    def next_id(self, prefix: str) -> str:
        """Allocate the next stable identifier with the given prefix (C, A, B, G, R)."""
        number = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = number
        return f"{prefix}{number}"

    @property
    def current_statement(self) -> str:
        return self.statement_versions[-1].text if self.statement_versions else ""

    @property
    def current_epoch(self) -> EpochRecord | None:
        return self.epochs[-1] if self.epochs else None

    @property
    def active_formal_statement(self) -> FormalStatement | None:
        for statement in reversed(self.formal_statements):
            if statement.active:
                return statement
        return None

    def claim(self, claim_id: str) -> Claim | None:
        return next((claim for claim in self.claims if claim.id == claim_id), None)

    def branch(self, branch_id: str | None) -> Branch | None:
        return next((branch for branch in self.branches if branch.id == branch_id), None)

    def subgoal(self, subgoal_id: str | None) -> Subgoal | None:
        return next((goal for goal in self.subgoals if goal.id == subgoal_id), None)

    def open_branches(self) -> list[Branch]:
        return [branch for branch in self.branches if branch.status == "open"]

    def accepted_claims(self) -> list[Claim]:
        return [claim for claim in self.claims if claim.trust in ACCEPTED_TRUST]


def load_state(path) -> ResearchState:
    return from_dict(ResearchState, read_json(path))


def state_to_json(state: ResearchState) -> dict:
    return to_dict(state)


def save_state(path, state: ResearchState) -> None:
    write_json(path, to_dict(state))
