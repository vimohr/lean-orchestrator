"""One clean process per calculation; Docker denies calculation networking."""

import os
import re
import shutil
import sys
from pathlib import Path

from lean_orchestrator.procutil import run_captured

from .models import Policy
from .storage import safe_path


class Sandbox:
    def __init__(self, policy: Policy) -> None:
        self.policy = policy

    def preflight(self, work: Path) -> None:
        if self.policy.sandbox == "docker":
            if shutil.which("docker") is None:
                raise ValueError("Docker is unavailable; provision the calculation environment before research")
            result = run_captured(["docker", "image", "inspect", self.policy.docker_image], cwd=work, timeout=10)
            if result.returncode:
                raise ValueError("the configured Docker image is not ready; preflight never downloads it")
        modules = self.policy.preflight_imports
        if modules:
            if not all(isinstance(module, str) and re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", module) for module in modules):
                raise ValueError("preflight_imports must contain Python module names")
            script = work / "preflight.py"
            script.write_text("\n".join(f"import {module}" for module in modules))
            result = self.run(work, "preflight.py")
            if not result["ok"]:
                raise ValueError(f"calculation imports failed: {result.get('stderr', result.get('error', ''))}")

    def run(self, work: Path, script: str) -> dict:
        safe_path(work, script)
        if self.policy.sandbox == "docker":
            command = ["docker", "run", "--rm", "--pull", "never", "--network", "none", "--cap-drop", "ALL",
                       "--security-opt", "no-new-privileges", "--memory", "2g", "--cpus", "2",
                       "--pids-limit", "128", "--mount", f"type=bind,src={work.resolve()},dst=/work",
                       "--workdir", "/work", self.policy.docker_image, "python", "-B", script]
            environment = None
        else:
            command = [sys.executable, script]
            environment = {key: os.environ[key] for key in ("PATH", "LANG", "SYSTEMROOT") if key in os.environ}
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            result = run_captured(command, cwd=work, timeout=self.policy.tool_timeout_seconds, env=environment)
        except OSError as error:
            return {"ok": False, "execution_failed": True, "error": str(error)}
        return {"ok": result.returncode == 0, "execution_failed": result.returncode != 0,
                "exit_code": result.returncode, "timed_out": result.timed_out,
                "stdout": result.stdout, "stderr": result.stderr}

    def action(self, work: Path, action: dict) -> dict:
        kind = action.get("kind")
        if kind == "none":
            return {"ok": True, "note": "no tool action"}
        path = safe_path(work, action.get("path", ""))
        if kind == "read":
            return {"ok": True, "path": action["path"], "content": path.read_text()}
        if kind == "write":
            if "library" in Path(action["path"]).parts:
                raise ValueError("the inherited library is fixed for this attempt")
            text = action.get("text")
            if not isinstance(text, str) or len(text) > 200_000:
                raise ValueError("write action requires text of at most 200000 characters")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            return {"ok": True, "path": action["path"], "bytes": len(text.encode())}
        if kind == "python":
            if not path.is_file() or path.suffix != ".py":
                raise ValueError("python action requires a saved .py script")
            return self.run(work, action["path"])
        raise ValueError(f"unknown action {kind!r}")
