from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from lean_orchestrator import cli
from lean_orchestrator.cli import build_parser, main
from lean_orchestrator.paths import WorkspacePaths
from lean_orchestrator.portfolio import PortfolioStore


@pytest.fixture
def cli_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "research"
    assert main(["init", str(root), "--no-lean-setup", "--no-python-env"]) == 0
    return root


def test_init_creates_a_complete_workspace(cli_workspace: Path):
    for name in ("lean-orch.toml", "agents.toml", "README.md", ".gitignore", "pyproject.toml", "prompts/critic.md",
                 "schemas/researcher.schema.json", "lean/lakefile.toml", "lean/lean-toolchain", "lean/OpenQ.lean",
                 "lean/OpenQ/Foundations/Basic.lean", ".lean-orch/portfolio.json", ".lean-orch/mcp.json"):
        assert (cli_workspace / name).is_file(), name
    assert (cli_workspace / ".git").is_dir()
    assert 'name = "Physlib"' in (cli_workspace / "lean" / "lakefile.toml").read_text()
    assert main(["init", str(cli_workspace), "--no-lean-setup", "--no-python-env"]) == 2


def test_add_status_show_and_commands(cli_workspace: Path, capsys):
    workspace = ["-w", str(cli_workspace)]
    assert main([*workspace, "add", "--title", "PPT squared for d = 4",
                 "--statement", "Is $\\Phi\\circ\\Psi$ entanglement breaking?", "--candidate"]) == 0
    store = PortfolioStore(WorkspacePaths(cli_workspace))
    assert store.get("local_ppt_squared_for_d_4").status == "candidate"
    capsys.readouterr()
    assert main([*workspace, "status"]) == 0
    output = capsys.readouterr().out
    assert "local_ppt_squared_for_d_4" in output and "candidate" in output
    assert main([*workspace, "status", "--json"]) == 0
    assert "local_ppt_squared_for_d_4" in json.loads(capsys.readouterr().out)["entries"]
    assert main([*workspace, "show", "local_ppt"]) == 1, "no folder before the first epoch"
    assert main([*workspace, "hint", "local_ppt", "Use the Choi matrix."]) == 0
    assert list((cli_workspace / "problems").glob("ppt-squared*"))
    assert main([*workspace, "show", "local_ppt", "--dossier"]) == 0
    assert "Use the Choi matrix." in capsys.readouterr().out
    assert main([*workspace, "suspend", "local_ppt"]) == 0
    assert PortfolioStore(WorkspacePaths(cli_workspace)).get("local_ppt_squared_for_d_4").status == "suspended"
    assert main([*workspace, "review"]) == 0
    assert "No claimed resolutions" in capsys.readouterr().out
    assert main([*workspace, "stop"]) == 0 and (cli_workspace / ".lean-orch" / "STOP").exists()
    assert main([*workspace, "show", "nonexistent"]) == 2


def test_parser_rejects_bad_arguments():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--email", "not-an-address"])
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--parallel", "0"])
    assert parser.parse_args(["run", "--epochs", "3", "--problem", "a", "--problem", "b"]).problem == ["a", "b"]


def test_workspace_is_found_from_a_subdirectory(cli_workspace: Path, monkeypatch, capsys):
    monkeypatch.chdir(cli_workspace / "prompts")
    monkeypatch.delenv("LEAN_ORCH_WORKSPACE", raising=False)
    assert main(["status"]) == 0
    assert str(cli_workspace) in capsys.readouterr().out


def test_agents_toml_needs_one_confirmation(cli_workspace: Path, monkeypatch, capsys):
    paths = WorkspacePaths(cli_workspace)
    assert paths.agents_config.is_file() and not paths.agents_confirmed.exists()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert not cli._agents_ready(cli_workspace, cli.Console(), assume_yes=False)
    output = capsys.readouterr().out
    assert "researcher" in output and "codex --search exec" in output
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = iter(["maybe", "no"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert not cli._agents_ready(cli_workspace, cli.Console(), assume_yes=False)
    assert "Please answer yes or no." in capsys.readouterr().out
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert cli._agents_ready(cli_workspace, cli.Console(), assume_yes=False)
    assert paths.agents_confirmed.is_file()
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("must not ask twice"))
    assert cli._agents_ready(cli_workspace, cli.Console(), assume_yes=False)


def test_missing_agents_toml_is_recreated_and_yes_confirms(cli_workspace: Path):
    paths = WorkspacePaths(cli_workspace)
    paths.agents_config.unlink()
    assert cli._agents_ready(cli_workspace, cli.Console(), assume_yes=True)
    assert paths.agents_config.is_file() and paths.agents_confirmed.is_file()


def test_doctor_reports_each_check(cli_workspace: Path, capsys):
    (cli_workspace / "agents.toml").write_text(
        "\n".join(f'[{role}]\ncommand = ["{sys.executable}"]' for role in
                  ("researcher", "critic", "supervisor", "literature", "triage")) + "\n")
    config_path = cli_workspace / "lean-orch.toml"
    config_path.write_text(config_path.read_text().replace("[catalogue.qiqcop]\nenabled = true",
                                                           "[catalogue.qiqcop]\nenabled = false"))
    status = main(["-w", str(cli_workspace), "doctor"])
    output = capsys.readouterr().out
    assert "OK    agent researcher" in output and "agents.toml" in output and "experiment python" in output
    assert status in (0, 1)
