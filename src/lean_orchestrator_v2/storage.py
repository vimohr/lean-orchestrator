"""Atomic state, immutable packets, frozen submissions, and process exclusion."""

import contextlib
import fcntl
import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path

from lean_orchestrator.jsonio import write_json
from lean_orchestrator.serde import from_dict, to_dict

from .models import CAMPAIGN_SCHEMA, Campaign, Problem


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"path must be relative and stay inside its artifact directory: {relative!r}")
    result = root / path
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"symlink escapes artifact directory: {relative}")
    return result


def copy_files(source: Path, destination: Path) -> None:
    """Copy inspectable source artifacts, never symlinks or generated environments."""
    destination.mkdir(parents=True, exist_ok=False)
    for path in sorted(source.rglob("*")):
        if any(part in (".lake", ".venv", "__pycache__", ".git") for part in path.relative_to(source).parts):
            continue
        if path.is_symlink():
            raise ValueError(f"artifact snapshots cannot contain symlinks: {path}")
        if path.is_file():
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


def seal(directory: Path) -> dict[str, str]:
    hashes = {path.relative_to(directory).as_posix(): digest(path)
              for path in sorted(directory.rglob("*")) if path.is_file() and path != directory / "manifest.json"}
    write_json(directory / "manifest.json", hashes)
    return hashes


def check_seal(directory: Path, expected: dict[str, str] | None = None) -> None:
    hashes = json.loads((directory / "manifest.json").read_text())
    if expected is not None and hashes != expected:
        raise ValueError(f"frozen manifest changed: {directory}")
    actual = {path.relative_to(directory).as_posix(): digest(path)
              for path in sorted(directory.rglob("*")) if path.is_file() and path != directory / "manifest.json"}
    if actual != hashes or any(path.is_symlink() for path in directory.rglob("*")):
        raise ValueError(f"frozen artifacts changed: {directory}")


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.state_path = self.root / "campaign.json"

    @contextlib.contextmanager
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".campaign.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("another command is modifying this campaign") from error
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def load(self) -> Campaign:
        if not self.state_path.exists():
            return Campaign()
        data = json.loads(self.state_path.read_text())
        if data.get("schema") != CAMPAIGN_SCHEMA:
            # Earlier namespaces used the same version and complete field layout.
            schema = data.get("schema")
            parts = schema.split("/") if isinstance(schema, str) else []
            if (len(parts) != 3 or not parts[0] or parts[1:] != ["campaign", "2"]
                    or set(data) != set(Campaign.__dataclass_fields__)):
                raise ValueError("unsupported campaign schema; v2 does not silently migrate another engine's state")
            data["schema"] = CAMPAIGN_SCHEMA
        return from_dict(Campaign, data)

    def save(self, campaign: Campaign) -> None:
        write_json(self.state_path, to_dict(campaign))

    def add(self, campaign: Campaign, packet: dict, base: Path) -> Problem:
        identifier = packet.get("id", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", identifier) or identifier in campaign.problems:
            raise ValueError("packet.id must be a new identifier using letters, digits, underscores or hyphens")
        for key in ("title", "original_statement"):
            if not isinstance(packet.get(key), str) or not packet[key].strip():
                raise ValueError(f"packet.{key} is required")
        contract = packet.get("scope_contract", {})
        for key in ("quantifiers", "dimensions", "allowed_resources", "conventions", "source_locators", "unresolved"):
            if not isinstance(contract.get(key), list) or not all(isinstance(item, str) for item in contract[key]):
                raise ValueError(f"packet.scope_contract.{key} must be an explicit list")
        directory = self.root / "packets" / identifier
        directory.mkdir(parents=True, exist_ok=False)
        prepared = {**packet, "sources": []}
        try:
            for index, source in enumerate(packet.get("sources", [])):
                original = safe_path(base, source["path"])
                if not original.is_file() or original.is_symlink():
                    raise ValueError(f"source must be a regular local file: {original}")
                relative = f"sources/{index:03d}-{original.name}"
                target = safe_path(directory, relative)
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(original, target)
                excerpt = source.get("excerpt", "")
                if not excerpt and original.suffix.lower() in (".txt", ".md", ".tex", ".json", ".lean", ".py"):
                    excerpt = original.read_text()
                if not isinstance(excerpt, str) or len(excerpt) > 60_000:
                    raise ValueError("prepared source excerpts must be text of at most 60000 characters")
                if original.suffix.lower() == ".pdf" and not excerpt.strip():
                    raise ValueError("prepare a relevant PDF excerpt and locator outside the isolated solver")
                prepared["sources"].append({**source, "path": relative, "sha256": digest(target), "excerpt": excerpt})
            write_json(directory / "packet.json", prepared)
            seal(directory)
        except Exception:
            quarantine = self.root / "rejected-packets" / f"{identifier}-{uuid.uuid4().hex[:8]}"
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(directory, quarantine)
            raise
        problem = Problem(identifier, packet["title"], directory.relative_to(self.root).as_posix(),
                          digest(directory / "packet.json"))
        campaign.problems[identifier] = problem
        return problem

    def packet(self, problem: Problem) -> dict:
        directory = safe_path(self.root, problem.packet)
        check_seal(directory)
        if digest(directory / "packet.json") != problem.packet_hash:
            raise ValueError("original packet changed")
        packet = json.loads((directory / "packet.json").read_text())
        for source in packet["sources"]:
            if digest(safe_path(directory, source["path"])) != source["sha256"]:
                raise ValueError("prepared source changed")
        return packet
