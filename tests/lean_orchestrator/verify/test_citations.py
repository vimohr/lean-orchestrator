from __future__ import annotations

import json
import urllib.error

import pytest

from lean_orchestrator.verify import citations
from lean_orchestrator.verify.citations import CitationChecker, normalize_arxiv_id, title_overlap

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>http://arxiv.org/abs/2405.09613v2</id>
  <title>Computable entanglement cost under positive partial transpose operations</title></entry>
</feed>"""


@pytest.mark.parametrize(
    ("value", "expected"),
    [("2405.09613", "2405.09613"), ("arXiv:2405.09613v3", "2405.09613"), ("quant-ph/0405123", "quant-ph/0405123"),
     ("not an id", None)],
)
def test_arxiv_identifiers(value, expected):
    assert normalize_arxiv_id(value) == expected


def test_title_overlap_ignores_stop_words():
    assert title_overlap("The entanglement cost", "L. Lami et al., Computable entanglement cost, PRL (2025)") == 1.0
    assert title_overlap("Quantum error correction", "Entanglement cost") == 0.0


def test_arxiv_check_verifies_and_detects_mismatches(monkeypatch):
    checker = CitationChecker()
    monkeypatch.setattr(citations.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(checker, "_get", lambda url: ATOM.encode())
    good = checker.check("Lami, Mele, Regula: computable entanglement cost under PPT operations", arxiv="2405.09613")
    assert good.status == "verified"
    bad = checker.check("Superactivation of quantum capacity", arxiv="2405.09613")
    assert bad.status == "title_mismatch"
    assert checker.check("x", arxiv="9999.99999").status == "not_found"


def test_doi_check_and_network_failures(monkeypatch):
    checker = CitationChecker()
    payload = json.dumps({"message": {"title": ["Computable Entanglement Cost under Positive Partial Transpose Operations"]}})
    monkeypatch.setattr(checker, "_get", lambda url: payload.encode())
    assert checker.check("Computable entanglement cost (PRL 2025)", doi="10.1103/PhysRevLett.134.090202").status == "verified"

    def missing(url):
        raise urllib.error.HTTPError(url, 404, "not found", None, None)

    monkeypatch.setattr(checker, "_get", missing)
    assert checker.check("anything", doi="10.9999/none").status == "not_found"

    def offline(url):
        raise OSError("no network")

    monkeypatch.setattr(checker, "_get", offline)
    assert checker.check("anything else", doi="10.9999/other").status == "unchecked"
    assert checker.check("no identifiers").status == "no_identifier"
    assert CitationChecker(enabled=False).check("x", arxiv="2405.09613").status == "unchecked"


def test_arxiv_identifiers_are_batched_and_rate_limit_errors_retried(monkeypatch):
    checker = CitationChecker()
    monkeypatch.setattr(citations.time, "sleep", lambda seconds: None)
    requests = []
    feed = ATOM.replace("</feed>", "<entry><id>http://arxiv.org/abs/1807.01266v1</id>"
                                   "<title>When Do Composed Maps Become Entanglement Breaking?</title></entry></feed>")

    def fake_get(url):
        requests.append(url)
        if len(requests) == 1:
            raise urllib.error.HTTPError(url, 406, "Not Acceptable", None, None)
        return feed.encode()

    monkeypatch.setattr(checker, "_get", fake_get)
    results = checker.check_many([
        ("Lami et al., Computable entanglement cost under positive partial transpose operations", "2405.09613", None),
        ("Christandl, Mueller-Hermes, Wolf: When do composed maps become entanglement breaking?", "1807.01266", None),
        ("A missing preprint", "2601.00001", None),
    ])
    assert [result.status for result in results] == ["verified", "verified", "not_found"]
    assert len(requests) == 2 and "id_list=1807.01266%2C2405.09613%2C2601.00001" in requests[-1]
    assert checker.check("Composed maps entanglement breaking", arxiv="1807.01266").status == "verified"
    assert len(requests) == 2, "titles are cached"


def test_doi_is_used_when_arxiv_is_unreachable(monkeypatch):
    checker = CitationChecker()
    monkeypatch.setattr(citations.time, "sleep", lambda seconds: None)
    crossref = json.dumps({"message": {"title": ["When Do Composed Maps Become Entanglement Breaking?"]}})

    def fake_get(url):
        if "arxiv" in url:
            raise OSError("arXiv unreachable")
        return crossref.encode()

    monkeypatch.setattr(checker, "_get", fake_get)
    result = checker.check("When do composed maps become entanglement breaking (AHP 2019)", arxiv="1807.01266",
                           doi="10.1007/s00023-019-00774-7")
    assert result.status == "verified"
