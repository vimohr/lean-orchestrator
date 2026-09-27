from __future__ import annotations

from support import add_problem

from lean_orchestrator.problem_setup import ensure_problem_folder, protect_problem
from lean_orchestrator.state import load_state


def test_folders_are_created_once_with_unique_names(make_orchestrator):
    orchestrator = make_orchestrator()
    services = orchestrator.services
    add_problem(orchestrator, "local_twin_a", "Twin problem")
    add_problem(orchestrator, "local_twin_b", "Twin problem")
    first = services.portfolio.get("local_twin_a")
    problem_a, state_a = ensure_problem_folder(services, first, services.catalogue.load("local", "local_twin_a"))
    second = services.portfolio.get("local_twin_b")
    problem_b, _ = ensure_problem_folder(services, second, services.catalogue.load("local", "local_twin_b"))
    assert problem_a.root != problem_b.root
    assert problem_a.lean_namespace != problem_b.lean_namespace
    assert state_a.statement_versions[0].text.startswith("Show that")
    assert (problem_a.lean_dir / "README.md").is_file() and problem_a.lean_link.resolve() == problem_a.lean_dir.resolve()
    again, state_again = ensure_problem_folder(services, services.portfolio.get("local_twin_a"),
                                               services.catalogue.load("local", "local_twin_a"))
    assert again.root == problem_a.root and state_again == load_state(problem_a.state)


def test_protect_problem_reports_changed_locked_files(make_orchestrator):
    orchestrator = make_orchestrator()
    services = orchestrator.services
    add_problem(orchestrator, "local_lock", "Locked files")
    problem, state = ensure_problem_folder(services, services.portfolio.get("local_lock"),
                                           services.catalogue.load("local", "local_lock"))
    locked = problem.lean_dir / "Verified.lean"
    locked.write_text("theorem x : True := trivial\n")
    state.locked_lean_files = {services.paths.relative(locked): "0" * 64,
                               "lean/OpenQ/Problems/Missing.lean": "1" * 64}
    warnings = protect_problem(services, problem, state)
    assert any("changed while the orchestrator was not running" in warning for warning in warnings)
    assert any("is missing" in warning for warning in warnings)
    assert services.guard.is_protected(locked)
