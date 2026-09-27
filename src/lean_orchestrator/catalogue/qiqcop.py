"""Import open problems from the QIQCOP Zoo (https://qiqc-op.com).

The zoo publishes a bulk JSONL snapshot (schema ``qiqcop-zoo/problem/3``) and a
small release manifest whose ``catalogDigest`` changes whenever any record
changes. The importer polls the manifest and downloads the snapshot only when
the digest moves.
"""

from __future__ import annotations

import gzip
import json
import re
import urllib.request
from typing import Any

from .base import CatalogueEntry, CatalogueReference

SOURCE = "qiqcop"
USER_AGENT = "lean-orchestrator (+https://github.com/vimohr)"


class CatalogueFetchError(RuntimeError):
    """Raised when the upstream catalogue cannot be downloaded or parsed."""


def _get(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
            if response.headers.get("Content-Encoding") == "gzip" or url.endswith(".gz"):
                payload = gzip.decompress(payload)
            return payload
    except OSError as error:
        raise CatalogueFetchError(f"could not fetch {url}: {error}") from error


def fetch_release(url: str, timeout: float = 30.0) -> dict[str, Any]:
    try:
        release = json.loads(_get(url, timeout))
    except json.JSONDecodeError as error:
        raise CatalogueFetchError(f"{url} did not return JSON: {error}") from error
    if not isinstance(release, dict) or "catalogDigest" not in release:
        raise CatalogueFetchError(f"{url} is not a QIQCOP release manifest")
    return release


def fetch_records(url: str, timeout: float = 60.0) -> list[dict[str, Any]]:
    text = _get(url, timeout).decode("utf-8")
    records = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise CatalogueFetchError(f"{url}: line {number} is not valid JSON: {error}") from error
    return records


def _text(value: Any, prefer: str = "tex") -> str:
    """Pick a rendering from the zoo's ``{tex, html, text}`` triples."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in (prefer, "tex", "text"):
            if isinstance(value.get(key), str):
                return value[key]
    return ""


_HREF_PATTERN = re.compile(r"\\href\{([^}]*)\}\{([^}]*)\}")


def _with_links(item: Any) -> str:
    """Plain-text rendering of a progress item, keeping the URLs its TeX links to."""
    text = _text(item, prefer="text")
    links = [f"[{label}]({url})" for url, label in _HREF_PATTERN.findall(_text(item))]
    return f"{text} ({'; '.join(links)})" if links else text


_ARXIV_PATTERN = re.compile(r"arxiv\.org/abs/([0-9]{4}\.[0-9]{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/[0-9]{7})(?:v\d+)?", re.I)


def _reference(item: dict[str, Any]) -> CatalogueReference:
    urls, arxiv, doi = [], None, None
    for link in item.get("links") or []:
        url = link.get("url")
        if url:
            urls.append(url)
        if link.get("kind") == "arxiv" and link.get("id"):
            arxiv = str(link["id"])
        if link.get("kind") == "doi" and link.get("id"):
            doi = str(link["id"])
    if arxiv is None:
        match = _ARXIV_PATTERN.search(_text(item))
        if match:
            arxiv = match.group(1)
    return CatalogueReference(
        key=str(item.get("key") or item.get("label") or ""),
        text=_text(item, prefer="text"),
        urls=urls,
        arxiv=arxiv,
        doi=doi,
    )


def normalize(record: dict[str, Any]) -> CatalogueEntry:
    """Map one ``qiqcop-zoo/problem/3`` record to a :class:`CatalogueEntry`."""
    try:
        problem_id = str(record["id"])
        status = str(record["status"])
    except KeyError as error:
        raise CatalogueFetchError(f"QIQCOP record lacks required key {error}") from error
    entry = CatalogueEntry(
        id=problem_id,
        source=SOURCE,
        title=_text(record.get("title"), prefer="text"),
        status=status,
        statement=_text(record.get("statement")),
        url=record.get("url"),
        fields=[str(item) for item in record.get("fields") or []],
        topics=[str(item) for item in record.get("topics") or []],
        attribution=_text(record.get("source")),
        progress=[_text(item) for item in record.get("progress") or []],
        comment=_text(record.get("comment")),
        references=[_reference(item) for item in record.get("references") or [] if isinstance(item, dict)],
        related=[str(item.get("id")) for item in record.get("related") or [] if isinstance(item, dict) and item.get("id")],
        aliases=[str(item) for item in record.get("aliases") or []],
        updated=str(record.get("updatedAt") or record.get("updated") or ""),
        statement_text=_text(record.get("statement"), prefer="text"),
        attribution_text=_text(record.get("source"), prefer="text"),
        progress_text=[_with_links(item) for item in record.get("progress") or []],
        comment_text=_text(record.get("comment"), prefer="text"),
    )
    entry.digest = str(record.get("sha256") or entry.content_digest())
    return entry


def keep(entry: CatalogueEntry, include_status: tuple[str, ...], include_fields: tuple[str, ...],
         exclude_topics: tuple[str, ...]) -> bool:
    """Apply the configured import filters."""
    if include_status and entry.status not in include_status:
        return False
    if include_fields and not set(entry.fields) & set(include_fields):
        return False
    return not (exclude_topics and set(entry.topics) & set(exclude_topics))
