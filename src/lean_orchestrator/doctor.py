"""Check that a workstation can run the research loop."""

from __future__ import annotations

import platform
import shutil
from dataclasses import dataclass
from pathlib import Path

from .catalogue.qiqcop import CatalogueFetchError, fetch_release
from .config import ROLES, Config, expand_placeholders
from .paths import WorkspacePaths
from .procutil import run_captured
from .verify.lean import LeanVerifier

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _version(executable: str, cwd: Path | None = None) -> tuple[bool, str]:
    completed = run_captured([executable, "--version"], cwd=cwd or Path.cwd(), timeout=60)
    lines = completed.output.strip().splitlines()
    return completed.returncode == 0, lines[0] if lines else f"exit code {completed.returncode}"


def check_agents(paths: WorkspacePaths, config: Config) -> list[Check]:
    checks = []
    versions: dict[str, str] = {}
    for role in ROLES:
        command = expand_placeholders(config.agents.for_role(role).command,
                                      {"workspace": str(paths.root), "mcp_config": str(paths.mcp_config)})
        executable = shutil.which(command[0])
        if executable is None:
            checks.append(Check(f"agent {role}", FAIL, f"{command[0]} is not installed or not on PATH"))
            continue
        if executable not in versions:
            versions[executable] = _version(executable)[1]
        checks.append(Check(f"agent {role}", OK, f"{command[0]} ({versions[executable]})"))
    if paths.agents_config.is_file():
        confirmed = paths.agents_confirmed.is_file()
        checks.append(Check("agents.toml", OK if confirmed else WARN,
                            "reviewed" if confirmed else "not yet confirmed; 'lean-orch run' will ask"))
    else:
        checks.append(Check("agents.toml", WARN, "missing; 'lean-orch run' will create it from the installed CLIs"))
    uses_codex_sandbox = any("--sandbox" in config.agents.for_role(role).command for role in ROLES)
    if uses_codex_sandbox and platform.system() == "Linux":
        lsm = Path("/sys/kernel/security/lsm")
        try:
            active = lsm.read_text().strip().split(",")
        except OSError:
            checks.append(Check("codex sandbox", WARN, "cannot read the active Linux security modules"))
        else:
            if "landlock" in active:
                checks.append(Check("codex sandbox", OK, "Landlock is active"))
            else:
                checks.append(Check("codex sandbox", FAIL, "Landlock is not active; codex --sandbox needs it "
                                                            "(kernel 5.13 or later with lsm=landlock)"))
    return checks


def check_lean(paths: WorkspacePaths, config: Config) -> list[Check]:
    verifier = LeanVerifier(paths, config.verification.lean)
    project = verifier.project
    lake = verifier.lake_command()
    if not config.verification.lean.enabled:
        return [Check("lean", WARN, "Lean verification is disabled in lean-orch.toml")]
    if lake is None:
        return [Check("lean", FAIL, "lake not found; install elan (https://github.com/leanprover/elan)")]
    if not (project / "lakefile.toml").is_file():
        return [Check("lean project", FAIL, f"no lakefile.toml in {project}")]
    # Run inside the project so that elan selects the toolchain from lean-toolchain.
    works, version = _version(lake, cwd=project)
    checks = [Check("lake", OK if works else FAIL, version if works else f"lake does not start: {version}")]
    toolchain = (project / "lean-toolchain").read_text().strip() if (project / "lean-toolchain").is_file() else "?"
    packages = project / ".lake" / "packages"
    if not packages.is_dir() or not any(packages.iterdir()):
        checks.append(Check("lean dependencies", FAIL, f"not fetched; run 'lake update && lake exe cache get' in {project}"))
        return checks
    names = sorted(path.name for path in packages.iterdir())
    checks.append(Check("lean dependencies", OK, f"{toolchain}; {', '.join(names)}"))
    foundations = project / ".lake" / "build" / "lib" / "lean" / "OpenQ" / "Foundations" / "Basic.olean"
    checks.append(Check("OpenQ.Foundations", OK if foundations.is_file() else WARN,
                        "built" if foundations.is_file() else "not built yet (built on first use)"))
    if config.verification.lean.comparator == "main":
        available = verifier.comparator_available()
        sandbox = shutil.which("bwrap") is not None
        if available:
            checks.append(Check("lake comparator", OK if sandbox else WARN,
                                "available" + ("" if sandbox else "; runs unsandboxed because bubblewrap is missing")))
        else:
            checks.append(Check("lake comparator", WARN,
                                "not available for this toolchain; main results fall back to a leanchecker replay"))
    return checks


def check_environment(paths: WorkspacePaths, config: Config) -> list[Check]:
    checks = []
    python = expand_placeholders(config.verification.experiments.python, {"workspace": str(paths.root)})
    executable = python[0] if Path(python[0]).is_absolute() else shutil.which(python[0])
    if executable and Path(executable).exists():
        probe = run_captured([*python, "-c", "import numpy, scipy, sympy; print('ok')"], cwd=paths.root, timeout=120)
        checks.append(Check("experiment python", OK if probe.returncode == 0 else WARN,
                            executable if probe.returncode == 0 else "numpy, scipy, or sympy missing; run 'uv sync'"))
    else:
        checks.append(Check("experiment python", FAIL, f"{python[0]} not found; run 'uv sync' in the workspace"))
    git = run_captured(["git", "rev-parse", "--is-inside-work-tree"], cwd=paths.root, timeout=30)
    checks.append(Check("git", OK if git.returncode == 0 else WARN,
                        "workspace is a repository" if git.returncode == 0 else "not a git repository"))
    if config.catalogue.qiqcop.enabled:
        try:
            release = fetch_release(config.catalogue.qiqcop.release_url, timeout=20)
        except CatalogueFetchError as error:
            checks.append(Check("QIQCOP Zoo", WARN, f"unreachable: {error}"))
        else:
            counts = release.get("counts", {})
            checks.append(Check("QIQCOP Zoo", OK, f"reachable; {counts.get('unsolved', '?')} unsolved of "
                                                  f"{counts.get('total', '?')} records ({release.get('updated', '?')})"))
    return checks


def run_checks(paths: WorkspacePaths, config: Config) -> list[Check]:
    return [*check_agents(paths, config), *check_lean(paths, config), *check_environment(paths, config)]
