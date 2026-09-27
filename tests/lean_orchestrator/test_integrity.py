from __future__ import annotations

import os
from pathlib import Path

import pytest

from lean_orchestrator.integrity import IntegrityGuard
from lean_orchestrator.paths import WorkspacePaths


def make_guard(tmp_path: Path) -> tuple[IntegrityGuard, WorkspacePaths]:
    paths = WorkspacePaths(tmp_path)
    return IntegrityGuard(paths), paths


def test_modified_and_deleted_files_are_restored(tmp_path):
    guard, _ = make_guard(tmp_path)
    state = tmp_path / "problems" / "p" / "state.json"
    guard.write_text(state, '{"stagnation_count": 0}')
    state.write_text('{"stagnation_count": 99}')
    violations = guard.check_and_restore()
    assert [(v.path, v.kind, v.action) for v in violations] == [("problems/p/state.json", "modified", "restored")]
    assert state.read_text() == '{"stagnation_count": 0}'
    state.unlink()
    assert [v.kind for v in guard.check_and_restore()] == ["deleted"]
    assert state.read_text() == '{"stagnation_count": 0}'
    assert guard.check_and_restore() == []


def test_new_files_in_watched_directories_are_quarantined_unless_allowed(tmp_path):
    guard, paths = make_guard(tmp_path)
    iterations = tmp_path / "problems" / "p" / "iterations"
    iterations.mkdir(parents=True)
    guard.protect_tree(iterations, watch=True)
    allowed = iterations / "e001-i01" / "report.json"
    guard.allow_new(allowed)
    allowed.parent.mkdir()
    allowed.write_text("{}")
    forged = iterations / "e001-i01" / "critique.json"
    forged.write_text("{}")
    violations = guard.check_and_restore()
    assert [(v.path, v.action) for v in violations] == [("problems/p/iterations/e001-i01/critique.json", "quarantined")]
    assert not forged.exists() and allowed.exists()
    assert list(paths.quarantine_dir.rglob("critique.json"))
    guard.adopt(allowed)
    allowed.write_text('{"changed": true}')
    assert [v.kind for v in guard.check_and_restore()] == ["modified"]


def test_orchestrator_writes_never_count_as_violations_and_blobs_are_collected(tmp_path):
    guard, paths = make_guard(tmp_path)
    target = tmp_path / "knowledge" / "results.jsonl"
    for index in range(5):
        guard.write_text(target, f"line {index}\n")
        assert guard.check_and_restore() == []
    blobs = [path for path in paths.objects_dir.rglob("*") if path.is_file()]
    assert len(blobs) == 1
    guard.unprotect(target)
    assert not [path for path in paths.objects_dir.rglob("*") if path.is_file()]
    target.write_text("free now")
    assert guard.check_and_restore() == []


def test_touching_without_changing_content_is_not_a_violation(tmp_path):
    guard, _ = make_guard(tmp_path)
    target = tmp_path / "PORTFOLIO.md"
    guard.write_text(target, "same")
    target.write_text("same")
    assert guard.check_and_restore() == []
    assert guard.manifest() == {"PORTFOLIO.md": guard.manifest()["PORTFOLIO.md"]}


def test_same_size_edits_with_an_unchanged_timestamp_are_detected(tmp_path):
    guard, _ = make_guard(tmp_path)
    target = tmp_path / "state.json"
    guard.write_text(target, '{"score": 1}')
    before = target.stat()
    target.write_text('{"score": 3}')
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert [violation.kind for violation in guard.check_and_restore()] == ["modified"]
    assert target.read_text() == '{"score": 1}'


def test_directories_and_links_in_place_of_protected_files_are_quarantined(tmp_path):
    guard, paths = make_guard(tmp_path)
    state = tmp_path / "problems" / "p" / "state.json"
    progress = tmp_path / "problems" / "p" / "PROGRESS.md"
    guard.write_text(state, "{}")
    guard.write_text(progress, "# Progress")
    state.unlink()
    (state / "nested").mkdir(parents=True)
    progress.unlink()
    progress.symlink_to(tmp_path / "elsewhere.md")
    violations = guard.check_and_restore()
    assert sorted((v.path, v.kind) for v in violations) == [("problems/p/PROGRESS.md", "replaced"),
                                                            ("problems/p/state.json", "replaced")]
    assert state.read_text() == "{}" and progress.read_text() == "# Progress" and not progress.is_symlink()
    assert list(paths.quarantine_dir.rglob("state.json/nested"))


def test_a_file_in_place_of_a_parent_directory_is_moved_aside(tmp_path):
    guard, paths = make_guard(tmp_path)
    iterations = tmp_path / "problems" / "p" / "iterations"
    report = iterations / "e001-i01" / "report.json"
    guard.write_text(report, "{}")
    guard.protect_tree(iterations, watch=True)
    report.unlink()
    report.parent.rmdir()
    iterations.rmdir()
    iterations.write_text("not a directory")
    assert [(v.path, v.kind) for v in guard.check_and_restore()] == [("problems/p/iterations/e001-i01/report.json",
                                                                       "deleted")]
    assert report.read_text() == "{}"
    assert [path.read_text() for path in paths.quarantine_dir.rglob("iterations")] == ["not a directory"]


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs file permissions that bind the user")
def test_unreadable_protected_files_are_restored(tmp_path):
    guard, _ = make_guard(tmp_path)
    target = tmp_path / "state.json"
    guard.write_text(target, "{}")
    target.chmod(0)
    assert [v.kind for v in guard.check_and_restore()] == ["modified"]
    assert target.read_text() == "{}"


def test_quarantines_in_the_same_second_keep_every_file(tmp_path):
    guard, paths = make_guard(tmp_path)
    reports = tmp_path / "reports"
    reports.mkdir()
    guard.protect_tree(reports, watch=True)
    for content in ("first", "second"):
        (reports / "forged.json").write_text(content)
        assert [v.kind for v in guard.check_and_restore()] == ["created"]
    assert sorted(path.read_text() for path in paths.quarantine_dir.rglob("forged.json")) == ["first", "second"]
