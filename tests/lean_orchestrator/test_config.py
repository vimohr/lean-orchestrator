from __future__ import annotations

import tomllib
from importlib import resources

import pytest

from lean_orchestrator.config import Config, ConfigurationError, expand_placeholders, load_config, parse_config


def template_table() -> dict:
    text = resources.files("lean_orchestrator").joinpath("templates", "workspace", "lean-orch.toml").read_text()
    return tomllib.loads(text)


def test_the_shipped_template_matches_the_code_defaults():
    assert parse_config(template_table()) == Config()


def test_unknown_keys_and_wrong_types_are_rejected():
    with pytest.raises(ConfigurationError, match="unknown key"):
        parse_config({"loop": {"iterations": 3}})
    with pytest.raises(ConfigurationError, match="must be an integer"):
        parse_config({"loop": {"iterations_per_epoch": "12"}})
    with pytest.raises(ConfigurationError, match="list of arguments"):
        parse_config({"agents": {"critic": {"command": "claude -p"}}})
    with pytest.raises(ConfigurationError, match="must be true or false"):
        parse_config({"git": {"enabled": 1}})


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ({"loop": {"iterations_per_epoch": 0}}, "iterations_per_epoch"),
        ({"scheduler": {"exploration_reserve": 1.0}}, "exploration_reserve"),
        ({"progress": {"threshold": 4}}, "progress.threshold"),
        ({"progress": {"escalate_after": 5, "suspend_after": 4}}, "escalate_after"),
        ({"agents": {"critic": {"command": []}}}, "agents.critic.command"),
        ({"agents": {"critic": {"command": ["x"], "prompt_via": "file"}}}, "prompt_via"),
        ({"verification": {"lean": {"comparator": "always"}}}, "comparator"),
    ],
)
def test_validation(table, message):
    with pytest.raises(ConfigurationError, match=message):
        parse_config(table).validate()


def test_load_config_requires_the_file(tmp_path):
    with pytest.raises(ConfigurationError, match="lean-orch init"):
        load_config(tmp_path)
    (tmp_path / "lean-orch.toml").write_text("[loop]\nmax_parallel = 2\n")
    assert load_config(tmp_path).loop.max_parallel == 2


def test_placeholders_expand_only_known_names():
    arguments = ["--mcp-config", "{mcp_config}", '{"json": {"keep": 1}}', "{unknown}"]
    assert expand_placeholders(arguments, {"mcp_config": "/w/mcp.json"}) == [
        "--mcp-config", "/w/mcp.json", '{"json": {"keep": 1}}', "{unknown}"]


def test_agent_commands_are_read_from_agents_toml(tmp_path):
    (tmp_path / "lean-orch.toml").write_text("[loop]\nmax_parallel = 2\n")
    (tmp_path / "agents.toml").write_text('max_repair_attempts = 2\n[critic]\ncommand = ["my-critic", "--flag"]\n')
    config = load_config(tmp_path)
    assert config.loop.max_parallel == 2
    assert config.agents.critic.command == ("my-critic", "--flag")
    assert config.agents.max_repair_attempts == 2
    assert config.agents.researcher == Config().agents.researcher
    (tmp_path / "agents.toml").write_text('[critic]\ncommand = "a string"\n')
    with pytest.raises(ConfigurationError, match=r"agents\.toml\.critic\.command"):
        load_config(tmp_path)
    (tmp_path / "agents.toml").unlink()
    (tmp_path / "lean-orch.toml").write_text('[agents.critic]\ncommand = ["x"]\n')
    with pytest.raises(ConfigurationError, match=r"live in agents\.toml"):
        load_config(tmp_path)
