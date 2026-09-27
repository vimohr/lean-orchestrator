from __future__ import annotations

import pytest

from lean_orchestrator.paths import WorkspacePaths
from lean_orchestrator.portfolio import ACTIVE, PortfolioEntry, PortfolioStore


def test_store_persists_edits_and_resolves_identifiers(tmp_path):
    paths = WorkspacePaths(tmp_path)
    store = PortfolioStore(paths)
    with store.edit() as portfolio:
        portfolio.entries["op_2e43f525333b67c0"] = PortfolioEntry(id="op_2e43f525333b67c0", source="qiqcop",
                                                                   title="T", folder="ppt-cost-2e43f5")
        portfolio.entries["op_2e99"] = PortfolioEntry(id="op_2e99", source="qiqcop", title="U")
    reloaded = PortfolioStore(paths)
    assert set(reloaded.snapshot().entries) == {"op_2e43f525333b67c0", "op_2e99"}
    assert reloaded.resolve("op_2e43") == "op_2e43f525333b67c0"
    assert reloaded.resolve("ppt-cost") == "op_2e43f525333b67c0"
    with pytest.raises(KeyError, match="ambiguous"):
        reloaded.resolve("op_2e")
    with pytest.raises(KeyError, match="no problem"):
        reloaded.resolve("zzz")
    updated = reloaded.update_entry("op_2e99", lambda entry: setattr(entry, "status", ACTIVE))
    assert updated.status == ACTIVE and updated.updated
    snapshot = reloaded.snapshot()
    snapshot.entries.clear()
    assert reloaded.get("op_2e99").status == ACTIVE, "snapshots are copies"


def test_display_status_marks_promising_problems():
    entry = PortfolioEntry(id="p", source="local", title="P", status=ACTIVE, ewma_progress=0.6)
    assert entry.is_promising and entry.display_status == "promising"
    entry.ewma_progress = 0.2
    assert entry.display_status == ACTIVE
