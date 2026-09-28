"""Workspace layout: the single source of truth for where files live."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

CONFIG_FILENAME = "lean-orch.toml"
AGENTS_FILENAME = "agents.toml"
LEAN_ROOT_NAMESPACE = "OpenQ"


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path

    @property
    def config(self) -> Path:
        return self.root / CONFIG_FILENAME

    @property
    def agents_config(self) -> Path:
        return self.root / AGENTS_FILENAME

    @property
    def agents_confirmed(self) -> Path:
        return self.internal_dir / "agents-confirmed"

    @property
    def readme(self) -> Path:
        return self.root / "README.md"

    @property
    def portfolio_md(self) -> Path:
        return self.root / "PORTFOLIO.md"

    @property
    def prompts_dir(self) -> Path:
        return self.root / "prompts"

    @property
    def schemas_dir(self) -> Path:
        return self.root / "schemas"

    @property
    def catalogue_dir(self) -> Path:
        return self.root / "catalogue"

    @property
    def catalogue_sources(self) -> Path:
        return self.catalogue_dir / "sources.json"

    @property
    def problems_dir(self) -> Path:
        return self.root / "problems"

    @property
    def lean_dir(self) -> Path:
        return self.root / "lean"

    @property
    def lean_problems_dir(self) -> Path:
        return self.lean_dir / LEAN_ROOT_NAMESPACE / "Problems"

    @property
    def knowledge_dir(self) -> Path:
        return self.root / "knowledge"

    @property
    def knowledge_results(self) -> Path:
        return self.knowledge_dir / "results.jsonl"

    @property
    def knowledge_relevance(self) -> Path:
        return self.knowledge_dir / "relevance.jsonl"

    @property
    def knowledge_md(self) -> Path:
        return self.knowledge_dir / "RESULTS.md"

    @property
    def internal_dir(self) -> Path:
        return self.root / ".lean-orch"

    @property
    def portfolio(self) -> Path:
        return self.internal_dir / "portfolio.json"

    @property
    def events(self) -> Path:
        return self.internal_dir / "events.jsonl"

    @property
    def runs_dir(self) -> Path:
        return self.internal_dir / "runs"

    @property
    def logs_dir(self) -> Path:
        return self.internal_dir / "logs"

    @property
    def objects_dir(self) -> Path:
        return self.internal_dir / "objects"

    @property
    def quarantine_dir(self) -> Path:
        return self.internal_dir / "quarantine"

    @property
    def tmp_dir(self) -> Path:
        return self.internal_dir / "tmp"

    @property
    def lock_file(self) -> Path:
        return self.internal_dir / "run.lock"

    @property
    def stop_file(self) -> Path:
        return self.internal_dir / "STOP"

    @property
    def pause_file(self) -> Path:
        return self.internal_dir / "PAUSE"

    @property
    def mcp_config(self) -> Path:
        return self.internal_dir / "mcp.json"

    def catalogue_entry(self, source: str, problem_id: str) -> Path:
        return self.catalogue_dir / source / f"{problem_id}.json"

    def problem(self, folder: str, lean_namespace: str) -> ProblemPaths:
        return ProblemPaths(workspace=self, folder=folder, lean_namespace=lean_namespace)

    def relative(self, path: Path) -> str:
        """Return ``path`` relative to the workspace root in POSIX form."""
        return Path(path).resolve().relative_to(self.root.resolve()).as_posix()


@dataclass(frozen=True)
class ProblemPaths:
    workspace: WorkspacePaths
    folder: str
    lean_namespace: str

    @property
    def root(self) -> Path:
        return self.workspace.problems_dir / self.folder

    @property
    def problem_md(self) -> Path:
        return self.root / "PROBLEM.md"

    @property
    def progress_md(self) -> Path:
        return self.root / "PROGRESS.md"

    @property
    def state(self) -> Path:
        return self.root / "state.json"

    @property
    def source(self) -> Path:
        return self.root / "source.json"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def iterations_dir(self) -> Path:
        return self.root / "iterations"

    @property
    def work_dir(self) -> Path:
        return self.root / "work"

    @property
    def experiments_dir(self) -> Path:
        return self.root / "experiments"

    @property
    def lean_link(self) -> Path:
        return self.root / "lean"

    @property
    def lean_dir(self) -> Path:
        return self.workspace.lean_problems_dir / self.lean_namespace

    @property
    def lean_module_prefix(self) -> str:
        return f"{LEAN_ROOT_NAMESPACE}.Problems.{self.lean_namespace}"

    def iteration_dir(self, epoch: int, iteration: int) -> Path:
        return self.iterations_dir / f"e{epoch:03d}-i{iteration:02d}"

    def epoch_report(self, epoch: int) -> Path:
        return self.reports_dir / f"epoch-{epoch:03d}.md"

    def writable_paths(self) -> list[Path]:
        """Directories that agents working on this problem may write to."""
        return [self.work_dir, self.experiments_dir, self.lean_dir]


def slugify(text: str, max_length: int = 60) -> str:
    """Make a lowercase, hyphen-separated ASCII slug for folder names."""
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"\$[^$]*\$", " ", normalized)
    words = re.findall(r"[a-zA-Z0-9]+", normalized.lower())
    slug = ""
    for word in words:
        candidate = f"{slug}-{word}" if slug else word
        if len(candidate) > max_length:
            break
        slug = candidate
    return slug or "problem"


def lean_identifier(text: str, max_words: int = 5) -> str:
    """Make an UpperCamelCase Lean identifier from free text."""
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"\$[^$]*\$", " ", normalized)
    stop_words = {"a", "an", "the", "of", "for", "and", "on", "in", "to", "is", "are", "with", "by", "does"}
    words = [word for word in re.findall(r"[a-zA-Z0-9]+", normalized) if word.lower() not in stop_words]
    identifier = "".join(word[:1].upper() + word[1:] for word in words[:max_words])
    if not identifier or identifier[0].isdigit():
        identifier = "P" + identifier
    return identifier


def short_id(problem_id: str, length: int = 6) -> str:
    """Return a short, stable suffix from an upstream identifier."""
    token = re.sub(r"[^a-zA-Z0-9]", "", problem_id.split("_", 1)[-1])
    return (token or re.sub(r"[^a-zA-Z0-9]", "", problem_id))[:length].lower()
