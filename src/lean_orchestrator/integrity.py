"""Protect orchestrator-owned files from modification by agents.

Agents run with write access to the workspace. Every file the orchestrator owns
(research state, iteration records, locked Lean statements, configuration) is
registered here with its expected hash, and a copy is kept in a content-addressed
object store. After each agent call, :meth:`IntegrityGuard.check_and_restore`
restores modified or deleted files and quarantines unexpected new files inside
watched directories. Orchestrator writes go through the guard, so concurrent
epochs never trigger false alarms.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .jsonio import atomic_write_bytes, dumps, sha256_bytes, utc_now
from .paths import WorkspacePaths


@dataclass(frozen=True)
class _Expected:
    sha256: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class Violation:
    path: str
    kind: str
    action: str
    time: str

    def describe(self) -> str:
        return f"{self.path} was {self.kind}; {self.action}"


class IntegrityGuard:
    def __init__(self, paths: WorkspacePaths) -> None:
        self.paths = paths
        self._lock = threading.RLock()
        self._expected: dict[str, _Expected] = {}
        self._watched_dirs: set[str] = set()
        self._allowed_new: set[str] = set()
        self._blob_refs: Counter[str] = Counter()

    def reset_store(self) -> None:
        """Drop blobs left by a previous run; call once before protecting files."""
        with self._lock:
            self._expected.clear()
            self._blob_refs.clear()
            self._watched_dirs.clear()
            self._allowed_new.clear()
            shutil.rmtree(self.paths.objects_dir, ignore_errors=True)

    def _relative(self, path: Path) -> str:
        return self.paths.relative(path)

    def _absolute(self, relative: str) -> Path:
        return self.paths.root / relative

    def _blob_path(self, digest: str) -> Path:
        return self.paths.objects_dir / digest[:2] / digest

    def _store_blob(self, digest: str, data: bytes) -> None:
        blob = self._blob_path(digest)
        if not blob.exists():
            atomic_write_bytes(blob, data)

    def _release_blob(self, digest: str) -> None:
        self._blob_refs[digest] -= 1
        if self._blob_refs[digest] <= 0:
            del self._blob_refs[digest]
            self._blob_path(digest).unlink(missing_ok=True)

    def _record(self, relative: str, data: bytes) -> None:
        path = self._absolute(relative)
        stat = path.stat()
        digest = sha256_bytes(data)
        previous = self._expected.get(relative)
        if previous is not None and previous.sha256 == digest:
            self._expected[relative] = _Expected(digest, stat.st_size, stat.st_mtime_ns)
            return
        self._store_blob(digest, data)
        self._blob_refs[digest] += 1
        self._expected[relative] = _Expected(digest, stat.st_size, stat.st_mtime_ns)
        if previous is not None:
            self._release_blob(previous.sha256)

    def protect(self, path: Path) -> None:
        """Freeze the current content of an existing file."""
        with self._lock:
            relative = self._relative(path)
            self._record(relative, path.read_bytes())
            self._allowed_new.discard(relative)

    def protect_tree(self, directory: Path, *, watch: bool = True) -> None:
        """Protect every file below ``directory``; optionally forbid new files there."""
        with self._lock:
            if watch:
                self._watched_dirs.add(self._relative(directory))
            if directory.is_dir():
                for path in sorted(directory.rglob("*")):
                    if path.is_file() and not path.is_symlink():
                        self.protect(path)

    def unprotect(self, path: Path) -> None:
        with self._lock:
            expected = self._expected.pop(self._relative(path), None)
            if expected is not None:
                self._release_blob(expected.sha256)

    def is_protected(self, path: Path) -> bool:
        with self._lock:
            return self._relative(path) in self._expected

    def write_bytes(self, path: Path, data: bytes) -> None:
        with self._lock:
            atomic_write_bytes(path, data)
            self._record(self._relative(path), data)
            self._allowed_new.discard(self._relative(path))

    def write_text(self, path: Path, text: str) -> None:
        self.write_bytes(path, text.encode("utf-8"))

    def write_json(self, path: Path, value: Any) -> None:
        self.write_text(path, dumps(value))

    def allow_new(self, path: Path) -> None:
        """Permit an agent to create ``path`` inside a watched directory (its output file)."""
        with self._lock:
            self._allowed_new.add(self._relative(path))

    def adopt(self, path: Path) -> None:
        """Take ownership of an agent-written output after reading it."""
        with self._lock:
            self._allowed_new.discard(self._relative(path))
            if path.is_file():
                self.protect(path)

    def check_and_restore(self) -> list[Violation]:
        """Undo unexpected changes to protected files; return what was found."""
        violations: list[Violation] = []
        with self._lock:
            for relative, expected in list(self._expected.items()):
                path = self._absolute(relative)
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    self._restore(relative, expected)
                    violations.append(Violation(relative, "deleted", "restored", utc_now()))
                    continue
                if (stat.st_size, stat.st_mtime_ns) == (expected.size, expected.mtime_ns):
                    continue
                data = path.read_bytes()
                if sha256_bytes(data) == expected.sha256:
                    self._expected[relative] = _Expected(expected.sha256, stat.st_size, stat.st_mtime_ns)
                    continue
                self._restore(relative, expected)
                violations.append(Violation(relative, "modified", "restored", utc_now()))
            for watched in sorted(self._watched_dirs):
                directory = self._absolute(watched)
                if not directory.is_dir():
                    continue
                for path in sorted(directory.rglob("*")):
                    if not path.is_file() and not path.is_symlink():
                        continue
                    relative = self._relative(path) if not path.is_symlink() else path.relative_to(self.paths.root).as_posix()
                    if relative in self._expected or relative in self._allowed_new:
                        continue
                    self._quarantine(path, relative)
                    violations.append(Violation(relative, "created", "quarantined", utc_now()))
        return violations

    def _restore(self, relative: str, expected: _Expected) -> None:
        path = self._absolute(relative)
        if path.is_symlink() or path.is_dir():
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        data = self._blob_path(expected.sha256).read_bytes()
        atomic_write_bytes(path, data)
        stat = path.stat()
        self._expected[relative] = _Expected(expected.sha256, stat.st_size, stat.st_mtime_ns)

    def _quarantine(self, path: Path, relative: str) -> None:
        target = self.paths.quarantine_dir / time.strftime("%Y%m%d-%H%M%S") / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, target)

    def manifest(self) -> dict[str, str]:
        """Expected hashes of all protected files, for audits and tests."""
        with self._lock:
            return {relative: expected.sha256 for relative, expected in sorted(self._expected.items())}


def describe_violations(violations: list[Violation]) -> str:
    return json.dumps([violation.describe() for violation in violations], indent=2)
