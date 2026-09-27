"""Workspace configuration (``lean-orch.toml``)."""

from __future__ import annotations

import dataclasses
import tomllib
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import AGENTS_FILENAME, CONFIG_FILENAME

ROLES = ("researcher", "critic", "supervisor", "literature", "triage")


class ConfigurationError(ValueError):
    """Raised when ``lean-orch.toml`` is missing, malformed, or inconsistent."""


@dataclass(frozen=True)
class AgentConfig:
    """How to launch one agent role. The rendered prompt is passed last or on stdin."""

    command: tuple[str, ...]
    prompt_via: str = "argument"
    timeout_minutes: float = 0.0


def _codex() -> AgentConfig:
    # --sandbox workspace-write confines writes to the workspace and blocks network access;
    # --search keeps literature search available because it runs on the model provider's side.
    return AgentConfig(command=(
        "codex", "--search", "exec", "--model", "gpt-6-astra",
        "--config", 'model_reasoning_effort="max"',
        "--sandbox", "workspace-write", "--skip-git-repo-check",
    ))


def _claude(effort: str = "max") -> AgentConfig:
    return AgentConfig(command=("claude", "-p", "--model", "claude-opus-5-5", "--effort", effort))


@dataclass(frozen=True)
class AgentsConfig:
    researcher: AgentConfig = field(default_factory=_codex)
    critic: AgentConfig = field(default_factory=_claude)
    supervisor: AgentConfig = field(default_factory=_claude)
    literature: AgentConfig = field(default_factory=_claude)
    triage: AgentConfig = field(default_factory=lambda: _claude("high"))
    retry_delays_seconds: tuple[float, ...] = (15, 30, 60, 120, 300)
    usage_limit_wait_minutes: float = 15.0
    usage_limit_max_hours: float = 12.0
    max_repair_attempts: int = 1

    def for_role(self, role: str) -> AgentConfig:
        if role not in ROLES:
            raise KeyError(f"unknown agent role {role!r}")
        return getattr(self, role)


@dataclass(frozen=True)
class LoopConfig:
    iterations_per_epoch: int = 12
    max_parallel: int = 1
    max_epochs: int = 0
    max_hours: float = 0.0
    agent_timeout_minutes: float = 0.0
    heartbeat_seconds: float = 60.0
    max_consecutive_failures: int = 3
    literature_recheck_epochs: int = 5
    branch_neglect_iterations: int = 4


@dataclass(frozen=True)
class SchedulerConfig:
    exploration_reserve: float = 0.2
    exploration_weight: float = 0.4
    relevance_weight: float = 0.3
    stagnation_weight: float = 0.3
    suspended_penalty: float = 0.6
    suspended_decay_epochs: float = 10.0
    prior_strength: float = 1.0
    ewma_alpha: float = 0.5
    min_suitability: float = 2.5
    max_relevance_bonus: float = 3.0


@dataclass(frozen=True)
class ProgressConfig:
    """How iteration outcomes become progress scores and stagnation counts."""

    threshold: int = 2
    escalate_after: int = 2
    suspend_after: int = 4
    meaningful_gain: float = 1.0
    weight_lean_verified: float = 1.0
    weight_reproduced: float = 1.0
    weight_formal_statement: float = 1.0
    weight_informal_claim: float = 0.5
    weight_dead_end: float = 0.5
    weight_reference: float = 0.25


@dataclass(frozen=True)
class LeanConfig:
    enabled: bool = True
    project_dir: str = "lean"
    lake: str = "lake"
    build_timeout_minutes: float = 30.0
    allowed_axioms: tuple[str, ...] = ("propext", "Classical.choice", "Quot.sound")
    forbid_native_decide: bool = True
    kernel_recheck: str = "main"
    comparator: str = "main"
    comparator_paranoid: bool = True
    allow_unsandboxed_comparator: bool = True
    triviality_probe: bool = True
    triviality_timeout_minutes: float = 5.0


@dataclass(frozen=True)
class ExperimentsConfig:
    enabled: bool = True
    python: tuple[str, ...] = ("{workspace}/.venv/bin/python",)
    timeout_minutes: float = 30.0
    float_rtol: float = 1e-6
    float_atol: float = 1e-9
    max_copy_megabytes: float = 200.0


@dataclass(frozen=True)
class CitationsConfig:
    enabled: bool = True
    timeout_seconds: float = 15.0


@dataclass(frozen=True)
class VerificationConfig:
    lean: LeanConfig = field(default_factory=LeanConfig)
    experiments: ExperimentsConfig = field(default_factory=ExperimentsConfig)
    citations: CitationsConfig = field(default_factory=CitationsConfig)


@dataclass(frozen=True)
class QiqcopConfig:
    enabled: bool = True
    jsonl_url: str = "https://qiqc-op.com/api/v1/problems.jsonl"
    release_url: str = "https://qiqc-op.com/api/v1/release.json"
    include_status: tuple[str, ...] = ("Unsolved",)
    include_fields: tuple[str, ...] = ()
    exclude_topics: tuple[str, ...] = ()
    sync_interval_hours: float = 24.0
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class CatalogueConfig:
    qiqcop: QiqcopConfig = field(default_factory=QiqcopConfig)


@dataclass(frozen=True)
class TriageConfig:
    enabled: bool = True
    batch_size: int = 10
    max_per_run: int = 40
    preferred_topics: tuple[str, ...] = (
        "Matrix and entropy inequalities", "Quantum separability", "Bound entanglement", "Bell nonlocality",
        "Bell-diagonal states", "Absolutely maximally entangled states", "Mutually unbiased bases",
        "Symmetric informationally complete measurements", "Quantum channel structure", "Local unitary equivalence",
    )
    weights: dict[str, float] = field(default_factory=lambda: {
        "precision": 1.0,
        "finite_dimensional": 1.0,
        "formalizability": 1.0,
        "closability": 1.0,
        "tractable_subcases": 1.5,
        "background": 0.5,
    })


@dataclass(frozen=True)
class NotifyConfig:
    email: str = ""
    events: tuple[str, ...] = ("resolution_claimed", "resolved_externally", "run_stopped")


@dataclass(frozen=True)
class GitConfig:
    enabled: bool = True
    author_name: str = "lean-orch"
    author_email: str = "lean-orch@localhost"


@dataclass(frozen=True)
class McpConfig:
    """Optional MCP servers written to ``.lean-orch/mcp.json`` for agents that accept it."""

    qiqcop_url: str = "https://api.qiqc-op.com/mcp"
    lean_lsp: bool = False


@dataclass(frozen=True)
class Config:
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    loop: LoopConfig = field(default_factory=LoopConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    progress: ProgressConfig = field(default_factory=ProgressConfig)
    verification: VerificationConfig = field(default_factory=VerificationConfig)
    catalogue: CatalogueConfig = field(default_factory=CatalogueConfig)
    triage: TriageConfig = field(default_factory=TriageConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    git: GitConfig = field(default_factory=GitConfig)
    mcp: McpConfig = field(default_factory=McpConfig)

    def validate(self) -> None:
        for role in ROLES:
            agent = self.agents.for_role(role)
            if not agent.command or not all(isinstance(part, str) and part for part in agent.command):
                raise ConfigurationError(f"agents.{role}.command must be a non-empty list of strings")
            if agent.prompt_via not in ("argument", "stdin"):
                raise ConfigurationError(f"agents.{role}.prompt_via must be 'argument' or 'stdin'")
        if self.loop.iterations_per_epoch < 1:
            raise ConfigurationError("loop.iterations_per_epoch must be at least 1")
        if self.loop.max_parallel < 1:
            raise ConfigurationError("loop.max_parallel must be at least 1")
        if not 0.0 <= self.scheduler.exploration_reserve < 1.0:
            raise ConfigurationError("scheduler.exploration_reserve must lie in [0, 1)")
        if not 0.0 < self.scheduler.ewma_alpha <= 1.0:
            raise ConfigurationError("scheduler.ewma_alpha must lie in (0, 1]")
        if self.progress.threshold not in (1, 2, 3):
            raise ConfigurationError("progress.threshold must be 1, 2, or 3")
        if not 1 <= self.progress.escalate_after <= self.progress.suspend_after:
            raise ConfigurationError("progress.escalate_after must lie between 1 and progress.suspend_after")
        if self.verification.lean.kernel_recheck not in ("off", "main", "all"):
            raise ConfigurationError("verification.lean.kernel_recheck must be 'off', 'main', or 'all'")
        if self.verification.lean.comparator not in ("off", "main"):
            raise ConfigurationError("verification.lean.comparator must be 'off' or 'main'")
        if self.triage.batch_size < 1:
            raise ConfigurationError("triage.batch_size must be at least 1")


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(f"could not read {path}: {error}") from error


def load_config(workspace: Path) -> Config:
    """Read ``lean-orch.toml`` and, if present, the agent commands in ``agents.toml``."""
    path = workspace / CONFIG_FILENAME
    if not path.is_file():
        raise ConfigurationError(f"{CONFIG_FILENAME} is missing from {workspace}; run 'lean-orch init' first")
    table = _read_toml(path)
    if "agents" in table:
        raise ConfigurationError(
            f"agent commands live in {AGENTS_FILENAME}; move the [agents] tables of {CONFIG_FILENAME} there"
        )
    agents_path = workspace / AGENTS_FILENAME
    agents = parse_agents(_read_toml(agents_path)) if agents_path.is_file() else AgentsConfig()
    config = dataclasses.replace(parse_config(table), agents=agents)
    config.validate()
    return config


def parse_config(table: dict[str, Any]) -> Config:
    return _build(Config, table, "")


def parse_agents(table: dict[str, Any]) -> AgentsConfig:
    """Parse the contents of ``agents.toml``."""
    return _build(AgentsConfig, table, AGENTS_FILENAME)


def _build(cls: type, table: Any, where: str) -> Any:
    if not isinstance(table, dict):
        raise ConfigurationError(f"{where or 'config'} must be a table")
    hints = typing.get_type_hints(cls)
    names = {item.name for item in dataclasses.fields(cls)}
    unknown = sorted(set(table) - names)
    if unknown:
        raise ConfigurationError(f"unknown key(s) in [{where or 'root'}]: {', '.join(unknown)}")
    values = {}
    for item in dataclasses.fields(cls):
        if item.name not in table:
            continue
        key = f"{where}.{item.name}" if where else item.name
        values[item.name] = _coerce(hints[item.name], table[item.name], key)
    return cls(**values)


def _coerce(annotation: Any, value: Any, where: str) -> Any:
    origin = typing.get_origin(annotation)
    if dataclasses.is_dataclass(annotation):
        return _build(annotation, value, where)
    if origin is tuple:
        if isinstance(value, str) and where.endswith(".command"):
            raise ConfigurationError(f"{where} must be a list of arguments, not a string")
        if not isinstance(value, list):
            raise ConfigurationError(f"{where} must be a list")
        (item_type, _ellipsis) = typing.get_args(annotation)
        return tuple(_coerce(item_type, item, f"{where}[{index}]") for index, item in enumerate(value))
    if origin is dict:
        if not isinstance(value, dict):
            raise ConfigurationError(f"{where} must be a table")
        _key_type, item_type = typing.get_args(annotation)
        return {str(key): _coerce(item_type, item, f"{where}.{key}") for key, item in value.items()}
    if annotation is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigurationError(f"{where} must be a number")
        return float(value)
    if annotation is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigurationError(f"{where} must be an integer")
        return value
    if annotation is bool:
        if not isinstance(value, bool):
            raise ConfigurationError(f"{where} must be true or false")
        return value
    if annotation is str:
        if not isinstance(value, str):
            raise ConfigurationError(f"{where} must be a string")
        return value
    raise ConfigurationError(f"{where}: unsupported configuration type {annotation!r}")


def expand_placeholders(arguments: tuple[str, ...] | list[str], values: dict[str, str]) -> list[str]:
    """Replace ``{name}`` placeholders for the documented names only."""
    expanded = []
    for argument in arguments:
        for name, value in values.items():
            argument = argument.replace("{" + name + "}", value)
        expanded.append(argument)
    return expanded
