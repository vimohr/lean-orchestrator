from __future__ import annotations

import subprocess

from lean_orchestrator.config import GitConfig
from lean_orchestrator.gitops import GitRepo


def _log(root) -> list[str]:
    return subprocess.run(["git", "log", "--format=%s|%an"], cwd=root, capture_output=True, text=True).stdout.split("\n")


def test_commit_only_touches_the_given_paths(tmp_path):
    repo = GitRepo(tmp_path, GitConfig())
    repo.ensure()
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.txt").write_text("1")
    (tmp_path / "b.txt").write_text("2")
    assert repo.commit([tmp_path / "a"], "research(a): first")
    assert not repo.commit([tmp_path / "a"], "nothing changed")
    assert _log(tmp_path)[0] == "research(a): first|lean-orch"
    status = subprocess.run(["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True, text=True).stdout
    assert "b.txt" in status
    assert repo.commit_all("chore: rest")
    assert not repo.commit([tmp_path / "missing"], "skip")


def test_disabled_git_does_nothing(tmp_path):
    repo = GitRepo(tmp_path, GitConfig(enabled=False))
    repo.ensure()
    assert not (tmp_path / ".git").exists()
    assert not repo.commit_all("x")
