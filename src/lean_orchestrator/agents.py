"""Launch disposable CLI agents with logging, heartbeats, timeouts, and retries."""

from __future__ import annotations

import json
import os
import queue
import re
import secrets
import shlex
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from .config import AgentConfig, Config, expand_placeholders
from .events import Console, EventLog
from .jsonio import utc_now, write_json
from .paths import WorkspacePaths

_CAPACITY_PATTERNS = (
    "model is at capacity", "overloaded", "rate limit", "rate_limit", "too many requests",
    "server is busy", "temporarily unavailable", "503 service unavailable", "529",
)
_USAGE_LIMIT_PATTERNS = (
    "usage limit", "hit your limit", "limit reached", "insufficient_quota", "quota exceeded",
    "credit balance is too low",
)


class AgentError(RuntimeError):
    """An agent process failed."""

    def __init__(self, message: str, run_dir: Path | None = None) -> None:
        super().__init__(message)
        self.run_dir = run_dir


class AgentTransientError(AgentError):
    """The model was temporarily at capacity or rate limited."""


class AgentUsageLimitError(AgentError):
    """The account's usage limit was reached; waiting may help."""


class AgentTimeoutError(AgentError):
    """The agent exceeded its time limit."""


class AgentStopped(AgentError):
    """The orchestrator is shutting down."""


@dataclass
class AgentResult:
    role: str
    run_dir: Path
    exit_code: int
    duration_seconds: float
    stdout: str


def _elapsed(seconds: float) -> str:
    minutes, remainder = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {remainder}s"
    if minutes:
        return f"{minutes}m {remainder}s"
    return f"{remainder}s"


def _stop_process(process: subprocess.Popen) -> None:
    """Terminate the agent and every process it started."""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (AttributeError, ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (AttributeError, ProcessLookupError, PermissionError):
            process.kill()
        process.wait(timeout=10)


def classify_failure(text: str) -> type[AgentError]:
    lowered = text.lower()
    if any(pattern in lowered for pattern in _USAGE_LIMIT_PATTERNS):
        return AgentUsageLimitError
    if any(pattern in lowered for pattern in _CAPACITY_PATTERNS):
        return AgentTransientError
    return AgentError


class AgentRunner:
    """Run agent roles as subprocesses in the workspace."""

    def __init__(
        self,
        paths: WorkspacePaths,
        config: Config,
        console: Console,
        events: EventLog,
        stop_event: threading.Event | None = None,
        *,
        stream_output: bool = False,
    ) -> None:
        self.paths = paths
        self.config = config
        self.console = console
        self.events = events
        self.stop_event = stop_event or threading.Event()
        self.stream_output = stream_output
        self.calls = 0
        self._calls_lock = threading.Lock()

    def run(
        self,
        role: str,
        prompt: str,
        *,
        tag: str,
        problem_id: str | None = None,
        output_path: Path | None = None,
        agent: AgentConfig | None = None,
        clear_output: bool = True,
    ) -> AgentResult:
        """Run ``role`` with ``prompt``, retrying capacity and usage-limit failures.

        The output file is deleted before each attempt unless ``clear_output`` is false
        (a repair run must see the file it is asked to fix).
        """
        agent = agent or self.config.agents.for_role(role)
        delays = list(self.config.agents.retry_delays_seconds)
        usage_waited = 0.0
        usage_wait = self.config.agents.usage_limit_wait_minutes * 60
        usage_budget = self.config.agents.usage_limit_max_hours * 3600
        attempt = 0
        while True:
            attempt += 1
            if output_path is not None and clear_output:
                output_path.unlink(missing_ok=True)
            try:
                return self._run_once(role, prompt, agent, tag=tag, problem_id=problem_id, output_path=output_path)
            except AgentTransientError as error:
                if not delays:
                    raise AgentError(f"{role} stayed at capacity after {attempt} attempts: {error}", error.run_dir) from error
                delay = delays.pop(0)
                self.console.warn(f"model at capacity; retrying in {delay:g}s (attempt {attempt + 1})", tag=tag)
                self._sleep(delay)
            except AgentUsageLimitError as error:
                if usage_waited >= usage_budget:
                    raise AgentError(f"{role} usage limit persisted for {_elapsed(usage_waited)}: {error}", error.run_dir) from error
                self.console.warn(f"usage limit reached; waiting {_elapsed(usage_wait)} before retrying", tag=tag)
                self.events.emit("agent_usage_limit", role=role, problem=problem_id, wait_seconds=usage_wait)
                self._sleep(usage_wait)
                usage_waited += usage_wait

    def _sleep(self, seconds: float) -> None:
        if self.stop_event.wait(seconds):
            raise AgentStopped("stop requested while waiting to retry")

    def _new_run_dir(self, role: str, problem_id: str | None) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        label = re.sub(r"[^a-zA-Z0-9_.-]", "-", problem_id or "global")[:40]
        run_dir = self.paths.runs_dir / f"{stamp}-{label}-{role}-{secrets.token_hex(3)}"
        run_dir.mkdir(parents=True, exist_ok=False)
        return run_dir

    def _run_once(
        self,
        role: str,
        prompt: str,
        agent: AgentConfig,
        *,
        tag: str,
        problem_id: str | None,
        output_path: Path | None,
    ) -> AgentResult:
        if self.stop_event.is_set():
            raise AgentStopped("stop requested before the agent started")
        run_dir = self._new_run_dir(role, problem_id)
        placeholders = {
            "workspace": str(self.paths.root),
            "run_dir": str(run_dir),
            "role": role,
            "problem": problem_id or "",
            "output": str(output_path or ""),
            "mcp_config": str(self.paths.mcp_config),
        }
        command = expand_placeholders(agent.command, placeholders)
        if agent.prompt_via == "argument":
            command.append(prompt)
        (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        timeout = agent.timeout_minutes * 60 or self.config.loop.agent_timeout_minutes * 60 or None
        environment = {
            **os.environ,
            "LEAN_ORCH_WORKSPACE": str(self.paths.root),
            "LEAN_ORCH_ROLE": role,
            "LEAN_ORCH_PROBLEM": problem_id or "",
            "LEAN_ORCH_OUTPUT": str(output_path or ""),
            "LEAN_ORCH_RUN_DIR": str(run_dir),
        }
        display = shlex.join(command[:-1]) + " <prompt>" if agent.prompt_via == "argument" else shlex.join(command)
        meta = {
            "role": role, "problem": problem_id, "command": display, "started": utc_now(),
            "output_path": str(output_path) if output_path else None, "timeout_seconds": timeout,
        }
        write_json(run_dir / "meta.json", meta)

        started = time.monotonic()
        try:
            process = subprocess.Popen(
                command,
                cwd=self.paths.root,
                env=environment,
                text=True,
                stdin=subprocess.PIPE if agent.prompt_via == "stdin" else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                errors="replace",
                start_new_session=True,
            )
        except FileNotFoundError as error:
            raise AgentError(f"agent command not found for {role}: {command[0]}", run_dir) from error
        with self._calls_lock:
            self.calls += 1
        self.console.info(f"{role} started (PID {process.pid}); run log {self.paths.relative(run_dir)}", tag=tag)

        if agent.prompt_via == "stdin":
            assert process.stdin is not None
            try:
                process.stdin.write(prompt)
                process.stdin.close()
            except BrokenPipeError:
                pass

        lines: queue.Queue[tuple[str, str | None]] = queue.Queue()

        def pump(name: str, stream: TextIO) -> None:
            try:
                for line in stream:
                    lines.put((name, line))
            finally:
                lines.put((name, None))

        assert process.stdout is not None and process.stderr is not None
        readers = [
            threading.Thread(target=pump, args=("stdout", process.stdout), daemon=True),
            threading.Thread(target=pump, args=("stderr", process.stderr), daemon=True),
        ]
        for reader in readers:
            reader.start()

        stdout_parts: list[str] = []
        recent: deque[str] = deque(maxlen=40)
        heartbeat = self.config.loop.heartbeat_seconds
        next_heartbeat = started + heartbeat
        finished_streams = 0
        timed_out = stopped = False
        with (run_dir / "stdout.log").open("w", encoding="utf-8") as out_log, \
                (run_dir / "stderr.log").open("w", encoding="utf-8") as err_log:
            try:
                while finished_streams < len(readers):
                    now = time.monotonic()
                    if timeout and now - started >= timeout and not timed_out and process.poll() is None:
                        timed_out = True
                        self.console.warn(f"{role} timed out after {_elapsed(now - started)}; stopping", tag=tag)
                        _stop_process(process)
                    if self.stop_event.is_set() and not stopped and process.poll() is None:
                        stopped = True
                        self.console.warn(f"stopping {role} (PID {process.pid})", tag=tag)
                        _stop_process(process)
                    if heartbeat and now >= next_heartbeat and process.poll() is None:
                        self.console.info(f"{role} still running ({_elapsed(now - started)})", tag=tag)
                        next_heartbeat = now + heartbeat
                    try:
                        name, line = lines.get(timeout=0.25)
                    except queue.Empty:
                        continue
                    if line is None:
                        finished_streams += 1
                        continue
                    (out_log if name == "stdout" else err_log).write(line)
                    if name == "stdout":
                        stdout_parts.append(line)
                    if line.strip():
                        recent.append(line.strip()[-400:])
                    if self.stream_output:
                        self.console.info(line.rstrip("\n"), tag=f"{tag}:{name}")
            except KeyboardInterrupt:
                _stop_process(process)
                raise
            returncode = process.wait()

        duration = time.monotonic() - started
        meta.update({"ended": utc_now(), "exit_code": returncode, "duration_seconds": round(duration, 1),
                     "timed_out": timed_out, "stopped": stopped})
        write_json(run_dir / "meta.json", meta)
        self.events.emit("agent_call", role=role, problem=problem_id, run=self.paths.relative(run_dir),
                         exit_code=returncode, seconds=round(duration, 1), timed_out=timed_out)

        if stopped:
            raise AgentStopped(f"{role} stopped by request", run_dir)
        if timed_out:
            raise AgentTimeoutError(f"{role} exceeded its {timeout:g}s time limit", run_dir)
        if returncode != 0:
            tail = "\n".join(recent)
            error_type = classify_failure(tail)
            excerpt = "\n  ".join(list(recent)[-6:])
            self.console.warn(f"{role} failed with exit code {returncode}:\n  {excerpt}", tag=tag)
            raise error_type(f"{role} exited with code {returncode}; see {run_dir}", run_dir)
        self.console.info(f"{role} finished in {_elapsed(duration)}", tag=tag)
        return AgentResult(role=role, run_dir=run_dir, exit_code=returncode,
                           duration_seconds=duration, stdout="".join(stdout_parts))


def extract_json_block(text: str) -> dict | None:
    """Recover a JSON object from an agent's final message when no file was written."""
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    candidates = list(reversed(fenced))
    last_open = text.rfind("\n{")
    if last_open != -1:
        candidates.append(text[last_open + 1:])
    stripped = text.strip()
    if stripped.startswith("{"):
        candidates.append(stripped)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None
