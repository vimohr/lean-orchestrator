from __future__ import annotations

from lean_orchestrator.catalogue.base import CatalogueEntry, CatalogueStore
from lean_orchestrator.paths import WorkspacePaths


def test_store_round_trip_and_listing(tmp_path):
    store = CatalogueStore(WorkspacePaths(tmp_path))
    first = CatalogueEntry(id="a", source="local", title="A", status="Open", statement="s")
    second = CatalogueEntry(id="b", source="qiqcop", title="B", status="Unsolved", statement="t")
    store.save(first)
    store.save(second)
    assert store.exists("local", "a") and not store.exists("local", "b")
    assert store.load("qiqcop", "b") == second
    assert [entry.id for entry in store.all()] == ["a", "b"]


def test_content_digest_changes_with_research_content_only():
    entry = CatalogueEntry(id="a", source="local", title="A", status="Open", statement="s")
    digest = entry.content_digest()
    entry.updated = "2026-01-01"
    assert entry.content_digest() == digest
    entry.progress.append("new result")
    assert entry.content_digest() != digest
