"""Fresh CLI invocations or single Responses requests; no transcript replay."""

import json
import os
import signal
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from lean_orchestrator.agents import extract_json_block
from lean_orchestrator.usage import TokenUsage, parse_usage

from .config import BackendConfig


@dataclass
class Completion:
    data: dict | None = None
    usage: TokenUsage | None = None
    stdout: str = ""
    stderr: str = ""
    error: str = ""


class Backend:
    def __init__(self, config: BackendConfig) -> None:
        self.config = config

    def preflight(self) -> None:
        if self.config.kind == "responses":
            if not os.environ.get("OPENAI_API_KEY"):
                raise ValueError("OPENAI_API_KEY is required for the Responses backend")
        elif shutil.which(self.config.command[0]) is None:
            raise ValueError(f"agent command is unavailable: {self.config.command[0]}")

    def complete(self, request: dict, work: Path, max_output_tokens: int) -> Completion:
        prompt = json.dumps(request, ensure_ascii=False)
        if self.config.kind == "responses":
            return self._responses(prompt, max_output_tokens)
        process = None
        try:
            process = subprocess.Popen(self.config.command, cwd=work, env=os.environ.copy(),
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, errors="replace", start_new_session=True)
            out, err = process.communicate(prompt, timeout=self.config.timeout_seconds)
            return Completion(extract_json_block(out), parse_usage(out), out, err,
                              f"CLI exited {process.returncode}" if process.returncode else "")
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            out, err = process.communicate()
            return Completion(None, parse_usage(out), out, err, "CLI timed out")
        except OSError as error:
            return Completion(error=str(error))
        except BaseException:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise

    def _responses(self, prompt: str, max_output_tokens: int) -> Completion:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            return Completion(error="OPENAI_API_KEY is required for the Responses backend")
        payload = {"model": self.config.model, "input": prompt, "store": False,
                   "max_output_tokens": max_output_tokens,
                   "reasoning": {"effort": self.config.reasoning_effort}}
        request = urllib.request.Request("https://api.openai.com/v1/responses", data=json.dumps(payload).encode(),
                                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                body = json.load(response)
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            return Completion(error=f"Responses request failed: {error}")
        text = "\n".join(content.get("text", "") for item in body.get("output", [])
                         for content in item.get("content", []) if content.get("type") == "output_text")
        usage = body.get("usage")
        if isinstance(usage, dict):
            usage = {**usage, "cached_input_tokens": usage.get("input_tokens_details", {}).get("cached_tokens", 0),
                     "reasoning_output_tokens": usage.get("output_tokens_details", {}).get("reasoning_tokens", 0)}
        returned = parse_usage(json.dumps({"type": "usage", "usage": usage}))
        error = "" if body.get("status") == "completed" else f"Responses status: {body.get('status', 'unknown')}"
        return Completion(extract_json_block(text), returned, json.dumps(body), "", error)
