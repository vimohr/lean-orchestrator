"""Human policy and replaceable transports; no v1 scheduling configuration."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from lean_orchestrator.notify import valid_email
from lean_orchestrator.serde import from_dict

from .models import Policy


@dataclass
class BackendConfig:
    kind: str = "cli"
    command: list[str] = field(default_factory=list)
    model: str = "gpt-6-astra"
    reasoning_effort: str = "max"
    timeout_seconds: float = 300


NOTIFICATION_EVENTS = frozenset({"resolution_claimed", "approval_required", "run_stopped", "run_failed"})


@dataclass
class NotifyConfig:
    email: str = ""
    events: list[str] = field(default_factory=lambda: ["resolution_claimed", "approval_required", "run_stopped", "run_failed"])

    def validate(self) -> None:
        if not isinstance(self.email, str) or (self.email and not valid_email(self.email)):
            raise ValueError("notify.email must be a single email address, or empty to disable notifications")
        if (not isinstance(self.events, list) or not all(isinstance(event, str) for event in self.events)
                or set(self.events) - NOTIFICATION_EVENTS):
            raise ValueError(f"notify.events must contain only: {', '.join(sorted(NOTIFICATION_EVENTS))}")


@dataclass
class Config:
    policy: Policy = field(default_factory=Policy)
    solver: BackendConfig = field(default_factory=BackendConfig)
    coordinator: BackendConfig = field(default_factory=BackendConfig)
    reviewer: BackendConfig = field(default_factory=BackendConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)

    def validate(self) -> None:
        self.policy.validate()
        self.notify.validate()
        for role in (self.solver, self.coordinator, self.reviewer):
            if role.kind not in ("cli", "responses") or role.timeout_seconds <= 0:
                raise ValueError("backend kind must be cli/responses and timeout positive")
            if role.kind == "cli" and (not role.command or not role.command[0]
                                       or not all(isinstance(item, str) for item in role.command)):
                raise ValueError("CLI backends require a command argument list")
            if role.kind == "responses" and not role.model:
                raise ValueError("Responses backends require an explicit model")


DEFAULT_CONFIG = '''# Policy numbers are configurable starting points, not measured optima.
[policy]
max_tokens = 2000000
focus_slots = 2
exploration_slots = 5
parallel = 3
stagnation_rounds = 2
focus_tokens = 160000
exploration_tokens = 80000
audit_tokens = 20000
call_tokens = 8000
submission_tokens = 12000
review_tokens = 16000
turns = 6
state_tokens = 600
feedback_tokens = 800
tokenizer = "utf8"               # conservative offline byte cap; "o200k_base" uses tiktoken
sandbox = "docker"              # calculation processes have --network none
docker_image = "python:3.11-slim" # provision a scientific image before starting real work
focus_renewal_requires_approval = true
lean_template = ""               # optional prepared OpenQ/Mathlib Lake project

[notify]
email = ""                      # optional recipient; 'run --email' overrides it for one invocation
events = ["resolution_claimed", "approval_required", "run_stopped", "run_failed"]

[solver]
kind = "cli"
command = ["codex", "exec", "--ignore-user-config", "--ephemeral", "--json", "--model", "gpt-6-astra", "--config", 'model_reasoning_effort="max"', "--sandbox", "workspace-write", "--skip-git-repo-check", "-"]

[coordinator]
kind = "cli"
command = ["claude", "-p", "--model", "claude-opus-5-5", "--effort", "max", "--tools", "", "--output-format", "json"]

[reviewer]
kind = "cli"
command = ["claude", "-p", "--model", "claude-opus-5-5", "--effort", "max", "--tools", "", "--output-format", "json"]
'''


def load_config(root: Path) -> Config:
    table = tomllib.loads((root / "harness.toml").read_text())
    sections = {"policy": Policy, "notify": NotifyConfig,
                "solver": BackendConfig, "coordinator": BackendConfig, "reviewer": BackendConfig}
    if set(table) - set(sections):
        raise ValueError("unknown harness configuration table")
    for key, value in table.items():
        cls = sections[key]
        if set(value) - set(cls.__dataclass_fields__):
            raise ValueError(f"unknown configuration field in {key}")
    result = from_dict(Config, table)
    result.validate()
    return result
