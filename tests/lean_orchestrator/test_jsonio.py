from __future__ import annotations

import re

import pytest

from lean_orchestrator.jsonio import (
    append_jsonl, atomic_write_text, read_json, read_jsonl, sha256_bytes, sha256_file, utc_now, write_json,
)


def test_json_round_trip_and_atomic_writes(tmp_path):
    path = tmp_path / "nested" / "value.json"
    write_json(path, {"ρ": [1, 2]})
    assert read_json(path) == {"ρ": [1, 2]}
    assert "ρ" in path.read_text(encoding="utf-8")
    atomic_write_text(path, "replaced")
    assert path.read_text() == "replaced"
    assert not list(path.parent.glob(".*tmp"))


def test_jsonl_append_and_read(tmp_path):
    path = tmp_path / "log.jsonl"
    assert read_jsonl(path) == []
    append_jsonl(path, {"a": 1})
    append_jsonl(path, {"b": 2})
    assert read_jsonl(path) == [{"a": 1}, {"b": 2}]
    path.write_text('{"a": 1}\nnot json\n')
    with pytest.raises(ValueError, match=":2:"):
        read_jsonl(path)


def test_hashes_and_timestamps(tmp_path):
    path = tmp_path / "f"
    path.write_bytes(b"abc")
    assert sha256_file(path) == sha256_bytes(b"abc").lower()
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", utc_now())
