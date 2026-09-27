"""Problems added by hand with ``lean-orch add``."""

from __future__ import annotations

from ..jsonio import utc_now
from ..paths import slugify
from .base import CatalogueEntry, CatalogueReference

SOURCE = "local"


def make_entry(
    title: str,
    statement: str,
    *,
    problem_id: str | None = None,
    fields: list[str] | None = None,
    topics: list[str] | None = None,
    attribution: str = "",
    references: list[str] | None = None,
) -> CatalogueEntry:
    if not title.strip():
        raise ValueError("a problem needs a title")
    if not statement.strip():
        raise ValueError("a problem needs a statement")
    entry = CatalogueEntry(
        id=problem_id or f"local_{slugify(title, max_length=40).replace('-', '_')}",
        source=SOURCE,
        title=title.strip(),
        status="Open",
        statement=statement.strip(),
        fields=list(fields or []),
        topics=list(topics or []),
        attribution=attribution,
        references=[CatalogueReference(key=f"R{index}", text=text) for index, text in enumerate(references or [], 1)],
        updated=utc_now(),
    )
    entry.digest = entry.content_digest()
    return entry
