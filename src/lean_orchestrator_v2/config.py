"""Human policy and replaceable transports; no v1 scheduling configuration."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from lean_orchestrator.serde import from_dict

from .models import Policy


@dataclass
class BackendConfig:
    kind: str = "cli"
    command: list[str] = field(default_factory=list)
    model: str = "gpt-6-astra"
    reasoning_effort: str = "max"
    timeout_seconds: float = 300


@dataclass
class Config:
    policy: Policy = field(default_factory=Policy)
    solver: BackendConfig = field(default_factory=BackendConfig)
    coordinator: BackendConfig = field(default_factory=BackendConfig)
    reviewer: BackendConfig = field(default_factory=BackendConfig)

    def validate(self) -> None:
        self.policy.validate()
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
    if set(table) - {"policy", "solver", "coordinator", "reviewer"}:
        raise ValueError("unknown harness configuration table")
    for key, value in table.items():
        cls = Policy if key == "policy" else BackendConfig
        if set(value) - set(cls.__dataclass_fields__):
            raise ValueError(f"unknown configuration field in {key}")
    result = from_dict(Config, table)
    result.validate()
    return result
