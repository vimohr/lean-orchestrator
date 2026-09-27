"""One bounded research epoch on one problem.

Each iteration follows the research loop of the design:

1. read the accumulated state (agents read ``DOSSIER.md`` and ``PROGRESS.md``);
2. the supervisor's plan names one promising next step;
3. the researcher attempts it;
4. Lean, experiment re-runs, and citation checks verify it, and the critic reviews it;
5. the orchestrator records what was learned, with trust levels it assigns itself;
6. the supervisor assesses the iteration and decides what happens next.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any

from . import blocks
from .agents import AgentError, AgentStopped
from .context import Services
from .jsonio import sha256_file, utc_now
from .portfolio import ACTIVE, PENDING_REVIEW, RESOLVED_EXTERNALLY, SUSPENDED, TERMINAL, PortfolioEntry
from .problem_setup import ensure_problem_folder, write_views
from .recording import (
    DecisionEffects, IterationOutcome, apply_decision, record_failed_iteration, record_iteration, record_literature,
    score_progress,
)
from .render import render_epoch_report_fallback
from .roles import RoleOutputError
from .serde import to_dict
from .state import ACCEPTED_TRUST, Branch, EpochRecord, Hint, Stamp
from .verify.citations import VERIFIED

MAX_CONSECUTIVE_ITERATION_FAILURES = 2
_BRANCH_GOALS = {
    "prove": "Prove the working statement.",
    "disprove": "Find and certify a counterexample to the working statement.",
    "explore": "Collect structural and numerical evidence about which direction is true.",
}


@dataclass
class EpochResult:
    problem_id: str
    global_epoch: int
    epoch: int = 0
    iterations: int = 0
    scores: list[int] = field(default_factory=list)
    decision: str = ""
    status: str = ""
    error: str | None = None
    failed: bool = False

    @property
    def mean_score(self) -> float | None:
        return mean(self.scores) if self.scores else None


class EpochRunner:
    def __init__(self, services: Services, problem_id: str, global_epoch: int, *, exploration: bool) -> None:
        self.services = services
        self.config = services.config
        self.problem_id = problem_id
        self.global_epoch = global_epoch
        self.exploration = exploration
        self.result = EpochResult(problem_id=problem_id, global_epoch=global_epoch)
        self.violations: list[str] = []

    def run(self) -> EpochResult:
        services = self.services
        entry = services.portfolio.get(self.problem_id)
        source = services.catalogue.load(entry.source, entry.id)
        self.problem, self.state = ensure_problem_folder(services, entry, source)
        self.tag = self.state.folder
        self.epoch = EpochRecord(index=len(self.state.epochs) + 1, global_index=self.global_epoch,
                                 started=utc_now(), exploration=self.exploration)
        self.state.epochs.append(self.epoch)
        self.state.status = ACTIVE
        self.result.epoch = self.epoch.index

        def start(item: PortfolioEntry) -> None:
            item.status = ACTIVE
            item.epochs += 1
            item.last_global_epoch = self.global_epoch
            item.suspended_at = None

        services.portfolio.update_entry(self.problem_id, start)
        services.events.emit("epoch_started", problem=self.problem_id, epoch=self.epoch.index,
                             global_epoch=self.global_epoch, exploration=self.exploration)
        services.console.info(f"epoch {self.epoch.index} started (global {self.global_epoch}"
                              f"{', exploration' if self.exploration else ''})", tag=self.tag)
        services.control.mark_running(self.problem_id)
        try:
            self._save(f"epoch {self.epoch.index} started")
            self._run_epoch(entry)
        except AgentStopped:
            self.result.decision = self.result.decision or "stopped"
            services.console.warn("epoch interrupted by a stop request", tag=self.tag)
        except Exception as error:
            self.result.error = f"{type(error).__name__}: {error}"
            self.result.decision = self.result.decision or "error"
            self.result.failed = True
            services.events.emit("epoch_error", problem=self.problem_id, error=self.result.error)
            services.console.warn(f"epoch failed: {self.result.error}", tag=self.tag)
            raise
        finally:
            services.control.mark_done(self.problem_id)
            self._finish()
        return self.result

    def _run_epoch(self, entry: PortfolioEntry) -> None:
        state = self.state
        if self._absorb_control():
            return
        if self._needs_literature() and self._literature():
            return
        self._ensure_branches(entry)
        plan = state.next_plan
        branch = state.branch(plan.get("branch")) if plan else None
        if branch is None or branch.status != "open":
            decision = self._supervise(None, ending=False)
            if decision is None or self._act_on(decision, self._record_decision(decision, Stamp(self.epoch.index, 0))):
                return
        failures = 0
        iterations = self.config.loop.iterations_per_epoch
        for iteration in range(1, iterations + 1):
            if self.services.should_stop():
                self.result.decision = self.result.decision or "stopped"
                return
            if self._absorb_control():
                return
            plan = state.next_plan
            if plan is None:
                self.result.decision = "no plan"
                return
            stamp = Stamp(self.epoch.index, iteration)
            directory = self.problem.iteration_dir(self.epoch.index, iteration)
            directory.mkdir(parents=True, exist_ok=True)
            self.services.guard.write_json(directory / "plan.json", plan)
            self.epoch.iterations = iteration
            self.result.iterations = iteration
            outcome, failure = self._iterate(plan, stamp, directory)
            failures = failures + 1 if outcome is None else 0
            if failures >= MAX_CONSECUTIVE_ITERATION_FAILURES:
                self.result.decision = "failed"
                self.result.failed = True
                self.result.error = self.result.error or failure
                return
            decision = self._supervise(directory, ending=iteration == iterations, failure=failure)
            if decision is None:
                self._save(f"epoch {self.epoch.index} iteration {iteration} (no supervisor decision)")
                return
            record = score_progress(state, stamp=stamp, assessment=decision.get("assessment"), outcome=outcome,
                                    progress=self.config.progress)
            self.result.scores.append(record.effective_score)
            adjustments = f" (supervisor {record.supervisor_score}; {'; '.join(record.adjustments)})" \
                if record.adjustments else ""
            self.services.console.info(f"iteration {iteration}: score {record.effective_score}{adjustments}, "
                                       f"stagnation {state.stagnation_count}", tag=self.tag)
            effects = self._record_decision(decision, stamp)
            if outcome is not None and outcome.main_result is not None:
                claim_id, direction, trust = outcome.main_result
                self._set_pending_review(claim_id, direction, trust)
                return
            if self._act_on(decision, effects):
                return
            if state.stagnation_count >= self.config.progress.suspend_after:
                self._suspend(f"no meaningful progress for {state.stagnation_count} iterations")
                return
            self._save(f"epoch {self.epoch.index} iteration {iteration}, score {record.effective_score}")
        self.result.decision = self.result.decision or "epoch complete"

    def _iterate(self, plan: dict[str, Any], stamp: Stamp, directory: Path) -> tuple[IterationOutcome | None, str]:
        services, state, problem = self.services, self.state, self.problem
        values = {
            **blocks.problem_values(services.paths, problem, state),
            "plan": blocks.format_plan(plan),
            "branch_guidance": blocks.branch_guidance(plan),
            "warnings": blocks.warnings_block(state, plan, self.violations),
            "lean_dir": services.paths.relative(problem.lean_dir),
            "lean_module_prefix": problem.lean_module_prefix,
            "lean_project": services.paths.relative(services.lean.project),
            "lean_libraries": self._lean_libraries(),
            "lean_library_note": self._lean_library_note(),
            "self_check": services.self_check,
            "formal_statement_note": blocks.formal_statement_note(state),
            "python_command": " ".join(self.config.verification.experiments.python).replace(
                "{workspace}", str(services.paths.root)),
            "python_packages": self._python_packages(),
        }
        try:
            report = self._call("researcher", directory / "report.json", values)
        except AgentStopped:
            raise
        except (RoleOutputError, AgentError) as error:
            reason = f"the researcher failed: {error}"
            services.console.warn(reason, tag=self.tag)
            services.events.emit("iteration_failed", problem=self.problem_id, epoch=stamp.epoch,
                                 iteration=stamp.iteration, reason=reason)
            record_failed_iteration(state, stamp=stamp, plan=plan, reason=reason)
            return None, reason
        verification = services.verifier.verify_report(report, state, problem)
        services.guard.write_json(directory / "verification.json", to_dict(verification))
        critique = self._criticize(directory, report)
        outcome = record_iteration(state, stamp=stamp, plan=plan, report=report, verification=verification,
                                   critique=critique)
        self._lock_files(outcome)
        services.guard.write_json(directory / "outcome.json", {
            "attempt": outcome.attempt_id,
            "claims": {
                local: {"id": global_id, "trust": state.claim(global_id).trust,
                        "verification": state.claim(global_id).verification}
                for local, global_id in outcome.claim_ids.items()
            },
            "repeat_of": outcome.repeat_of,
            "weighted_gain": outcome.weighted_gain(self.config.progress),
            "notes": outcome.notes,
        })
        accepted = [claim for claim in (state.claim(claim_id) for claim_id in outcome.accepted) if claim is not None]
        if accepted:
            services.knowledge.add_results(problem_id=self.problem_id, title=state.title, folder=state.folder,
                                           claims=accepted, global_epoch=self.global_epoch)
        services.events.emit(
            "iteration_recorded", problem=self.problem_id, epoch=stamp.epoch, iteration=stamp.iteration,
            accepted=outcome.accepted, lean_verified=outcome.lean_verified, rejected=outcome.rejected,
            repeat_of=outcome.repeat_of,
        )
        return outcome, ""

    def _criticize(self, directory: Path, report: dict[str, Any]) -> dict[str, Any] | None:
        services, problem = self.services, self.problem
        if not report.get("claims") and not report.get("dead_ends"):
            return None
        values = {
            **blocks.problem_values(services.paths, problem, self.state),
            "report_path": services.paths.relative(directory / "report.json"),
            "plan_path": services.paths.relative(directory / "plan.json"),
            "verification_path": services.paths.relative(directory / "verification.json"),
            "lean_project": services.paths.relative(services.lean.project),
            "critic_dir": services.paths.relative(problem.work_dir / "critic"),
        }
        context = {"claim_ids": [claim["id"] for claim in report.get("claims", [])]}
        try:
            return self._call("critic", directory / "critique.json", values, context)
        except AgentStopped:
            raise
        except (RoleOutputError, AgentError) as error:
            services.console.warn(f"critic failed; claims stay unreviewed: {error}", tag=self.tag)
            services.events.emit("critic_failed", problem=self.problem_id, error=str(error))
            return None

    def _supervise(self, directory: Path | None, *, ending: bool, failure: str = "") -> dict[str, Any] | None:
        services, state, problem = self.services, self.state, self.problem
        output_dir = directory or problem.iteration_dir(self.epoch.index, 0)
        output_dir.mkdir(parents=True, exist_ok=True)
        incoming = services.knowledge.relevance_to(self.problem_id, after_global_epoch=self._previous_global_epoch())
        materials = blocks.iteration_materials(services.paths, directory)
        if failure:
            materials += f"\n\nThe researcher did not produce a usable report: {failure}"
        values = {
            **blocks.problem_values(services.paths, problem, state),
            "branch_list": blocks.branch_list(state),
            "iteration_materials": materials,
            "assessment_instructions": blocks.ASSESSMENT_INSTRUCTIONS if directory is not None else "",
            "branch_notes": blocks.neglected_branches(state, self.config.loop.branch_neglect_iterations),
            "escalation": blocks.escalation_block(state, self.config.progress),
            "hints": blocks.hints_block(state),
            "epoch_end_instructions": blocks.epoch_end_instructions(ending),
            "relevance_block": blocks.relevance_block(state, services.portfolio.snapshot(), incoming),
        }
        context = {
            "expects_assessment": directory is not None,
            "epoch_ending": ending,
            "branch_ids": [branch.id for branch in state.branches],
        }
        try:
            return self._call("supervisor", output_dir / "decision.json", values, context)
        except AgentStopped:
            raise
        except (RoleOutputError, AgentError) as error:
            self.result.error = f"the supervisor failed: {error}"
            self.result.decision = "failed"
            self.result.failed = True
            services.console.warn(self.result.error, tag=self.tag)
            services.events.emit("supervisor_failed", problem=self.problem_id, error=str(error))
            return None

    def _record_decision(self, decision: dict[str, Any], stamp: Stamp) -> DecisionEffects:
        """Apply the supervisor's updates to the state and record cross-problem links and reports."""
        services = self.services
        effects = apply_decision(self.state, decision, stamp=stamp)
        if effects.relevance:
            known = set(services.portfolio.snapshot().entries)
            accepted = services.knowledge.add_relevance(source_problem=self.problem_id, links=effects.relevance,
                                                        known_problems=known, global_epoch=self.global_epoch)
            if accepted:
                services.events.emit("relevance_recorded", problem=self.problem_id,
                                     targets=[link["to"] for link in accepted])
        if effects.epoch_report:
            self._write_epoch_report(effects.epoch_report)
        self.result.decision = decision["decision"]
        return effects

    def _act_on(self, decision: dict[str, Any], effects: DecisionEffects) -> bool:
        """Carry out the supervisor's decision; return True when the epoch should end."""
        kind = decision["decision"]
        if kind == "suspend":
            self._suspend(decision.get("rationale", "suspended by the supervisor"))
            return True
        if kind == "switch":
            return True
        if kind in ("close_proved", "close_disproved"):
            wanted = "proves" if kind == "close_proved" else "disproves"
            main = [claim for claim in self.state.claims
                    if claim.kind == "main_result" and claim.resolves_main == wanted and claim.trust in ACCEPTED_TRUST]
            if main:
                self._set_pending_review(main[-1].id, wanted, main[-1].trust)
                return True
            self.services.console.warn(f"the supervisor chose {kind}, but no accepted main result exists",
                                       tag=self.tag)
            self.result.decision = "continue"
        if effects.plan is None:
            self.result.decision = "no plan"
            return True
        return False

    def _call(self, role: str, output: Path, values: dict[str, Any], context: dict[str, Any] | None = None) -> dict:
        services = self.services
        # Agents read the state from disk, so it must be current before every call.
        write_views(services, self.problem, self.state, services.portfolio.get(self.problem_id))
        result = services.roles.call(role, tag=f"{self.tag}:{role}", problem_id=self.problem_id, output_path=output,
                                     values=values, context=context)
        if result.violations:
            descriptions = [violation.describe() for violation in result.violations]
            self.violations.extend(descriptions)
            self.state.violations.extend(f"{utc_now()} during {role}: {text}" for text in descriptions)
            services.events.emit("integrity_violation", problem=self.problem_id, role=role, violations=descriptions)
            services.console.warn(f"{role} touched protected files, which were restored: {'; '.join(descriptions)}",
                                  tag=self.tag)
        return result.data

    def _literature(self) -> bool:
        """Run the literature check; return True if the problem turned out to be resolved."""
        services, state, problem = self.services, self.state, self.problem
        directory = problem.iteration_dir(self.epoch.index, 0)
        directory.mkdir(parents=True, exist_ok=True)
        values = {
            **blocks.problem_values(services.paths, problem, state),
            "previous_literature": blocks.previous_literature(state),
            "qiqcop_mcp": self.config.mcp.qiqcop_url,
        }
        try:
            literature = self._call("literature", directory / "literature.json", values)
        except AgentStopped:
            raise
        except (RoleOutputError, AgentError) as error:
            services.console.warn(f"literature check failed; continuing without it: {error}", tag=self.tag)
            return False
        checks = services.verifier.citations.check_many(
            [(item["citation"], item.get("arxiv"), item.get("doi")) for item in literature.get("references", [])])
        verified = record_literature(state, literature, checks, stamp=Stamp(self.epoch.index, 0))
        state.upstream_updates.clear()
        services.events.emit("literature_checked", problem=self.problem_id, still_open=literature["still_open"],
                             references=len(checks), verified=verified)
        services.console.info(f"literature check: still open = {literature['still_open']} "
                              f"({verified}/{len(checks)} references verified)", tag=self.tag)
        if literature["still_open"] != "no":
            return False
        note = literature.get("resolution", "")
        if not any(check.status == VERIFIED for check in checks):
            note += " (no cited reference could be verified automatically; confirm by hand)"
        self._set_status(RESOLVED_EXTERNALLY, note)
        self.result.decision = "resolved_externally"
        services.notifier.notify(
            "resolved_externally",
            f"{state.title} appears to be resolved in the literature",
            f"The literature agent reports that this problem is already resolved.\n\n{note}\n\n"
            f"Details: {services.paths.relative(problem.progress_md)}\n"
            f"Reopen it with: lean-orch activate {self.problem_id}",
        )
        return True

    def _needs_literature(self) -> bool:
        literature = self.state.literature
        if literature.checked is None:
            return True
        if self.state.upstream_updates:
            return True
        interval = self.config.loop.literature_recheck_epochs
        return interval > 0 and self.epoch.index - literature.checked.epoch >= interval

    def _ensure_branches(self, entry: PortfolioEntry) -> None:
        """Start with competing prove and disprove branches unless triage suggested otherwise."""
        state = self.state
        if state.branches:
            return
        modes = [mode for mode in (entry.triage.modes if entry.triage else []) if mode in _BRANCH_GOALS]
        for mode in modes or ["prove", "disprove"]:
            state.branches.append(Branch(id=state.next_id("B"), kind=mode, goal=_BRANCH_GOALS[mode],
                                         created=Stamp(self.epoch.index, 0)))

    def _lock_files(self, outcome: IterationOutcome) -> None:
        """Protect Lean files holding verified results or a locked statement, with their local imports."""
        services, state = self.services, self.state
        for relative in outcome.lock_files:
            path = services.paths.root / relative
            if not path.is_file():
                continue
            locked: dict[str, str] = {}
            for item in [path, *services.lean.local_imports(path)]:
                if item.is_file():
                    services.guard.protect(item)
                    locked[services.paths.relative(item)] = sha256_file(item)
            state.locked_lean_files.update(locked)
            for formal in state.formal_statements:
                if formal.active and formal.file == relative and not formal.locked_files:
                    formal.locked_files = dict(locked)
            services.events.emit("lean_files_locked", problem=self.problem_id, files=sorted(locked))

    def _set_pending_review(self, claim_id: str, direction: str, trust: str) -> None:
        services, state = self.services, self.state
        claim = state.claim(claim_id)
        certified = "Lean-certified" if trust == "lean_verified" else f"not Lean-certified (trust: {trust})"
        note = f"claim {claim_id} {direction} the main statement; {certified}"
        self._set_status(PENDING_REVIEW, note)
        self.result.decision = f"resolution claimed ({direction})"
        services.events.emit("resolution_claimed", problem=self.problem_id, claim=claim_id, direction=direction,
                             trust=trust)
        services.console.info(f"RESOLUTION CLAIMED: {note}. Awaiting human review.", tag=self.tag)
        services.notifier.notify(
            "resolution_claimed",
            f"resolution claimed for {state.title}",
            f"{note}.\n\nStatement: {claim.statement if claim else ''}\n\n"
            f"Verification: {claim.verification if claim else ''}\n\n"
            f"Review {services.paths.relative(self.problem.progress_md)}, then run\n"
            f"  lean-orch review {self.problem_id} --accept   or   --reject \"reason\"",
        )

    def _suspend(self, reason: str) -> None:
        self._set_status(SUSPENDED, reason)
        self.result.decision = "suspend"
        self.services.console.info(f"suspended: {reason}", tag=self.tag)

    def _set_status(self, status: str, note: str) -> None:
        self.state.status = status

        def change(item: PortfolioEntry) -> None:
            if item.status in TERMINAL and status not in TERMINAL:
                return
            item.status = status
            item.note = note
            if status == SUSPENDED:
                item.suspended_at = self.global_epoch

        self.services.portfolio.update_entry(self.problem_id, change)
        self.services.events.emit("status_changed", problem=self.problem_id, status=status, note=note)

    def _absorb_control(self) -> bool:
        """Apply hints, upstream notes, and status requests; return True to end the epoch."""
        control = self.services.control
        for text in control.take_hints(self.problem_id):
            self.state.hints.append(Hint(text=text))
        for text in control.take_notes(self.problem_id):
            self.state.upstream_updates.append(text)
        requested = control.take_status(self.problem_id)
        if requested:
            self._set_status(requested, "requested by a human")
            self.result.decision = f"status set to {requested} by a human"
            return True
        return False

    def _previous_global_epoch(self) -> int | None:
        earlier = [record.global_index for record in self.state.epochs[:-1]]
        return max(earlier) if earlier else None

    def _write_epoch_report(self, text: str) -> None:
        path = self.problem.epoch_report(self.epoch.index)
        header = "" if text.lstrip().startswith("#") else f"# Epoch {self.epoch.index} report: {self.state.title}\n\n"
        self.services.guard.write_text(path, header + text.strip() + "\n")
        self.epoch.report = path.relative_to(self.problem.root).as_posix()

    def _save(self, message: str) -> None:
        services = self.services
        write_views(services, self.problem, self.state, services.portfolio.get(self.problem_id))
        try:
            services.git.commit(
                [self.problem.root, self.problem.lean_dir, services.paths.knowledge_dir, services.paths.portfolio],
                f"research({self.state.folder}): {message}",
            )
        except Exception as error:  # a failed commit must not stop the research loop; the files are saved
            services.console.warn(f"git commit failed: {error}", tag=self.tag)
            services.events.emit("git_failed", problem=self.problem_id, error=str(error))

    def _finish(self) -> None:
        services, state, epoch = self.services, self.state, self.epoch
        epoch.ended = utc_now()
        epoch.mean_score = self.result.mean_score
        epoch.decision = self.result.decision
        if epoch.report is None and (epoch.iterations or self.result.decision not in ("", "stopped")):
            self._write_epoch_report(render_epoch_report_fallback(state, epoch.index))
        alpha = self.config.scheduler.ewma_alpha
        normalized = None if self.result.mean_score is None else self.result.mean_score / 3.0
        failed = self.result.failed

        def finish(item: PortfolioEntry) -> None:
            item.iterations += epoch.iterations
            item.stagnation = state.stagnation_count
            if normalized is not None:
                item.ewma_progress = normalized if item.ewma_progress is None else (
                    alpha * normalized + (1 - alpha) * item.ewma_progress)
            item.consecutive_failures = item.consecutive_failures + 1 if failed else 0

        entry = services.portfolio.update_entry(self.problem_id, finish)
        self.result.status = entry.status
        state.status = entry.status
        services.events.emit("epoch_finished", problem=self.problem_id, epoch=epoch.index,
                             global_epoch=self.global_epoch, iterations=epoch.iterations,
                             mean_score=epoch.mean_score, decision=epoch.decision, status=entry.status,
                             error=self.result.error)
        services.console.info(f"epoch {epoch.index} finished: {epoch.iterations} iteration(s), "
                              f"decision {epoch.decision or 'n/a'}, status {entry.status}", tag=self.tag)
        self._save(f"epoch {epoch.index} finished ({epoch.decision or 'n/a'})")

    def _lean_libraries(self) -> str:
        manifest = self.services.lean.project / "lake-manifest.json"
        try:
            packages = [package.get("name", "") for package in json.loads(manifest.read_text()).get("packages", [])]
        except (OSError, ValueError):
            return ""
        extra = [name for name in packages if name.lower() in ("physlib", "quantuminfo", "physlean")]
        return (" plus " + ", ".join(extra)) if extra else ""

    def _lean_library_note(self) -> str:
        project = self.services.paths.relative(self.services.lean.project)
        foundations = ("- `OpenQ.Foundations` (shared, locked) defines density matrices, the partial transpose, "
                       "PPT, partial traces, separable states, and the separable cone on plain matrices.")
        if "physlib" not in self._lean_libraries().lower():
            return foundations
        return (
            "- Physlib's QuantumInfo library is available (`import QuantumInfo` or its modules). It formalizes "
            "states (`MState`), channels (`CPTPMap`, and `MatrixMap` with Choi and Kraus forms), partial traces, "
            "the von Neumann entropy `Sᵥₙ`, the relative entropy `qRelativeEnt`, sandwiched Rényi divergences, "
            "fidelity, trace distance, and separable states. Prefer these definitions to ad hoc ones: shared "
            "definitions make formal statements easier to trust. Sources: "
            f"`{project}/.lake/packages/Physlib/QuantumInfo`; overview: "
            f"`{project}/.lake/packages/Physlib/docs/QI_DOC.md`. A few QuantumInfo lemmas are still proved with "
            "`sorry`, and the axiom audit rejects any proof that depends on them. QuantumInfo uses Lean's module "
            "system, so definitions it does not expose cannot be unfolded; reason with its lemmas instead.\n"
            + foundations + " These apply to `ρ.m` for a state `ρ : MState (m × n)`; Physlib has no partial "
            "transpose of its own yet."
        )

    def _python_packages(self) -> str:
        pyproject = self.services.paths.root / "pyproject.toml"
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "whatever is installed in the workspace environment"
        dependencies = data.get("project", {}).get("dependencies", [])
        return ", ".join(dependencies) + " (declared in pyproject.toml at the workspace root)"
