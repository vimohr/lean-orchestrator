from __future__ import annotations

import json

import pytest
from support import zoo_record

from lean_orchestrator.catalogue import qiqcop
from lean_orchestrator.catalogue.sync import sync_qiqcop
from lean_orchestrator.portfolio import ACTIVE, RESOLVED_EXTERNALLY, RETIRED, UNEXPLORED


@pytest.fixture
def upstream(monkeypatch):
    feed = {"digest": "sha256:one", "records": [zoo_record("op_a000000000000000", title="Alpha"),
                                                 zoo_record("op_b000000000000000", title="Beta"),
                                                 zoo_record("op_c000000000000000", status="Solved", title="Gamma")]}
    calls = {"records": 0}

    def fetch_release(url, timeout=30.0):
        return {"catalogDigest": feed["digest"], "updated": "2026-09-25", "counts": {"total": len(feed["records"])}}

    def fetch_records(url, timeout=60.0):
        calls["records"] += 1
        return [json.loads(json.dumps(record)) for record in feed["records"]]

    monkeypatch.setattr(qiqcop, "fetch_release", fetch_release)
    monkeypatch.setattr(qiqcop, "fetch_records", fetch_records)
    return feed, calls


def test_sync_imports_open_problems_and_tracks_upstream_changes(make_orchestrator, upstream):
    feed, calls = upstream
    orchestrator = make_orchestrator({"catalogue": {"qiqcop": {"enabled": True}}})
    services = orchestrator.services

    report = sync_qiqcop(services)
    assert sorted(report.new) == ["op_a000000000000000", "op_b000000000000000"]
    assert services.portfolio.get("op_a000000000000000").status == UNEXPLORED
    assert services.catalogue.exists("qiqcop", "op_c000000000000000"), "solved records are stored but not scheduled"

    assert sync_qiqcop(services).unchanged and calls["records"] == 1

    services.portfolio.update_entry("op_a000000000000000", lambda entry: setattr(entry, "status", ACTIVE))
    services.portfolio.update_entry("op_a000000000000000", lambda entry: setattr(entry, "folder", "alpha"))
    feed["digest"] = "sha256:two"
    feed["records"][0] = zoo_record("op_a000000000000000", title="Alpha", status="Solved", digest="d2")
    feed["records"].pop(1)
    report = sync_qiqcop(services)
    assert report.updated == ["op_a000000000000000"]
    assert report.resolved_upstream == ["op_a000000000000000"]
    assert report.retired == ["op_b000000000000000"]
    assert services.portfolio.get("op_a000000000000000").status == RESOLVED_EXTERNALLY
    assert services.portfolio.get("op_b000000000000000").status == RETIRED
    assert services.control.take_notes("op_a000000000000000")
    sources = json.loads(services.paths.catalogue_sources.read_text())
    assert sources["qiqcop"]["digest"] == "sha256:two"


def test_sync_respects_the_interval(make_orchestrator, upstream):
    _, calls = upstream
    orchestrator = make_orchestrator({"catalogue": {"qiqcop": {"enabled": True}}})
    sync_qiqcop(orchestrator.services)
    assert sync_qiqcop(orchestrator.services, respect_interval=True).skipped
    assert calls["records"] == 1


def test_disabled_source_is_skipped(make_orchestrator):
    orchestrator = make_orchestrator()
    assert sync_qiqcop(orchestrator.services).skipped
