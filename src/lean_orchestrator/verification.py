"""Run every deterministic check a researcher report calls for."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import ProblemPaths, WorkspacePaths
from .state import ResearchState
from .verify.citations import CitationCheck, CitationChecker
from .verify.experiments import ExperimentCheck, ExperimentVerifier
from .verify.lean import LeanCheck, LeanVerifier


@dataclass
class ClaimVerification:
    claim: str
    lean: LeanCheck | None = None
    experiment: ExperimentCheck | None = None
    citation: CitationCheck | None = None
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.lean is not None:
            parts.append(f"Lean: {self.lean.summary}")
        if self.experiment is not None:
            parts.append(f"experiment: {self.experiment.summary}")
        if self.citation is not None:
            parts.append(f"citation: {self.citation.status}")
        parts.extend(self.notes)
        return "; ".join(parts) or "no machine-checkable evidence"


@dataclass
class ReferenceVerification:
    citation: str
    check: CitationCheck


@dataclass
class IterationVerification:
    claims: dict[str, ClaimVerification] = field(default_factory=dict)
    references: list[ReferenceVerification] = field(default_factory=list)
    experiments: dict[str, ExperimentCheck] = field(default_factory=dict)
    lean_available: bool = True


def resolve_workspace_path(paths: WorkspacePaths, problem: ProblemPaths, value: str) -> Path:
    """Interpret an agent-supplied path relative to the workspace or the problem folder."""
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    for base in (paths.root, problem.root):
        resolved = base / candidate
        if resolved.exists():
            return resolved
    return paths.root / candidate


class Verifier:
    """Coordinate the Lean, experiment, and citation checks for one iteration."""

    def __init__(self, paths: WorkspacePaths, lean: LeanVerifier, experiments: ExperimentVerifier,
                 citations: CitationChecker) -> None:
        self.paths = paths
        self.lean = lean
        self.experiments = experiments
        self.citations = citations

    def verify_report(self, report: dict[str, Any], state: ResearchState, problem: ProblemPaths) -> IterationVerification:
        outcome = IterationVerification(lean_available=self.lean.available())
        for claim in report.get("claims", []):
            verification = ClaimVerification(claim=claim["id"])
            if claim.get("lean"):
                self._verify_lean(claim, verification, state, problem, outcome.lean_available)
            experiment = claim.get("experiment")
            if experiment:
                directory = resolve_workspace_path(self.paths, problem, experiment)
                if directory.is_file():
                    directory = directory.parent  # agents sometimes cite the manifest instead of its directory
                key = str(directory.resolve())
                if key not in outcome.experiments:
                    outcome.experiments[key] = self.experiments.check(directory, allowed_root=problem.experiments_dir)
                verification.experiment = outcome.experiments[key]
            outcome.claims[claim["id"]] = verification
        references = report.get("references", [])
        checks = self.citations.check_many(
            [(reference["citation"], reference.get("arxiv"), reference.get("doi")) for reference in references])
        outcome.references = [ReferenceVerification(citation=reference["citation"], check=check)
                              for reference, check in zip(references, checks)]
        return outcome

    def _verify_lean(self, claim: dict[str, Any], verification: ClaimVerification, state: ResearchState,
                     problem: ProblemPaths, lean_available: bool) -> None:
        if not lean_available:
            verification.notes.append("Lean is not available in this workspace; the formal check was skipped")
            return
        config = self.lean.config
        lean_ref = claim["lean"]
        file = resolve_workspace_path(self.paths, problem, lean_ref["file"])
        kind = claim.get("kind")
        resolves = claim.get("resolves_main")
        formal = state.active_formal_statement
        target = formal.decl if (resolves and formal is not None) else None
        if resolves and formal is None:
            verification.notes.append("no locked formal statement exists, so this cannot be a Lean-certified resolution")
        use_comparator = bool(target) and config.comparator == "main" and self.lean.comparator_available()
        replay = config.kernel_recheck == "all" or (
            config.kernel_recheck == "main" and bool(resolves) and not use_comparator
        )
        check = self.lean.check(
            file,
            lean_ref["declaration"],
            allowed_root=problem.lean_dir,
            target=target,
            negated=resolves == "disproves",
            expect="prop_def" if kind == "formal_statement" else "theorem",
            kernel_recheck=replay,
        )
        verification.lean = check
        if kind == "formal_statement" and check.ok and check.module and config.triviality_probe:
            check.triviality = self.lean.triviality(check.module, lean_ref["declaration"], check.unfold)
            check.summary = self.lean.summarize(check, "prop_def")
        if target and check.ok and use_comparator and formal is not None and check.module:
            statement_module = self.lean.module_name(self.paths.root / formal.file)
            if statement_module is None:
                verification.notes.append("the locked statement file is not a library module")
                return
            check.comparator = self.lean.judge_main_result(
                namespace=problem.lean_namespace,
                statement_module=statement_module,
                statement_decl=formal.decl,
                proof_module=check.module,
                proof_decl=lean_ref["declaration"],
                negated=resolves == "disproves",
            )
            if check.comparator.status == "rejected":
                check.ok = False
                check.errors.append("lake comparator rejected the proof: " + check.comparator.output_tail[-500:])
            check.summary = self.lean.summarize(check, "theorem")
