from __future__ import annotations

import pytest

from lean_orchestrator.catalogue.local import make_entry


def test_make_entry_derives_an_id_and_digest():
    entry = make_entry("PPT squared in dimension four", "Is every composition of two PPT channels EB for d = 4?",
                       fields=["Quantum Resource Theory"], references=["M. Christandl, open problem list (2012)"])
    assert entry.id == "local_ppt_squared_in_dimension_four"
    assert entry.source == "local" and entry.status == "Open" and entry.is_open
    assert entry.references[0].key == "R1"
    assert entry.digest == entry.content_digest()


def test_make_entry_requires_title_and_statement():
    with pytest.raises(ValueError, match="title"):
        make_entry(" ", "s")
    with pytest.raises(ValueError, match="statement"):
        make_entry("t", "")
