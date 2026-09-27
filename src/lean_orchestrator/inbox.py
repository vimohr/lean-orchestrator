"""Commands from the CLI that the orchestrator applies at safe points.

``lean-orch hint`` or ``lean-orch suspend`` may run while a research loop is
active. Instead of editing state the loop is using, they drop a small JSON
message into ``.lean-orch/inbox/``; the orchestrator applies it between
iterations (for a problem that is running) or immediately (otherwise).
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .jsonio import utc_now, write_json
from .paths import WorkspacePaths

KINDS = ("hint", "set_status", "pin", "review")


@dataclass
class InboxMessage:
    path: Path
    kind: str
    problem: str
    payload: dict[str, Any]
    time: str


def inbox_dir(paths: WorkspacePaths) -> Path:
    return paths.internal_dir / "inbox"


def post(paths: WorkspacePaths, kind: str, problem: str, **payload: Any) -> Path:
    if kind not in KINDS:
        raise ValueError(f"unknown inbox message kind {kind!r}")
    directory = inbox_dir(paths)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{time.time_ns():020d}-{secrets.token_hex(3)}-{kind}.json"
    write_json(path, {"kind": kind, "problem": problem, "payload": payload, "time": utc_now()})
    return path


def pending(paths: WorkspacePaths) -> list[InboxMessage]:
    directory = inbox_dir(paths)
    if not directory.is_dir():
        return []
    messages = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("kind") in KINDS and isinstance(data.get("problem"), str):
            messages.append(InboxMessage(path, data["kind"], data["problem"], dict(data.get("payload") or {}),
                                         str(data.get("time", ""))))
    return messages


def acknowledge(message: InboxMessage) -> None:
    message.path.unlink(missing_ok=True)
