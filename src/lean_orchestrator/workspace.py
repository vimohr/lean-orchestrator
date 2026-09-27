"""Create a research workspace and its Lean and Python environments."""

from __future__ import annotations

import json
import os
import shutil
import time
import urllib.request
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .config import ROLES, AgentsConfig, Config, GitConfig
from .events import Console
from .gitops import GitRepo
from .jsonio import atomic_write_text, sha256_file, utc_now
from .paths import WorkspacePaths
from .portfolio import PortfolioStore
from .procutil import run_captured
from .prompts import TEMPLATE_NAMES, packaged_template
from .schemas import write_schema_files

# Physlib (with the QuantumInfo library) is the default: it already formalizes states,
# channels, partial traces, and entropies. Plain Mathlib remains available.
DEFAULT_LEAN_LIBRARY = "physlib"
PHYSLIB_GIT = "https://github.com/leanprover-community/physlib"
DEFAULT_PHYSLIB_REV = "44c66d54be78db4693be9f8f92bd3b5ad124ed6f"
DEFAULT_PHYSLIB_TOOLCHAIN = "leanprover/lean4:v4.34.1"
DEFAULT_MATHLIB_REV = "v4.35.0-rc3"
DEFAULT_MATHLIB_TOOLCHAIN = "leanprover/lean4:v4.35.0-rc3"
LEAN_LIBRARIES = ("physlib", "mathlib")


class WorkspaceError(RuntimeError):
    """The workspace cannot be created or set up."""


@dataclass(frozen=True)
class InitOptions:
    lean_setup: bool = True
    python_env: bool = True
    git: bool = True
    lean_library: str = DEFAULT_LEAN_LIBRARY
    physlib_rev: str = DEFAULT_PHYSLIB_REV
    mathlib_rev: str = DEFAULT_MATHLIB_REV
    toolchain: str | None = None


def _template(*parts: str) -> str:
    return resources.files("lean_orchestrator").joinpath("templates", *parts).read_text(encoding="utf-8")


def lean_requires(options: InitOptions) -> str:
    if options.lean_library not in LEAN_LIBRARIES:
        raise WorkspaceError(f"unknown Lean library {options.lean_library!r}; choose one of {', '.join(LEAN_LIBRARIES)}")
    if options.lean_library == "physlib":
        return (
            "[[require]]\n"
            'name = "Physlib"\n'
            f'git = "{PHYSLIB_GIT}"\n'
            f'rev = "{options.physlib_rev}"\n'
        )
    return (
        "[[require]]\n"
        'name = "mathlib"\n'
        'scope = "leanprover-community"\n'
        f'rev = "{options.mathlib_rev}"\n'
    )


def lean_toolchain(options: InitOptions) -> str:
    """The toolchain must equal the one the chosen library was built with."""
    if options.toolchain:
        return options.toolchain
    if options.lean_library == "physlib":
        if options.physlib_rev == DEFAULT_PHYSLIB_REV:
            return DEFAULT_PHYSLIB_TOOLCHAIN
        return physlib_toolchain(options.physlib_rev)
    return DEFAULT_MATHLIB_TOOLCHAIN


def physlib_toolchain(revision: str, timeout: float = 30.0) -> str:
    """Physlib pins its own Lean version; a dependent project must use the same one."""
    url = f"https://raw.githubusercontent.com/leanprover-community/physlib/{revision}/lean-toolchain"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read().decode().strip()
    except OSError as error:
        raise WorkspaceError(f"could not read Physlib's lean-toolchain at {revision}: {error}") from error


def write_mcp_config(paths: WorkspacePaths, config: Config) -> None:
    """MCP servers that agent commands can load with --mcp-config {mcp_config}."""
    servers: dict[str, dict] = {}
    if config.mcp.qiqcop_url:
        servers["quantum-open-problems"] = {"type": "http", "url": config.mcp.qiqcop_url}
    if config.mcp.lean_lsp:
        servers["lean-lsp"] = {
            "type": "stdio",
            "command": "uvx",
            "args": ["lean-lsp-mcp", "--lean-project-path", str(paths.root / config.verification.lean.project_dir)],
            "env": {"LEAN_LOG_LEVEL": "NONE", "LEAN_MCP_DISABLED_TOOLS": "lean_build"},
        }
    atomic_write_text(paths.mcp_config, json.dumps({"mcpServers": servers}, indent=2) + "\n")


def _claude_command(effort: str) -> tuple[str, ...]:
    return ("claude", "-p", "--model", "claude-opus-5-5", "--effort", effort)


def default_agent_commands(available: set[str]) -> tuple[dict[str, tuple[str, ...]], str]:
    """Choose per-role commands from the installed CLIs; return them with a note for the file."""
    codex = AgentsConfig().researcher.command
    has_codex, has_claude = "codex" in available, "claude" in available
    if has_claude and not has_codex:
        commands = {role: _claude_command("high" if role == "triage" else "max") for role in ROLES}
        note = "# Generated for this machine: only the claude CLI was found, so Claude plays every role.\n"
    elif has_codex and not has_claude:
        commands = {role: codex for role in ROLES}
        note = "# Generated for this machine: only the codex CLI was found, so Codex plays every role.\n"
    else:
        commands = {role: codex if role == "researcher" else _claude_command("high" if role == "triage" else "max")
                    for role in ROLES}
        note = ("# Generated for this machine: Codex researches, Claude criticizes, supervises, and reads.\n"
                if has_codex else "# Neither the claude nor the codex CLI was found; install one and adjust below.\n")
    return commands, note


def render_agents_config(available: set[str]) -> str:
    commands, note = default_agent_commands(available)
    text = _template("workspace", "agents.toml.in").replace("{detection_note}", note)
    for role in ROLES:
        text = text.replace("{" + role + "}", json.dumps(list(commands[role])))
    return text


def write_agents_config(paths: WorkspacePaths) -> Path:
    """Create ``agents.toml`` from the CLIs installed on this machine (never overwrites)."""
    if not paths.agents_config.exists():
        available = {name for name in ("claude", "codex") if shutil.which(name)}
        atomic_write_text(paths.agents_config, render_agents_config(available))
    return paths.agents_config


def agents_confirmed(paths: WorkspacePaths) -> bool:
    """Whether a human confirmed ``agents.toml`` as it is now; any later edit needs a new confirmation."""
    try:
        record = json.loads(paths.agents_confirmed.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return (isinstance(record, dict) and paths.agents_config.is_file()
            and record.get("agents_toml_sha256") == sha256_file(paths.agents_config))


def confirm_agents(paths: WorkspacePaths) -> None:
    """Record that a human reviewed ``agents.toml``; later runs do not ask again."""
    atomic_write_text(paths.agents_confirmed,
                      json.dumps({"time": utc_now(), "agents_toml_sha256": sha256_file(paths.agents_config)}) + "\n")


def init_workspace(target: Path, options: InitOptions, console: Console) -> WorkspacePaths:
    paths = WorkspacePaths(target.resolve())
    if paths.config.exists():
        raise WorkspaceError(f"{paths.root} is already a lean-orch workspace")
    paths.root.mkdir(parents=True, exist_ok=True)
    for directory in (paths.prompts_dir, paths.schemas_dir, paths.catalogue_dir, paths.problems_dir,
                      paths.knowledge_dir, paths.internal_dir, paths.lean_problems_dir,
                      paths.lean_dir / "OpenQ" / "Foundations"):
        directory.mkdir(parents=True, exist_ok=True)

    atomic_write_text(paths.config, _template("workspace", "lean-orch.toml"))
    write_agents_config(paths)
    atomic_write_text(paths.readme, _template("workspace", "README.md"))
    atomic_write_text(paths.root / ".gitignore", _template("workspace", "gitignore.txt"))
    atomic_write_text(paths.root / "pyproject.toml", _template("workspace", "pyproject.toml"))
    for name in TEMPLATE_NAMES:
        atomic_write_text(paths.prompts_dir / f"{name}.md", packaged_template(name))
    write_schema_files(paths.schemas_dir)
    (paths.problems_dir / ".gitkeep").touch()
    (paths.lean_problems_dir / ".gitkeep").touch()

    atomic_write_text(paths.lean_dir / "lean-toolchain", lean_toolchain(options) + "\n")
    atomic_write_text(paths.lean_dir / "lakefile.toml",
                      _template("lean", "lakefile.toml.in").replace("{requires}", lean_requires(options).rstrip()))
    atomic_write_text(paths.lean_dir / "OpenQ.lean", _template("lean", "OpenQ.lean"))
    atomic_write_text(paths.lean_dir / "OpenQ" / "Foundations" / "Basic.lean",
                      _template("lean", "OpenQ", "Foundations", "Basic.lean"))
    PortfolioStore(paths).save()
    write_mcp_config(paths, Config())
    console.info(f"created workspace files in {paths.root}")

    repo = GitRepo(paths.root, GitConfig(enabled=options.git))
    repo.ensure()
    repo.commit_all("chore: initialize lean-orch workspace")
    if options.lean_setup:
        setup_lean(paths, console, physlib=options.lean_library == "physlib")
    if options.python_env:
        setup_python(paths, console)
    # lake-manifest.json and uv.lock pin the exact dependency versions of this workspace.
    repo.commit_all("chore: pin lean and python dependencies")
    return paths


def refresh_prompts(paths: WorkspacePaths) -> list[str]:
    """Replace the workspace prompts with the packaged ones; back up any that differ."""
    backup = paths.prompts_dir / f".backup-{time.strftime('%Y%m%d-%H%M%S')}"
    changed = []
    for name in TEMPLATE_NAMES:
        target = paths.prompts_dir / f"{name}.md"
        fresh = packaged_template(name)
        if target.is_file() and target.read_text(encoding="utf-8") == fresh:
            continue
        if target.is_file():
            backup.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup / target.name)
        atomic_write_text(target, fresh)
        changed.append(name)
    return changed


def _lake() -> str | None:
    found = shutil.which("lake")
    if found:
        return found
    candidate = Path.home() / ".elan" / "bin" / "lake"
    return str(candidate) if candidate.is_file() else None


def setup_lean(paths: WorkspacePaths, console: Console, *, physlib: bool) -> None:
    """Fetch the Lean libraries and Mathlib's prebuilt cache, then build what agents import."""
    lake = _lake()
    if lake is None:
        console.warn("Lean is not installed: install elan (https://github.com/leanprover/elan), then run "
                     f"'lake update && lake exe cache get' in {paths.lean_dir}")
        return
    environment = dict(os.environ)
    environment["PATH"] = str(Path(lake).parent) + os.pathsep + environment.get("PATH", "")
    steps = [
        (["update"], "fetching Lean dependencies (several GB on first use)"),
        (["exe", "cache", "get"], "downloading the Mathlib build cache"),
    ]
    if physlib:
        steps.append((["build", "QuantumInfo"], "building Physlib's QuantumInfo library (a few minutes)"))
    steps.append((["build", "OpenQ.Foundations.Basic"], "building the shared foundations"))
    for arguments, description in steps:
        console.info(f"lean: {description} ...")
        completed = run_captured([lake, *arguments], cwd=paths.lean_dir, timeout=4 * 3600, env=environment)
        if completed.returncode != 0:
            console.warn(f"lean: 'lake {' '.join(arguments)}' failed; fix it and rerun it in {paths.lean_dir}.\n"
                         + completed.output.strip()[-2000:])
            return
    console.info("lean: ready")


def setup_python(paths: WorkspacePaths, console: Console) -> None:
    uv = shutil.which("uv")
    if uv is None:
        console.warn("uv is not installed; create the experiment environment from pyproject.toml yourself")
        return
    console.info("python: creating the experiment environment with uv ...")
    completed = run_captured([uv, "sync", "--quiet"], cwd=paths.root, timeout=1800)
    if completed.returncode != 0:
        console.warn("python: 'uv sync' failed:\n" + completed.output.strip()[-2000:])
        return
    console.info("python: ready")
