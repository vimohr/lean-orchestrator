"""Invoke an agent role and return its validated JSON output."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agents import AgentError, AgentRunner, AgentStopped, extract_json_block
from .events import Console, EventLog
from .integrity import IntegrityGuard, Violation
from .jsonio import dumps
from .paths import WorkspacePaths
from .prompts import PromptLibrary
from .schemas import SCHEMAS, schema_filename, semantic_errors, validate


class RoleOutputError(RuntimeError):
    """An agent did not produce a valid output file, even after repair."""

    def __init__(self, role: str, errors: list[str]) -> None:
        super().__init__(f"{role} output is invalid: " + "; ".join(errors[:5]))
        self.role = role
        self.errors = errors


@dataclass
class RoleResult:
    data: dict[str, Any]
    violations: list[Violation] = field(default_factory=list)
    run_dirs: list[Path] = field(default_factory=list)
    recovered_from: str | None = None


class RoleRunner:
    def __init__(self, paths: WorkspacePaths, agents: AgentRunner, prompts: PromptLibrary,
                 guard: IntegrityGuard, console: Console, events: EventLog, *, max_repairs: int = 1) -> None:
        self.paths = paths
        self.agents = agents
        self.prompts = prompts
        self.guard = guard
        self.console = console
        self.events = events
        self.max_repairs = max_repairs

    def schema_path(self, role: str) -> Path:
        return self.paths.schemas_dir / schema_filename(role)

    def call(
        self,
        role: str,
        *,
        tag: str,
        problem_id: str | None,
        output_path: Path,
        values: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> RoleResult:
        values = {
            **values,
            "role": role,
            "workspace": str(self.paths.root),
            "output_path": self.paths.relative(output_path),
            "schema_path": self.paths.relative(self.schema_path(role)),
        }
        prompt = self.prompts.render(role, **values)
        result = RoleResult(data={})
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.guard.allow_new(output_path)
        execution_error = None
        stdout = ""
        try:
            call = self.agents.run(role, prompt, tag=tag, problem_id=problem_id, output_path=output_path)
            result.run_dirs.append(call.run_dir)
            stdout = call.stdout
        except AgentStopped:
            raise
        except AgentError as error:
            if role != "researcher" or error.run_dir is None:
                raise
            execution_error = error
            if error.run_dir is not None:
                result.run_dirs.append(error.run_dir)
                log = error.run_dir / "stdout.log"
                stdout = log.read_text(encoding="utf-8") if log.is_file() else ""
        finally:
            result.violations.extend(self.guard.check_and_restore())
        data, errors = self._load(role, output_path, stdout, context)
        if execution_error is not None:
            # Salvage only an already complete, schema-valid submission. An invalid or
            # partial draft still belongs to a failed attempt; never ask a repair to invent it.
            if errors or data.get("outcome") not in ("success", "partial", "failure"):
                raise execution_error
            result.recovered_from = str(execution_error)
            self.events.emit("researcher_submission_recovered", problem=problem_id,
                             failure=str(execution_error), output=self.paths.relative(output_path))
            self.guard.write_json(output_path.with_name("recovery.json"), {
                "failure": str(execution_error), "run": str(execution_error.run_dir),
                "action": "recovered complete saved submission; verification and critic still required",
            })
        repairs = 0
        while errors and repairs < self.max_repairs:
            repairs += 1
            self.console.warn(f"{role} output invalid ({len(errors)} error(s)); asking for a repair", tag=tag)
            self.events.emit("role_output_invalid", role=role, problem=problem_id, errors=errors[:10])
            repair_prompt = self.prompts.render(
                "repair",
                **values,
                failed_role=role,
                errors="\n".join(f"- {error}" for error in errors[:30]),
                previous_output=self.paths.relative(result.run_dirs[-1] / "stdout.log"),
            )
            self.guard.allow_new(output_path)
            try:
                call = self.agents.run(role, repair_prompt, tag=f"{tag}:repair", problem_id=problem_id,
                                       output_path=output_path, clear_output=False)
                result.run_dirs.append(call.run_dir)
                stdout = call.stdout
            finally:
                result.violations.extend(self.guard.check_and_restore())
            data, errors = self._load(role, output_path, stdout, context)
        if errors:
            self.events.emit("role_output_rejected", role=role, problem=problem_id, errors=errors[:10])
            raise RoleOutputError(role, errors)
        self.guard.write_text(output_path, dumps(data))
        result.data = data
        return result

    def _load(self, role: str, output_path: Path, stdout: str,
              context: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
        data: Any = None
        if output_path.is_file():
            try:
                data = json.loads(output_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                return {}, [f"{self.paths.relative(output_path)} is not valid JSON: {error}"]
        else:
            data = extract_json_block(stdout)
            if data is None:
                return {}, [f"{self.paths.relative(output_path)} was not written"]
        if not isinstance(data, dict):
            return {}, ["the output must be a JSON object"]
        errors = validate(data, SCHEMAS[role])
        if not errors:
            errors = semantic_errors(role, data, context)
        return data, errors
