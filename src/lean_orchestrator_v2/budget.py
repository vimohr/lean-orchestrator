"""Staged allocation leases preserve submission and review allowance."""

import json
import threading
import uuid
from pathlib import Path

from lean_orchestrator.jsonio import write_json
from lean_orchestrator.usage import UsageLedger

from .backends import Backend


class BudgetExhausted(RuntimeError):
    pass


class BudgetPool:
    def __init__(self, root: Path, max_tokens: int, call_tokens: int) -> None:
        self.root = root
        self.ledger = UsageLedger(root / "usage.jsonl", max_tokens=max_tokens, call_reserve=call_tokens)
        self.leases: dict[str, int] = {}
        self.spent: dict[str, int] = {}
        self.lock = threading.RLock()

    def acquire(self, identifier: str, amount: int) -> bool:
        with self.lock:
            if identifier in self.leases:
                raise ValueError("allocation is already running")
            if self.ledger.charged_tokens + sum(self.leases.values()) + amount > self.ledger.max_tokens:
                return False
            self.leases[identifier] = amount
            self.spent[identifier] = 0
            return True

    def release(self, identifier: str) -> None:
        with self.lock:
            self.leases.pop(identifier, None)

    def remaining(self, identifier: str) -> int:
        with self.lock:
            return self.leases.get(identifier, 0)

    def call(self, backend: Backend, request: dict, work: Path, *, reserve: int,
             allocation_id: str = "", attempt_id: str = "", retain: int = 0) -> dict:
        call_id = uuid.uuid4().hex
        with self.lock:
            if allocation_id:
                if self.leases.get(allocation_id, 0) < reserve + retain:
                    raise BudgetExhausted("allocation reserve exhausted; protected later phases remain")
                self.leases[allocation_id] -= reserve
            elif self.ledger.charged_tokens + sum(self.leases.values()) + reserve > self.ledger.max_tokens:
                raise BudgetExhausted("campaign admission budget exhausted")
            try:
                self.ledger.reserve(call_id, reserve, role=request["role"], allocation=allocation_id, attempt=attempt_id)
            except ValueError as error:
                if allocation_id:
                    self.leases[allocation_id] += reserve
                raise BudgetExhausted(str(error)) from error
        directory = self.root / "calls" / call_id
        directory.mkdir(parents=True)
        write_json(directory / "request.json", request)
        completion = None
        try:
            completion = backend.complete(request, work, reserve)
            (directory / "stdout.log").write_text(completion.stdout)
            (directory / "stderr.log").write_text(completion.stderr)
            if completion.error or completion.data is None:
                raise RuntimeError(completion.error or "backend did not return a JSON object")
            write_json(directory / "reply.json", completion.data)
            return completion.data
        finally:
            record = self.ledger.finish(call_id, completion.usage if completion else None,
                                        role=request["role"], allocation=allocation_id, attempt=attempt_id,
                                        error=completion.error if completion else "interrupted")
            charged = record["total_tokens"] if record["total_tokens"] is not None else record["unknown_reserve"]
            with self.lock:
                if allocation_id:
                    self.spent[allocation_id] += charged
                    self.leases[allocation_id] = max(0, self.leases[allocation_id] + reserve - charged)
            write_json(directory / "usage.json", record)
