from __future__ import annotations

import pytest

from lean_orchestrator.catalogue.base import CatalogueEntry
from lean_orchestrator.config import TriageConfig
from lean_orchestrator.triage import batch_markdown, suitability


def test_suitability_is_a_weighted_mean_on_the_score_scale():
    config = TriageConfig()
    scores = {"precision": 5, "finite_dimensional": 5, "formalizability": 5, "closability": 5,
              "tractable_subcases": 5, "background": 5}
    assert suitability(scores, config) == pytest.approx(5.0)
    scores["tractable_subcases"] = 0
    assert suitability(scores, config) == pytest.approx((5 * 4 + 0 * 1.5 + 5 * 0.5) / 6)
    assert suitability({"unknown": 5}, config) == 0.0


def test_batch_markdown_contains_every_problem():
    entries = [CatalogueEntry(id=f"op_{index}", source="qiqcop", title=f"T{index}", status="Unsolved",
                              statement=f"Statement {index}", progress=["progress"], comment="comment")
               for index in range(2)]
    text = batch_markdown(entries)
    assert "## `op_0`: T0" in text and "## `op_1`: T1" in text
    assert text.count("### Known progress") == 2 and "### Comment" in text
