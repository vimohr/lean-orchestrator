"""Append-only event log and thread-safe terminal reporting."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

from .jsonio import append_jsonl, read_jsonl, utc_now


class EventLog:
    """Record orchestrator events as JSON lines, one writer at a time."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def emit(self, kind: str, **fields: Any) -> dict[str, Any]:
        event = {"time": utc_now(), "kind": kind, **fields}
        with self._lock:
            append_jsonl(self.path, event)
        return event

    def read(self, kinds: set[str] | None = None) -> list[dict[str, Any]]:
        with self._lock:
            events = read_jsonl(self.path)
        if kinds is None:
            return events
        return [event for event in events if event.get("kind") in kinds]


class Console:
    """Serialize terminal output from concurrent epochs."""

    def __init__(self, stream=None, error_stream=None) -> None:
        self._stream = stream or sys.stdout
        self._error_stream = error_stream or sys.stderr
        self._lock = threading.Lock()

    def info(self, message: str, *, tag: str = "") -> None:
        self._write(self._stream, message, tag)

    def warn(self, message: str, *, tag: str = "") -> None:
        self._write(self._error_stream, message, tag)

    def _write(self, stream, message: str, tag: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        prefix = f"{stamp} [{tag}] " if tag else f"{stamp} "
        with self._lock:
            for line in message.splitlines() or [""]:
                print(prefix + line, file=stream, flush=True)
