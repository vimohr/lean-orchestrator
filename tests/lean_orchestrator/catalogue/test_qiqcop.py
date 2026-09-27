from __future__ import annotations

import gzip
import json

import pytest
from support import zoo_record

from lean_orchestrator.catalogue import qiqcop
from lean_orchestrator.catalogue.qiqcop import CatalogueFetchError, keep, normalize


def test_normalize_keeps_tex_and_readable_text():
    entry = normalize(zoo_record())
    assert entry.id == "op_0123456789abcdef" and entry.source == "qiqcop"
    assert entry.title == "Synthetic problem" and entry.status == "Unsolved" and entry.is_open
    assert "\\label{eq:a}" in entry.statement and "\\tag{1}" in entry.statement_text
    assert entry.readable("statement") == entry.statement_text
    assert entry.progress_text == ["Report in Pull request #7. ([Pull request \\#7](https://github.com/x/y/pull/7))"]
    reference = entry.references[0]
    assert (reference.key, reference.arxiv, reference.doi) == ("AB20", "2001.00001", "10.1/x")
    assert entry.related == ["op_fedcba9876543210"] and entry.digest == "d1"


def test_normalize_requires_id_and_status():
    record = zoo_record()
    del record["status"]
    with pytest.raises(CatalogueFetchError, match="status"):
        normalize(record)


def test_filters():
    entry = normalize(zoo_record())
    assert keep(entry, ("Unsolved",), (), ())
    assert not keep(entry, ("Solved",), (), ())
    assert not keep(entry, ("Unsolved",), ("Quantum algorithm",), ())
    assert not keep(entry, ("Unsolved",), (), ("Bound entanglement",))


def test_fetch_records_parses_jsonl_and_gzip(monkeypatch):
    payload = "\n".join(json.dumps(zoo_record(f"op_{index:016x}")) for index in range(3)).encode()
    monkeypatch.setattr(qiqcop, "_get", lambda url, timeout: payload)
    assert [record["id"] for record in qiqcop.fetch_records("https://x/problems.jsonl")] == [
        "op_0000000000000000", "op_0000000000000001", "op_0000000000000002"]
    monkeypatch.setattr(qiqcop, "_get", lambda url, timeout: b"{broken")
    with pytest.raises(CatalogueFetchError, match="line 1"):
        qiqcop.fetch_records("https://x/problems.jsonl")
    monkeypatch.setattr(qiqcop, "_get", lambda url, timeout: json.dumps({"no": "digest"}).encode())
    with pytest.raises(CatalogueFetchError, match="release manifest"):
        qiqcop.fetch_release("https://x/release.json")
    assert gzip.decompress(gzip.compress(payload)) == payload
