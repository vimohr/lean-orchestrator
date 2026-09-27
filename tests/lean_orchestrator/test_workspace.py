from __future__ import annotations

import json
import subprocess
import tomllib

import pytest
from support import quiet_console

from lean_orchestrator import workspace as workspace_module
from lean_orchestrator.config import AgentsConfig, Config, load_config, parse_agents, parse_config
from lean_orchestrator.paths import WorkspacePaths
from lean_orchestrator.workspace import (
    InitOptions, WorkspaceError, init_workspace, lean_requires, refresh_prompts, render_agents_config,
    write_mcp_config,
)


def test_initialized_workspace_loads_with_default_configuration(tmp_path):
    paths = init_workspace(tmp_path / "w", InitOptions(lean_setup=False, python_env=False, git=False), quiet_console())
    assert load_config(paths.root) == Config()
    assert not (paths.root / ".git").exists()
    assert (paths.lean_dir / "lean-toolchain").read_text().strip() == "leanprover/lean4:v4.34.1"
    lakefile = (paths.lean_dir / "lakefile.toml").read_text()
    assert 'name = "Physlib"' in lakefile and 'rev = "44c66d54be78db4693be9f8f92bd3b5ad124ed6f"' in lakefile
    gitignore = (paths.root / ".gitignore").read_text()
    assert ".lean-orch/runs/" in gitignore and "lean/.lake/" in gitignore
    with pytest.raises(WorkspaceError, match="already"):
        init_workspace(paths.root, InitOptions(lean_setup=False, python_env=False, git=False), quiet_console())


def test_lean_library_choice_sets_requirements_and_toolchain(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_module, "physlib_toolchain", lambda revision: "leanprover/lean4:v4.36.0")
    options = InitOptions(lean_setup=False, python_env=False, git=False, physlib_rev="abc1234")
    paths = init_workspace(tmp_path / "w", options, quiet_console())
    lakefile = (paths.lean_dir / "lakefile.toml").read_text()
    assert 'name = "Physlib"' in lakefile and 'rev = "abc1234"' in lakefile and "mathlib" not in lakefile
    assert (paths.lean_dir / "lean-toolchain").read_text().strip() == "leanprover/lean4:v4.36.0"
    mathlib = InitOptions(lean_setup=False, python_env=False, git=False, lean_library="mathlib")
    paths = init_workspace(tmp_path / "m", mathlib, quiet_console())
    assert 'scope = "leanprover-community"' in (paths.lean_dir / "lakefile.toml").read_text()
    assert (paths.lean_dir / "lean-toolchain").read_text().strip() == "leanprover/lean4:v4.35.0-rc3"
    with pytest.raises(WorkspaceError, match="unknown Lean library"):
        lean_requires(InitOptions(lean_library="coq"))


def test_mcp_configuration(tmp_path):
    paths = WorkspacePaths(tmp_path)
    paths.internal_dir.mkdir()
    write_mcp_config(paths, parse_config({"mcp": {"lean_lsp": True}}))
    servers = json.loads(paths.mcp_config.read_text())["mcpServers"]
    assert servers["quantum-open-problems"] == {"type": "http", "url": "https://api.qiqc-op.com/mcp"}
    assert servers["lean-lsp"]["args"][-1] == str(tmp_path / "lean")
    assert servers["lean-lsp"]["env"]["LEAN_MCP_DISABLED_TOOLS"] == "lean_build"


def test_refresh_prompts_backs_up_edited_templates(tmp_path):
    paths = init_workspace(tmp_path / "w", InitOptions(lean_setup=False, python_env=False, git=False), quiet_console())
    assert refresh_prompts(paths) == []
    (paths.prompts_dir / "critic.md").write_text("my custom critic")
    assert refresh_prompts(paths) == ["critic"]
    assert "SKEPTIC" in (paths.prompts_dir / "critic.md").read_text()
    backups = list(paths.prompts_dir.glob(".backup-*/critic.md"))
    assert len(backups) == 1 and backups[0].read_text() == "my custom critic"


def test_agents_toml_is_generated_for_the_installed_clis():
    both = parse_agents(tomllib.loads(render_agents_config({"claude", "codex"})))
    assert both == AgentsConfig()
    assert both.researcher.command[:3] == ("codex", "--search", "exec")
    assert "--sandbox" in both.researcher.command and both.critic.command[0] == "claude"
    only_claude = parse_agents(tomllib.loads(render_agents_config({"claude"})))
    assert {only_claude.for_role(role).command[0] for role in ("researcher", "critic", "literature")} == {"claude"}
    only_codex = parse_agents(tomllib.loads(render_agents_config({"codex"})))
    assert only_codex.literature.command == AgentsConfig().researcher.command
    assert "Neither the claude nor the codex CLI was found" in render_agents_config(set())


def test_dependency_pins_are_committed_after_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_module, "setup_python",
                        lambda paths, console: (paths.root / "uv.lock").write_text("version = 1\n"))
    paths = init_workspace(tmp_path / "w", InitOptions(lean_setup=False, python_env=True, git=True), quiet_console())
    log = subprocess.run(["git", "log", "--format=%s"], cwd=paths.root, capture_output=True, text=True).stdout.split("\n")
    assert log[:2] == ["chore: pin lean and python dependencies", "chore: initialize lean-orch workspace"]
    tracked = subprocess.run(["git", "ls-files"], cwd=paths.root, capture_output=True, text=True).stdout
    assert "uv.lock" in tracked and "agents.toml" in tracked
