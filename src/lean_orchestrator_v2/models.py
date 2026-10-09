"""Research decisions, execution status, checking status, and human judgment."""

from dataclasses import dataclass, field
from typing import Any


CAMPAIGN_SCHEMA = "lean-orchestrator/campaign/2"


@dataclass
class Policy:
    max_tokens: int = 2_000_000
    focus_slots: int = 2
    exploration_slots: int = 5
    parallel: int = 3
    max_arms_per_problem: int = 2
    stagnation_rounds: int = 2
    focus_tokens: int = 160_000
    exploration_tokens: int = 80_000
    audit_tokens: int = 20_000
    call_tokens: int = 8_000
    submission_tokens: int = 12_000
    review_tokens: int = 16_000
    turns: int = 6
    state_tokens: int = 600
    feedback_tokens: int = 800
    synthesis_chars: int = 60_000
    tokenizer: str = "utf8"
    sandbox: str = "docker"
    docker_image: str = "python:3.11-slim"
    tool_timeout_seconds: float = 120
    focus_renewal_requires_approval: bool = True
    lean_template: str = ""
    preflight_imports: list[str] = field(default_factory=list)

    def validate(self) -> None:
        for name in ("max_tokens", "parallel", "max_arms_per_problem", "stagnation_rounds", "focus_tokens", "exploration_tokens", "audit_tokens",
                     "call_tokens", "submission_tokens", "review_tokens", "turns", "state_tokens", "feedback_tokens",
                     "synthesis_chars"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"policy.{name} must be a positive integer")
        if any(type(getattr(self, name)) is not int or getattr(self, name) < 0 for name in ("focus_slots", "exploration_slots")):
            raise ValueError("slot counts must be nonnegative integers")
        if self.sandbox not in ("docker", "local") or self.tokenizer not in ("utf8", "o200k_base"):
            raise ValueError("sandbox must be docker/local and tokenizer utf8/o200k_base")
        if self.tool_timeout_seconds <= 0:
            raise ValueError("tool timeout must be positive")
        if min(self.focus_tokens, self.exploration_tokens) < self.call_tokens + self.submission_tokens + self.review_tokens:
            raise ValueError("research allocations must include research, submission, and review reserves")
        if self.audit_tokens < self.review_tokens:
            raise ValueError("audit_tokens must cover the review reserve")


@dataclass
class Problem:
    id: str
    title: str
    packet: str
    packet_hash: str
    status: str = "active"
    stagnation: int = 0
    focus_rounds: int = 0
    revisit_method: str = ""
    revisit_credit: int = 0
    revisit_condition: str = ""
    candidates: list[str] = field(default_factory=list)
    rounds: list[dict] = field(default_factory=list)


@dataclass
class Allocation:
    id: str
    problem_id: str
    role: str
    action: str
    target: str
    baseline: str
    remaining_gap: str
    method: str
    success_evidence: str
    why_now: str
    alternative: str
    reversal_condition: str
    revisit_condition: str
    max_tokens: int
    baseline_candidates: list[str] = field(default_factory=list)
    target_scope: str = "restricted"
    candidate_id: str = ""
    repair_attempt_id: str = ""
    approved: bool = False
    approval_reason: str = ""
    status: str = "proposed"
    decision_actor: str = "coordinator"


@dataclass
class Attempt:
    id: str
    allocation_id: str
    problem_id: str
    round: int
    role: str
    status: str = "running"
    candidate_id: str = ""
    checkpoint: str = ""
    state: str = ""
    feedback: str = ""
    turns: int = 0
    charged_tokens: int = 0
    effective: bool = False
    material_progress: bool = False
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    id: str
    problem_id: str
    attempt_id: str
    frozen_dir: str
    claim: dict[str, Any]
    status: str = "proposed"
    verification: dict[str, Any] = field(default_factory=dict)
    review: dict[str, Any] = field(default_factory=dict)
    human_review: str = "pending"
    human_reason: str = ""
    external_confirmation: str = ""
    manifest: dict[str, str] = field(default_factory=dict)


@dataclass
class Campaign:
    schema: str = CAMPAIGN_SCHEMA
    round: int = 0
    problems: dict[str, Problem] = field(default_factory=dict)
    allocations: dict[str, Allocation] = field(default_factory=dict)
    attempts: dict[str, Attempt] = field(default_factory=dict)
    candidates: dict[str, Candidate] = field(default_factory=dict)
    decisions: list[dict] = field(default_factory=list)
