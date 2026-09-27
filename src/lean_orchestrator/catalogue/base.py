"""Normalized catalogue entries shared by every problem source."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..jsonio import read_json, sha256_bytes, write_json
from ..paths import WorkspacePaths
from ..serde import from_dict, to_dict

OPEN_STATUSES = frozenset({"Unsolved", "Open"})


@dataclass
class CatalogueReference:
    key: str
    text: str
    urls: list[str] = field(default_factory=list)
    arxiv: str | None = None
    doi: str | None = None


@dataclass
class CatalogueEntry:
    """One open problem as imported from an upstream source, before any research."""

    id: str
    source: str
    title: str
    status: str
    statement: str
    url: str | None = None
    fields: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    attribution: str = ""
    progress: list[str] = field(default_factory=list)
    comment: str = ""
    references: list[CatalogueReference] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    updated: str = ""
    digest: str = ""
    statement_text: str = ""
    attribution_text: str = ""
    progress_text: list[str] = field(default_factory=list)
    comment_text: str = ""

    def readable(self, name: str) -> str | list[str]:
        """Prefer the upstream plain-text rendering, falling back to TeX."""
        text = getattr(self, f"{name}_text", None)
        return text if text else getattr(self, name)

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    def content_digest(self) -> str:
        """Hash of the research-relevant content, used to detect upstream changes."""
        material = "\x1f".join([
            self.title, self.status, self.statement, self.attribution,
            "\x1e".join(self.progress), self.comment,
            "\x1e".join(reference.text for reference in self.references),
        ])
        return sha256_bytes(material.encode("utf-8"))


class CatalogueStore:
    """Persist normalized entries as ``catalogue/<source>/<id>.json``."""

    def __init__(self, paths: WorkspacePaths, writer: Callable[[Path, Any], None] | None = None) -> None:
        self.paths = paths
        self._write = writer or write_json

    def path(self, entry_source: str, problem_id: str) -> Path:
        return self.paths.catalogue_entry(entry_source, problem_id)

    def save(self, entry: CatalogueEntry) -> None:
        self._write(self.path(entry.source, entry.id), to_dict(entry))

    def load(self, entry_source: str, problem_id: str) -> CatalogueEntry:
        return from_dict(CatalogueEntry, read_json(self.path(entry_source, problem_id)))

    def exists(self, entry_source: str, problem_id: str) -> bool:
        return self.path(entry_source, problem_id).is_file()

    def all(self) -> list[CatalogueEntry]:
        entries = []
        if not self.paths.catalogue_dir.is_dir():
            return entries
        for source_dir in sorted(p for p in self.paths.catalogue_dir.iterdir() if p.is_dir()):
            for path in sorted(source_dir.glob("*.json")):
                entries.append(from_dict(CatalogueEntry, read_json(path)))
        return entries
