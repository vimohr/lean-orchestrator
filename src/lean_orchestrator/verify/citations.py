"""Existence checks for cited arXiv preprints and DOIs.

Language models can invent references. These checks confirm that an identifier
resolves and that its title resembles the citation. They do not check that the
paper proves what is attributed to it; the critic remains responsible for that.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass

USER_AGENT = "lean-orchestrator/0.1 (research citation check)"
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV_ID = re.compile(r"^(?:arxiv:)?\s*([0-9]{4}\.[0-9]{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/[0-9]{7})(?:v\d+)?$", re.I)
_STOP_WORDS = frozenset({"the", "a", "an", "of", "and", "in", "on", "for", "to", "with", "by", "from", "via"})

VERIFIED = "verified"
TITLE_MISMATCH = "title_mismatch"
NOT_FOUND = "not_found"
UNCHECKED = "unchecked"
NO_IDENTIFIER = "no_identifier"


@dataclass
class CitationCheck:
    status: str
    found_title: str = ""
    detail: str = ""


def normalize_arxiv_id(value: str) -> str | None:
    match = _ARXIV_ID.match(value.strip())
    return match.group(1) if match else None


def clean_title(title: str) -> str:
    """Drop markup such as the MathML that Crossref embeds in titles."""
    return " ".join(re.sub(r"<[^>]+>", " ", title).split())


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in _STOP_WORDS and len(word) > 2}


def title_overlap(title: str, citation: str) -> float:
    """Shared content words relative to the shorter of the two word sets.

    Citations often abbreviate titles, so the shorter set is the fair denominator.
    """
    found, cited = _words(title), _words(citation)
    if not found or not cited:
        return 0.0
    return len(found & cited) / min(len(found), len(cited))


def titles_match(title: str, citation: str) -> bool:
    """Require substantial overlap: half of the shorter set and at least three words (or all of a short title)."""
    shared = len(_words(title) & _words(citation))
    return title_overlap(title, citation) >= 0.5 and shared >= min(3, len(_words(title)))


class CitationChecker:
    """Resolve arXiv identifiers and DOIs.

    arXiv identifiers are resolved with batched arXiv API requests; when arXiv
    throttles (its CDN answers 406 or 429), DataCite, which registers every arXiv
    paper under ``10.48550/arXiv.<id>``, is used instead. DOIs go to Crossref,
    except arXiv DOIs, which go to DataCite.
    """

    ARXIV_BATCH = 20
    RETRY_DELAYS = (5.0, 15.0)
    RETRY_CODES = frozenset({406, 429, 500, 502, 503})
    THROTTLE_PAUSE = 600.0

    def __init__(self, timeout: float = 15.0, enabled: bool = True) -> None:
        self.timeout = timeout
        self.enabled = enabled
        self._lock = threading.Lock()
        self._last_arxiv_call = 0.0
        self._arxiv_paused_until = 0.0
        self._titles: dict[str, str | None] = {}
        self._doi_cache: dict[str, tuple[str, str]] = {}

    def _get(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read()

    def _arxiv_request(self, identifiers: list[str]) -> dict[str, str]:
        """One API call for several identifiers, spaced and retried as arXiv asks."""
        if time.monotonic() < self._arxiv_paused_until:
            raise OSError("arXiv API is throttling this host; using DataCite")
        query = urllib.parse.urlencode({"id_list": ",".join(identifiers), "max_results": len(identifiers)})
        delays = list(self.RETRY_DELAYS)
        while True:
            with self._lock:
                wait = 3.0 - (time.monotonic() - self._last_arxiv_call)
                if wait > 0:
                    time.sleep(wait)
                self._last_arxiv_call = time.monotonic()
            try:
                payload = self._get(f"https://export.arxiv.org/api/query?{query}")
                break
            except urllib.error.HTTPError as error:
                if error.code not in self.RETRY_CODES:
                    raise
                if not delays:
                    self._arxiv_paused_until = time.monotonic() + self.THROTTLE_PAUSE
                    raise
                time.sleep(delays.pop(0))
        root = ElementTree.fromstring(payload)
        titles: dict[str, str] = {}
        for entry in root.findall(f"{_ATOM}entry"):
            entry_id = (entry.findtext(f"{_ATOM}id") or "").rsplit("/abs/", 1)[-1]
            title = " ".join((entry.findtext(f"{_ATOM}title") or "").split())
            if title and title.lower() != "error":
                titles[re.sub(r"v\d+$", "", entry_id)] = clean_title(title)
        return titles

    def arxiv_titles(self, identifiers: list[str]) -> dict[str, str]:
        """Titles of the given arXiv identifiers that exist, fetched in batches and cached."""
        missing = sorted({identifier for identifier in identifiers if identifier not in self._titles})
        for start in range(0, len(missing), self.ARXIV_BATCH):
            chunk = missing[start:start + self.ARXIV_BATCH]
            found = self._arxiv_request(chunk)
            for identifier in chunk:
                self._titles[identifier] = found.get(identifier)
        return {identifier: title for identifier in identifiers
                if (title := self._titles.get(identifier)) is not None}

    def _doi_title(self, doi: str) -> tuple[str, str]:
        """Return ("found", title), (NOT_FOUND, ""), or (UNCHECKED, reason) for a DOI."""
        if doi in self._doi_cache:
            return self._doi_cache[doi]
        datacite = doi.lower().startswith("10.48550/")
        url = (f"https://api.datacite.org/dois/{urllib.parse.quote(doi.lower())}" if datacite
               else f"https://api.crossref.org/works/{urllib.parse.quote(doi)}")
        service = "DataCite" if datacite else "Crossref"
        try:
            payload = json.loads(self._get(url))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                self._doi_cache[doi] = (NOT_FOUND, "")
                return self._doi_cache[doi]
            return UNCHECKED, f"{service} lookup failed: {error}"
        except (OSError, json.JSONDecodeError) as error:
            return UNCHECKED, f"{service} lookup failed: {error}"
        if datacite:
            titles = [item.get("title", "") for item in payload.get("data", {}).get("attributes", {}).get("titles", [])]
        else:
            titles = payload.get("message", {}).get("title") or []
        self._doi_cache[doi] = ("found", clean_title(str(titles[0]) if titles else ""))
        return self._doi_cache[doi]

    def check_doi(self, doi: str, citation: str) -> CitationCheck:
        doi = doi.strip().removeprefix("https://doi.org/").removeprefix("doi:")
        status, title = self._doi_title(doi)
        if status == NOT_FOUND:
            return CitationCheck(NOT_FOUND, detail=f"DOI {doi} does not resolve")
        if status == UNCHECKED:
            return CitationCheck(UNCHECKED, detail=title)
        if title and titles_match(title, citation):
            return CitationCheck(VERIFIED, found_title=title)
        return CitationCheck(TITLE_MISMATCH, found_title=title, detail=f"DOI {doi} is titled {title!r}")

    def check_many(self, items: list[tuple[str, str | None, str | None]]) -> list[CitationCheck]:
        """Check (citation, arXiv ID, DOI) triples, resolving all arXiv IDs in batched requests."""
        if not self.enabled:
            return [CitationCheck(UNCHECKED, detail="citation checks disabled") for _ in items]
        wanted = [normalize_arxiv_id(arxiv) for _citation, arxiv, _doi in items if arxiv]
        titles: dict[str, str] = {}
        failure = ""
        try:
            titles = self.arxiv_titles([identifier for identifier in wanted if identifier])
        except (OSError, ElementTree.ParseError) as error:
            failure = f"arXiv lookup failed: {error}"
        results = []
        for citation, arxiv, doi in items:
            outcome = self._arxiv_outcome(citation, arxiv, titles, failure) if arxiv else None
            if doi and (outcome is None or outcome.status != VERIFIED):
                by_doi = self.check_doi(doi, citation)
                if outcome is None or by_doi.status == VERIFIED or outcome.status == UNCHECKED:
                    if outcome is not None and by_doi.status == VERIFIED and outcome.status != UNCHECKED:
                        by_doi.detail = f"verified by DOI; {outcome.detail}"
                    outcome = by_doi
            results.append(outcome or CitationCheck(NO_IDENTIFIER, detail="no arXiv identifier or DOI given"))
        return results

    def _arxiv_outcome(self, citation: str, arxiv: str, titles: dict[str, str], failure: str) -> CitationCheck:
        normalized = normalize_arxiv_id(arxiv)
        if normalized is None:
            return CitationCheck(NOT_FOUND, detail=f"malformed arXiv identifier {arxiv!r}")
        title = titles.get(normalized)
        if title is None:
            if not failure and normalized in self._titles:
                return CitationCheck(NOT_FOUND, detail=f"arXiv:{normalized} does not resolve")
            status, found = self._doi_title(f"10.48550/arXiv.{normalized}")
            if status == NOT_FOUND:
                return CitationCheck(NOT_FOUND, detail=f"arXiv:{normalized} does not resolve (DataCite)")
            if status == UNCHECKED:
                return CitationCheck(UNCHECKED, detail=f"{failure}; {found}".strip("; "))
            title = found
        if titles_match(title, citation):
            return CitationCheck(VERIFIED, found_title=title)
        return CitationCheck(TITLE_MISMATCH, found_title=title, detail=f"arXiv:{normalized} is titled {title!r}")

    def check(self, citation: str, *, arxiv: str | None = None, doi: str | None = None) -> CitationCheck:
        return self.check_many([(citation, arxiv, doi)])[0]
