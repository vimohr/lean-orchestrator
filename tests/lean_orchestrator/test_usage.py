import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from lean_orchestrator.agents import extract_json_block
from lean_orchestrator.usage import TokenUsage, UsageLedger, parse_usage


def test_codex_components_are_not_added_twice():
    usage = parse_usage(json.dumps({"type": "turn.completed", "usage": {
        "input_tokens": 100, "output_tokens": 40, "cached_input_tokens": 80, "reasoning_output_tokens": 30}}))
    assert usage.total_tokens == 140
    assert parse_usage("failed request") is None


def test_claude_cache_categories_are_additional_input():
    usage = parse_usage(json.dumps({"type": "result", "usage": {
        "input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 40}}))
    assert usage.input_tokens == 150 and usage.total_tokens == 170


def test_admission_is_atomic_and_missing_usage_survives_restart(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.jsonl", max_tokens=100, call_reserve=60)
    def reserve(name):
        try:
            ledger.reserve(name)
            return name
        except ValueError:
            return None
    with ThreadPoolExecutor(2) as pool:
        admitted = [name for name in pool.map(reserve, ["a", "b"]) if name]
    assert len(admitted) == 1
    ledger.finish(admitted[0], None, role="researcher")
    restored = UsageLedger(ledger.path, max_tokens=100, call_reserve=60)
    assert restored.known_tokens == 0 and restored.unknown_reserve == 60
    assert not restored.can_start()


def test_actual_spend_can_overshoot_a_planning_reserve(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.jsonl", max_tokens=100, call_reserve=20)
    ledger.reserve("a")
    ledger.finish("a", TokenUsage(90, 30), role="critic")
    assert ledger.known_tokens == 120 and not ledger.can_start()
    with pytest.raises(ValueError, match="budget"):
        ledger.reserve("b")


def test_lost_process_leaves_durable_unknown_charge(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.jsonl", max_tokens=100, call_reserve=60)
    ledger.reserve("lost")
    restarted = UsageLedger(ledger.path, max_tokens=100, call_reserve=60)
    assert restarted.unknown_reserve == 60 and not restarted.can_start()


def test_provider_envelopes_preserve_final_json_fallback():
    answer = {"summary": "ok"}
    codex = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(answer)}})
    codex += '\n{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
    assert extract_json_block(codex) == answer
    assert extract_json_block(json.dumps({"type": "result", "result": json.dumps(answer)})) == answer
