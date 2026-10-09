"""Returned token usage and admission reserves; unknown usage is never zero.

Codex input already includes cached tokens. Claude's cache read/write counters
are additional input categories. Reasoning tokens are a component of output.
Reserves are planning charges, never reported as measured consumption.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from .jsonio import utc_now


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    reasoning_output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def json_events(text: str) -> list[dict]:
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        return [parsed]
    events = []
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def parse_usage(text: str) -> TokenUsage | None:
    totals = [0, 0, 0, 0]
    found = False
    for event in json_events(text):
        if event.get("type") not in ("turn.completed", "result", "usage"):
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        names = ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_output_tokens",
                 "cache_read_input_tokens", "cache_creation_input_tokens")
        if any(key in usage and (type(usage[key]) is not int or usage[key] < 0) for key in names):
            continue
        if "input_tokens" not in usage or "output_tokens" not in usage:
            continue
        cached = usage.get("cached_input_tokens", usage.get("cache_read_input_tokens", 0))
        input_tokens = usage["input_tokens"]
        if event.get("type") == "result":
            input_tokens += usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
        reasoning = usage.get("reasoning_output_tokens", 0)
        if cached > input_tokens or reasoning > usage["output_tokens"]:
            continue
        for index, value in enumerate((input_tokens, usage["output_tokens"], cached, reasoning)):
            totals[index] += value
        found = True
    return TokenUsage(*totals) if found else None


class UsageLedger:
    """Thread-safe call admission with durable, distinct usage and reserve fields."""

    def __init__(self, path: Path, *, max_tokens: int = 0, call_reserve: int = 25_000) -> None:
        self.path = path
        self.max_tokens = max_tokens
        self.call_reserve = call_reserve
        self._lock = threading.RLock()
        # Admission itself is journaled. A process lost before settlement leaves an
        # explicit unknown charge on restart instead of silently disappearing.
        journal = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
        by_call = {item["call_id"]: item for item in journal}
        self.records = list(by_call.values())
        self._pending: dict[str, int] = {}

    @property
    def known_tokens(self) -> int:
        with self._lock:
            return sum(item.get("total_tokens") or 0 for item in self.records)

    @property
    def unknown_reserve(self) -> int:
        with self._lock:
            return sum(item.get("unknown_reserve", 0) for item in self.records)

    @property
    def charged_tokens(self) -> int:
        with self._lock:
            return self.known_tokens + self.unknown_reserve + sum(self._pending.values())

    def can_start(self, reserve: int | None = None) -> bool:
        with self._lock:
            return not self.max_tokens or self.charged_tokens + (self.call_reserve if reserve is None else reserve) <= self.max_tokens

    def reserve(self, call_id: str, amount: int | None = None, **metadata) -> None:
        with self._lock:
            if call_id in self._pending or any(record["call_id"] == call_id for record in self.records):
                raise ValueError(f"duplicate usage call {call_id}")
            amount = self.call_reserve if amount is None else amount
            if amount < 0 or not self.can_start(amount):
                raise ValueError("token admission budget exhausted")
            self._append({**metadata, "call_id": call_id, "time": utc_now(), "status": "admitted",
                          "usage": None, "total_tokens": None, "unknown_reserve": amount})
            self._pending[call_id] = amount

    def _append(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def finish(self, call_id: str, usage: TokenUsage | None, **metadata) -> dict:
        with self._lock:
            reserve = self._pending[call_id]
            record = {**metadata, "call_id": call_id, "time": utc_now(), "status": "settled",
                      "usage": asdict(usage) if usage is not None else None,
                      "total_tokens": usage.total_tokens if usage is not None else None,
                      "unknown_reserve": reserve if usage is None else 0}
            self._append(record)
            self.records.append(record)
            del self._pending[call_id]
            return record

    def summary(self) -> dict:
        with self._lock:
            return {"known_tokens": self.known_tokens, "unknown_calls": sum(item["usage"] is None for item in self.records),
                    "unknown_reserve": self.unknown_reserve, "in_flight_reserve": sum(self._pending.values()),
                    "charged_tokens": self.charged_tokens, "max_tokens": self.max_tokens,
                    "calls": len(self.records)}
