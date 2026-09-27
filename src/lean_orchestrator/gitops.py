"""Version the workspace: every recorded iteration becomes a commit."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from .config import GitConfig


class GitError(RuntimeError):
    """A git command failed."""


class GitRepo:
    def __init__(self, root: Path, config: GitConfig) -> None:
        self.root = root
        self.config = config
        self._lock = threading.Lock()

    def _git(self, *arguments: str, check: bool = True, stdin: str | None = None) -> subprocess.CompletedProcess:
        command = ["git", "-c", f"user.name={self.config.author_name}", "-c", f"user.email={self.config.author_email}",
                   *arguments]
        completed = subprocess.run(command, cwd=self.root, capture_output=True, text=True, input=stdin)
        if check and completed.returncode != 0:
            raise GitError(f"git {' '.join(arguments[:3])} failed: {completed.stderr.strip() or completed.stdout.strip()}")
        return completed

    def is_repository(self) -> bool:
        completed = self._git("rev-parse", "--is-inside-work-tree", check=False)
        return completed.returncode == 0 and completed.stdout.strip() == "true"

    def ensure(self) -> None:
        if not self.config.enabled:
            return
        with self._lock:
            if not self.is_repository():
                self._git("init", "-b", "main")

    def commit(self, paths: list[Path], message: str) -> bool:
        """Commit changes under ``paths`` only; returns whether a commit was made."""
        if not self.config.enabled:
            return False
        existing = [str(path.relative_to(self.root)) for path in paths if path.exists() or path.is_symlink()]
        if not existing:
            return False
        with self._lock:
            self._git("add", "-A", "--", *existing)
            staged = self._git("diff", "--cached", "--name-only", "-z", "--", *existing).stdout
            if not staged.strip("\0"):
                return False
            # Pass the exact staged files on stdin: pathspecs that match nothing make git fail,
            # and long file lists must not hit the argument-length limit.
            self._git("commit", "--no-verify", "-q", "-m", message, "--pathspec-from-file=-", "--pathspec-file-nul",
                      stdin=staged)
            return True

    def commit_all(self, message: str) -> bool:
        if not self.config.enabled:
            return False
        with self._lock:
            self._git("add", "-A")
            staged = self._git("diff", "--cached", "--quiet", check=False)
            if staged.returncode == 0:
                return False
            self._git("commit", "--no-verify", "-q", "-m", message)
            return True
