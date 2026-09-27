"""Helpers for tests that drive workspaces with the scripted fake agent."""

from __future__ import annotations

import copy
import io
import json
import sys
from pathlib import Path
from typing import Any

from lean_orchestrator.catalogue.local import make_entry
from lean_orchestrator.config import Config, parse_config
from lean_orchestrator.events import Console
from lean_orchestrator.orchestrator import Orchestrator
from lean_orchestrator.portfolio import CANDIDATE, PortfolioEntry

FAKE_AGENT = Path(__file__).parent / "fixtures" / "fake_agent.py"
ROLES = ("researcher", "critic", "supervisor", "literature", "triage")


def quiet_console() -> Console:
    return Console(stream=io.StringIO(), error_stream=io.StringIO())


def _merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def fake_config(overrides: dict[str, Any] | None = None) -> Config:
    agent = {"command": [sys.executable, str(FAKE_AGENT)]}
    table: dict[str, Any] = {
        "agents": {**{role: dict(agent) for role in ROLES}, "retry_delays_seconds": [0], "max_repair_attempts": 1},
        "loop": {"iterations_per_epoch": 3, "heartbeat_seconds": 0, "max_consecutive_failures": 3},
        "verification": {
            "lean": {"enabled": False},
            "citations": {"enabled": False},
            "experiments": {"python": [sys.executable]},
        },
        "catalogue": {"qiqcop": {"enabled": False}},
    }
    config = parse_config(_merge(table, overrides or {}))
    config.validate()
    return config


def add_problem(orchestrator: Orchestrator, problem_id: str, title: str, *, status: str = CANDIDATE,
                statement: str = "Show that $Q_d \\geq 0$ for every dimension $d \\geq 2$.") -> None:
    services = orchestrator.services
    entry = make_entry(title, statement, problem_id=problem_id)
    services.catalogue.save(entry)
    with services.portfolio.edit() as portfolio:
        portfolio.entries[problem_id] = PortfolioEntry(
            id=problem_id, source=entry.source, title=title, status=status,
            upstream_status=entry.status, upstream_digest=entry.digest,
        )


def set_scenarios(workspace: Path, scenarios: dict[str, str]) -> None:
    (workspace / "fake_scenario.json").write_text(json.dumps(scenarios))


def fake_calls(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / "fake_calls.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def zoo_record(problem_id: str = "op_0123456789abcdef", *, status: str = "Unsolved", title: str = "Synthetic problem",
               digest: str = "d1", fields=("Quantum Resource Theory",), topics=("Bound entanglement",)) -> dict:
    """A synthetic record in the ``qiqcop-zoo/problem/3`` layout."""
    return {
        "schema": "qiqcop-zoo/problem/3",
        "id": problem_id,
        "aliases": [problem_id],
        "url": f"https://qiqc-op.com/problem/{problem_id}/",
        "title": {"tex": title, "html": title, "text": title},
        "status": status,
        "fields": list(fields),
        "topics": list(topics),
        "updatedAt": "2026-09-25T07:39:47.000Z",
        "statement": {"tex": "Is $X \\succeq 0$?\n\\begin{equation}\nX = Y\n\\label{eq:a}\n\\end{equation}",
                      "text": "Is $X \\succeq 0$?\n\\begin{equation}\nX = Y\n\\tag{1}\n\\end{equation}"},
        "source": {"tex": "Posed in \\sourcecite{ref:a}{AB20}.", "text": "Posed in [AB20]."},
        "progress": [{"tex": "Report in \\href{https://github.com/x/y/pull/7}{Pull request \\#7}.",
                      "text": "Report in Pull request #7."}],
        "comment": {"tex": "Still open.", "text": "Still open."},
        "references": [{
            "key": "AB20", "label": "ref:a", "tex": "A. B., Title, \\href{https://arxiv.org/abs/2001.00001}{arXiv}.",
            "text": "A. B., Title.",
            "links": [{"kind": "arxiv", "url": "https://arxiv.org/abs/2001.00001", "id": "2001.00001"},
                      {"kind": "doi", "url": "https://doi.org/10.1/x", "id": "10.1/x"}],
        }],
        "related": [{"id": "op_fedcba9876543210", "title": "Other"}],
        "sha256": digest,
    }
