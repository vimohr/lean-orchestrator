"""Shared fixtures: temporary workspaces driven by a scripted fake agent."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from support import fake_config, quiet_console

from lean_orchestrator.orchestrator import Orchestrator, build_services
from lean_orchestrator.workspace import InitOptions, init_workspace


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    init_workspace(root, InitOptions(lean_setup=False, python_env=False, git=True), quiet_console())
    return root


@pytest.fixture
def make_orchestrator(workspace: Path) -> Iterator[Callable[..., Orchestrator]]:
    created: list[Orchestrator] = []

    def factory(overrides: dict[str, Any] | None = None) -> Orchestrator:
        services = build_services(workspace, config=fake_config(overrides), console=quiet_console())
        orchestrator = Orchestrator(services)
        orchestrator.prepare()
        created.append(orchestrator)
        return orchestrator

    yield factory
    for orchestrator in created:
        orchestrator.close()
